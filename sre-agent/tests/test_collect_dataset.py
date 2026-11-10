# Run from sre-agent/: PYTHONPATH=.:experiments_runner poetry run python tests/test_collect_dataset.py  (no cluster needed)
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS

import collect_dataset as c

# --- load schedule: reproducible from the seed, different between runs, long enough
one = c.make_schedule("0:clean/noop__r1", 900, (120, 480), [10, 30, 50], [0, 1])
assert one == c.make_schedule("0:clean/noop__r1", 900, (120, 480), [10, 30, 50], [0, 1])
segments = [[int(v) for v in s.split(":")] for s in c.make_schedule("k", 3000, (120, 480), [10, 30, 50], [0, 1, 2]).split(",")]
assert sum(d for d, _, _ in segments) >= 3000 and all(120 <= d <= 480 for d, _, _ in segments)  # the load outlasts the observation
assert {r for _, r, _ in segments} <= {10, 30, 50} and {m for _, _, m in segments} <= {0, 1, 2}
assert len({d for d, _, _ in segments}) > 1  # no fixed period
many = {c.make_schedule(f"k{i}", 3000, (120, 480), [10, 30, 50]) for i in range(20)}
assert len(many) == 20

# Ramps: a rate change becomes 2-6 steps of 30 s spaced geometrically between the two levels, always moving towards the new one
ramped = [[int(v) for v in s.split(":")] for s in c.make_schedule("r", 6000, (120, 480), [10, 160], ramp_share=1.0).split(",")]
levels = [i for i, (d, r, _) in enumerate(ramped) if r in (10, 160) and d != 30]
for a, b in zip(levels, levels[1:]):
    if ramped[a][1] == ramped[b][1]:
        assert b == a + 1  # same rate twice: nothing to ramp
        continue
    steps = [r for d, r, _ in ramped[a + 1:b]]
    assert all(d == 30 for d, _, _ in ramped[a + 1:b]) and 2 <= len(steps) <= 6, ramped[a:b + 1]
    lo, hi = sorted((ramped[a][1], ramped[b][1]))
    assert all(lo < r < hi for r in steps) and steps == sorted(steps, reverse=ramped[a][1] > ramped[b][1])
assert c.make_schedule("r", 6000, (120, 480), [10, 160]) == c.make_schedule("r", 6000, (120, 480), [10, 160], ramp_share=0.0)


def gap_to_injection(schedule, settle=300):
    """Seconds between the injection and the nearest load change of a run."""
    ends, t = [], 0
    for segment in schedule.split(","):
        t += int(segment.split(":")[0])
        ends.append(t)
    return min(abs(e - settle) for e in ends)


# With fixed 300 s segments and a 300 s settle every injection fell on a load change; now it falls anywhere in a segment
gaps = [gap_to_injection(c.make_schedule(f"7:test/x__r{i}", 1500, (120, 480), [10])) for i in range(200)]
assert 0.2 < sum(g < 60 for g in gaps) / len(gaps) < 0.6 and max(gaps) > 120

# --- clean sessions: split fixed by repetition number before collecting, the last two test sessions on unseen load levels
assert [c.clean_split(r, [6, 2, 4], 2) for r in range(1, 13)] == ["train"] * 6 + ["val"] * 2 + ["test"] * 2 + ["test-unseen"] * 2
args = c.parse_args(["--app", "hotel", "--out", "rel/raw", "--rates", "10,30", "--rates-unseen", "20,45"])
assert args.out.is_absolute() and args.mixes == [0, 1, 2, 3] and args.segment == [120, 480] and args.split == [6, 2, 4]
assert c.parse_args(["--app", "social", "--out", "/r", "--rates", "10", "--clean", "2"]).mixes == [0, 1, 2]  # a pilot needs no unseen levels
for bad in (["--app", "hotel", "--out", "/r", "--rates", "10,30"],  # sessions 11 and 12 without unseen levels
            ["--app", "hotel", "--out", "/r", "--rates", "10,30", "--rates-unseen", "30,45"],  # an unseen level that training sees
            ["--app", "social", "--out", "/r", "--rates", "10", "--clean", "2", "--mixes", "3"],  # Social has mixes 0..2
            ["--app", "hotel", "--out", "/r", "--rates", "10", "--clean", "13", "--rates-unseen", "20"]):  # more sessions than the split
    try:
        c.parse_args(bad)
        raise AssertionError(f"accepted {bad}")
    except SystemExit:
        pass

