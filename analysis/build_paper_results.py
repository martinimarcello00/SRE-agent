"""Rebuild paper results directly from the complete raw JSON files in Results/."""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from analysis_metrics import (
    numeric, percentile, read_json, require, sha, summarize, write_csv, write_json,
)

ROOT = Path(__file__).resolve().parent.parent
COHORTS = ("F", "N", "O", "P", "M")
APPS = ("Hotel Reservation", "Social Network", "Astronomy Shop")
STAGES = {
    "triage_agent": ("Triage", "#E69F00"),
    "planner_agent": ("Planner", "#56B4E9"),
    "rca_agent": ("Workers (pooled)", "#666666"),
    "supervisor_agent": ("Supervisor", "#009E73"),
    "react-agent": ("ReAct", "#D55E00"),
}
SHORT_LABELS = {
    "Geo application misconfiguration": "Geo misconfiguration",
    "Revoke auth MongoDB (Geo)": "Revoke auth (Geo)",
    "Revoke auth MongoDB (Rate)": "Revoke auth (Rate)",
    "User unregistered MongoDB (Geo)": "Unregistered user (Geo)",
    "User unregistered MongoDB (Rate)": "Unregistered user (Rate)",
    "Assign Pod to Non-Existent Node": "Assign nonexistent node",
    "MongoDB url-shorten TLS requirement": "MongoDB TLS requirement",
    "Port misconfig post-storage": "Port: post-storage",
    "Port misconfig text": "Port: text",
    "Port misconfig user": "Port: user",
    "Scale Pod to Zero": "Scale to zero",
    "Ad service failure": "Ad failure",
    "Ad service high CPU": "Ad high CPU",
    "Ad service manual GC": "Ad manual GC",
    "Cart service failure": "Cart failure",
    "Load generator flood homepage": "Load flood homepage",
    "Payment service failure": "Payment failure",
    "Payment service unreachable": "Payment unreachable",
    "Product catalog failure": "Product catalog failure",
    "Recommendation service cache failure": "Recommendation cache",
}
Row = dict[str, Any]


@dataclass(frozen=True)
class Run:
    id: str
    cohort: str
    app: str
    fault: str
    healthy: bool
    component_case: bool
    detected: bool
    strict: bool
    any_target: bool
    tokens: int
    seconds: float
    symptoms: int | None
    tasks: int
    workers: int
    worker_calls: int
    triage_calls: int | None
    status: str
    source: str
    sha256: str


@dataclass(frozen=True)
class StageCost:
    cohort: str
    stage: str
    tokens: int
    mean_tokens: float
    eligible_runs: int
    recorded_runs: int


@dataclass
class Study:
    runs: list[Run]
    costs: list[StageCost]
    summary: Row
    source_root: Path
    details: Row


def boolean(value: str) -> bool:
    require(value in {"True", "False"}, f"Missing or invalid Boolean: {value!r}")
    return value == "True"


def integer(value: str | int) -> int:
    require(type(value) is int or (isinstance(value, str) and bool(value)),
            "Missing or invalid integer telemetry")
    number = int(value)
    require(number >= 0, f"Negative integer telemetry: {value}")
    return number


def select_runs(rows: list[Row], selected: list[Row]) -> list[Run]:
    additions = {entry["id"]: entry for entry in selected}
    require(len(additions) == len(selected), "Duplicate amendment id")
    ids = {row["id"] for row in rows}
    require(len(ids) == len(rows), "Duplicate run id")
    require(set(additions) <= ids, "Missing selected amendment export")
    result = []
    cells = set()
    for row in rows:
        if row["role"] != "primary" and row["id"] not in additions:
            continue
        entry = additions.get(row["id"])
        cohort = entry["configuration"] if entry else row["cohort"]
        require(cohort in COHORTS, f"Unknown main cohort: {cohort}")
        if entry:
            require(row["source"] == entry["source_relative_to_results"]
                    and row["sha256"] == entry["sha256"], "Amendment source mismatch")
            require(row["cohort"] == {"N": "N_old", "M": "M_aux"}[cohort],
                    "Unexpected amendment origin")
        cell = (cohort, row["app"], row["fault"])
        require(cell not in cells, f"Duplicate configuration/scenario: {cell}")
        cells.add(cell)
        strict, any_target = boolean(row["strict"]), boolean(row["any"])
        require(not strict or any_target, "Strict success without any-target success")
        seconds = float(row["seconds"])
        require(math.isfinite(seconds) and seconds >= 0, "Invalid duration")
        result.append(Run(
            id=row["id"], cohort=cohort, app=row["app"], fault=row["fault"],
            healthy=boolean(row["healthy"]), component_case=boolean(row["component_case"]),
            detected=boolean(row["detected"]), strict=strict, any_target=any_target,
            tokens=integer(row["tokens"]), seconds=seconds,
            symptoms=integer(row["symptom_count"]) if row["symptom_count"] else None,
            tasks=integer(row["task_count"]), workers=integer(row["workers"]),
            worker_calls=integer(row["worker_calls"]),
            triage_calls=integer(row["triage_calls"]) if row["triage_calls"] else None,
            status=row["status"], source=row["source"],
            sha256=row["sha256"],
        ))
    return result


