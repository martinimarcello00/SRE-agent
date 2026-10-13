#!/usr/bin/env bash
# Preflight for automated_experiment.py: brings up registry mirrors + Neo4j and checks the environment.
# Usage: scripts/preflight.sh [--clean] [--run]
#   --clean  delete a leftover kind cluster named "kind" (default: abort)
#   --run    launch automated_experiment.py when all checks pass
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
CLEAN=0; RUN=0
for a in "$@"; do
  case "$a" in
    --clean) CLEAN=1 ;;
    --run) RUN=1 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

ok()   { echo "✓ $*"; }
fail() { echo "✗ $*" >&2; exit 1; }

# Read a value from .env without sourcing it (never printed)
envval() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

echo "== Tools"
for t in docker kind kubectl helm npx poetry curl python3; do
  command -v "$t" >/dev/null || fail "missing tool: $t"
done
ok "docker kind kubectl helm npx poetry curl python3"
poetry env info -p >/dev/null 2>&1 || fail "no Poetry venv: run 'poetry install' in $ROOT"
ok "Poetry venv"

echo "== .env"
[ -f "$ENV_FILE" ] || fail "$ENV_FILE not found"
for k in OPENAI_API_KEY OPENAI_ADMIN_API_KEY NEO4J_USER NEO4J_PASSWORD AIOPSLAB_DIR RESULTS_PATH; do
  [ -n "$(envval "$k")" ] || fail "$k is empty or missing in .env"
done
ok "required keys set"
AIOPSLAB_DIR="$(envval AIOPSLAB_DIR)"
RESULTS_PATH="$(envval RESULTS_PATH)"
[ -f "$AIOPSLAB_DIR/kind/kind-config-x86.yaml" ] || fail "kind config not found in $AIOPSLAB_DIR/kind"
mkdir -p "$RESULTS_PATH" && [ -w "$RESULTS_PATH" ] || fail "RESULTS_PATH not writable: $RESULTS_PATH"
ok "AIOPSLAB_DIR and RESULTS_PATH"

echo "== Docker"
docker info >/dev/null 2>&1 || fail "docker daemon not reachable"
ok "docker daemon"

echo "== Registry mirrors"
"$ROOT/registry/setup-registry.sh"
for p in 5001 5002 5003; do
  curl -fs --max-time 5 "http://127.0.0.1:$p/v2/" >/dev/null || fail "registry on :$p does not answer (docker logs kind-registry*)"
done
ok "registries answer on 5001/5002/5003"

echo "== Neo4j"
docker inspect neo4j >/dev/null 2>&1 || fail "container 'neo4j' does not exist (create it first)"
if [ "$(docker inspect -f '{{.State.Running}}' neo4j)" != true ]; then
  docker start neo4j >/dev/null
  ok "neo4j started"
fi
for _ in $(seq 60); do port_open 7687 && break; sleep 1; done
port_open 7687 || fail "neo4j bolt port 7687 not open after 60s (docker logs neo4j)"
ok "neo4j bolt on :7687"

echo "== kind"
if kind get clusters 2>/dev/null | grep -qx kind; then
  if [ "$CLEAN" = 1 ]; then
    kind delete cluster
    ok "leftover kind cluster deleted"
  else
    fail "a kind cluster named 'kind' already exists (the run would delete it): re-run with --clean or delete it yourself"
  fi
else
  ok "no leftover kind cluster"
fi

echo "== Experiments to run"
python3 - "$ROOT/sre-agent/experiments_runner" <<'EOF'
import json, sys, glob
base = sys.argv[1]
def load(d):
    return [json.load(open(f)) for f in sorted(glob.glob(f"{base}/{d}/*.json"))]
scen = [s for s in load("fault-scenarios") if s.get("execute")]
agents = [a for a in load("agent-configurations") if a.get("execute")]
total = len(scen) * sum(a.get("runs", 1) for a in agents)
apps = sorted({s.get("scenario", "?") for s in scen})
print(f"{len(scen)} scenarios ({', '.join(apps)}) x {len(agents)} agents -> {total} experiments")
EOF

if [ "$RUN" = 1 ]; then
  echo "== Launching automated_experiment.py"
  cd "$ROOT/sre-agent"
  exec poetry run python automated_experiment.py
fi
ok "preflight passed"