# --- plan: counts, exclusions, shuffled order that the seed reproduces
H, S = "Hotel Reservation", "Social Network"
scenarios = [{"_source_file": "noop_hotel_res.json", "scenario": H, "fault_type": "No fault"},
             {"_source_file": "pod_failure_hotel_res.json", "scenario": H, "fault_type": "Pod failure"},
             {"_source_file": "pod_kill_hotel_res.json", "scenario": H, "fault_type": "Pod kill"},
             {"_source_file": "redeploy_without_PV_hotel_res.json", "scenario": H, "fault_type": "Redeploy"},
             {"_source_file": "noop_social_network.json", "scenario": S, "fault_type": "No fault"}]
dev = [{"_source_file": "trial_cpu_search_hotel_res.json", "scenario": H, "fault_type": "CPU"}]
plan = c.build_plan("hotel", scenarios, dev, clean=12, reps=5, dev_reps=2, seed=0)
ids = [run["id"] for run in plan]
assert len(ids) == len(set(ids)) == 12 + 2 * 5 + 2
assert sum(i.startswith("clean/noop_hotel_res") for i in ids) == 12
assert not any("pod_kill" in i or "redeploy" in i or "social" in i for i in ids)  # audit A9, and the other app
assert "dev/trial_cpu_search_hotel_res__r2" in ids and "test/pod_failure_hotel_res__r5" in ids and "test/noop_hotel_res__r1" in ids
assert ids == [run["id"] for run in c.build_plan("hotel", scenarios, dev, 12, 5, 2, 0)]
assert ids != [run["id"] for run in c.build_plan("hotel", scenarios, dev, 12, 5, 2, 1)]
assert ids != sorted(ids)  # the kinds are mixed: the day of collection must not coincide with train and test
assert [run["kind"] for run in plan].count("clean") == 12

# --- loader: collection ignores the benchmark's "execute" flags (only_executable=False used to return nothing)
from get_scenarios import load_fault_scenarios
folder = Path(tempfile.mkdtemp())
(folder / "on.json").write_text(json.dumps({"scenario": H, "fault_type": "a", "execute": True}))
(folder / "off.json").write_text(json.dumps({"scenario": H, "fault_type": "b", "execute": False}))
(folder / "dev").mkdir()
(folder / "dev" / "nested.json").write_text(json.dumps({"scenario": H, "fault_type": "c", "execute": True}))
assert [s["_source_file"] for s in load_fault_scenarios(folder)] == ["on.json"]  # what the benchmark sees: never dev/
assert [s["_source_file"] for s in load_fault_scenarios(folder, only_executable=False)] == ["off.json", "on.json"]

# --- environment: the dev fault reaches AIOpsLab as JSON, and nothing leaks into the next run
chaos = {"service": "search", "kind": "StressChaos", "params": {}}
env = c.scenario_env({"target_namespace": "ns", "env_variables": {"JAEGER_URL": "http://x", "AIOPSLAB_DEV_CHAOS": chaos}}, "300:10", 120, 4, "/m/run.json")
assert json.loads(env["AIOPSLAB_DEV_CHAOS"]) == chaos and env["JAEGER_URL"] == "http://x" and env["TARGET_NAMESPACE"] == "ns"
assert env["AIOPSLAB_SETTLE_S"] == "120" and env["AIOPSLAB_WORKLOAD_SCHEDULE"] == "300:10" and env["AIOPSLAB_CHAOS_MESH_ALWAYS"] == "1"
assert env["AIOPSLAB_RUN_META"] == "/m/run.json" and env["AIOPSLAB_WORKLOAD_CONNECTIONS"] == "4"
assert "AIOPSLAB_SOCIAL_GRAPH" not in env  # only Social has a graph to load
social_env = c.scenario_env({"target_namespace": "ns", "scenario": S}, "300:10", 120, 4, "/m")
assert social_env["AIOPSLAB_SOCIAL_GRAPH"] == "socfb-Reed98" and social_env["AIOPSLAB_SOCIAL_SAMPLING"] == "0.6"
assert "AIOPSLAB_SOCIAL_SAMPLING" not in env  # Hotel traces every request already
os.environ["JAEGER_URL"] = "before"
os.environ.pop("AIOPSLAB_DEV_CHAOS", None)
try:
    with c.patched_env(env):
        assert os.environ["JAEGER_URL"] == "http://x" and "AIOPSLAB_DEV_CHAOS" in os.environ
        raise ValueError
