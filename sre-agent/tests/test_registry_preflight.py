# Run from sre-agent/: PYTHONPATH=experiments_runner python tests/test_registry_preflight.py  (no docker needed)
import subprocess
import urllib.request
from types import SimpleNamespace

import automate_cluster_creation as acc

ports = {"kind-registry": "5001", "kind-registry-ghcr": None, "kind-registry-quay": "5003"}  # ghcr is stopped


def fake_run(cmd, **kw):
    port = ports[cmd[2]]
    return SimpleNamespace(stdout=f"127.0.0.1:{port}\n" if port else "")  # docker port prints nothing when stopped


class Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): pass


acc.subprocess.run, acc.urllib.request.urlopen = fake_run, lambda *a, **k: Resp()
assert not acc.check_registry_mirrors()  # one stopped mirror is enough to refuse the launch
ports["kind-registry-ghcr"] = "5002"
assert acc.check_registry_mirrors()
