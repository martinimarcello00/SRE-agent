# Local Registry Implementation Summary


## How It Works

1.  **Registry Container**: A local Docker registry runs on `localhost:5001` (mapped to port 5000 inside the container). It is configured to proxy requests to Docker Hub.
2.  **Kind Configuration**: The Kind cluster is configured via `containerdConfigPatches` to look for registry configurations in `/etc/containerd/certs.d`.
3.  **Node Configuration**: Each Kind node (control-plane and workers) has a configuration file (`hosts.toml`) injected into `/etc/containerd/certs.d/docker.io/`.
4.  **Transparent Mirroring**: When a pod requests an image (e.g., `redis:latest`), `containerd` on the node checks the `docker.io` config, sees the mirror at `http://kind-registry:5000`, and requests the image from there.
    *   **Cache Miss**: The registry pulls from Docker Hub, caches it, and serves it.
    *   **Cache Hit**: The registry serves it instantly from disk.

---

## Upstreams: Docker Hub, ghcr.io, quay.io

A registry in proxy mode mirrors only **one** upstream, so there is one container per upstream:

| Container | Host port | Upstream |
|---|---|---|
| `kind-registry` | 5001 | Docker Hub (`docker.io`) |
| `kind-registry-ghcr` | 5002 | `ghcr.io` |
| `kind-registry-quay` | 5003 | `quay.io` |

Astronomy Shop (OTel demo chart 0.42.1) pulls 25 of its ~35 images from `ghcr.io` and 2 from `quay.io`, so the Docker Hub mirror alone does not speed it up. The mirror table lives in `REGISTRY_MIRRORS` in `automate_cluster_creation.py`.

Note: `/v2/_catalog` may be empty or incomplete on proxy registries even when caching works. Check the cache with
`docker exec kind-registry-ghcr du -sh /var/lib/registry` or `docker logs kind-registry-ghcr`.

Cache lifetime and storage:
- The cache lives in the container's **anonymous volume**. It survives `docker stop`/`start`, but `docker rm` orphans it. `setup-registry.sh` therefore only creates missing containers and `docker start`s stopped ones.
- `registry:2.8.3` has no `proxy.ttl` option: expiry is fixed at 168h and, without `storage.delete.enabled`, is expected to remove nothing from disk (inferred from the code, to be confirmed in the logs after the first expiry). Do **not** add `ttl:` (ignored) or `storage.delete.enabled: true` (known bugs with blobs shared across repositories).
- A stopped mirror does not break pulls (containerd falls back to the upstream) but loses the cache benefit, so `setup_cluster_and_aiopslab` refuses to start unless all three mirrors are running and answer `/v2/`. Fix with `docker start kind-registry kind-registry-ghcr kind-registry-quay`.
- The Astronomy Shop chart is pinned to `0.42.1` in AIOpsLab's `astronomy-shop.json`; a different chart version changes image tags and makes the cache cold.

---

## Step 1: Start the Registry Container

We use a helper script to start the registry with the correct configuration (proxy mode enabled).

**Script:** `registry/setup-registry.sh`

```bash
# Run the setup script
chmod +x registry/setup-registry.sh
./registry/setup-registry.sh
```

This script:
1.  Starts the three registry containers (ports `5001`-`5003`).
2.  Mounts the matching `registry-config*.yml` into each to enable its upstream mirror.
3.  Connects the registry to the `kind` network (if it exists).

**Important:** Do not just run `docker run registry:2`. You **must** use the script or mount the config file, otherwise the registry won't cache anything from Docker Hub.

---

## Step 2: Kind Cluster Configuration

The Kind configuration file must instruct `containerd` to use the `certs.d` directory for registry configurations. This avoids the need to manually patch `config.toml` and restart services later.

**File:** `kind/kind-config-x86.yaml`

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4

# This patch tells containerd to look for registry configs in /etc/containerd/certs.d
containerdConfigPatches:
- |-
  [plugins."io.containerd.grpc.v1.cri".registry]
    config_path = "/etc/containerd/certs.d"

nodes:
  - role: control-plane
    image: jacksonarthurclark/aiopslab-kind-x86:latest
    extraMounts:
      - hostPath: /run/udev
        containerPath: /run/udev
    extraPortMappings:
      - containerPort: 32000 # Prometheus server
        hostPort: 32000
      - containerPort: 2333 # Chaos mesh dashboard
        hostPort: 2333
      - containerPort: 30686 # Jaeger UI
        hostPort: 16686
  - role: worker
    image: jacksonarthurclark/aiopslab-kind-x86:latest
    extraMounts:
      - hostPath: /run/udev
        containerPath: /run/udev
```

---

## Step 3: Node Configuration (Automated)

The automation script (`sre-agent/experiments_runner/automate_cluster_creation.py`) handles the injection of the mirror configuration into the nodes.

It performs the following actions on every node:

1.  Creates the directory `/etc/containerd/certs.d/docker.io`.
2.  Creates a `hosts.toml` file with the following content:

```toml
server = "https://registry-1.docker.io"

[host."http://kind-registry:5000"]
  capabilities = ["pull", "resolve"]
```

This tells `containerd` that for `docker.io` images, it should try `http://kind-registry:5000` first.

---

## Verification

### 1. Check Registry Catalog
After pulling an image in the cluster, check if it appears in the local registry catalog:

```bash
curl -s http://localhost:5001/v2/_catalog
```
*   **Empty `[]`**: Nothing cached yet.
*   **List of images**: Caching is working.

### 2. Check Registry Logs
Watch the logs to see if requests are hitting the registry:

```bash
docker logs -f kind-registry
```
*   Look for `GET /v2/...` requests.
*   **First Pull**: Slower response time (downloading from Hub).
*   **Subsequent Pulls**: Fast response time (serving from cache).


## Quick Start

### 1. One-time Registry Setup

```bash
./setup-registry.sh
```

This:
- Creates the three registry containers (`kind-registry`, `-ghcr`, `-quay`), or starts them if stopped
- Sets each up to auto-cache images from its upstream
- Stores cached images in the container's anonymous volume (`/var/lib/registry`)
- Configured to restart automatically

### 2. Use Images Normally

No changes needed to your Kubernetes manifests:

```yaml
containers:
- name: my-app
  image: redis:latest
- name: my-service
  image: yinfangchen/geo:app3
```

### 3. Automatic Behavior

- **First pull**: Registry downloads from Docker Hub, caches locally
- **Subsequent pulls**: Served instantly from cache
- **Offline**: Works offline for cached images

## Usage Examples

### Check Cached Images

```bash
curl -s http://localhost:5001/v2/_catalog | python3 -m json.tool   # 5002 ghcr, 5003 quay
```

### View Registry Logs

```bash
docker logs kind-registry
```

### Stop/Start Registry

```bash
docker stop kind-registry    # Keeps cached images
docker start kind-registry
```

### Clear Cache

```bash
docker rm -f -v kind-registry   # -v also deletes the anonymous volume holding the cache
./setup-registry.sh             # Creates fresh registry
```

## Integration with Experiments

In `automated_experiment.py`:

- Registry is enabled by default: `enable_local_registry=True`
- Cluster automatically configured on startup
- Images cached transparently as experiments run

To disable (not recommended):

```python
success = setup_cluster_and_aiopslab(
    problem_id=scenario["aiopslab_command"],
    aiopslab_dir=AIOPSLAB_DIR,
    enable_local_registry=False
)
```