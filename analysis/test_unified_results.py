import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import build_paper_results as builder


def export(cohort, fault="Example", app="Hotel Reservation", **updates):
    healthy = fault == "No fault"
    stage = "react-agent" if cohort == "M" else "rca_agent"
    raw = {
        "agent_id": cohort,
        "app_name": app,
        "testbed": {"fault_name": fault},
        "prompts_config": {} if cohort == "M" else {"rca_agent": "autonomous"},
        "final_report": {
            "detection": not healthy,
            "localization": [] if healthy else ["example"],
            "root_cause": "A diagnosis",
        },
        "evaluation": {
            "detection": True,
            "localization": not healthy,
            "localization_any": not healthy,
            "rca_score": 2,
            "rca_motivation": "Judge assessment.",
        },
        "symptoms": [{"affected_resource": "example"}],
        "rca_tasks": [{"target_resource": "example"}],
        "rca_analyses_list": [{"tools_stats": {"kubectl_get": 2, "submit_report": 1}}],
        "stats": {
            "run_id": f"{cohort}/{app}/{fault}",
            "total_tokens": 1000,
            "execution_time_seconds": 10,
            "status": "success",
            "agent_stats": {stage: {
                "total_tokens": 1000, "input_tokens": 900, "output_tokens": 100,
            }},
        },
    }
    raw.update(updates)
    return raw


def save(root, name, raw):
    path = root / name
    path.write_text(json.dumps(raw, indent=2) + "\n")
    return path


