# Run from sre-agent/: PYTHONPATH=.:experiments_runner python tests/test_readiness.py  (no cluster needed)
import json
import os
import time
from types import SimpleNamespace as NS

import readiness as r


def pod(name, ready=True, phase="Running"):
    return NS(metadata=NS(name=name), status=NS(phase=phase, container_statuses=[NS(ready=ready)]))


class Core:  # CoreV1Api-like fake of a healthy astronomy-shop with adFailure injected
    def __init__(self, ns_phase="Active", pods=None, flag="on"):
        self.ns_phase = ns_phase
        self.pods = [pod("frontend-proxy-7d9f8b6c5-abcde"), pod("ad-5d8f9c7b4-x2k9p")] if pods is None else pods
        self.flag = flag

    def read_namespace(self, ns):
        if self.ns_phase is None:
            raise RuntimeError("(404) Reason: Not Found")
        return NS(status=NS(phase=self.ns_phase))

    def list_namespaced_pod(self, ns):
        return NS(items=self.pods if self.ns_phase else [])  # k8s answers an empty list for a missing namespace

    def read_namespaced_config_map(self, name, ns):
        if self.ns_phase is None:
            raise RuntimeError("(404) Reason: Not Found")
        flags = {} if self.flag is None else {"adFailure": {"defaultVariant": self.flag}}
        return NS(data={"demo.flagd.json": json.dumps({"flags": flags})})


scenario = {"scenario": "Astronomy Shop", "target_namespace": "astronomy-shop", "service_starting_point": "frontend-proxy",
            "target": "ad", "_injection": {"mechanism": "flagd", "flag": "adFailure"}}
fresh = [{"spans": [{"startTime": int(time.time() * 1e6)}]}]
args = dict(traces=lambda svc: fresh, prom_up=lambda: True)


def failed(s=scenario, core=None, **kw):
    return r.check_environment(s, core or Core(), **{**args, **kw})


assert failed() == {}  # all good

assert list(failed(core=Core(ns_phase=None))) == ["C1", "C2", "C4"]  # 2026-10-05: namespace astronomy-shop never existed
assert "C1" in failed(core=Core(ns_phase="Terminating"))
assert list(failed(core=Core(pods=[]))) == ["C2"]  # namespace there but empty

assert list(failed(core=Core(pods=[pod("frontend-proxy-7d9f8b6c5-abcde", ready=False)]))) == ["C3"]
assert failed(core=Core(pods=[pod("frontend-proxy-7d9f8b6c5-abcde"), pod("ad-5d8f9c7b4-x2k9p", ready=False)])) == {}  # target may be broken
assert failed(core=Core(pods=[pod("load-generator-1", phase="Succeeded")])) == {}

assert list(failed(core=Core(flag="off"))) == ["C4"]
assert list(failed(core=Core(flag=None))) == ["C4"]  # flag missing from the ConfigMap
assert failed(s={**scenario, "_injection": {"mechanism": "flagd", "flag": "adFailure", "variant": "10sec"}}, core=Core(flag="on"))["C4"]
assert failed(s={**scenario, "_injection": {"mechanism": "flagd", "flag": "adFailure", "variant": "on"}}) == {}
assert failed(s={k: v for k, v in scenario.items() if k != "_injection"}, core=Core(flag="off")) == {}  # noop: C4 skipped

assert list(failed(traces=lambda svc: None)) == ["C5", "C6"]
assert list(failed(traces=lambda svc: [])) == ["C6"]
assert list(failed(traces=lambda svc: [{"spans": [{"startTime": int((time.time() - 900) * 1e6)}]}])) == ["C6"]  # stale
assert failed(s={**scenario, "scenario": "Hotel Reservation"}, traces=lambda svc: []) == {}  # C6 only with continuous load
assert list(failed(prom_up=lambda: False)) == ["C7"]
queries = []
assert failed(traces=lambda svc: queries.append(svc) or fresh) == {} and queries == ["frontend-proxy"]  # C5 and C6 share one query


def boom():
    raise ConnectionError("refused")


assert list(failed(prom_up=boom)) == ["C7"]  # an unreachable client is a failed check, not a crash

