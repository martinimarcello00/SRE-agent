"""Strict input handling and descriptive statistics used by the paper analysis."""

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

Row = dict[str, Any]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique_object(pairs: list[tuple[str, Any]]) -> Row:
    result: Row = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON constant: {value}")


def read_json(path: Path, root: Path) -> Row:
    require(path.is_file() and not path.is_symlink(), f"Not a regular input: {path.name}")
    require(path.resolve().is_relative_to(root.resolve()), "Input outside declared root")
    require(path.stat().st_size <= 64 * 1024**2, "Input exceeds 64 MiB")
    result = json.loads(path.read_text(), object_pairs_hook=unique_object,
                        parse_constant=reject_constant)
    require(isinstance(result, dict), f"Expected JSON object: {path.name}")
    return result


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n")


def write_csv(path: Path, rows: list[Row]) -> None:
    require(bool(rows), f"Empty output table: {path.name}")
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def numeric(value: Any, label: str) -> int | float:
    require(not isinstance(value, bool) and isinstance(value, (int, float)),
            f"{label}: expected number")
    require(math.isfinite(value) and value >= 0, f"{label}: invalid numeric value")
    return value


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def describe(values: list[int | float | None]) -> Row:
    observed = [value for value in values if value is not None]
    result: Row = {"eligible": len(values), "n": len(observed),
                   "missing": len(values) - len(observed)}
    if not observed:
        return result
    median = statistics.median(observed)
    result.update(
        sum=sum(observed), mean=statistics.mean(observed), median=median,
        q1=percentile(observed, .25), q3=percentile(observed, .75),
        sd=statistics.stdev(observed) if len(observed) > 1 else None,
        mad=statistics.median(abs(value - median) for value in observed),
        minimum=min(observed), maximum=max(observed),
        largest_share_pct=100 * max(observed) / sum(observed) if sum(observed) else None,
        mean_without_largest=statistics.mean(sorted(observed)[:-1])
        if len(observed) > 1 else None,
    )
    return result


def summarize(rows: list[Row]) -> Row:
    faults = [row for row in rows if not row["healthy"]]
    components = [row for row in rows if row["component_case"]]
    controls = [row for row in rows if row["healthy"]]
    return {
        "runs": len(rows), "fault_n": len(faults), "component_n": len(components),
        "control_n": len(controls),
        **{name: {"yes": sum(row[field] for row in group), "n": len(group),
                  "eligible": len(group), "missing": 0}
           for name, field, group in (
               ("detection", "detected", faults), ("strict", "strict", components),
               ("any", "any_target", components), ("false_alarms", "detected", controls))},
        "costs": {field: describe([row[field] for row in faults])
                  for field in ("tokens", "seconds", "workers", "worker_calls", "triage_calls")},
        "component_costs": {field: describe([row[field] for row in components])
                            for field in ("tokens", "seconds")},
    }
