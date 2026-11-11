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

MIN_ENTRY_RX = 1000  # B/s: an idle entry point stays under ~150, 10 req/s give 12-20 KB/s (L1, L4, L7 of 2026-11-03)
CHAOS_PLURALS = ("podchaos", "networkchaos", "stresschaos")


def _key(name):
    return normalize_service(name).replace("-", "")


def _targets(s):
    return {_key(t) for t in s.get("accepted_targets") or [s.get("target")] if t}


def _collection_env():  # set by collect_dataset.py: the benchmark gate stays as it is until its rerun adopts this environment
    return bool(os.environ.get("AIOPSLAB_WORKLOAD_SCHEDULE"))


def _ready(pod):  # same rule as AIOpsLab KubeCtl.wait_for_ready
    st = pod.status
    return st.phase == "Succeeded" or bool(st.container_statuses) and all(c.ready for c in st.container_statuses)


def _c1(s, core, **_):  # namespace exists and is Active
    phase = core.read_namespace(s["target_namespace"]).status.phase
    return None if phase == "Active" else f"namespace {s['target_namespace']} is {phase}"


def _c2(s, core, **_):  # at least one pod
    return None if core.list_namespaced_pod(s["target_namespace"]).items else f"no pods in {s['target_namespace']}"


def _c3(s, core, **_):  # pods Ready/Succeeded; the target's own pods may be broken by a legitimate fault
    skip = _targets(s)
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


def _c6(s, traces, now, **_):  # with continuous load (Astronomy Shop, collection runs): a trace in the last 5 min
    if s.get("scenario") != "Astronomy Shop" and not _collection_env():
        return None
    svc = s.get("service_starting_point") or "frontend"
    # Jaeger ignores `lookback` (see MCP-server), so freshness is checked on the span start times (microseconds)
    fresh = [t for t in traces(svc) or [] if max(sp["startTime"] for sp in t["spans"]) >= (now() - 300) * 1e6]
    return None if fresh else f"no trace for {svc} in the last 5 min"


def _c7(s, prom_up, **_):  # Prometheus (NodePort 32000) answers
    return None if prom_up() else "Prometheus does not answer"


def _c10(s, prom_query, **_):  # the wrk2 Job can fail without failing the setup: traffic must reach the entry point
    entry = s.get("service_starting_point") or "frontend"
    if not _collection_env() or _key(entry) in _targets(s):  # a fault on the entry point may legitimately cut its traffic
        return None
    result = prom_query(f'sum(rate(container_network_receive_bytes_total{{namespace="{s["target_namespace"]}",pod=~"{entry}-.*"}}[1m]))')
    rx = float(result[0]["value"][1]) if result else 0.0
    return None if rx >= s.get("min_entry_rx", MIN_ENTRY_RX) else f"only {rx:.0f} B/s at {entry}"


def _c11(s, chaos, **_):  # kubectl apply succeeding does not mean the fault is active: a wrong selector matches no pod
    if not _collection_env():
        return None
    experiments = chaos(s["target_namespace"])
    if s.get("fault_duration") and not experiments:
        return "no Chaos Mesh experiment found"
    bad = [e["metadata"]["name"] for e in experiments
           if not any(c.get("type") == "AllInjected" and c.get("status") == "True" for c in e.get("status", {}).get("conditions", []))]
    return f"experiments not injected: {bad}" if bad else None


CHECKS = {"C1": _c1, "C2": _c2, "C3": _c3, "C4": _c4, "C5": _c5, "C6": _c6, "C7": _c7, "C10": _c10, "C11": _c11}


def check_environment(scenario, core, traces, prom_up, now=time.time, prom_query=None, chaos=None):
    """Failed checks as {id: reason}; empty when the environment is healthy."""
    failed = {}
    fetched = {}

    def traces_once(svc):  # C5 and C6 read the same traces: one Jaeger query per poll
        if svc not in fetched:
            fetched[svc] = traces(svc)
        return fetched[svc]

    for cid, check in CHECKS.items():
        try:
            err = check(scenario, core=core, traces=traces_once, prom_up=prom_up, now=now, prom_query=prom_query, chaos=chaos)
        except Exception as e:  # an unreachable API is a failed check, not a crash
            err = f"{type(e).__name__}: {' '.join(str(e).splitlines()[:2])}"[:120]
        if err:
            failed[cid] = err
    return failed


def wait_until_ready(scenario, core, traces, prom_up, interval=15, sleep=time.sleep, clock=time.monotonic, now=time.time,
                     prom_query=None, chaos=None):
    """Poll every `interval` s up to scenario['readiness_timeout'] (default 300 s); return the last failed checks."""
    if os.environ.get("SKIP_READINESS_GATE") == "1":
        logger.warning("SKIP_READINESS_GATE=1: readiness gate bypassed")
        return {}
    deadline = clock() + int(scenario.get("readiness_timeout", 300))
    while True:
        failed = check_environment(scenario, core, traces, prom_up, now, prom_query, chaos)
        if not failed or clock() >= deadline:
            return failed
        logger.info("Environment not ready yet, retrying in %ds: %s", interval, failed)
        sleep(interval)


def run_readiness_gate(scenario: dict) -> dict:
    """Wait for the freshly set-up environment to be healthy; returns the failed checks ({} when ready or bypassed)."""
    from api.jaeger_api import JaegerAPI  # MCP-server is on sys.path via config; fresh clients: every kind create rewrites the kube-context
    from kubernetes import client
    import requests
    jaeger = JaegerAPI(jaeger_url=os.environ.get("JAEGER_URL"))
    prom_url = os.environ.get("PROMETHEUS_SERVER_URL", "http://localhost:32000")
    prom_query = lambda q: requests.get(f"{prom_url}/api/v1/query", params={"query": q}, timeout=5).json()["data"]["result"]
    custom = client.CustomObjectsApi(jaeger.k8s_client.api_client)
    chaos = lambda ns: [e for plural in CHAOS_PLURALS
                        for e in custom.list_namespaced_custom_object("chaos-mesh.org", "v1alpha1", ns, plural)["items"]]
    return wait_until_ready(scenario, jaeger.k8s_client, lambda svc: jaeger.get_jaeger_traces(svc, limit=20, lookback="5m"),
                            lambda: bool(prom_query("up")), prom_query=prom_query, chaos=chaos)