def stage_costs(runs: list[Run], rows: list[Row]) -> list[StageCost]:
    eligible = {run.id: run for run in runs if not run.healthy}
    totals: dict[tuple[str, str], int] = defaultdict(int)
    recorded: Counter[tuple[str, str]] = Counter()
    run_totals: Counter[str] = Counter()
    seen = set()
    for row in rows:
        if row["id"] not in eligible:
            continue
        key = (row["id"], row["stage"])
        require(key not in seen, f"Duplicate stage record: {key}")
        seen.add(key)
        tokens = integer(row["total_tokens"])
        run = eligible[row["id"]]
        run_totals[run.id] += tokens
        require(row["stage"] in STAGES or tokens == 0,
                f"Unmapped nonzero stage: {row['stage']}")
        if row["stage"] in STAGES:
            totals[run.cohort, row["stage"]] += tokens
            recorded[run.cohort, row["stage"]] += 1
    for run in eligible.values():
        require(run_totals[run.id] == run.tokens, f"Stage accounting mismatch: {run.source}")
    output = []
    for cohort in COHORTS:
        count = sum(run.cohort == cohort for run in eligible.values())
        if count:
            for stage in STAGES:
                tokens = totals[cohort, stage]
                output.append(StageCost(cohort, stage, tokens, tokens / count, count,
                                        recorded[cohort, stage]))
    return output


def cost_distributions(runs: list[Run]) -> list[Row]:
    output = []
    for cohort in COHORTS:
        group = [run for run in runs if run.cohort == cohort and not run.healthy]
        if not group:
            continue
        require(all(run.tokens > 0 for run in group),
                "Log-token plot requires strictly positive token counts")
        for metric in ("tokens", "seconds"):
            values = [getattr(run, metric) for run in group]
            output.append({
                "cohort": cohort, "metric": metric, "values": values, "n": len(values),
                "q1": percentile(values, .25), "median": percentile(values, .5),
                "q3": percentile(values, .75), "minimum": min(values), "maximum": max(values),
            })
    return output


def rca_score_records(runs: list[Run], source_root: Path) -> list[Row]:
    records = []
    for run in runs:
        if run.healthy:
            continue
        path = source_root / "Results" / run.source
        require(sha(path) == run.sha256, f"Changed RCA score source: {run.source}")
        raw = read_json(path, source_root)
        score = raw["evaluation"]["rca_score"]
        require(type(score) is int and 1 <= score <= 5,
                f"Invalid RCA score: {run.source}")
        analysis = raw["final_report"]["root_cause"]
        motivation = raw["evaluation"]["rca_motivation"]
        require(isinstance(analysis, str) and isinstance(motivation, str)
                and bool(motivation.strip()), f"Invalid RCA score provenance: {run.source}")
        no_analysis = not analysis.strip() or analysis.strip() == "No analysis data available"
        automatic = "judge not called" in motivation.lower()
        require(automatic == no_analysis and (not automatic or score == 1),
                f"Inconsistent RCA score provenance: {run.source}")
        records.append({
            "id": run.id, "variant": run.cohort, "application": run.app,
            "scenario": run.fault, "component_case": run.component_case,
            "restrictive": run.strict, "score": score,
            "origin": "automatic_no_analysis" if automatic else "judge",
            "source": run.source, "sha256": run.sha256,
        })
    return records


def rca_score_summary(records: list[Row]) -> list[Row]:
    summary = []
    for cohort in COHORTS:
        group = [r for r in records if r["variant"] == cohort]
        if not group:
            continue
        counts = Counter(r["score"] for r in group)
        automatic = sum(r["origin"] == "automatic_no_analysis" for r in group)
        localized = [r for r in group if r["component_case"] and r["restrictive"]]
        summary.append({
            "variant": cohort, "fault_n": len(group),
            "score_1_auto": automatic, "score_1_judge": counts[1] - automatic,
            **{f"score_{score}": counts[score] for score in range(2, 6)},
            "localized_n": len(localized),
            "localized_low_score_n": sum(r["score"] <= 2 for r in localized),
        })
    return summary


