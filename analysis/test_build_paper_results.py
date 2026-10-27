import importlib
import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


def csv_run(**updates):
    row = {
        "id": "run-f", "cohort": "F", "role": "primary",
        "app": "Hotel Reservation", "fault": "Example",
        "healthy": "False", "component_case": "True",
        "detected": "True", "strict": "True", "any": "True",
        "tokens": "100", "seconds": "10", "symptom_count": "1",
        "task_count": "1", "workers": "1", "status": "success",
        "worker_calls": "2", "triage_calls": "",
        "source": "campaign/run-f.json", "snapshot_path": "raw/run-f.json",
        "sha256": "0" * 64,
    }
    row.update(updates)
    return row


def score_export(builder, root, name, score, analysis="A diagnosis",
                 motivation="Judge assessment.", **updates):
    source = f"campaign/{name}.json"
    path = root / "Results" / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "final_report": {"root_cause": analysis},
        "evaluation": {"rca_score": score, "rca_motivation": motivation},
    }))
    return builder.select_runs([
        csv_run(id=name, fault=name, source=source, sha256=builder.sha(path), **updates)
    ], [])[0]


class PaperResultsTests(unittest.TestCase):
    def setUp(self):
        if importlib.util.find_spec("build_paper_results") is None:
            self.fail("The requested reproducible Results builder is not implemented")
        self.builder = importlib.import_module("build_paper_results")

    def test_amendments_promote_only_the_manifest_selected_runs(self):
        selected = csv_run(id="n-old", cohort="N_old", role="historical_N",
                           source="old/n.json")
        unselected = csv_run(id="n-other", cohort="N_old", role="historical_N",
                             fault="Other", source="old/other.json")
        additions = [{"id": "n-old", "configuration": "N",
                      "source_relative_to_results": "old/n.json", "sha256": "0" * 64}]
        runs = self.builder.select_runs([csv_run(), selected, unselected], additions)
        self.assertEqual([(r.id, r.cohort) for r in runs], [("run-f", "F"), ("n-old", "N")])

    def test_missing_selected_export_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Missing selected"):
            self.builder.select_runs([csv_run()], [{"id": "absent", "configuration": "N",
                                                   "source_relative_to_results": "n.json",
                                                   "sha256": "0" * 64}])

    def test_missing_cost_is_not_replaced_by_zero(self):
        with self.assertRaises(ValueError):
            self.builder.select_runs([csv_run(tokens="")], [])

    def test_invalid_boolean_is_not_treated_as_a_failure(self):
        with self.assertRaises(ValueError):
            self.builder.select_runs([csv_run(strict="unknown")], [])

    def test_duplicate_configuration_scenario_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.builder.select_runs([csv_run(), csv_run(id="duplicate")], [])

    def test_strict_localization_implies_any_target(self):
        with self.assertRaises(ValueError):
            self.builder.select_runs([csv_run(strict="True", any="False")], [])

    def test_stage_mean_uses_all_eligible_fault_runs_not_only_recorded_stages(self):
        runs = self.builder.select_runs([
            csv_run(),
            csv_run(id="idle", fault="Idle", tokens="0", workers="0",
                    symptom_count="0", task_count="0", detected="False",
                    strict="False", any="False"),
            csv_run(id="control", fault="No fault", healthy="True",
                    component_case="False", tokens="999"),
        ], [])
        costs = self.builder.stage_costs(runs, [
            {"id": "run-f", "stage": "rca_agent", "total_tokens": "100"},
            {"id": "control", "stage": "triage_agent", "total_tokens": "999"},
        ])
        worker = next(c for c in costs if c.stage == "rca_agent")
        self.assertEqual((worker.tokens, worker.mean_tokens, worker.eligible_runs,
                          worker.recorded_runs), (100, 50, 2, 1))
        self.assertFalse(any(c.tokens == 999 for c in costs))

    def test_unaccounted_stage_tokens_are_not_silently_dropped(self):
        runs = self.builder.select_runs([csv_run(tokens="101")], [])
        with self.assertRaisesRegex(ValueError, "Stage accounting"):
            self.builder.stage_costs(runs, [
                {"id": "run-f", "stage": "rca_agent", "total_tokens": "100"}])

    def test_missing_stage_telemetry_is_not_zero(self):
        runs = self.builder.select_runs([csv_run()], [])
        with self.assertRaises(ValueError):
            self.builder.stage_costs(runs, [
                {"id": "run-f", "stage": "rca_agent", "total_tokens": ""}])

    def test_cost_distribution_retains_fault_points_and_excludes_controls(self):
        runs = self.builder.select_runs([
            csv_run(id=str(i), fault=str(i), tokens=str(tokens), seconds=str(seconds))
            for i, (tokens, seconds) in enumerate(
                [(1000, 1), (3000, 3), (6000, 6), (10000, 10)])
        ] + [csv_run(id="healthy", fault="No fault", healthy="True",
                     component_case="False", tokens="999999")], [])
        data = self.builder.cost_distributions(runs)
        tokens = next(r for r in data if r["metric"] == "tokens")
        self.assertEqual(tokens["values"], [1000, 3000, 6000, 10000])
        self.assertEqual((tokens["n"], tokens["q1"], tokens["median"], tokens["q3"]),
                         (4, 2500, 4500, 7000))
        seconds = next(r for r in data if r["metric"] == "seconds")
        self.assertEqual((seconds["minimum"], seconds["maximum"]), (1, 10))

    def test_cost_distribution_does_not_drop_early_termination_runs(self):
        runs = self.builder.select_runs([
            csv_run(tokens="800", workers="0", symptom_count="0",
                    task_count="0", strict="False", any="False", detected="False"),
        ], [])
        tokens = next(r for r in self.builder.cost_distributions(runs)
                      if r["metric"] == "tokens")
        self.assertEqual(tokens["values"], [800])

    def test_log_token_plot_rejects_zero_instead_of_hiding_the_run(self):
        runs = self.builder.select_runs([csv_run(tokens="0")], [])
        with self.assertRaisesRegex(ValueError, "positive"):
            self.builder.cost_distributions(runs)

    def test_topology_sensitivity_excludes_only_upstream_nonstarts(self):
        rows = []
        for name, f_tokens, n_tokens, n_symptoms in (
            ("eligible", 100, 50, "1"), ("nonstart", 200, 60, "0"),
            ("also-eligible", 300, 70, "1"),
        ):
            rows.extend([
                csv_run(id="F-" + name, cohort="F", fault=name, tokens=str(f_tokens)),
                csv_run(id="N-" + name, cohort="N", fault=name, tokens=str(n_tokens),
                        symptom_count=n_symptoms, strict="False", any="False"),
            ])
        runs = self.builder.select_runs(rows, [])
        result = self.builder.symptom_conditioned_topology(runs)
        self.assertEqual((result["component_cases"], result["F_matches"],
                          result["N_matches"]), (2, 2, 0))
        self.assertEqual((result["F_mean_tokens"], result["N_mean_tokens"]), (200, 60))
        self.assertEqual(result["run_pairs"][0]["scenario"], "eligible")

    def test_topology_sensitivity_requires_the_matching_F_execution(self):
        runs = self.builder.select_runs([csv_run(cohort="N")], [])
        with self.assertRaisesRegex(ValueError, "Missing F"):
            self.builder.symptom_conditioned_topology(runs)

    def test_case_pointer_supports_lists_and_escaped_keys(self):
        value = {"a/b": [{"~key": "evidence"}]}
        self.assertEqual(self.builder.read_pointer(value, "/a~1b/0/~0key"), "evidence")

    def test_case_evidence_mismatch_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Case evidence"):
            self.builder.check_case(
                {"report": "wrong component"},
                [{"pointer": "/report", "contains": "accepted component"}])

    def test_overview_export_is_a_complete_tabular_environment(self):
        summary = {"main": {c: {
            "fault_n": 29, "component_n": 28, "control_n": 3,
            "detection": {"yes": 2}, "strict": {"yes": 1},
            "any": {"yes": 2}, "false_alarms": {"yes": 0},
            "costs": {"tokens": {"mean": 12500}, "seconds": {"mean": 9.5}},
        } for c in ("F", "N", "O", "P", "M")}}
        tex = self.builder.overview_table(summary)
        self.assertTrue(tex.startswith(r"\begin{tabular*}"))
        self.assertTrue(tex.rstrip().endswith(r"\end{tabular*}"))
        self.assertIn(r"N & 2/29 & 1/28 & 2/28 & 0/3 & 12.5 & 9.5 \\", tex)
        for heading in ("Variant", "Detection", "Restrictive", "Any-target",
                        "Alarms/controls", "Mean tokens (k)", "Mean time (s)"):
            self.assertIn(r"\textbf{" + heading + "}", tex)
        self.assertNotIn(r"\shortstack", tex)

    def test_rca_scores_retain_nonstarts_and_namespace_faults_but_exclude_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            automatic = score_export(
                self.builder, root, "nonstart", 1,
                analysis="No analysis data available",
                motivation="No analysis was produced; judge not called.",
                strict="False", any="False")
            judged = score_export(self.builder, root, "judged", 1)
            namespace = score_export(
                self.builder, root, "namespace", 5, component_case="False")
            control = self.builder.select_runs([
                csv_run(id="control", fault="No fault", healthy="True",
                        component_case="False")
            ], [])[0]
            rows = self.builder.rca_score_records(
                [automatic, judged, namespace, control], root)
            self.assertEqual(
                [(r["id"], r["score"], r["origin"]) for r in rows],
                [("nonstart", 1, "automatic_no_analysis"),
                 ("judged", 1, "judge"), ("namespace", 5, "judge")])
            self.assertFalse(rows[2]["component_case"])

    def test_rca_scores_reject_missing_noninteger_or_out_of_range_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for score in (None, True, 0, 6, "2", 2.5):
                with self.subTest(score=score):
                    run = score_export(self.builder, root, "invalid", score)
                    with self.assertRaisesRegex(ValueError, "Invalid RCA score"):
                        self.builder.rca_score_records([run], root)

    def test_rca_scores_reject_inconsistent_automatic_score_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for score, analysis, motivation in (
                (2, "", "No analysis was produced; judge not called."),
                (1, "", "Judge assessment."),
                (1, "A diagnosis", "No analysis was produced; judge not called."),
                (1, "A diagnosis", ""),
            ):
                with self.subTest(score=score, analysis=analysis, motivation=motivation):
                    run = score_export(self.builder, root, "invalid", score,
                                       analysis, motivation)
                    with self.assertRaisesRegex(ValueError, "RCA score provenance"):
                        self.builder.rca_score_records([run], root)

    def test_rca_scores_reject_modified_original_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = score_export(self.builder, root, "modified", 2)
            (root / "Results" / run.source).write_text("{}")
            with self.assertRaisesRegex(ValueError, "Changed RCA score source"):
                self.builder.rca_score_records([run], root)

    def test_rca_score_summary_cross_tab_uses_only_restrictive_component_matches(self):
        records = [
            {"variant": "F", "score": 1, "origin": "automatic_no_analysis",
             "component_case": True, "restrictive": False},
            {"variant": "F", "score": 1, "origin": "automatic_no_analysis",
             "component_case": True, "restrictive": False},
            {"variant": "F", "score": 1, "origin": "judge",
             "component_case": True, "restrictive": True},
            {"variant": "F", "score": 2, "origin": "judge",
             "component_case": False, "restrictive": True},
            {"variant": "F", "score": 3, "origin": "judge",
             "component_case": True, "restrictive": True},
            {"variant": "F", "score": 4, "origin": "judge",
             "component_case": True, "restrictive": False},
            {"variant": "F", "score": 5, "origin": "judge",
             "component_case": True, "restrictive": True},
        ]
        summary = self.builder.rca_score_summary(records)
        self.assertEqual(summary, [{
            "variant": "F", "fault_n": 7, "score_1_auto": 2, "score_1_judge": 1,
            "score_2": 1, "score_3": 1, "score_4": 1, "score_5": 1,
            "localized_n": 3, "localized_low_score_n": 1,
        }])
        tex = self.builder.rca_score_table(summary)
        self.assertTrue(tex.startswith(r"\begin{tabular*}{\columnwidth}"))
        self.assertTrue(tex.rstrip().endswith(r"\end{tabular*}"))
        self.assertIn(r"F & 2 & 1 & 1 & 1 & 1 & 1 & 1/3 \\", tex)
        for heading in ("Variant", "No analysis", "1", "2", "3", "4", "5", "Low/localized"):
            self.assertIn(r"\textbf{" + heading + "}", tex)
        self.assertNotIn(r"\shortstack", tex)

    def test_plot_export_produces_vector_pdf_svg_and_png(self):
        runs = []
        stages = []
        for cohort in ("F", "N", "O", "P", "M"):
            runs.extend(self.builder.select_runs([
                csv_run(id=cohort, cohort=cohort, tokens="1000")], []))
            stages.append({"id": cohort, "stage": "react-agent" if cohort == "M"
                           else "rca_agent", "total_tokens": "1000"})
        costs = self.builder.stage_costs(runs, stages)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            self.builder.render_figures(runs, costs, destination)
            for stem in ("results-stage-tokens", "results-scenario-matrix",
                         "results-cost-distributions"):
                self.assertTrue((destination / f"{stem}.pdf").is_file(),
                                f"Missing requested figure: {stem}")
                self.assertTrue((destination / f"{stem}.pdf").read_bytes().startswith(b"%PDF"))
                self.assertGreater((destination / f"{stem}.png").stat().st_size, 1000)
                svg = (destination / f"{stem}.svg").read_text()
                self.assertIn("<svg", svg)
                self.assertNotIn("<pattern", svg, "Publication plots must use solid fills")

    def test_N_source_name_does_not_change_published_matrix_encoding(self):
        runs = self.builder.select_runs([
            csv_run(id=c, cohort=c, tokens="1000") for c in ("F", "N", "O", "P", "M")
        ], [])
        costs = self.builder.stage_costs(runs, [
            {"id": r.id, "stage": "react-agent" if r.cohort == "M" else "rca_agent",
             "total_tokens": "1000"} for r in runs
        ])
        with tempfile.TemporaryDirectory() as temp:
            baseline, amended = Path(temp) / "baseline", Path(temp) / "amended"
            self.builder.render_figures(runs, costs, baseline)
            self.builder.render_figures(
                [replace(r, source="another-export.json") if r.cohort == "N" else r for r in runs],
                costs, amended)
            self.assertEqual(
                (baseline / "results-scenario-matrix.svg").read_text(),
                (amended / "results-scenario-matrix.svg").read_text(),
                "N must have the same visual encoding regardless of export name")


if __name__ == "__main__":
    unittest.main()
