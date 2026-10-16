# Pending re-evaluation of the merged A–L dataset

The JSON results in this folder keep the evaluation recorded when each experiment ran. Since then, the evaluator, the judge rubric and the fault ground truth have changed, so **248 of the 353 records hold stale scores**. This is a re-scoring of the saved reports with the current evaluator; the experiments do not need to be run again.

The per-record list is in [reevaluation_plan.csv](./reevaluation_plan.csv). Use `reevaluate=True` to select records and `needs_llm_judge=True` for the ones that need a gpt-5.1 call. The `reasons` column says why each record is stale.

## What changed after these runs were scored

| Date | Change | Commit |
|---|---|---|
| 2026-09-28 | Hotel/Social fault scenarios rewritten (the commit message mentions only AstronomyShop): new `RCA_gt` for every Hotel and Social fault; two faults renamed; targets corrected for Container Kill (frontend → geo), Pod kill (recommendation → user), Port misconfig post-storage (user → post-storage) and Redeploy Without PV | `c817583` |
| 2026-10-02 | Judge rubric: blaming the injection mechanism (flagd, Chaos Mesh) scores 1–2 | `87ca971` |
| 2026-10-02 | Localization: canonical service names, exact match against `accepted_targets` (adds MongoDB targets for the Mongo faults); new `localization_any`, `localization_legacy` and `blamed_harness` fields | `e8d22d3`, `77da070`, `c90be20` |
| 2026-10-06 | New ground truth for Astronomy *Image slow load* and *Ad service high CPU* | `6988c9f` |
| 2026-10-06 | Empty analysis on a fault scores 1 without calling the judge | `0f1b8e6` |
| 2026-10-14 | *No fault* runs go to the judge even when the analysis is empty | `4ef081d` |

## Records to re-evaluate

| Application | Records | gpt-5.1 judge calls | Why |
|---|---:|---:|---|
| Hotel Reservation | 138 (all) | 122 | Scored before every change listed above; no new evaluation fields |
| Social Network | 84 (all) | 76 | Same as Hotel Reservation |
| Astronomy Shop: Image slow load | 6 (A, B, C, E, F, G; run 2026-10-06 before the ground-truth commit) | 6 | Scored against the previous ground truth |
| Astronomy Shop: No fault | 11 | 11 | Skipped by the judge before the 2026-10-14 fix |
| Astronomy Shop: Ad service high CPU | 9 (D–L, 2026-10-05) | 0 | Scored against the previous ground truth, but every report is empty, so the score stays 1 |
| **Total** | **248** | **215** | |

- The 105 other Astronomy Shop records are already scored with the current evaluator and do not need re-evaluation.
- 33 stale records have an empty report on a real fault. The current evaluator gives them 1 without calling the judge, so only `localization` and the new fields change.
- Of the 215 judge calls, 5 are Hotel runs (Container Kill, Pod kill, Redeploy Without PV) that the analysis notebook excludes. Skipping them leaves 210 calls.

## Fault names updated in this folder

Two Hotel/Social faults were renamed in the scenario configurations. The 24 affected copies in this folder now use the current names in the filename, `testbed.fault_name`, `experiment_name` and `stats.experiment_name`. The old name is kept in `testbed.original_fault_name`. The original files in the source batches are unchanged. The full old-to-new file mapping is in [fault_renames.csv](./fault_renames.csv).

| Old name | Current name | Files |
|---|---|---:|
| Port missmatch geo | Geo application misconfiguration | 12 (Hotel Reservation) |
| MongoDB url-shorten authentication missing | MongoDB url-shorten TLS requirement | 12 (Social Network) |

Both renames also come with a new ground truth: the injected fault is a buggy geo image and an enforced MongoDB TLS requirement, not a port mismatch and missing authentication. Their scores are part of the re-evaluation above.

## How to run it later

1. Put `OPENAI_API_KEY` and `OPENAI_ADMIN_API_KEY` in the repository `.env`. The judge refuses to run when daily gpt-5.1 usage is above 240k tokens, so 215 calls may need more than one day.
2. For every record with `reevaluate=True`, call `evaluation.evaluation.evaluate_experiment(fault_scenario, report)`, where `fault_scenario` is the current scenario JSON in `sre-agent/experiments_runner/fault-scenarios/` matched on app name and `testbed.fault_name`, and `report` is the result JSON itself. Write the result back to the `evaluation` key of the file in this folder.
3. Before overwriting, keep the previous result under a separate key such as `evaluation_v1` so the old and new scores can be compared.
4. Re-run `notebooks/analysis/result-analysis.ipynb` on this folder to regenerate the summaries and plots.

## Status: completed on 2026-10-16

- All 248 records were re-scored with the evaluator at `4ef081d`, using the current fault scenarios (`accepted_targets` included). 215 gpt-5.1 judge calls, none failed. The previous evaluation is in `evaluation_v1`; `evaluation_meta` records the date, evaluator version and scenario file.
- **No-fault fix.** At `4ef081d` the judge gave RCA 1 to No-fault runs where the agent correctly reported nothing, and the old scores ranged from 1 to 5 for the same answer. The evaluator now scores No fault without the judge: 5 when the agent reports nothing, 1 when it reports a fault that does not exist. All 36 No-fault records were re-scored this way (`evaluator_commit: 4ef081d+no-fault-deterministic-rca`). 35 got 5; Astronomy L got 1 because it blamed `shipping`.
- The analysis notebook was re-run on the new scores. The summaries and plots in this folder are up to date.

### Corrections after reviewing the changes (2026-10-16)

- **Geo application misconfiguration ground truth.** The `c817583` text said AIOpsLab "does not establish a specific MongoDB port mismatch". The judge read this as "there is no port mismatch" and scored 2 the agents that found one. The container logs show it does exist: `Read database URL: mongodb-geo:27777`, while MongoDB listens on 27017. The `RCA_gt` now states this, and the 12 records were judged again: 9 get 4–5, while H, I and L get 2 because they blame missing configuration instead.
- **Localization normalizer.** `image yinfangchen/geo:app3` used to be read as "image"; it is now read as `geo`. Only Geo D changes, from false to true.
- **`secret/mongodb-tls` is not accepted** for the TLS fault: it is a Kubernetes Secret, not a service. Agents that list it next to `url-shorten-mongodb` stay false.
- Records touched by these corrections have `evaluation_meta.evaluator_commit = 4ef081d+no-fault-deterministic-rca+image-normalizer+geo-gt`.
