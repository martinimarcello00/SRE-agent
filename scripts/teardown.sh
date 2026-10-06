#!/usr/bin/env bash
# Teardown: undo what preflight.sh / automated_experiment.py bring up.
# Deletes the kind cluster, stops Neo4j and the registry mirrors. Never `docker rm`: the registry cache
# lives in the containers' anonymous volumes and would be lost.
# Usage: scripts/teardown.sh
set -euo pipefail

ok()   { echo "✓ $*"; }
fail() { echo "✗ $*" >&2; exit 1; }

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

echo "== kind"
if kind get clusters 2>/dev/null | grep -qx kind; then
  kind delete cluster
  ok "kind cluster deleted"
else
  ok "no kind cluster"
fi

echo "== Containers"
for c in neo4j kind-registry kind-registry-ghcr kind-registry-quay; do
  if ! docker inspect "$c" >/dev/null 2>&1; then
    ok "$c does not exist"
    continue
  fi
  docker update --restart=no "$c" >/dev/null   # registries are created with --restart=always
  docker stop "$c" >/dev/null
  ok "$c stopped"
done

echo "== Check"
for p in 5001 5002 5003 7687; do
  ! port_open "$p" || fail "port $p still open"
done
ok "ports 5001/5002/5003/7687 closed"
