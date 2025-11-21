#!/bin/sh
# Setup local Docker registry with pull-through cache for kind cluster
# Based on: https://kind.sigs.k8s.io/docs/user/local-registry/

set -o errexit

REG_NAME='kind-registry'
REG_PORT='5001'
CONFIG_FILE="$(cd "$(dirname "$0")" && pwd)/registry-config.yml"

echo "Setting up local Docker registry with pull-through cache..."
echo ""

# Verify config file exists
if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: registry-config.yml not found at $CONFIG_FILE"
  exit 1
fi

# Create registry container unless it already exists
if [ "$(docker inspect -f '{{.State.Running}}' "${REG_NAME}" 2>/dev/null || true)" != 'true' ]; 
then                                                                                              echo "Creating registry container on port ${REG_PORT}..."
  docker run \
    -d --restart=always \
    -p "127.0.0.1:${REG_PORT}:5000" \
    --network bridge \
    --name "${REG_NAME}" \
    -v "${CONFIG_FILE}:/etc/docker/registry/config.yml:ro" \
    registry:2 /etc/docker/registry/config.yml
  echo "✓ Registry created with pull-through cache enabled"
  sleep 2
else
  echo "✓ Registry already running"
fi

# Connect registry to kind network (if kind network exists)
if docker network ls | grep -q "^kind "; then
  docker network connect kind "${REG_NAME}" 2>/dev/null || true
  echo "✓ Registry connected to kind network"
else
  echo "ℹ kind network not found (kind cluster may not be created yet)"
fi

echo ""
echo "Registry is ready at: localhost:${REG_PORT}"
echo ""
echo "How it works:"
echo "  1. Cluster requests image (e.g., redis:latest)"
echo "  2. Registry checks local cache - if found, serves it"
echo "  3. If not found, registry downloads from Docker Hub and caches it"
echo "  4. Next request for same image is instant (from cache)"
echo ""
echo "No manual image management needed - everything is automatic!"
echo ""
echo "Useful commands:"
echo "  List cached images: curl -s http://localhost:${REG_PORT}/v2/_catalog | python3 -m json.tool"
echo "  View logs: docker logs ${REG_NAME}"
echo "  Stop: docker stop ${REG_NAME}"
echo "  Remove: docker rm -f ${REG_NAME}"