except ValueError:
    pass
assert os.environ.pop("JAEGER_URL") == "before" and "AIOPSLAB_DEV_CHAOS" not in os.environ

# --- rounds: every run once, then what is not valid; a restart skips what is done
tmp = Path(tempfile.mkdtemp())
small = [{"id": f"clean/noop__r{i}"} for i in range(1, 4)]
calls = []
outcomes = {"clean/noop__r2": ["invalid", "valid"], "clean/noop__r3": ["invalid", "invalid", "invalid"]}


def execute(run, attempt_dir):
    calls.append((run["id"], attempt_dir.name))
    status = outcomes.get(run["id"], ["valid"]).pop(0)
    (attempt_dir / "meta.json").write_text(json.dumps({"status": status}))
    return status


left = c.collect(small, tmp, 3, execute)
assert calls[:3] == [("clean/noop__r1", "attempt-1"), ("clean/noop__r2", "attempt-1"), ("clean/noop__r3", "attempt-1")]  # invalid ones go to the end
assert calls[3:] == [("clean/noop__r2", "attempt-2"), ("clean/noop__r3", "attempt-2"), ("clean/noop__r3", "attempt-3")]
assert left == {"clean/noop__r1": False, "clean/noop__r2": False, "clean/noop__r3": True}
assert len(c.attempt_dirs(tmp / "clean/noop__r3")) == 3  # invalid attempts stay on disk

calls.clear()
outcomes["clean/noop__r3"] = ["valid"]
c.collect(small, tmp, 4, execute)  # resume with one more attempt allowed: only the run still not valid
assert calls == [("clean/noop__r3", "attempt-4")]

crash = Path(tempfile.mkdtemp())
(crash / "clean/noop__r1/attempt-1").mkdir(parents=True)  # the machine stopped mid-run: no meta.json
calls.clear()
c.collect([{"id": "clean/noop__r1"}], crash, 2, execute)
assert calls == [("clean/noop__r1", "attempt-2")]  # the cut attempt counts, and nothing is overwritten

# --- export: real timestamps (range vector at one instant, never query_range), per family, also outside the namespace
out = Path(tempfile.mkdtemp())
queries = []


def prom_get(path, params):
    if path.startswith("label/"):
        assert params["match[]"] == '{namespace="ns"}'
        return ["container_threads", "kube_pod_owner", "go_goroutines"]
    assert path == "query"
    queries.append(params)
    return {"result": [{"metric": {"pod": "a"}, "values": [[1.5, "7"]]}]}


counts = c.export_prometheus(prom_get, "ns", 1000.2, 1600.0, out)
assert 'go_goroutines' not in counts and counts["container_threads"] == 1 and "extra_wrk2" in counts and "extra_node_cpu" in counts
assert queries[0]["query"] == '{__name__="container_threads",namespace="ns"}[600s]' and queries[0]["time"] == 1600.0
with gzip.open(out / "prometheus/kube_pod_owner.json.gz", "rt") as f:
    assert json.load(f)[0]["values"] == [[1.5, "7"]]
try:
    c.export_prometheus(lambda p, q: [] if p.startswith("label/") else {"result": []}, "ns", 0, 10, out)
    raise AssertionError("no series of the app must be an error")
except RuntimeError:
    pass

# --- traces: blocks that cover the whole window, each one capped
assert c.jaeger_blocks(0, 700, 300) == [(0, 300), (300, 600), (600, 700)]
seen = []
result = c.export_jaeger(lambda p: seen.append(p) or [{"traceID": "t"}], "frontend", 1000, 1700, out)
assert result["traces"] == 70 and result["full_blocks"] == [] and result["seconds"] >= 0
assert seen[0] == {"service": "frontend", "start": 1000 * 10**6, "end": 1010 * 10**6, "limit": c.JAEGER_TRACES_PER_BLOCK}
assert (out / "jaeger/1000.json.gz").exists() and (out / "jaeger/1690.json.gz").exists()
# A block that reaches the limit has lost traces: it is counted
full = c.export_jaeger(lambda p: [{"traceID": "t"}] * (c.JAEGER_TRACES_PER_BLOCK if p["start"] == 1000 * 10**6 else 3), "frontend", 1000, 1020, out)
assert full["full_blocks"] == [1000] and full["traces"] == c.JAEGER_TRACES_PER_BLOCK + 3

