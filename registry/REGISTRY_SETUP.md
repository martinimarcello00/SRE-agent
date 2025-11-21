# Local Registry Implementation Summary


## How It Works

1.  **Registry Container**: A local Docker registry runs on `localhost:5001` (mapped to port 5000 inside the container). It is configured to proxy requests to Docker Hub.
2.  **Kind Configuration**: The Kind cluster is configured via `containerdConfigPatches` to look for registry configurations in `/etc/containerd/certs.d`.
3.  **Node Configuration**: Each Kind node (control-plane and workers) has a configuration file (`hosts.toml`) injected into `/etc/containerd/certs.d/docker.io/`.
4.  **Transparent Mirroring**: When a pod requests an image (e.g., `redis:latest`), `containerd` on the node checks the `docker.io` config, sees the mirror at `http://kind-registry:5000`, and requests the image from there.
    *   **Cache Miss**: The registry pulls from Docker Hub, caches it, and serves it.
    *   **Cache Hit**: The registry serves it instantly from disk.

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
1.  Starts the `kind-registry` container on port `5001`.
2.  Mounts `registry/registry-config.yml` to enable the Docker Hub mirror.
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
- Creates a `kind-registry` Docker container
- Sets it up to auto-cache images from Docker Hub
- Stores cached images in `./registry-data/`
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
curl -s http://localhost:5000/v2/_catalog | python3 -m json.tool
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
docker rm -f kind-registry
rm -rf registry-data/
./setup-registry.sh          # Creates fresh registry
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