class UnifiedResultsTests(unittest.TestCase):
    def test_bundled_dataset_reproduces_published_outcomes_and_costs(self):
        study = builder.load_study(Path(builder.__file__).resolve().parent.parent / "Results")
        expected = {
            "F": (23, 14, 15, 0, 116464.37931034483, 219.82328896551724),
            "N": (19, 9, 10, 0, 60887.93103448276, 108.47701210344827),
            "O": (28, 12, 16, 2, 252124.8620689655, 314.5726184827586),
            "P": (21, 13, 13, 1, 66752.20689655172, 90.59353848275862),
            "M": (25, 17, 17, 1, 101873.41379310345, 65.72540707978084),
        }
        self.assertEqual(len(study.runs), 160)
        for cohort, values in expected.items():
            with self.subTest(cohort=cohort):
                summary = study.summary["main"][cohort]
                self.assertEqual((summary["fault_n"], summary["component_n"],
                                  summary["control_n"]), (29, 28, 3))
                self.assertEqual(tuple(summary[field]["yes"] for field in
                                       ("detection", "strict", "any", "false_alarms")),
                                 values[:4])
                self.assertAlmostEqual(summary["costs"]["tokens"]["mean"], values[4])
                self.assertAlmostEqual(summary["costs"]["seconds"]["mean"], values[5])
        self.assertEqual(sum(run.status != "success" for run in study.runs), 1)
        sensitivity = builder.symptom_conditioned_topology(study.runs)
        self.assertEqual((sensitivity["component_cases"], sensitivity["F_matches"],
                          sensitivity["N_matches"]), (17, 11, 8))
        self.assertEqual(study.summary["without_pending_Kafka"]["M"]["strict"]["yes"], 17)

    def test_cli_builds_from_flat_raw_json_without_snapshot_or_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "Results"
            results.mkdir()
            apps = ("Hotel Reservation", "Social Network", "Astronomy Shop")
            for cohort in builder.COHORTS:
                for index in range(29):
                    fault = "Redeploy Without PV" if index == 28 else f"Fault {index}"
                    raw = export(cohort, fault)
                    save(results, f"{cohort}-{index}.json", raw)
                for index, app in enumerate(apps):
                    save(results, f"{cohort}-control-{index}.json",
                         export(cohort, "No fault", app))
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in results.iterdir()}
            cases = save(root, "cases.json", {"cases": []})
            output = root / "output"
            process = subprocess.run(
                [sys.executable, str(Path(builder.__file__)), "--results", str(results),
                 "--output", str(output), "--cases", str(cases)],
                capture_output=True, text=True,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertTrue((output / "results-overview-table.tex").is_file())
            self.assertTrue((output / "results-cost-distributions.pdf").is_file())
            summary = json.loads((output / "results-summary.json").read_text())
            self.assertEqual(summary["main"]["F"]["strict"]["yes"], 28)
            self.assertEqual(before, {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in results.iterdir()
            })
            self.assertFalse(any("manifest" in p.name or "provenance" in p.name
                                 for p in root.rglob("*")))

    def test_reading_raw_preserves_names_contents_and_saved_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = export("F")
            raw["stats"]["status"] = "pending"
            path = save(root, "original name.json", raw)
            before = path.read_bytes()
            runs, stages = builder.load_results(root)
            self.assertEqual((runs[0].source, runs[0].status),
                             ("original name.json", "pending"))
            self.assertEqual(runs[0].worker_calls, 2)
            self.assertEqual(stages[0]["total_tokens"], 1000)
            self.assertEqual(path.read_bytes(), before)

    def test_N_analysis_population_is_independent_of_export_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save(root, "f-example.json", export("F"))
            save(root, "f-other.json", export("F", "Other"))
            save(root, "n-example.json", export("N"))
            save(root, "n-other.json", export(
                "N", "Other", prompts_config={"rca_agent": "export metadata"}))
            runs, _ = builder.load_results(root)
            sensitivity = builder.symptom_conditioned_topology(runs)
            self.assertEqual((sensitivity["component_cases"], sensitivity["F_matches"],
                              sensitivity["N_matches"]), (2, 2, 2))
            self.assertEqual({row["scenario"] for row in sensitivity["run_pairs"]},
                             {"Example", "Other"})

    def test_N_loader_does_not_require_reference_configuration_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = export("N", prompts_config={})
            save(root, "n.json", raw)
            runs, stages = builder.load_results(root)
            self.assertEqual([(run.cohort, run.fault) for run in runs], [("N", "Example")])
            self.assertEqual(stages[0]["total_tokens"], 1000)

    def test_zero_token_stages_are_valid_recorded_telemetry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = export("F")
            raw["stats"]["agent_stats"]["schedule_rca_tasks"] = {
                "total_tokens": 0, "input_tokens": 0, "output_tokens": 0,
            }
            save(root, "run.json", raw)
            runs, stages = builder.load_results(root)
            costs = builder.stage_costs(runs, stages)
            self.assertEqual(next(row["total_tokens"] for row in stages
                                  if row["stage"] == "schedule_rca_tasks"), 0)
            self.assertEqual(next(cost.tokens for cost in costs
                                  if cost.stage == "rca_agent"), 1000)

    def test_duplicate_configuration_scenario_or_run_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = export("F")
            save(root, "one.json", first)
            second = copy.deepcopy(first)
            second["stats"]["run_id"] = "another-id"
            path = save(root, "two.json", second)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                builder.load_results(root)
            second["testbed"]["fault_name"] = "Other"
            second["stats"]["run_id"] = first["stats"]["run_id"]
            path.write_text(json.dumps(second))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                builder.load_results(root)

    def test_missing_telemetry_and_invalid_verdicts_are_not_silent_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for field in ("total_tokens", "execution_time_seconds"):
                raw = export("F")
                del raw["stats"][field]
                save(root, "run.json", raw)
                with self.subTest(field=field), self.assertRaises(ValueError):
                    builder.load_results(root)
            raw = export("F")
            raw["final_report"]["detection"] = "false"
            save(root, "run.json", raw)
            with self.assertRaises(ValueError):
                builder.load_results(root)

    def test_campaign_subdirectories_and_empty_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "No JSON"):
                builder.load_results(root)
            save(root, "run.json", export("F"))
            (root / "old-campaign").mkdir()
            with self.assertRaisesRegex(ValueError, "subdirector"):
                builder.load_results(root)


if __name__ == "__main__":
    unittest.main()
