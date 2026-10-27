import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analyze.py"
AGENTS = "ABCDEFGHIJKL"


def export(agent="A", fault="Example", app="Hotel Reservation"):
    healthy = fault == "No fault"
    return {
        "agent_id": agent,
        "agent_configuration_name": f"Configuration {agent}",
        "app_name": app,
        "testbed": {"fault_name": fault, "rca_tasks_per_iteration": "2",
                    "max_tool_calls": "7"},
        "final_report": {"detection": not healthy,
                         "localization": [] if healthy else ["example"]},
        "evaluation": {"detection": True, "localization": True,
                       "localization_any": True, "rca_score": 3},
        "stats": {"run_id": f"{agent}/{app}/{fault}", "status": "success",
                  "total_tokens": 1000, "execution_time_seconds": 10},
    }


def save(root, name, raw):
    path = root / name
    path.write_text(json.dumps(raw, indent=2) + "\n")
    return path


class ExploratoryAnalysisTests(unittest.TestCase):
    def engine(self):
        self.assertTrue(SCRIPT.is_file(), "The offline exploratory analysis is missing")
        spec = importlib.util.spec_from_file_location("exploratory_analyze", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_bundled_data_reproduces_frozen_exploration_and_shared_F_runs(self):
        engine = self.engine()
        rows = engine.load_runs(ROOT / "Results")
        summary, coverage = engine.analyze_rows(rows)
        expected = {
            "A": (28, 10, 1252421), "B": (28, 12, 1953045),
            "C": (28, 11, 2522929), "D": (29, 12, 1619128),
            "E": (31, 11, 2550747), "F": (32, 12, 2893853),
            "G": (32, 10, 3371436), "H": (29, 10, 2128641),
            "I": (29, 9, 1935073), "J": (29, 11, 1692057),
            "K": (29, 12, 2386405), "L": (29, 11, 1898250),
        }
        self.assertEqual(len(rows), 353)
        self.assertEqual(len(coverage), 384)
        self.assertEqual(summary["inventory"]["missing_scenario_cells"], 31)
        self.assertEqual(summary["inventory"]["current_evaluations"], 353)
        self.assertEqual(summary["inventory"]["missing_tokens"], 2)
        self.assertEqual(summary["inventory"]["missing_seconds"], 2)
        self.assertEqual(summary["notebook_scope"]["excluded_runs"], 18)
        for agent, (available, localized, tokens) in expected.items():
            with self.subTest(agent=agent):
                group = summary["common_components"]["by_configuration"][agent]
                self.assertEqual(summary["available"][agent]["runs"], available)
                self.assertEqual((group["component_n"], group["strict"]["yes"]), (19, localized))
                self.assertEqual(group["costs"]["tokens"]["sum"], tokens)
        main = ROOT.parent / "Results"
        overlap = [row for row in rows if (main / row["file"]).is_file()]
        self.assertEqual(len(overlap), 32)
        self.assertTrue(all(row["agent"] == "F" for row in overlap))
        for row in overlap:
            self.assertEqual((ROOT / "Results" / row["file"]).read_bytes(),
                             (main / row["file"]).read_bytes())

    def test_cli_builds_offline_without_manifest_and_preserves_complete_raws(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "Results"
            results.mkdir()
            for agent in AGENTS:
                save(results, f"{agent}-fault.json", export(agent))
                save(results, f"{agent}-control.json", export(agent, "No fault"))
            missing = export("H", "Missing costs", "Social Network")
            missing["stats"] = {"error": "Recorded telemetry lookup failed"}
            save(results, "original name with missing stats.json", missing)
            save(results, "namespace.json", export("A", "Redeploy Without PV"))
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in results.iterdir()}
            output = root / "output"
            process = subprocess.run(
                [sys.executable, str(SCRIPT), "--results", str(results),
                 "--output", str(output)],
                capture_output=True, text=True, cwd=root,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["inventory"]["raw_runs"], 26)
            self.assertEqual(summary["inventory"]["missing_tokens"], 1)
            self.assertEqual(summary["common_components"]["scenario_count"], 1)
            self.assertEqual(summary["available"]["A"]["component_n"], 1)
            self.assertEqual(summary["available"]["H"]["costs"]["tokens"]["missing"], 1)
            self.assertEqual(summary["available"]["H"]["costs"]["tokens"]["mean"], 1000)
            for name in ("coverage", "common-tradeoffs"):
                self.assertTrue((output / f"{name}.pdf").read_bytes().startswith(b"%PDF"))
                self.assertIn("<svg", (output / f"{name}.svg").read_text())
                self.assertGreater((output / f"{name}.png").stat().st_size, 1000)
            self.assertEqual(before, {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in results.iterdir()
            })
            self.assertFalse(any("manifest" in p.name or "provenance" in p.name
                                 for p in root.rglob("*")))

    def test_legacy_localization_is_not_pooled_with_current_scoring(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = export()
            del legacy["evaluation"]["localization_any"]
            save(root, "legacy.json", legacy)
            current = export("B")
            current["evaluation"]["localization"] = False
            current["evaluation_v1"] = {"localization": True, "localization_any": True}
            save(root, "current.json", current)
            rows = engine.load_runs(root)
            summary, _ = engine.analyze_rows(rows)
            self.assertEqual(summary["available"]["A"]["strict"],
                             {"yes": 0, "n": 0, "eligible": 1, "missing": 1})
            self.assertEqual(summary["available"]["A"]["legacy_localization"]["yes"], 1)
            self.assertEqual(summary["available"]["B"]["strict"]["yes"], 0)
            self.assertEqual(summary["inventory"]["legacy_evaluations"], 1)

    def test_missing_and_zero_costs_are_distinct_and_controls_are_separate(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            zero = export()
            zero["stats"]["total_tokens"] = 0
            save(root, "zero.json", zero)
            missing = export("A", "Other")
            del missing["stats"]["total_tokens"]
            missing["stats"]["execution_time_seconds"] = None
            save(root, "missing.json", missing)
            save(root, "control.json", export("A", "No fault"))
            save(root, "namespace.json", export("A", "Redeploy Without PV"))
            rows = engine.load_runs(root)
            summary, _ = engine.analyze_rows(rows)
            available = summary["available"]["A"]
            self.assertEqual((available["fault_n"], available["component_n"],
                              available["control_n"]), (3, 2, 1))
            self.assertEqual(available["costs"]["tokens"]["n"], 2)
            self.assertEqual(available["costs"]["tokens"]["missing"], 1)
            self.assertEqual(available["costs"]["tokens"]["mean"], 500)
            self.assertEqual(available["false_alarms"]["yes"], 0)
            self.assertIsNone(next(row for row in rows
                                   if row["file"] == "missing.json")["tokens"])

    def test_common_population_is_intersection_across_all_twelve_configurations(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for agent in AGENTS:
                for fault in ("Example", "Missing H costs", "No fault", "Redeploy Without PV"):
                    raw = export(agent, fault)
                    if agent == "H" and fault == "Missing H costs":
                        raw["stats"] = {"error": "Unavailable"}
                    save(root, f"{agent}-{fault}.json", raw)
            save(root, "extra.json", export("A", "Only A"))
            summary, coverage = engine.analyze_rows(engine.load_runs(root))
            common = summary["common_components"]
            self.assertEqual(common["scenarios"],
                             [{"application": "Hotel Reservation", "scenario": "Example"}])
            self.assertTrue(all(group["component_n"] == 1
                                for group in common["by_configuration"].values()))
            self.assertEqual(len(coverage), 60)
            self.assertEqual(sum(row["runs"] for row in coverage), 49)
            self.assertEqual(sum(row["runs"] == 0 for row in coverage), 11)

    def test_absent_configuration_does_not_create_a_smaller_balanced_comparison(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save(root, "A.json", export("A"))
            save(root, "B.json", export("B"))
            summary, _ = engine.analyze_rows(engine.load_runs(root))
            self.assertEqual(summary["common_components"]["scenario_count"], 0)
            self.assertEqual(summary["available"]["L"]["runs"], 0)

    def test_duplicate_cells_and_recorded_run_ids_are_rejected(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = export()
            save(root, "one.json", raw)
            second = copy.deepcopy(raw)
            second["stats"]["run_id"] = "different"
            save(root, "two.json", second)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                engine.load_runs(root)
            second["testbed"]["fault_name"] = "Different"
            second["stats"]["run_id"] = raw["stats"]["run_id"]
            save(root, "two.json", second)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                engine.load_runs(root)

    def test_invalid_values_do_not_become_missing_or_success(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for key, invalid in (("total_tokens", True), ("total_tokens", -1),
                                 ("total_tokens", 1.5), ("execution_time_seconds", "10")):
                raw = export()
                raw["stats"][key] = invalid
                save(root, "run.json", raw)
                with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                    engine.load_runs(root)
            raw = export()
            raw["final_report"]["detection"] = "false"
            save(root, "run.json", raw)
            with self.assertRaises(ValueError):
                engine.load_runs(root)
            raw = export()
            raw["evaluation"]["localization_any"] = False
            save(root, "run.json", raw)
            with self.assertRaises(ValueError):
                engine.load_runs(root)

    def test_parameter_mismatch_is_not_silently_pooled(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save(root, "one.json", export())
            other = export("A", "Other")
            other["testbed"]["max_tool_calls"] = "15"
            save(root, "two.json", other)
            with self.assertRaisesRegex(ValueError, "configuration parameters"):
                engine.load_runs(root)

    def test_empty_nested_symlinked_and_invalid_json_inputs_fail(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                engine.load_runs(root)
            raw = save(root, "raw.json", export())
            folder = root / "campaign"
            folder.mkdir()
            with self.assertRaisesRegex(ValueError, "subdirector"):
                engine.load_runs(root)
            folder.rmdir()
            link = root / "link.json"
            link.symlink_to(raw)
            with self.assertRaises(ValueError):
                engine.load_runs(root)
            link.unlink()
            for text in ('{"agent_id":"A","agent_id":"B"}', '{"x":NaN}', '[]'):
                raw.write_text(text)
                with self.assertRaises(ValueError):
                    engine.load_runs(root)

    def test_historical_notebook_exclusions_are_a_separate_population(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, fault in enumerate(("Container Kill", "Pod kill", "Redeploy Without PV",
                                           "No fault", "Example")):
                save(root, f"{index}.json", export("A", fault))
            summary, _ = engine.analyze_rows(engine.load_runs(root))
            self.assertEqual(summary["available"]["A"]["runs"], 5)
            self.assertEqual(summary["notebook_scope"]["by_configuration"]["A"]["runs"], 2)
            self.assertEqual(summary["notebook_scope"]["excluded_runs"], 3)

    def test_cli_refuses_to_write_outputs_among_raw_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save(root, "run.json", export())
            process = subprocess.run(
                [sys.executable, str(SCRIPT), "--results", str(root),
                 "--output", str(root / "output")], capture_output=True, text=True,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("outside raw", process.stderr)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