def rca_score_table(summary: list[Row]) -> str:
    lines = []
    for row in summary:
        if row["variant"] == "M":
            lines.append(r"\hline")
        values = [row["variant"], str(row["score_1_auto"]), str(row["score_1_judge"])]
        values.extend(str(row[f"score_{score}"]) for score in range(2, 6))
        values.append(f"{row['localized_low_score_n']}/{row['localized_n']}")
        lines.append(" & ".join(values) + r" \\")
    return (
        r"\begin{tabular*}{\columnwidth}{@{\extracolsep{\fill}}lrrrrrrr@{}}" "\n"
        r"\hline" "\n"
        r"\textbf{Variant} & \textbf{No analysis} & \textbf{1} & \textbf{2} & "
        r"\textbf{3} & \textbf{4} & \textbf{5} & \textbf{Low/localized} \\" "\n"
        r"\hline" "\n" + "\n".join(lines) + "\n" +
        r"\hline" "\n" r"\end{tabular*}" "\n"
    )


def symptom_conditioned_topology(runs: list[Run]) -> Row:
    reference = {(r.app, r.fault): r for r in runs
                 if r.cohort == "F" and r.component_case}
    pairs = []
    for n in runs:
        if n.cohort != "N" or not n.component_case:
            continue
        require((n.app, n.fault) in reference, "Missing F execution for topology sensitivity")
        f = reference[n.app, n.fault]
        require(f.symptoms is not None and n.symptoms is not None,
                "Missing symptom count for topology sensitivity")
        if f.symptoms > 0 and n.symptoms > 0:
            pairs.append((f, n))
    require(bool(pairs), "No eligible topology sensitivity pairs")
    return {
        "selection": "N and matched F component runs with symptoms in both; "
                     "post hoc conditioning, not causal adjustment",
        "component_cases": len(pairs),
        "F_matches": sum(f.strict for f, _ in pairs),
        "N_matches": sum(n.strict for _, n in pairs),
        "F_mean_tokens": mean(f.tokens for f, _ in pairs),
        "N_mean_tokens": mean(n.tokens for _, n in pairs),
        "run_pairs": [{"application": f.app, "scenario": f.fault,
                       "F_id": f.id, "N_id": n.id} for f, n in pairs],
    }


def read_pointer(value: Any, pointer: str) -> Any:
    require(pointer.startswith("/"), f"Invalid JSON pointer: {pointer}")
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def check_case(raw: Row, checks: list[Row]) -> None:
    for check in checks:
        value = read_pointer(raw, check["pointer"])
        require(("equals" in check) != ("contains" in check), "Invalid case check")
        valid = (value == check["equals"] if "equals" in check else
                 isinstance(value, str) and check["contains"] in value)
        require(valid, f"Case evidence mismatch at {check['pointer']}")


def tool_count(analyses: list[Row]) -> int:
    total = 0
    for analysis in analyses:
        stats = analysis.get("tools_stats")
        require(isinstance(stats, dict), "Missing investigator tools_stats")
        for name, count in stats.items():
            require(type(count) is int and count >= 0, f"Invalid tool count: {name}")
            if not name.startswith("submit_"):
                total += count
    return total


