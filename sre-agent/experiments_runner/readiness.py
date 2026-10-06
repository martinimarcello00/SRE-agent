"""Environment gate: checks the cluster after setup and the Jaeger port-forward, before any agent runs.

Clients are injected (core: kubernetes CoreV1Api-like, traces: service -> list|None, prom_up: () -> bool),
so nothing here needs a cluster. Each check returns an error string, or None when it passes.
"""
import json
import logging
import os
import time

from evaluation.evaluation import normalize_service

logger = logging.getLogger(__name__)


def _key(name):
    return normalize_service(name).replace("-", "")


def _ready(pod):  # same rule as AIOpsLab KubeCtl.wait_for_ready
    st = pod.status
    return st.phase == "Succeeded" or bool(st.container_statuses) and all(c.ready for c in st.container_statuses)


def _c1(s, core, **_):  # namespace exists and is Active
    phase = core.read_namespace(s["target_namespace"]).status.phase
    return None if phase == "Active" else f"namespace {s['target_namespace']} is {phase}"


def _c2(s, core, **_):  # at least one pod
    return None if core.list_namespaced_pod(s["target_namespace"]).items else f"no pods in {s['target_namespace']}"


def _c3(s, core, **_):  # pods Ready/Succeeded; the target's own pods may be broken by a legitimate fault
    skip = {_key(t) for t in s.get("accepted_targets") or [s.get("target")] if t}
    bad = [p.metadata.name for p in core.list_namespaced_pod(s["target_namespace"]).items
           if not _ready(p) and _key(p.metadata.name) not in skip]
    return f"pods not ready: {bad}" if bad else None


def _c4(s, core, **_):  # flagd faults: the flag is applied in the ConfigMap, as AIOpsLab's OtelFaultInjector does
    inj = s.get("_injection") or {}
    if inj.get("mechanism") != "flagd":
        return None
    cm = core.read_namespaced_config_map("flagd-config", s["target_namespace"])
    got = json.loads(cm.data["demo.flagd.json"])["flags"][inj["flag"]]["defaultVariant"]
    ok = got == inj["variant"] if inj.get("variant") else got != "off"
    return None if ok else f"flag {inj['flag']} defaultVariant is {got!r}"


def _c5(s, traces, **_):  # Jaeger answers
    return "Jaeger does not answer" if traces(s.get("service_starting_point") or "frontend") is None else None


def _c6(s, traces, now, **_):  # Astronomy Shop has continuous load: a trace in the last 5 min
    if s.get("scenario") != "Astronomy Shop":
        return None
    svc = s.get("service_starting_point") or "frontend"
    # Jaeger ignores `lookback` (see MCP-server), so freshness is checked on the span start times (microseconds)
    fresh = [t for t in traces(svc) or [] if max(sp["startTime"] for sp in t["spans"]) >= (now() - 300) * 1e6]
    return None if fresh else f"no trace for {svc} in the last 5 min"


def _c7(s, prom_up, **_):  # Prometheus (NodePort 32000) answers
    return None if prom_up() else "Prometheus does not answer"


CHECKS = {"C1": _c1, "C2": _c2, "C3": _c3, "C4": _c4, "C5": _c5, "C6": _c6, "C7": _c7}


def check_environment(scenario, core, traces, prom_up, now=time.time):
    """Failed checks as {id: reason}; empty when the environment is healthy."""
    failed = {}
    for cid, check in CHECKS.items():
        try:
            err = check(scenario, core=core, traces=traces, prom_up=prom_up, now=now)
        except Exception as e:  # an unreachable API is a failed check, not a crash
            err = f"{type(e).__name__}: {' '.join(str(e).splitlines()[:2])}"[:120]
        if err:
            failed[cid] = err
    return failed


def wait_until_ready(scenario, core, traces, prom_up, interval=15, sleep=time.sleep, clock=time.monotonic, now=time.time):
    """Poll every `interval` s up to scenario['readiness_timeout'] (default 300 s); return the last failed checks."""
    if os.environ.get("SKIP_READINESS_GATE") == "1":
        logger.warning("SKIP_READINESS_GATE=1: readiness gate bypassed")
        return {}
    deadline = clock() + int(scenario.get("readiness_timeout", 300))
    while True:
        failed = check_environment(scenario, core, traces, prom_up, now)
        if not failed or clock() >= deadline:
            return failed
        logger.info("Environment not ready yet, retrying in %ds: %s", interval, failed)
        sleep(interval)
