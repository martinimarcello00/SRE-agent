# Reproducing the paper analysis

This branch contains the raw runs used by the current paper and a standalone,
offline analysis.

## Raw data

[`../Results/`](../Results/) contains 160 complete JSON exports, with their
original filenames and byte-for-byte unchanged contents. Each of F, N, O, P,
and M contributes 29 fault runs and three no-injection controls. The analysis
reads the configuration, application, scenario, saved verdicts, prompts, and
telemetry from each JSON rather than inferring them from its directory.

The raw files include message histories, tool responses, intermediate outputs,
final reports, and evaluation fields wherever these were recorded. Flattening
the dataset does not fill missing telemetry or change saved evaluations.

The historical A--L exploration is packaged separately in
[`../exploratory-analysis/`](../exploratory-analysis/), including its complete
raw exports and offline analysis. It does not enter this main cohort. The
32 F exports are shared executions, not additional repetitions.

## Run

From the repository root, using Python 3.13 or later:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r analysis/requirements.txt
.venv/bin/python analysis/build_paper_results.py
.venv/bin/python -m unittest discover -s analysis -p 'test_*.py'
```

The only additional dependency for offline analysis is the pinned Matplotlib
version in [`requirements.txt`](requirements.txt). Agent runtime dependencies
are not needed.

The script defaults to this checkout's `Results` directory, independently of
the working directory. To choose another output location:

```bash
.venv/bin/python analysis/build_paper_results.py --output /tmp/cain27-results
```

`--results /path/to/Results` selects another flat raw directory. Input directories
with campaign subdirectories, missing telemetry, duplicate run IDs, duplicate
configuration/scenario cells, or inconsistent token accounting fail explicitly.
The script never rewrites the raw files and rejects output inside `Results`.

## Outputs and interpretation

Generated files go to the ignored `analysis/output/` directory:

- The paper's overview and RCA-score tables in LaTeX.
- Overview, run costs, stage costs, scenario matrix, cost quantiles, and saved
  RCA-score distributions in CSV.
- Stage-token, scenario-matrix, and cost-distribution figures in PDF, SVG, and PNG.
- `results-summary.json`: recomputed diagnostic outcomes, descriptive costs,
  Kafka-exclusion sensitivity, localization disagreements,
  application breakdowns, and symptom-conditioned topology comparisons.
- `results-cases.json`: checks on the saved evidence for the paper's
  illustrative cases. These checks do not replace author scientific review.

Fault-present detection and costs use 29 runs per configuration. Restrictive
and any-target component localization use 28 cases, excluding namespace-wide
redeployment. Control alarms use three runs per configuration and do not enter
fault-present cost summaries. Stage-token means use all eligible fault runs,
not only runs with a recorded stage. Tool-call counts exclude submission calls.
Saved judge scores are secondary exploratory annotations, not independently
human-validated ground truth.

The symptom-conditioned topology comparison includes every matched F/N
component case with nonempty symptoms in both executions: 17 cases, with
11 restrictive matches for F and eight for N. This is post hoc conditioning
on intermediate outcomes, not a causal adjustment.

The cost scatter plot uses the saved run IDs to assign deterministic horizontal
jitter, rather than the earlier identifiers derived from campaign paths.
Horizontal point offsets can therefore differ from earlier paper exports;
token/time values, medians, and interquartile ranges are unchanged.