def load_results(results: Path) -> tuple[list[Run], list[Row]]:
    require(results.is_dir(), f"Results directory not found: {results}")
    require(not any(path.is_dir() for path in results.iterdir()),
            "Results must not contain campaign subdirectories")
    paths = sorted(results.glob("*.json"))
    require(bool(paths), f"No JSON run exports in {results}")
    apps = {name.casefold(): name for name in APPS}
    renames = {
        "Port missmatch geo": "Geo application misconfiguration",
        "MongoDB url-shorten authentication missing": "MongoDB url-shorten TLS requirement",
    }
    runs, stages = [], []
    ids, cells = set(), set()
    for path in paths:
        raw = read_json(path, results)
        cohort = raw["agent_id"]
        require(cohort in COHORTS, f"Unknown configuration in {path.name}: {cohort}")
        app_raw = raw["app_name"]
        require(isinstance(app_raw, str) and app_raw.casefold() in apps,
                f"Unknown application in {path.name}")
        app = apps[app_raw.casefold()]
        fault_raw = raw["testbed"]["fault_name"]
        require(isinstance(fault_raw, str) and bool(fault_raw), f"Missing fault in {path.name}")
        fault = renames.get(fault_raw, fault_raw)
        healthy = fault == "No fault"
        report, evaluation, stats = raw["final_report"], raw["evaluation"], raw["stats"]
        run_id = stats["run_id"]
        require(isinstance(run_id, str) and bool(run_id), f"Missing run ID: {path.name}")
        require(run_id not in ids, f"Duplicate run ID: {path.name}")
        ids.add(run_id)
        cell = (cohort, app, fault)
        require(cell not in cells, f"Duplicate configuration/scenario: {cell}")
        cells.add(cell)
        detected = report["detection"]
        strict, any_target = evaluation["localization"], evaluation["localization_any"]
        require(all(type(value) is bool for value in (detected, strict, any_target)),
                f"Invalid saved verdict: {path.name}")
        require(type(evaluation["detection"]) is bool
                and evaluation["detection"] == (detected != healthy),
                f"Detection correctness/verdict mismatch: {path.name}")
        require(not strict or any_target, f"Strict success without any-target success: {path.name}")
        tokens = stats.get("total_tokens")
        require(type(tokens) is int and tokens >= 0, f"Invalid total_tokens: {path.name}")
        seconds = numeric(stats.get("execution_time_seconds"), path.name + ": seconds")
        symptoms, tasks, analyses = raw["symptoms"], raw["rca_tasks"], raw["rca_analyses_list"]
        require(all(isinstance(value, list) for value in (symptoms, tasks, analyses)),
                f"Invalid saved investigation lists: {path.name}")
        status = stats["status"]
        require(isinstance(status, str) and bool(status), f"Missing saved status: {path.name}")
        triage = raw.get("triage_analysis")
        require(triage is None or isinstance(triage, dict), f"Invalid triage: {path.name}")
        runs.append(Run(
            id=run_id, cohort=cohort, app=app, fault=fault, healthy=healthy,
            component_case=not healthy and fault != "Redeploy Without PV",
            detected=detected, strict=strict, any_target=any_target, tokens=tokens,
            seconds=seconds, symptoms=None if cohort == "M" else len(symptoms),
            tasks=len(tasks), workers=len(analyses), worker_calls=tool_count(analyses),
            triage_calls=tool_count([triage]) if triage is not None else None,
            status=status, source=path.name, sha256=sha(path),
        ))
        telemetry = stats["agent_stats"]
        require(isinstance(telemetry, dict) and bool(telemetry),
                f"Missing stage telemetry: {path.name}")
        for stage, usage in telemetry.items():
            counts = {field: usage.get(field)
                      for field in ("total_tokens", "input_tokens", "output_tokens")}
            require(all(type(value) is int and value >= 0 for value in counts.values()),
                    f"Missing or invalid stage tokens: {path.name}/{stage}")
            require(counts["total_tokens"] == counts["input_tokens"] + counts["output_tokens"],
                    f"Stage input/output mismatch: {path.name}/{stage}")
            stages.append({"id": run_id, "stage": stage, **counts})
    return runs, stages


def load_study(results: Path) -> Study:
    results = results.resolve()
    runs, stages = load_results(results)
    costs = stage_costs(runs, stages)
    common = None
    for cohort in COHORTS:
        group = [run for run in runs if run.cohort == cohort]
        faults = [run for run in group if not run.healthy]
        components = [run for run in group if run.component_case]
        controls = [run for run in group if run.healthy]
        require((len(faults), len(components), len(controls)) == (29, 28, 3),
                f"Unexpected analysis population: {cohort}")
        keys = {(run.app, run.fault) for run in faults}
        require(common is None or keys == common, "Non-common fault population")
        common = keys
        require(len({run.app for run in controls}) == len(APPS),
                f"Expected one control per application: {cohort}")
    summary = {
        "main": {cohort: summarize([asdict(run) for run in runs if run.cohort == cohort])
                 for cohort in COHORTS},
        "without_pending_Kafka": {
            cohort: summarize([asdict(run) for run in runs if run.cohort == cohort
                               and (run.app, run.fault) !=
                               ("Astronomy Shop", "Kafka queue problems")])
            for cohort in COHORTS},
    }
    details = {
        "population": {"faults_per_variant": 29, "component_cases_per_variant": 28,
                       "controls_per_variant": 3},
        "uncertainty": "Descriptive scenario counts and means, not confidence intervals",
    }
    return Study(runs, costs, summary, results.parent, details)


