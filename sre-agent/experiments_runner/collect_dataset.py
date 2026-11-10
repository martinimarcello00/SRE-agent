"""Collect the raw dataset of the anomaly detector: clean sessions and fault runs, one fresh kind cluster each, no agent.

Run from sre-agent/ (settle, segments and rates come from the pilot):
  poetry run python -m experiments_runner.collect_dataset --app hotel --out /path/to/datasets/raw --rates 10,30,60 --rates-unseen 20,45 --dry-run

Each attempt is saved under <out>/<app>/<kind>/<scenario>__r<rep>/attempt-<n>/ and is complete when its meta.json exists.
Nothing here interprets the data: the builder in the MCP repo (anomaly/build.py) reads these files.
"""
import argparse
from contextlib import contextmanager
import gzip
import json
import logging
import math
import os
from pathlib import Path
import random
import signal
import subprocess
import time
from types import SimpleNamespace

import requests

logger = logging.getLogger(__name__)

APPS = {"hotel": "Hotel Reservation", "social": "Social Network"}
# Audit A9: pod kill is the same fault as pod failure, redeploy without PV is a noop with target = namespace
EXCLUDED = {"pod_kill_hotel_res.json", "redeploy_without_PV_hotel_res.json"}
REQUIRED_AIOPSLAB_COMMIT = "6bba8e1"  # continuous workload, settle, run meta, dev_chaos, request mixes, Social graph and sampling, kind logs and events, etcd on tmpfs
WRK2_POD = 'namespace="default",pod=~"wrk2-job.*"'
EXTRA_SERIES = {  # outside the app namespace: is the host or the load generator saturated?
    "extra_node_cpu": "node_cpu_seconds_total",
    "extra_node_memory": 'node_memory_MemAvailable_bytes',
    "extra_node_load": "node_load1",
    "extra_up": "up",
    "extra_wrk2": '{__name__=~"container_(cpu_usage_seconds_total|memory_working_set_bytes|network_receive_bytes_total|network_transmit_bytes_total)",%s}' % WRK2_POD,
}
# Every trace is kept: 10 s blocks under this limit hold up to 500 traces/s, against 240 req/s at most.
# A block that reaches the limit lost its oldest traces, and meta.json counts such blocks.
JAEGER_BLOCK_S = 10
JAEGER_TRACES_PER_BLOCK = 5000
SOCIAL_SAMPLING = 0.6  # share of Social requests traced (the chart's default is 0.01); Hotel traces all of them, its own default
SETUP_S = 410  # measured on 2026-11-03, cluster to fault without settle; only for the --dry-run estimate
# Longest wait for AIOpsLab to deploy and inject, settle excluded: the app deploy alone may take up to 900 s on a slow disk
SETUP_TIMEOUT_S = 1500
SOCIAL_GRAPH = "socfb-Reed98"  # users and follows loaded after the deploy; the wrk2 scripts assume its 962 users
MIXES = {"hotel": 4, "social": 3}  # request mixes defined in each app's wrk2 script (WRK_MIX)


def make_schedule(key, total_s, segment_s, rates, mixes=(0,), ramp_share=0.0, ramp_step=30):
    """Duration, rate and request mix of each segment, drawn from `key`: a run always gets the same load, different runs a different one.

    Durations are random within `segment_s` (min, max): the injection falls at a random point of a segment,
    so a load change is not tied to the fault, and the model sees no fixed period.
    A share `ramp_share` of the rate changes becomes a ramp of 2 to 6 steps of `ramp_step` seconds, spaced geometrically:
    real traffic also changes gradually, not only in steps.
    """
    rng = random.Random(key)
    segments, covered, previous = [], 0, None
    while covered < total_s:
        rate, mix = rng.choice(rates), rng.choice(mixes)
        if previous is not None and rate != previous and rng.random() < ramp_share:
            steps = rng.randint(2, 6)
            for k in range(1, steps + 1):
                segments.append(f"{ramp_step}:{round(previous * (rate / previous) ** (k / (steps + 1)))}:{mix}")
                covered += ramp_step
        seconds = rng.randint(*segment_s)
        segments.append(f"{seconds}:{rate}:{mix}")
        covered += seconds
        previous = rate
    return ",".join(segments)


def clean_split(rep, split, unseen):
    """Split of clean session `rep`, fixed before collecting: train, val, then test.

    The last `unseen` test sessions draw their rates from levels never used in training.
    """
    train, val, test = split
    if rep <= train:
        return "train"
    if rep <= train + val:
        return "val"
    return "test-unseen" if rep > train + val + test - unseen else "test"


