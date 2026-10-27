# Run from sre-agent/: PYTHONPATH=experiments_runner python tests/test_cluster_cleanup.py  (no docker / kind needed)
import tempfile
from pathlib import Path

import automate_cluster_creation as acc

aiopslab = Path(tempfile.mkdtemp())  # setup only needs kind/kind-config-x86.yaml to exist
(aiopslab / "kind").mkdir()
(aiopslab / "kind" / "kind-config-x86.yaml").touch()

acc.time.sleep = lambda s: None
deletes = []
acc.run_command_with_wait = lambda cmd, **kw: deletes.append(cmd) or (1, "could not kill container")

# Docker fails to kill the worker on the first delete: retry until the node is gone
nodes = [["abc"], []]
acc.kind_nodes = lambda: nodes.pop(0)
assert acc.cleanup_cluster() and len(deletes) == 2

# A node that never goes away: give up after `attempts`, so setup refuses to create over it
deletes.clear()
acc.kind_nodes = lambda: ["abc"]
assert not acc.cleanup_cluster(attempts=3) and len(deletes) == 3
assert not acc.setup_cluster_and_aiopslab("p", aiopslab_dir=aiopslab, enable_local_registry=False)
assert len(deletes) == 3 + 5 and not any("create" in c for c in deletes)

# Nothing left over: setup does not delete before creating
deletes.clear()
acc.kind_nodes = lambda: []
acc.setup_cluster_and_aiopslab("p", aiopslab_dir=aiopslab, enable_local_registry=False)
assert deletes[0].startswith("kind create cluster")
print("ok")