def render_figures(runs: list[Run], costs: list[StageCost], output: Path) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "build/matplotlib"))
    import matplotlib as mpl
    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    output.mkdir(parents=True, exist_ok=True)
    style = {
        "font.family": "DejaVu Sans", "font.size": 8, "axes.labelsize": 8,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 7.5,
        "pdf.fonttype": 42, "svg.fonttype": "none", "svg.hashsalt": "cain27-results",
        "savefig.facecolor": "white", "savefig.bbox": None,
    }

    def export(fig, name):
        fig.canvas.draw()
        width, height = fig.get_size_inches()
        box = fig.get_tightbbox(fig.canvas.get_renderer())
        require(box.x0 >= -.02 and box.y0 >= -.02 and
                box.x1 <= width + .02 and box.y1 <= height + .02,
                f"Clipped figure content in {name}")
        for extension in ("pdf", "svg", "png"):
            metadata: Row = {"Creator": "CAIN study figure builder"}
            if extension == "pdf":
                metadata["CreationDate"] = None
            elif extension == "svg":
                metadata["Date"] = None
            fig.savefig(output / f"{name}.{extension}", dpi=300, metadata=metadata)
        plt.close(fig)
        return {"width_inches": width, "height_inches": height}

    with mpl.rc_context(style):
        fig, ax = plt.subplots(figsize=(3.5, 2.35), layout="constrained")
        positions = [4.6, 3.6, 2.6, 1.6, 0]
        left = [0.0] * len(COHORTS)
        handles = []
        for stage, (label, color) in STAGES.items():
            values = [next(c.mean_tokens / 1000 for c in costs
                           if c.cohort == cohort and c.stage == stage) for cohort in COHORTS]
            ax.barh(positions, values, left=left, height=.65, color=color,
                    edgecolor="#222222", linewidth=.35, zorder=3)
            left = [a + b for a, b in zip(left, values)]
            handles.append(Patch(facecolor=color, edgecolor="#222222", label=label))
        for y, total in zip(positions, left):
            ax.text(total + max(left) * .015, y, f"{total:.1f}", va="center", fontsize=7.5)
        ax.set_yticks(positions, COHORTS)
        ax.set(xlabel="Mean tokens per fault (k)", xlim=(0, max(left) * 1.18))
        ax.grid(axis="x", color=".88", linewidth=.5, zorder=0)
        ax.axhline(.8, color=".5", linestyle="--", linewidth=.6)
        ax.spines[["top", "right"]].set_visible(False)
        fig.legend(handles=handles, loc="outside upper center", ncols=3, frameon=False)
        dimensions = {"results-stage-tokens": export(fig, "results-stage-tokens")}

        components = [run for run in runs if run.component_case]
        lookup = {(r.app, r.fault, r.cohort): r for r in components}
        layout = []
        for app in APPS:
            faults = sorted({r.fault for r in components if r.app == app})
            if faults:
                layout.append((app, None))
                layout.extend((app, fault) for fault in faults)
        require(bool(layout), "No component scenarios to plot")
        height = max(2.0, .13 * len(layout) + .6)
        fig, ax = plt.subplots(figsize=(3.5, height), layout="constrained")
        tick_positions, tick_labels = [], []
        for y, (app, fault) in enumerate(layout):
            tick_positions.append(y)
            if fault is None:
                tick_labels.append(app)
                ax.axhline(y + .5, color=".8", linewidth=.5)
                continue
            tick_labels.append(SHORT_LABELS.get(fault, fault))
            for x, cohort in enumerate(COHORTS):
                require((app, fault, cohort) in lookup, "Missing component matrix cell")
                run = lookup[app, fault, cohort]
                if run.strict:
                    ax.scatter(x, y, marker="o", s=23, c="black", zorder=3)
                elif run.any_target:
                    ax.scatter(x, y, marker="^", s=27, facecolors="none",
                               edgecolors="black", linewidths=.8, zorder=3)
                else:
                    ax.scatter(x, y, marker="x", s=22, c=".4", linewidths=.8, zorder=3)
                if run.status != "success":
                    ax.text(x + .23, y, "*", fontsize=9, weight="bold",
                            va="center", ha="left")
        ax.set_yticks(tick_positions, tick_labels)
        for label, (_, fault) in zip(ax.get_yticklabels(), layout):
            if fault is None:
                label.set_weight("bold")
        ax.tick_params(axis="both", length=0)
        ax.set_xticks(range(len(COHORTS)), COHORTS)
        ax.xaxis.tick_top()
        ax.set(xlim=(-.5, 4.5), ylim=(len(layout) - .5, -.5))
        ax.axvline(3.5, color=".6", linestyle="--", linewidth=.7)
        for spine in ax.spines.values():
            spine.set_visible(False)
        handles = [
            Line2D([], [], marker="o", color="black", linestyle="none", markersize=4,
                   label="Restrictive"),
            Line2D([], [], marker="^", markerfacecolor="none", color="black",
                   linestyle="none", markersize=4, label="Any-target only"),
            Line2D([], [], marker="x", color=".4", linestyle="none", markersize=4,
                   label="No accepted target"),
        ]
        fig.legend(handles=handles, loc="outside lower center", ncols=2, frameon=False)
        dimensions["results-scenario-matrix"] = export(fig, "results-scenario-matrix")

        distributions = cost_distributions(runs)
        fig, axes = plt.subplots(1, 2, figsize=(7.16, 2.25), layout="constrained")
        x_positions = dict(zip(COHORTS, (0, 1, 2, 3, 4.6)))
        generator = random.Random(20261024)
        offsets = {r.id: generator.uniform(-.17, .17)
                   for r in sorted(runs, key=lambda r: r.id) if not r.healthy}
        for ax, metric, title, divisor in (
            (axes[0], "tokens", "(a) Token consumption", 1000),
            (axes[1], "seconds", "(b) Diagnostic execution time", 1),
        ):
            for cohort in COHORTS:
                records = [r for r in runs if r.cohort == cohort and not r.healthy]
                stats = next(r for r in distributions
                             if r["cohort"] == cohort and r["metric"] == metric)
                x = x_positions[cohort]
                ax.scatter([x + offsets[r.id] for r in records],
                           [getattr(r, metric) / divisor for r in records],
                           s=9, color=".45", alpha=.65, linewidths=0, zorder=2)
                ax.vlines(x, stats["q1"] / divisor, stats["q3"] / divisor,
                          color="black", linewidth=2.5, zorder=3)
                ax.hlines(stats["median"] / divisor, x - .23, x + .23,
                          color="black", linewidth=1.5, zorder=4)
                for run in records:
                    if run.status != "success":
                        ax.scatter(x + offsets[run.id], getattr(run, metric) / divisor,
                                   marker="*", s=42, facecolors="none", edgecolors="black",
                                   linewidths=.8, zorder=5)
            ax.set_title(title, loc="left", fontsize=8, pad=3)
            ax.grid(axis="y", color=".88", linewidth=.5, zorder=0)
            ax.axvline(3.8, color=".6", linestyle="--", linewidth=.6)
            ax.spines[["top", "right"]].set_visible(False)
            ax.set_xlim(-.6, 5.2)
            ax.set_xticks(list(x_positions.values()), COHORTS)
        axes[0].set_yscale("log", base=10)
        axes[0].set_ylabel("Tokens (k, log$_{10}$ scale)")
        axes[1].set_ylabel("Time (s)")
        axes[1].set_ylim(bottom=0)
        handles = [
            Line2D([], [], marker="o", color=".45", linestyle="none", markersize=3,
                   label="Scenario run"),
            Line2D([], [], color="black", linewidth=2, label="Median / IQR"),
        ]
        fig.legend(handles=handles, loc="outside lower center", ncols=2, frameon=False)
        dimensions["results-cost-distributions"] = export(fig, "results-cost-distributions")
    write_json(output / "results-figure-metadata.json", {
        "matplotlib_version": mpl.__version__, "dimensions": dimensions,
        "formats": ["pdf", "svg", "png"], "png_dpi": 300,
        "font": "DejaVu Sans; PDF TrueType embedded; SVG editable text",
        "encoding": "Solid stage colors plus fixed segment order and labelled legend; "
                    "outcome shape plus explicit legend",
        "cost_distribution": {
            "point_unit": "One fault-scenario run",
            "points_per_variant": {c: sum(r.cohort == c and not r.healthy for r in runs)
                                   for c in COHORTS},
            "token_axis": "Thousands of tokens, base-10 logarithmic scale; positive values only",
            "time_axis": "Seconds, linear scale starting at zero",
            "summary": "Horizontal median and vertical 25th-75th percentile interval; not a CI",
            "jitter": "Horizontal only, shared by both panels, seed 20261024",
            "pending_status": "M Kafka retained and marked by an open star",
        },
        "alt_text": {
            "results-stage-tokens": "Mean recorded tokens per fault for F, N, O, P and M, "
            "stacked by Triage, Planner, pooled Workers, Supervisor and ReAct. "
            "All 29 fault scenarios contribute to each mean. See results-stage-costs.csv.",
            "results-scenario-matrix": "Component-localization outcomes for all five variants "
            "on 28 scenarios grouped by application. Circles mean restrictive success, "
            "triangles any-target-only success and crosses no accepted target. "
            "An asterisk marks pending M Kafka. "
            "See results-scenario-matrix.csv for full labels and values.",
            "results-cost-distributions": "Two panels show every fault-scenario run's tokens "
            "and execution time for F, N, O, P and M. The token axis is base-10 logarithmic "
            "in thousands; time is linear in seconds. Horizontal black marks are medians, "
            "vertical black intervals are interquartile ranges across scenarios, not confidence "
            "intervals. The pending M Kafka run remains as an open star. "
            "See results-run-costs.csv and results-cost-quantiles.csv.",
        },
    })