def build_plan(app, scenarios, dev_scenarios, clean, reps, dev_reps, seed):
    """All runs of an app in a shuffled order: otherwise the day of collection would coincide with train and test."""
    own = lambda items: [s for s in items if s.get("scenario") == APPS[app] and s["_source_file"] not in EXCLUDED]
    tests = own(scenarios)
    noop = next(s for s in tests if s.get("fault_type") == "No fault")
    runs = [("clean", noop, r) for r in range(1, clean + 1)]
    runs += [("test", s, r) for s in tests for r in range(1, reps + 1)]
    runs += [("dev", s, r) for s in own(dev_scenarios) for r in range(1, dev_reps + 1)]
    plan = [{"id": f"{kind}/{Path(s['_source_file']).stem}__r{rep}", "kind": kind, "scenario": s, "rep": rep} for kind, s, rep in runs]
    random.Random(seed).shuffle(plan)
    return plan


def scenario_env(scenario, schedule, settle_s, connections, run_meta_path):
    env = {
        "TARGET_NAMESPACE": scenario["target_namespace"],
        "AIOPSLAB_SETTLE_S": str(settle_s),
        "AIOPSLAB_WORKLOAD_SCHEDULE": schedule,
        "AIOPSLAB_WORKLOAD_CONNECTIONS": str(connections),
        "AIOPSLAB_CHAOS_MESH_ALWAYS": "1",
        "AIOPSLAB_RUN_META": str(run_meta_path),
    }
    if scenario.get("scenario") == APPS["social"]:
        env["AIOPSLAB_SOCIAL_GRAPH"] = SOCIAL_GRAPH
        env["AIOPSLAB_SOCIAL_SAMPLING"] = str(SOCIAL_SAMPLING)
    for key, value in (scenario.get("env_variables") or {}).items():
        env[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return env


@contextmanager
def patched_env(env):
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def attempt_dirs(run_dir):
    return sorted(Path(run_dir).glob("attempt-*"), key=lambda p: int(p.name.split("-")[1]))


def read_status(attempt_dir):
    meta = Path(attempt_dir) / "meta.json"
    return json.loads(meta.read_text())["status"] if meta.exists() else None


def is_pending(run_dir, round_no):
    """Not done yet in this round: resuming skips valid runs, and an attempt cut by a crash counts as a failed one."""
    attempts = attempt_dirs(run_dir)
    return not any(read_status(a) == "valid" for a in attempts) and len(attempts) < round_no


def collect(plan, root, max_attempts, execute):
    """Run the plan once, then repeat what is not valid: invalid attempts stay on disk with their reason."""
    for round_no in range(1, max_attempts + 1):
        pending = [run for run in plan if is_pending(Path(root) / run["id"], round_no)]
        logger.info("Round %d: %d of %d runs to do", round_no, len(pending), len(plan))
        for i, run in enumerate(pending, start=1):
            run_dir = Path(root) / run["id"]
            attempt_dir = run_dir / f"attempt-{len(attempt_dirs(run_dir)) + 1}"
            attempt_dir.mkdir(parents=True)
            logger.info("=== [%d/%d] %s %s", i, len(pending), run["id"], attempt_dir.name)
            logger.info("%s: %s", run["id"], execute(run, attempt_dir))
    return {run["id"]: is_pending(Path(root) / run["id"], max_attempts + 1) for run in plan}


def write_gz(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        f.write(content if isinstance(content, str) else json.dumps(content))


def export_prometheus(prom_get, namespace, start, end, out):
    """Raw samples with their real timestamps: cAdvisor's are irregular (10-30 s), so no query_range and no fixed step."""
    families = prom_get("label/__name__/values", {"match[]": f'{{namespace="{namespace}"}}', "start": start, "end": end})
    selectors = {f: f'{{__name__="{f}",namespace="{namespace}"}}' for f in families if f.startswith(("container_", "kube_"))}
    selectors.update(EXTRA_SERIES)
    window = math.ceil(end - start)
    counts = {}
    for name, selector in selectors.items():
        result = prom_get("query", {"query": f"{selector}[{window}s]", "time": end})["result"]
        write_gz(Path(out) / "prometheus" / f"{name}.json.gz", result)
        counts[name] = len(result)
    if not any(name.startswith("container_") for name in counts):
        raise RuntimeError(f"no container_* series for namespace {namespace}")
    return counts


def jaeger_blocks(start, end, step=JAEGER_BLOCK_S):
    edges = list(range(int(start), int(end), step)) + [int(end)]
    return list(zip(edges, edges[1:]))


def export_jaeger(jaeger_get, service, start, end, out):
    """Block by block: one request for all the traces of a session can be too much for the API.

    Returns the traces written, the start of each block that reached the limit (traces lost there) and the seconds it took.
    Blocks are listed by time: a full block during the deploy (Social's graph load) is setup traffic, one under load is data lost.
    """
    total, full, began = 0, [], time.time()
    for block_start, block_end in jaeger_blocks(start, end):
        traces = jaeger_get({"service": service, "start": block_start * 10**6, "end": block_end * 10**6, "limit": JAEGER_TRACES_PER_BLOCK})
        write_gz(Path(out) / "jaeger" / f"{block_start}.json.gz", traces)
        total += len(traces)
        if len(traces) >= JAEGER_TRACES_PER_BLOCK:
            full.append(block_start)
    return {"traces": total, "full_blocks": full, "seconds": round(time.time() - began, 1)}


def export_cluster_state(kubectl, namespace, out):
    """Events, pods and logs; returns the images in use and the logs kubectl could not give.

    --previous, because a restart hides the log of the crash. A pod that never started (Pending) has no log:
    that must not cost the logs of the other pods.
    """
    out = Path(out)
    for name, args in (("events", ["get", "events"]), ("deployments", ["get", "deployments"]),
                       ("chaos", ["get", "podchaos,networkchaos,stresschaos"])):
        write_gz(out / f"{name}.json.gz", kubectl([*args, "-n", namespace, "-o", "json"]))
    raw = kubectl(["get", "pods", "-n", namespace, "-o", "json"])
    write_gz(out / "pods.json.gz", raw)
    images, missing = set(), {}

    def save_log(args, path):
        try:
            write_gz(path, kubectl(args))
        except subprocess.CalledProcessError as e:
            missing[path.name] = (e.stderr or "").strip()[:200]

    for pod in json.loads(raw)["items"]:
        name = pod["metadata"]["name"]
        restarts = {c["name"]: c.get("restartCount", 0) for c in pod["status"].get("containerStatuses", [])}
        for container in pod["spec"]["containers"]:
            images.add(container["image"])
            log_args = ["logs", name, "-n", namespace, "-c", container["name"], "--timestamps"]
            save_log(log_args, out / "logs" / f"{name}__{container['name']}.log.gz")
            if restarts.get(container["name"]):
                save_log([*log_args, "--previous"], out / "logs" / f"{name}__{container['name']}.previous.log.gz")
    save_log(["logs", "job/wrk2-job", "-n", "default"], out / "logs" / "wrk2.log.gz")  # real start, rate and mix of each load segment
    return sorted(images), missing


def git_state(path):
    run = lambda *args: subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True).stdout.strip()
    return {"sha": run("rev-parse", "HEAD"), "changed_files": len(run("status", "--porcelain").splitlines())}


def provenance(aiopslab_dir, sre_agent_dir):
    """The lesson of the audit: silent drift between branches, stash and submodules. Which code produced this run?"""
    return {
        "aiopslab": git_state(aiopslab_dir),
        "aiopslab_applications": git_state(Path(aiopslab_dir) / "aiopslab-applications"),
        "sre_agent": git_state(sre_agent_dir),
        "mcp_server": git_state(Path(sre_agent_dir) / "MCP-server"),
    }


def run_once(run, attempt_dir, args, deps):
    """One attempt: cluster, gate after the injection, observation, export. The cluster is deleted whatever happens."""
    scenario, attempt_dir = run["scenario"], Path(attempt_dir)
    namespace = scenario["target_namespace"]
    observe_s = args.observe_clean if run["kind"] == "clean" else args.observe
    split = clean_split(run["rep"], args.split, args.unseen_sessions) if run["kind"] == "clean" else run["kind"]
    rates = args.rates_unseen if split == "test-unseen" else args.rates
    schedule = make_schedule(f"{args.seed}:{run['id']}", args.settle + observe_s + deps.margin_s, args.segment, rates, args.mixes,
                             args.ramp_share)
    run_meta_path = attempt_dir / "run.json"
    meta = {"id": run["id"], "kind": run["kind"], "rep": run["rep"], "split": split, "attempt": attempt_dir.name,
            "app_scenario": scenario.get("scenario"),
            "fault_type": scenario.get("fault_type"), "aiopslab_command": scenario["aiopslab_command"], "namespace": namespace,
            "target": scenario.get("target"), "accepted_targets": scenario.get("accepted_targets"), "seed": args.seed, "schedule": schedule,
            "rates": rates, "mixes": args.mixes, "ramp_share": args.ramp_share,
            "trace_sampling": SOCIAL_SAMPLING if scenario.get("scenario") == APPS["social"] else 1.0,
            "settle_s": args.settle, "observe_s": observe_s, "connections": args.connections, "versions": deps.provenance(),
            "t_start": deps.now(), "gate_failures": {}, "export_errors": {}, "status": "invalid", "reason": None}
    with patched_env(scenario_env(scenario, schedule, args.settle, args.connections, run_meta_path)):
        deps.refresh_config()
        try:
            ok = deps.setup(problem_id=scenario["aiopslab_command"], setup_timeout=int(scenario.get("setup_timeout", SETUP_TIMEOUT_S)) + args.settle,
                            fault_duration=scenario.get("fault_duration"))
            if not ok:
                meta["reason"] = "setup failed"
                return meta["reason"]
            meta["run_meta"] = json.loads(run_meta_path.read_text())
            with deps.port_forward(scenario):
                meta["gate_failures"] = deps.gate(scenario)
            if meta["gate_failures"]:  # no observation: the run is already invalid, what follows only explains why
                meta["reason"] = f"readiness gate: {sorted(meta['gate_failures'])}"
            else:
                deps.sleep(max(0.0, meta["run_meta"]["t_injected"] + observe_s - deps.now()))
            start, end = meta["run_meta"]["t_deployed"] - 60, deps.now()
            meta["t_export"] = end
            try:
                meta["series"] = deps.export_prometheus(namespace, start, end, attempt_dir)
            except Exception as e:  # without metrics the run is useless
                meta["export_errors"]["prometheus"] = repr(e)
                meta["reason"] = meta["reason"] or "Prometheus export failed"
            try:
                meta["images"], meta["missing_logs"] = deps.export_cluster_state(namespace, attempt_dir)
            except Exception as e:
                meta["export_errors"]["cluster_state"] = repr(e)
            try:
                with deps.port_forward(scenario):  # opened again: nothing needs Jaeger during the observation
                    jaeger = deps.export_jaeger(scenario.get("service_starting_point") or "frontend", start, end, attempt_dir)
                meta.update(traces=jaeger["traces"], jaeger_full_blocks=jaeger["full_blocks"], jaeger_export_s=jaeger["seconds"])
            except Exception as e:  # traces only serve later phases: recorded, but the run stays valid
                meta["export_errors"]["jaeger"] = repr(e)
            meta["status"] = "invalid" if meta["reason"] else "valid"
            return meta["status"]
        except Exception as e:
            logger.exception("Attempt failed: %s", run["id"])
            meta["reason"] = meta["reason"] or repr(e)
            return meta["reason"]
        finally:
            meta["t_end"] = deps.now()
            try:
                deps.cleanup()
            except Exception as e:
                meta["export_errors"]["cleanup"] = repr(e)
            tmp = attempt_dir / "meta.json.tmp"
            tmp.write_text(json.dumps(meta, indent=1))
            tmp.replace(attempt_dir / "meta.json")  # last of all: this file marks the attempt as finished


@contextmanager
def _port_forward(scenario):
    command = scenario.get("port_forward_command") or f"kubectl port-forward svc/jaeger 16686:16686 -n {scenario['target_namespace']}"
    process = subprocess.Popen(command, shell=True, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)
    try:
        yield
    finally:
        os.killpg(process.pid, signal.SIGTERM)


def _kubectl(args):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=300, check=True).stdout


