#!/bin/sh
# Setup local pull-through caches (Docker Hub, ghcr.io, quay.io) for the kind cluster
# Based on: https://kind.sigs.k8s.io/docs/user/local-registry/
# A registry in proxy mode can mirror only ONE upstream, hence one container per upstream.
# Astronomy Shop (OTel demo) pulls mostly from ghcr.io, so docker.io alone is not enough.

set -o errexit

DIR="$(cd "$(dirname "$0")" && pwd)"

# name:host-port:config-file
for entry in \
  "kind-registry:5001:registry-config.yml" \
  "kind-registry-ghcr:5002:registry-config-ghcr.yml" \
  "kind-registry-quay:5003:registry-config-quay.yml"; do
  REG_NAME="${entry%%:*}"; rest="${entry#*:}"
  REG_PORT="${rest%%:*}"; CONFIG_FILE="$DIR/${rest#*:}"

  [ -f "$CONFIG_FILE" ] || { echo "ERROR: $CONFIG_FILE not found"; exit 1; }

  # Never `docker rm` an existing container: the cache lives in its anonymous volume and would be orphaned.
  state="$(docker inspect -f '{{.State.Running}}' "${REG_NAME}" 2>/dev/null || echo absent)"
  if [ "$state" = absent ]; then
    docker run -d --restart=always -p "127.0.0.1:${REG_PORT}:5000" --network bridge \
      --name "${REG_NAME}" -v "${CONFIG_FILE}:/etc/docker/registry/config.yml:ro" \
      registry:2.8.3 /etc/docker/registry/config.yml >/dev/null
    echo "✓ ${REG_NAME} created on localhost:${REG_PORT}"
  elif [ "$state" = false ]; then
    docker start "${REG_NAME}" >/dev/null
    echo "✓ ${REG_NAME} started (cache kept)"
  else
    echo "✓ ${REG_NAME} already running"
  fi

  if docker network ls | grep -q "^[0-9a-f]* *kind "; then
    docker network connect kind "${REG_NAME}" 2>/dev/null || true
  fi
done

echo "List cached images: curl -s http://localhost:5001/v2/_catalog (5002 ghcr, 5003 quay)"