# --- cluster state: logs of every container, --previous only after a restart, wrk2 included
pods = {"items": [{"metadata": {"name": "geo-1"}, "spec": {"containers": [{"name": "geo", "image": "img:1"}]},
                   "status": {"containerStatuses": [{"name": "geo", "restartCount": 2}]}},
                  {"metadata": {"name": "up-1"}, "spec": {"containers": [{"name": "c", "image": "img:2"}]},
                   "status": {"containerStatuses": [{"name": "c", "restartCount": 0}]}}]}
state_out = Path(tempfile.mkdtemp())
kube_calls = []
images, missing = c.export_cluster_state(lambda a: kube_calls.append(a) or (json.dumps(pods) if a[:2] == ["get", "pods"] else "x"), "ns", state_out)
assert images == ["img:1", "img:2"] and missing == {}
assert (state_out / "logs/geo-1__geo.previous.log.gz").exists() and not (state_out / "logs/up-1__c.previous.log.gz").exists()
assert (state_out / "logs/wrk2.log.gz").exists() and (state_out / "events.json.gz").exists() and (state_out / "chaos.json.gz").exists()
assert ["logs", "job/wrk2-job", "-n", "default"] in kube_calls

# A Pending pod has no log: kubectl fails on it, and the other logs are still saved
import subprocess
pending = {"items": [{"metadata": {"name": "a-1"}, "spec": {"containers": [{"name": "a", "image": "i"}]}, "status": {}}, *pods["items"]]}


def kubectl_pending(a):
    if a[:2] == ["logs", "a-1"]:
        raise subprocess.CalledProcessError(1, a, stderr='container "a" in pod "a-1" is waiting to start')
    return json.dumps(pending) if a[:2] == ["get", "pods"] else "x"


state_out = Path(tempfile.mkdtemp())
images, missing = c.export_cluster_state(kubectl_pending, "ns", state_out)
assert list(missing) == ["a-1__a.log.gz"] and "waiting to start" in missing["a-1__a.log.gz"]
assert (state_out / "logs/geo-1__geo.log.gz").exists() and (state_out / "logs/wrk2.log.gz").exists()


# --- one attempt end to end, with every external call faked
def deps(**kw):
    log = []
    clock = {"t": 5000.0}

    @contextmanager
    def port_forward(scenario):
        log.append("pf-open")
        yield
        log.append("pf-close")

    def setup(problem_id, setup_timeout, fault_duration):
        log.append(("setup", problem_id, setup_timeout, fault_duration, os.environ["AIOPSLAB_WORKLOAD_SCHEDULE"]))
        Path(os.environ["AIOPSLAB_RUN_META"]).write_text(json.dumps({"t_deployed": 4000.0, "t_workload": 4010.0, "t_inject": 4130.0, "t_injected": 4136.0}))
        return True

    base = dict(margin_s=0, now=lambda: clock["t"], sleep=lambda s: (log.append(("sleep", s)), clock.__setitem__("t", clock["t"] + s)),
                refresh_config=lambda: log.append("refresh"), provenance=lambda: {"aiopslab": {"sha": "abc"}}, setup=setup,
                cleanup=lambda: log.append("cleanup"), gate=lambda s: log.append("gate") or {}, port_forward=port_forward,
                export_prometheus=lambda *a: log.append("prom") or {"container_threads": 1}, export_cluster_state=lambda *a: log.append("state") or (["img"], {}),
                export_jaeger=lambda *a: log.append("jaeger") or {"traces": 5, "full_blocks": [], "seconds": 0.1})
    return NS(**{**base, **kw}), log


args = NS(settle=120, segment=(60, 60), rates=[10, 30], rates_unseen=[20], mixes=[0], ramp_share=0.0, split=[6, 2, 4], unseen_sessions=2,
          observe=600, observe_clean=1200, connections=4, seed=7)
scenario = {"aiopslab_command": "pod_failure_hotel_res-detection-1", "target_namespace": "ns", "service_starting_point": "frontend",
            "scenario": H, "fault_type": "Pod failure", "target": "user", "accepted_targets": None, "fault_duration": 900}