def real_deps():
    from config import AIOPSLAB_DIR  # also puts MCP-server on sys.path
    from api.config_manager import ConfigManager
    from experiments_runner import setup_cluster_and_aiopslab, cleanup_cluster
    from experiments_runner.readiness import run_readiness_gate

    def prom_get(path, params):
        url = os.environ.get("PROMETHEUS_SERVER_URL", "http://localhost:32000")
        response = requests.get(f"{url}/api/v1/{path}", params=params, timeout=300)
        response.raise_for_status()
        return response.json()["data"]

    def jaeger_get(params):
        response = requests.get(f"{os.environ.get('JAEGER_URL', 'http://localhost:16686')}/api/traces", params=params, timeout=300)
        response.raise_for_status()
        return response.json().get("data") or []

    sre_agent_dir = Path(__file__).resolve().parents[2]
    return SimpleNamespace(
        margin_s=SETUP_S,
        now=time.time,
        sleep=time.sleep,
        refresh_config=lambda: ConfigManager().refresh_config(),
        provenance=lambda: provenance(AIOPSLAB_DIR, sre_agent_dir),
        setup=lambda **kw: setup_cluster_and_aiopslab(aiopslab_dir=AIOPSLAB_DIR, stream_cli_output=True, **kw),
        cleanup=cleanup_cluster,
        gate=run_readiness_gate,
        port_forward=_port_forward,
        export_prometheus=lambda ns, start, end, out: export_prometheus(prom_get, ns, start, end, out),
        export_cluster_state=lambda ns, out: export_cluster_state(_kubectl, ns, out),
        export_jaeger=lambda service, start, end, out: export_jaeger(jaeger_get, service, start, end, out),
        aiopslab_dir=AIOPSLAB_DIR,
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--app", required=True, choices=APPS)
    ints = lambda v: [int(r) for r in v.split(",")]
    # absolute: AIOpsLab writes run.json from its own working directory
    p.add_argument("--out", required=True, type=lambda v: Path(v).resolve(), help="outside the repo: the data does not belong in git")
    p.add_argument("--rates", required=True, type=ints, help="requests/s the segments draw from")
    p.add_argument("--rates-unseen", type=ints, help="requests/s of the last --unseen-sessions clean test sessions, absent from --rates")
    p.add_argument("--mixes", type=ints, help="request mixes the segments draw from (indices into the wrk2 script's mixes); default all")
    p.add_argument("--settle", type=int, default=300, help="seconds under load before the injection")
    p.add_argument("--segment", type=int, nargs=2, default=[120, 480], metavar=("MIN", "MAX"), help="seconds of a load segment, drawn per segment")
    p.add_argument("--ramp-share", type=float, default=0.5, help="share of the rate changes made as a ramp of 30 s steps")
    p.add_argument("--split", type=ints, default=[6, 2, 4], help="clean sessions for train, val and test, assigned by repetition number")
    p.add_argument("--unseen-sessions", type=int, default=2, help="last clean test sessions that use --rates-unseen")
    p.add_argument("--observe", type=int, default=900, help="seconds observed after the injection in test and dev runs")
    p.add_argument("--observe-clean", type=int, default=5400, help="seconds observed in a clean session")
    p.add_argument("--connections", type=int, default=128,
                   help="wrk2 connections; each holds one request in flight, so wrk2 sends at most connections/latency requests/s: "
                        "128 keep the load at 160 req/s up to 0.8 s of latency. wrk2's timeout count is wrong with idle connections")
    p.add_argument("--clean", type=int, default=12, help="clean sessions")
    p.add_argument("--reps", type=int, default=5, help="repetitions of each test scenario")
    p.add_argument("--dev-reps", type=int, default=2, help="repetitions of each dev scenario")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--only", help="keep only the runs whose id contains this text")
    p.add_argument("--dry-run", action="store_true", help="print the plan and the machine time, then exit")
    args = p.parse_args(argv)
    args.mixes = args.mixes or list(range(MIXES[args.app]))
    if max(args.mixes) >= MIXES[args.app] or min(args.mixes) < 0:
        p.error(f"--mixes: the {args.app} wrk2 script has mixes 0..{MIXES[args.app] - 1}")
    if len(args.split) != 3 or args.clean > sum(args.split):
        p.error("--split needs train,val,test with at least --clean sessions in all")
    unseen = [r for r in range(1, args.clean + 1) if clean_split(r, args.split, args.unseen_sessions) == "test-unseen"]
    if unseen and (not args.rates_unseen or set(args.rates_unseen) & set(args.rates)):
        p.error(f"clean sessions {unseen} need --rates-unseen, with no level in common with --rates")
    return args


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    from experiments_runner.get_scenarios import load_fault_scenarios
    scenarios_dir = Path(__file__).parent / "fault-scenarios"
    plan = build_plan(args.app, load_fault_scenarios(scenarios_dir, only_executable=False),
                      load_fault_scenarios(scenarios_dir / "dev", only_executable=False),
                      args.clean, args.reps, args.dev_reps, args.seed)
    if args.only:
        plan = [run for run in plan if args.only in run["id"]]
    hours = sum(SETUP_S + args.settle + (args.observe_clean if run["kind"] == "clean" else args.observe) + 60 for run in plan) / 3600
    logger.info("%d runs, about %.1f h of machine time", len(plan), hours)
    if args.dry_run:
        print("\n".join(run["id"] for run in plan))
        return
    deps = real_deps()
    # The runner uses whatever is checked out in AIOPSLAB_DIR: refuse a checkout without the collection environment
    subprocess.run(["git", "-C", str(deps.aiopslab_dir), "merge-base", "--is-ancestor", REQUIRED_AIOPSLAB_COMMIT, "HEAD"], check=True)
    root = args.out / args.app
    left = collect(plan, root, args.max_attempts, lambda run, attempt_dir: run_once(run, attempt_dir, args, deps))
    logger.info("Done: %d valid, still not valid: %s", sum(not v for v in left.values()), [k for k, v in left.items() if v])


if __name__ == "__main__":
    main()
