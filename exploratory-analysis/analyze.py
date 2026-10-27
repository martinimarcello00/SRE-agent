"""Reproduce the descriptive A--L exploration from complete, immutable raw exports."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "analysis"))

from analysis_metrics import describe, numeric, read_json, require, write_csv, write_json

Row = dict[str, Any]
AGENTS = tuple("ABCDEFGHIJKL")
APPS = ("Hotel Reservation", "Social Network", "Astronomy Shop")
NOTEBOOK_EXCLUSIONS = {"Container Kill", "Pod kill", "Redeploy Without PV"}
RENAMES = {
    "Port missmatch geo": "Geo application misconfiguration",
    "MongoDB url-shorten authentication missing": "MongoDB url-shorten TLS requirement",
}


def optional_boolean(value: Any, label: str) -> bool | None:
    require(value is None or type(value) is bool, f"{label}: expected Boolean or missing")
    return value


def optional_number(value: Any, label: str, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    result = numeric(value, label)
    require(not integer or type(result) is int, f"{label}: expected integer")
    return result


def parameter(value: Any, label: str) -> int:
    require(type(value) is int or (isinstance(value, str) and value.isdigit()),
            f"{label}: expected positive integer")
    result = int(value)
    require(result > 0, f"{label}: expected positive integer")
    return result


def load_runs(results: Path) -> list[Row]:
    require(results.is_dir(), f"Raw directory not found: {results}")
    require(not any(path.is_dir() for path in results.iterdir()),
            "Raw directory must not contain campaign subdirectories")
    files = sorted(results.glob("*.json"))
    require(bool(files), f"No JSON run exports in {results}")
    apps = {app.casefold(): app for app in APPS}
    rows, cells, ids, parameters = [], set(), set(), {}
    for path in files:
        raw = read_json(path, results)
        agent, app_raw = raw["agent_id"], raw["app_name"]
        require(agent in AGENTS, f"Unknown exploratory configuration: {agent}")
        require(isinstance(app_raw, str) and app_raw.casefold() in apps,
                f"Unknown application: {path.name}")
        app = apps[app_raw.casefold()]
        testbed, report = raw["testbed"], raw["final_report"]
        evaluation, stats = raw["evaluation"], raw.get("stats", {})
        require(all(isinstance(value, dict) for value in (testbed, report, evaluation, stats)),
                f"Invalid saved run structure: {path.name}")
        fault_raw = testbed["fault_name"]
        require(isinstance(fault_raw, str) and bool(fault_raw), f"Missing fault: {path.name}")
        fault = RENAMES.get(fault_raw, fault_raw)
        cell = (agent, app, fault)
        require(cell not in cells, f"Duplicate configuration/scenario: {cell}")
        cells.add(cell)
        run_id = stats.get("run_id")
        require(run_id is None or isinstance(run_id, str) and bool(run_id),
                f"Invalid recorded run ID: {path.name}")
        if run_id is not None:
            require(run_id not in ids, f"Duplicate recorded run ID: {path.name}")
            ids.add(run_id)
        fanout = parameter(testbed.get("rca_tasks_per_iteration"), path.name + ": fan-out")
        budget = parameter(testbed.get("max_tool_calls"), path.name + ": budget")
        require(agent not in parameters or parameters[agent] == (fanout, budget),
                f"Inconsistent configuration parameters: {agent}")
        parameters[agent] = (fanout, budget)
        healthy = fault == "No fault"
        detected = optional_boolean(report.get("detection"), path.name + ": detection")
        correctness = optional_boolean(evaluation.get("detection"),
                                       path.name + ": detection correctness")
        require(detected is None or correctness is None or correctness == (detected != healthy),
                f"Saved detection correctness/verdict mismatch: {path.name}")
        current = "localization_any" in evaluation
        strict = optional_boolean(evaluation.get("localization") if current else None,
                                  path.name + ": restrictive localization")
        any_target = optional_boolean(evaluation.get("localization_any"),
                                      path.name + ": any-target localization")
        require(strict is not True or any_target is True,
                f"Strict success without any-target success: {path.name}")
        legacy = optional_boolean(evaluation.get("localization_legacy") if current
                                  else evaluation.get("localization"),
                                  path.name + ": legacy localization")
        score = optional_number(evaluation.get("rca_score"), path.name + ": judge score",
                                integer=True)
        require(score is None or 1 <= score <= 5, f"Invalid judge score: {path.name}")
        status = stats.get("status")
        require(status is None or isinstance(status, str) and bool(status),
                f"Invalid recorded status: {path.name}")
        rows.append({
            "file": path.name, "agent": agent, "app": app, "fault": fault,
            "run_id": run_id, "fanout": fanout, "budget": budget,
            "healthy": healthy, "component_case": not healthy and fault != "Redeploy Without PV",
            "detected": detected, "strict": strict, "any": any_target,
            "legacy_localization": legacy, "scoring": "current" if current else "legacy",
            "judge_score": score, "previous_evaluation_saved": "evaluation_v1" in raw,
            "tokens": optional_number(stats.get("total_tokens"), path.name + ": tokens",
                                      integer=True),
            "seconds": optional_number(stats.get("execution_time_seconds"), path.name + ": time"),
            "status": status, "stats_error": stats.get("error"),
            "environment_valid": optional_boolean(raw.get("environment_valid"),
                                                  path.name + ": environment validity"),
            "notebook_excluded": app == "Hotel Reservation" and fault in NOTEBOOK_EXCLUSIONS,
        })
    return rows


def metric(rows: list[Row], field: str) -> Row:
    observed = [row[field] for row in rows if row[field] is not None]
    return {"yes": sum(observed), "n": len(observed), "eligible": len(rows),
            "missing": len(rows) - len(observed)}


def summarize(rows: list[Row]) -> Row:
    faults = [row for row in rows if not row["healthy"]]
    components = [row for row in rows if row["component_case"]]
    controls = [row for row in rows if row["healthy"]]
    return {
        "runs": len(rows), "fault_n": len(faults), "component_n": len(components),
        "control_n": len(controls),
        "detection": metric(faults, "detected"), "strict": metric(components, "strict"),
        "any": metric(components, "any"), "false_alarms": metric(controls, "detected"),
        "legacy_localization": metric(components, "legacy_localization"),
        "costs": {field: describe([row[field] for row in faults])
                  for field in ("tokens", "seconds")},
        "statuses": dict(Counter(row["status"] or "missing" for row in rows)),
    }


def analyze_rows(rows: list[Row]) -> tuple[Row, list[Row]]:
    require(bool(rows), "No exploratory runs to analyze")
    groups = {agent: [row for row in rows if row["agent"] == agent] for agent in AGENTS}
    common = set.intersection(*[
        {(row["app"], row["fault"]) for row in group if row["component_case"]
         and row["strict"] is not None and row["tokens"] is not None}
        for group in groups.values()
    ])
    scenarios = sorted({(row["app"], row["fault"]) for row in rows})
    lookup = {(row["agent"], row["app"], row["fault"]): row for row in rows}
    coverage = []
    for app, fault in scenarios:
        for agent in AGENTS:
            row = lookup.get((agent, app, fault))
            coverage.append({
                "application": app, "scenario": fault, "agent": agent,
                "runs": int(row is not None),
                "tokens_recorded": row is not None and row["tokens"] is not None,
                "seconds_recorded": row is not None and row["seconds"] is not None,
                "current_localization_recorded": row is not None and row["strict"] is not None,
                "common_component": (app, fault) in common,
            })
    summary = {
        "inventory": {
            "raw_runs": len(rows), "scenarios": len(scenarios),
            "configuration_scenario_cells": len(coverage),
            "missing_scenario_cells": len(coverage) - len(rows),
            "current_evaluations": sum(row["scoring"] == "current" for row in rows),
            "legacy_evaluations": sum(row["scoring"] == "legacy" for row in rows),
            "missing_tokens": sum(row["tokens"] is None for row in rows),
            "missing_seconds": sum(row["seconds"] is None for row in rows),
            "missing_status": sum(row["status"] is None for row in rows),
            "environment_unannotated": sum(row["environment_valid"] is None for row in rows),
            "environment_explicitly_invalid": sum(row["environment_valid"] is False for row in rows),
        },
        "configurations": [
            {"agent": agent, "fanout": group[0]["fanout"] if group else None,
             "budget": group[0]["budget"] if group else None}
            for agent, group in groups.items()
        ],
        "available": {agent: summarize(group) for agent, group in groups.items()},
        "by_application": {
            app: {agent: summarize([row for row in group if row["app"] == app])
                  for agent, group in groups.items()} for app in APPS
        },
        "common_components": {
            "selection": "Component scenarios present in all A-L with recorded current "
                         "restrictive localization and total tokens; time is not a selection filter",
            "scenario_count": len(common),
            "scenarios": [{"application": app, "scenario": fault} for app, fault in sorted(common)],
            "by_configuration": {
                agent: summarize([row for row in group if (row["app"], row["fault"]) in common])
                for agent, group in groups.items()
            },
        },
        "notebook_scope": {
            "selection": "Historical notebook excludes Hotel Reservation / Container Kill, "
                         "Pod kill and Redeploy Without PV; current metric definitions retained",
            "excluded_runs": sum(row["notebook_excluded"] for row in rows),
            "by_configuration": {
                agent: summarize([row for row in group if not row["notebook_excluded"]])
                for agent, group in groups.items()
            },
        },
        "interpretation": "Descriptive, retrospective exploration; one run per available "
                          "configuration/scenario cell; no significance tests or optimality claim",
    }
    return summary, coverage


def table_rows(groups: Row) -> list[Row]:
    rows = []
    for agent, group in groups.items():
        row = {"agent": agent, **{key: group[key]
                                for key in ("runs", "fault_n", "component_n", "control_n")}}
        for name in ("detection", "strict", "any", "false_alarms", "legacy_localization"):
            row.update({name + "_" + key: value for key, value in group[name].items()})
        for name, stats in group["costs"].items():
            row.update({name + "_" + key: stats.get(key) for key in
                        ("eligible", "n", "missing", "mean", "median", "sd", "q1", "q3",
                         "minimum", "maximum")})
        rows.append(row)
    return rows


def render_figures(summary: Row, coverage: list[Row], output: Path) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(HERE.parent / "build/matplotlib"))
    import matplotlib as mpl
    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    def export(fig, name):
        fig.canvas.draw()
        width, height = fig.get_size_inches()
        box = fig.get_tightbbox(fig.canvas.get_renderer())
        require(box.x0 >= -.02 and box.y0 >= -.02 and
                box.x1 <= width + .02 and box.y1 <= height + .02,
                f"Clipped content in exploratory figure: {name}")
        for extension in ("pdf", "svg", "png"):
            metadata = {"Creator": "A-L descriptive exploration"}
            if extension == "pdf":
                metadata["CreationDate"] = None
            elif extension == "svg":
                metadata["Date"] = None
            fig.savefig(output / f"{name}.{extension}", dpi=300, metadata=metadata,
                        facecolor="white", bbox_inches=None)
        plt.close(fig)

    with mpl.rc_context({
        "font.family": "DejaVu Sans", "font.size": 8, "pdf.fonttype": 42,
        "svg.fonttype": "none", "svg.hashsalt": "cain27-exploration",
    }):
        scenarios = list(dict.fromkeys((row["application"], row["scenario"]) for row in coverage))
        lookup = {(row["application"], row["scenario"], row["agent"]): row for row in coverage}
        matrix = []
        for app, fault in scenarios:
            states = []
            for agent in AGENTS:
                row = lookup[app, fault, agent]
                complete = all(row[key] for key in
                               ("tokens_recorded", "seconds_recorded", "current_localization_recorded"))
                states.append(0 if not row["runs"] else 2 if complete else 1)
            matrix.append(states)
        fig, ax = plt.subplots(figsize=(10.5, max(3, len(scenarios) * .22 + 1.4)),
                               layout="constrained")
        ax.imshow(matrix, cmap=ListedColormap(["white", "#bdbdbd", "#222222"]),
                  vmin=0, vmax=2, aspect="auto", interpolation="nearest")
        for y, states in enumerate(matrix):
            for x, state in enumerate(states):
                ax.text(x, y, ("-", "?", "1")[state], ha="center", va="center",
                        color="white" if state == 2 else "black", fontsize=7)
        ax.set_xticks(range(len(AGENTS)), AGENTS)
        ax.xaxis.tick_top()
        ax.set_yticks(range(len(scenarios)), [f"{app} / {fault}" for app, fault in scenarios])
        ax.tick_params(length=0)
        ax.set_title("A-L coverage (not diagnostic accuracy)", pad=12)
        fig.supxlabel("1: export + current localization + token/time telemetry   "
                      "?: export with missing fields   -: no export", fontsize=8)
        export(fig, "coverage")

        common = summary["common_components"]
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), layout="constrained")
        for ax, cost, label, scale in (
            (axes[0], "tokens", "Mean tokens per scenario (k)", 1000),
            (axes[1], "seconds", "Mean diagnostic time per scenario (s)", 1),
        ):
            for agent, group in common["by_configuration"].items():
                score, stats = group["strict"], group["costs"][cost]
                if not score["n"] or not stats["n"]:
                    continue
                x, y = stats["mean"] / scale, 100 * score["yes"] / score["n"]
                ax.scatter(x, y, marker="s" if agent == "F" else "o",
                           color="black" if agent == "F" else ".45", s=32)
                ax.annotate(agent, (x, y), xytext=(4, -11 if agent in "CGIK" else 4),
                            textcoords="offset points")
            means = [group["costs"][cost]["mean"] / scale
                     for group in common["by_configuration"].values()
                     if group["costs"][cost]["n"]]
            upper = max(means, default=0) * 1.15 or 1
            ax.set(xlabel=label, ylabel="Restrictive component localization (%)",
                   xlim=(0, upper), ylim=(0, 100))
            ax.grid(color=".9", linewidth=.5)
            if not common["scenario_count"]:
                ax.text(.5, .5, "No complete common A-L scenarios", transform=ax.transAxes,
                        ha="center")
        fig.suptitle(f"Descriptive A-L trade-offs: {common['scenario_count']} common component "
                     "scenarios per configuration", fontsize=10)
        fig.supxlabel("Points are configuration means/rates, not confidence intervals. "
                      "Square F is the reference, not a declared optimum.", fontsize=8)
        export(fig, "common-tradeoffs")


def build(results: Path, output: Path) -> None:
    require(not output.resolve().is_relative_to(results.resolve()),
            "Analysis output must be outside raw Results")
    rows = load_runs(results)
    summary, coverage = analyze_rows(rows)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "run_level.csv", rows)
    write_csv(output / "coverage.csv", coverage)
    write_csv(output / "overview.csv", table_rows(summary["available"]))
    write_csv(output / "common-components.csv",
              table_rows(summary["common_components"]["by_configuration"]))
    write_csv(output / "application-overview.csv", [
        {"application": app, **row}
        for app, groups in summary["by_application"].items() for row in table_rows(groups)
    ])
    write_json(output / "summary.json", summary)
    render_figures(summary, coverage, output)
    print(f"Analyzed {len(rows)} complete raw exports; "
          f"{summary['common_components']['scenario_count']} common component scenarios.")
    print(f"Missing telemetry retained explicitly: "
          f"{summary['inventory']['missing_tokens']} token and "
          f"{summary['inventory']['missing_seconds']} time records.")
    print(f"Outputs: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=HERE / "Results")
    parser.add_argument("--output", type=Path, default=HERE / "output")
    args = parser.parse_args()
    try:
        build(args.results, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Exploratory analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