run = {"id": "test/pod_failure_hotel_res__r1", "kind": "test", "rep": 1, "scenario": scenario}


def attempt(d, r=run):
    folder = Path(tempfile.mkdtemp())
    result = c.run_once(r, folder, args, d)
    return result, json.loads((folder / "meta.json").read_text()), folder


d, log = deps()
result, meta, _ = attempt(d)
assert result == "valid" and meta["status"] == "valid" and meta["reason"] is None and meta["series"] == {"container_threads": 1}
order = [e if isinstance(e, str) else e[0] for e in log]
assert order == ["refresh", "setup", "pf-open", "gate", "pf-close", "sleep", "prom", "state", "pf-open", "jaeger", "pf-close", "cleanup"]  # gate after the injection
assert log[1][2] == 1500 + 120 and log[1][3] == 900  # the settle counts in the setup timeout; fault duration of the scenario
assert meta["schedule"] == log[1][4] == c.make_schedule("7:test/pod_failure_hotel_res__r1", 720, (60, 60), [10, 30], [0])
assert meta["split"] == "test" and meta["rates"] == [10, 30] and meta["images"] == ["img"] and meta["missing_logs"] == {}
assert meta["traces"] == 5 and meta["jaeger_full_blocks"] == [] and meta["trace_sampling"] == 1.0
sleep = next(e for e in log if isinstance(e, tuple) and e[0] == "sleep")
assert sleep[1] == 0.0  # the fake clock is already past t_injected + observe: nothing more to wait
assert meta["versions"]["aiopslab"]["sha"] == "abc" and meta["run_meta"]["t_inject"] == 4130.0 and meta["target"] == "user"
assert "AIOPSLAB_WORKLOAD_SCHEDULE" not in os.environ

d, log = deps(now=lambda: 4200.0, sleep=lambda s: log_sleep.append(s))
log_sleep = []
attempt(d)
assert log_sleep == [4136.0 + 600 - 4200.0]  # observation counted from the end of the injection, gate included

d, log = deps(now=lambda: 4200.0, sleep=lambda s: log_sleep.append(s))
log_sleep.clear()
_, meta, _ = attempt(d, {**run, "kind": "clean", "id": "clean/noop__r1"})
assert log_sleep == [4136.0 + 1200 - 4200.0]  # a clean session has its own, longer observation
assert meta["split"] == "train" and meta["rates"] == [10, 30]

d, log = deps()
_, meta, _ = attempt(d, {**run, "kind": "clean", "id": "clean/noop__r12", "rep": 12})
assert meta["split"] == "test-unseen" and meta["rates"] == [20] and meta["schedule"].split(":")[1] == "20"  # load never seen in training

d, log = deps(gate=lambda s: {"C10": "only 134 B/s at frontend"})
result, meta, _ = attempt(d)
assert result == "invalid" and meta["reason"] == "readiness gate: ['C10']" and meta["gate_failures"]["C10"]
assert not any(isinstance(e, tuple) and e[0] == "sleep" for e in log) and "prom" in log and "cleanup" in log  # exported to explain it, no observation

d, log = deps(setup=lambda **kw: False)
result, meta, _ = attempt(d)
assert result == "setup failed" and meta["status"] == "invalid" and "gate" not in log and "cleanup" in log

d, log = deps(export_prometheus=lambda *a: 1 / 0)
_, meta, _ = attempt(d)
assert meta["status"] == "invalid" and meta["reason"] == "Prometheus export failed" and "division" in meta["export_errors"]["prometheus"]

d, log = deps(export_jaeger=lambda *a: 1 / 0)
_, meta, _ = attempt(d)
assert meta["status"] == "valid" and "jaeger" in meta["export_errors"]  # traces serve later phases: the run stays valid

d, log = deps(gate=lambda s: 1 / 0)
result, meta, _ = attempt(d)
assert meta["status"] == "invalid" and "division" in meta["reason"] and "cleanup" in log  # the cluster goes away whatever happens

d, log = deps(cleanup=lambda: 1 / 0)
_, meta, folder = attempt(d)
assert "cleanup" in meta["export_errors"] and (folder / "meta.json").exists()
assert not (folder / "meta.json.tmp").exists()

print("ok")