def overview_rows(summary: Row) -> str:
    lines = []
    for cohort in COHORTS:
        r = summary["main"][cohort]
        if cohort == "M":
            lines.append(r"\hline")
        lines.append(
            f"{cohort} & {r['detection']['yes']}/{r['fault_n']} & "
            f"{r['strict']['yes']}/{r['component_n']} & "
            f"{r['any']['yes']}/{r['component_n']} & "
            f"{r['false_alarms']['yes']}/{r['control_n']} & "
            f"{r['costs']['tokens']['mean'] / 1000:.1f} & "
            f"{r['costs']['seconds']['mean']:.1f} " + r"\\"
        )
    return "\n".join(lines) + "\n"


def overview_table(summary: Row) -> str:
    return (
        r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrrrrrr@{}}" "\n"
        r"\hline" "\n"
        r"\textbf{Variant} & \textbf{Detection} & \textbf{Restrictive} & "
        r"\textbf{Any-target} & \textbf{Alarms/controls} & "
        r"\textbf{Mean tokens (k)} & \textbf{Mean time (s)} \\" "\n"
        r"\hline" "\n" + overview_rows(summary) +
        r"\hline" "\n" r"\end{tabular*}" "\n"
    )


def build(results: Path, output: Path, cases: Path) -> None:
    require(results.resolve().name == "Results", "The raw input directory must be named Results")
    require(not output.resolve().is_relative_to(results.resolve()),
            "Analysis output must be outside raw Results")
    study = load_study(results)
    output.mkdir(parents=True, exist_ok=True)
    (output / "results-overview-table.tex").write_text(overview_table(study.summary))
    scores = rca_score_records(study.runs, study.source_root)
    score_summary = rca_score_summary(scores)
    require(len(scores) == 145 and all(r["fault_n"] == 29 for r in score_summary),
            "Unexpected RCA-score population")
    (output / "results-rca-scores-table.tex").write_text(rca_score_table(score_summary))
    write_csv(output / "results-rca-scores.csv", scores)
    write_csv(output / "results-rca-score-summary.csv", score_summary)
    study.details["rca_scores"] = {
        "fault_runs": len(scores), "controls_included": 0,
        "automatic_no_analysis": sum(r["origin"] == "automatic_no_analysis" for r in scores),
        "restrictive_component_matches": sum(r["localized_n"] for r in score_summary),
        "localized_score_1_or_2": sum(r["localized_low_score_n"] for r in score_summary),
        "low_score_cutoff": 2,
        "interpretation": "Exploratory saved annotations of root-cause text versus ground truth",
        "independent_human_validation": "not documented",
        "source_review": "Checking exports and case traces does not validate the judge metric",
    }
    write_csv(output / "results-stage-costs.csv", [asdict(c) for c in study.costs])
    write_csv(output / "results-scenario-matrix.csv", [
        {"application": r.app, "scenario": r.fault, "variant": r.cohort,
         "restrictive": r.strict, "any_target": r.any_target,
         "trace_status": r.status}
        for r in study.runs if r.component_case
    ])
    write_csv(output / "results-overview.csv", [
        {"variant": c, "faults": r["fault_n"], "component_cases": r["component_n"],
         "controls": r["control_n"], "detections": r["detection"]["yes"],
         "restrictive": r["strict"]["yes"], "any_target": r["any"]["yes"],
         "alarms": r["false_alarms"]["yes"],
         "mean_tokens": r["costs"]["tokens"]["mean"],
         "mean_seconds": r["costs"]["seconds"]["mean"]}
        for c, r in study.summary["main"].items()
    ])
    write_csv(output / "results-run-costs.csv", [
        {"application": r.app, "scenario": r.fault, "variant": r.cohort,
         "tokens": r.tokens, "seconds": r.seconds, "workers": r.workers,
         "worker_calls": r.worker_calls, "agentic_triage_calls": r.triage_calls,
         "tasks": r.tasks, "symptoms": r.symptoms, "trace_status": r.status}
        for r in study.runs if not r.healthy
    ])
    distributions = cost_distributions(study.runs)
    for row in distributions:
        reference = study.summary["main"][row["cohort"]]["costs"][row["metric"]]
        for field in ("n", "q1", "median", "q3", "minimum", "maximum"):
            require(math.isclose(row[field], reference[field], rel_tol=1e-10),
                    f"Cost distribution mismatch: {row['cohort']}/{row['metric']}/{field}")
    write_csv(output / "results-cost-quantiles.csv", [
        {key: value for key, value in row.items() if key != "values"}
        for row in distributions
    ])
    lookup = {(r.cohort, r.app, r.fault): r for r in study.runs}
    case_output = []
    for case in read_json(cases, cases.parent)["cases"]:
        records = []
        for specification in case["runs"]:
            run = lookup[specification["variant"], case["application"], case["scenario"]]
            path = study.source_root / "Results" / run.source
            raw = read_json(path, study.source_root)
            check_case(raw, specification["checks"])
            records.append({
                "variant": run.cohort, "source": run.source, "sha256": run.sha256,
                "run_id": run.id, "checks": specification["checks"],
                "detection": run.detected, "restrictive": run.strict,
                "tokens": run.tokens, "seconds": run.seconds,
                "time_window": raw.get("jaeger_start_us"),
            })
        case_output.append({**case, "source_checked_runs": records,
                            "scientific_review": "author_review_required"})
    write_json(output / "results-cases.json", {"cases": case_output})
    eligible = [r for r in study.runs if r.cohort != "M" and not r.healthy]
    empty = [r for r in eligible if r.symptoms == 0]
    for run in empty:
        raw = read_json(study.source_root / "Results" / run.source, study.source_root)
        require(raw["symptoms"] == [] and raw["rca_tasks"] == []
                and raw["rca_analyses_list"] == [] and raw["final_report"]["detection"] is False,
                f"Empty-symptom path is not verified: {run.source}")
    f_empty = [r for r in empty if r.cohort == "F"]
    o_matches = [lookup["O", r.app, r.fault] for r in f_empty]
    study.details["evidence_paths"] = {
        "eligible_fault_runs": len(eligible), "empty_symptoms": len(empty),
        "empty_by_variant": dict(Counter(r.cohort for r in empty)),
        "F_empty_cases": len(f_empty), "O_positive_on_F_empty": sum(r.detected for r in o_matches),
        "O_restrictive_on_F_empty": sum(r.strict for r in o_matches),
    }
    study.details["case_selection"] = "Retrospective illustrative disagreements, "
    study.details["case_selection"] += "including an empty path, component match and graph query"
    study.details["cost_contrasts_vs_F_pct"] = {
        c: {field: 100 * (study.summary["main"][c]["costs"][field]["mean"] /
                         study.summary["main"]["F"]["costs"][field]["mean"] - 1)
            for field in ("tokens", "seconds")}
        for c in ("N", "O", "P", "M")
    }
    study.details["localization_discordance"] = {}
    for cohort in ("N", "O", "P", "M"):
        counts = Counter()
        for run in study.runs:
            if run.cohort == "F" and run.component_case:
                other = lookup[cohort, run.app, run.fault]
                outcome = ("both" if run.strict and other.strict else
                           "F_only" if run.strict else "other_only" if other.strict else "neither")
                counts[outcome] += 1
        study.details["localization_discordance"][cohort] = dict(counts)
    study.details["application_localization"] = {
        app: {c: {"matches": sum(r.strict for r in study.runs
                                 if r.app == app and r.cohort == c and r.component_case),
                  "eligible": sum(r.app == app and r.cohort == c and r.component_case
                                  for r in study.runs)}
              for c in COHORTS}
        for app in APPS
    }
    study.details["symptom_conditioned_topology"] = symptom_conditioned_topology(study.runs)
    render_figures(study.runs, study.costs, output)
    write_json(output / "results-summary.json", {**study.summary, **study.details})
    print(f"Rebuilt Results displays from {len(study.runs)} complete raw exports in {output}")
    print(f"Source-checked {len(case_output)} illustrative cases; "
          f"{len(empty)}/{len(eligible)} empty-symptom paths.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "Results")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis/output")
    parser.add_argument("--cases", type=Path, default=Path(__file__).parent / "results_cases.json")
    args = parser.parse_args()
    try:
        build(args.results, args.output, args.cases)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Results build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