# polling with a fake clock: ready on the 3rd poll
t = [0]
core = Core(pods=[pod("frontend-proxy-7d9f8b6c5-abcde", ready=False)])
polls = []


def sleep(s):
    t[0] += s
    polls.append(t[0])
    if len(polls) == 2:
        core.pods = [pod("frontend-proxy-7d9f8b6c5-abcde")]


assert r.wait_until_ready(scenario, core, sleep=sleep, clock=lambda: t[0], **args) == {} and polls == [15, 30]

# never ready: gives up after readiness_timeout (default 300 s, overridable) without really sleeping
t, polls = [0], []
bad = Core(ns_phase=None)
sleeps = []
out = r.wait_until_ready(scenario, bad, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0], **args)
assert "C1" in out and t[0] == 300 and len(sleeps) == 20
t = [0]
sleeps.clear()
assert "C1" in r.wait_until_ready({**scenario, "readiness_timeout": 45}, bad, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0], **args)
assert t[0] == 45

# kill switch: bypasses the gate even on an empty cluster, and does not poll
os.environ["SKIP_READINESS_GATE"] = "1"
assert r.wait_until_ready(scenario, bad, sleep=lambda s: 1 / 0, clock=lambda: 0, **args) == {}
del os.environ["SKIP_READINESS_GATE"]
assert r.wait_until_ready(scenario, bad, sleep=lambda s: None, clock=iter([0, 999]).__next__, **args)  # back to normal

# collection environment: C6 on every app, C10 traffic at the entry point, C11 Chaos Mesh experiments
hotel = {"scenario": "Hotel Reservation", "target_namespace": "test-hotel-reservation", "service_starting_point": "frontend",
         "target": "user", "fault_duration": 900}
traffic = lambda rx: (lambda q: [{"value": [0, str(rx)]}])
injected = lambda *statuses: (lambda ns: [{"metadata": {"name": f"e{i}"}, "status": {"conditions": [{"type": "Selected", "status": "True"}, {"type": "AllInjected", "status": st}]}}
                                          for i, st in enumerate(statuses)])
env_args = dict(core=Core(pods=[pod("frontend-1-a")]), traces=lambda svc: fresh, prom_up=lambda: True, prom_query=traffic(15000), chaos=injected("True"))


def collecting(s=hotel, **kw):
    return r.check_environment(s, **{**env_args, **kw})


# without the collection environment the new checks do not run, even with broken clients: the benchmark gate is unchanged
assert collecting(traces=lambda svc: [], prom_query=boom, chaos=boom) == {}

os.environ["AIOPSLAB_WORKLOAD_SCHEDULE"] = "300:20"
assert collecting() == {}
assert list(collecting(traces=lambda svc: [])) == ["C6"]  # continuous load now also on Hotel: no fresh trace is a failure
assert list(collecting(prom_query=traffic(134))) == ["C10"]  # L6: no wrk2 Job, setup still succeeded
assert list(collecting(prom_query=lambda q: [])) == ["C10"]  # no series at all
assert list(collecting({**hotel, "min_entry_rx": 20000})) == ["C10"]  # threshold overridable from the scenario
assert collecting({**hotel, "target": "frontend"}, prom_query=traffic(0)) == {}  # a fault on the entry point may cut its traffic
assert 'pod=~"frontend-.*"' in (lambda seen: (collecting(prom_query=lambda q: seen.append(q) or [{"value": [0, "9999"]}]), seen[0])[1])([])
assert list(collecting(prom_query=boom)) == ["C10"]

assert list(collecting(chaos=injected("False"))) == ["C11"]
assert "e1" in collecting(chaos=injected("True", "False"))["C11"]
assert list(collecting(chaos=lambda ns: [])) == ["C11"]  # fault_duration says Chaos Mesh, but no experiment exists
assert collecting({**hotel, "fault_duration": None}, chaos=lambda ns: []) == {}  # not a Chaos Mesh scenario
assert list(collecting({**hotel, "fault_duration": None}, chaos=injected("False"))) == ["C11"]  # container kill has no fault_duration
assert r.wait_until_ready(hotel, sleep=lambda s: 1 / 0, **env_args) == {}  # the new clients reach the checks through the poll loop
del os.environ["AIOPSLAB_WORKLOAD_SCHEDULE"]
