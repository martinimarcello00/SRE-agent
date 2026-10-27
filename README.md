# Architectural Trade-offs in LLM-Based Root Cause Analysis

Replication package for the CAIN 2027 paper
"Architectural Trade-offs in LLM-Based Root Cause Analysis: An Empirical Study".

This artifact supports the empirical study of architectural trade-offs in
LLM-based root cause analysis for Kubernetes. It includes the complete raw
executions used in the analyses, offline analysis scripts, and the system
implementation, prompts, fault scenarios, and evaluator.

Start with the **offline analysis** below. Reproducing tables and figures does
not require Kubernetes, API keys, an LLM call, or the MCP server.

## Contents

| Path | Purpose |
|---|---|
| [`Results/`](Results/) | 160 complete raw exports for the main F/N/O/P/M comparison. |
| [`analysis/`](analysis/) | Main analysis, paper tables and figures, and tests. |
| [`exploratory-analysis/`](exploratory-analysis/) | Separate A--L exploration: 353 complete raw exports, analysis, figures, and tests. |
| [`sre-agent/`](sre-agent/) | Agent workflows, prompts, evaluator, experiment runner, configurations, and tests. |
| [`scripts/`](scripts/) and [`registry/`](registry/) | Environment helpers needed for new experimental executions, not for offline analysis. |
| [`assets/`](assets/) | Supporting system diagram. |

The raw files retain their original names and byte-for-byte unchanged contents.
The 32 F exports shared by the two datasets are the same executions, not extra
repetitions. No provenance manifest is required or generated. Historical
archives, notebooks, unused campaigns, and editor-specific files are omitted.

## Reproduce the analyses offline

Use Python 3.13 or later. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r analysis/requirements.txt

.venv/bin/python analysis/build_paper_results.py
.venv/bin/python exploratory-analysis/analyze.py

.venv/bin/python -m unittest discover -s analysis -p 'test_*.py'
.venv/bin/python -m unittest discover -s exploratory-analysis/tests -p 'test_*.py'
```

Generated files go to `analysis/output/` and `exploratory-analysis/output/`,
which are ignored by Git. Both commands also accept `--output PATH`.
They read the bundled raw exports without rewriting them.

- [Main analysis instructions](analysis/README.md): 29 fault runs and three
  controls per configuration; 28 component-localization cases; cost, stage,
  evidence-path, and sensitivity analyses.
- [Exploratory analysis instructions](exploratory-analysis/README.md):
  unequal A--L coverage, explicit missing telemetry, and the descriptive
  comparison on 19 common component scenarios.

## System implementation and new experiments

The system code is retained to make workflow choices, prompts, ground truth,
and scoring inspectable. See the [implementation guide](sre-agent/README.md)
and [workflow diagram](assets/sre_agent_architecture.png).

### External MCP server

The Kubernetes observability MCP server is provided separately at:

**<https://anonymous.4open.science/r/mcp-k8s-obs-cain27>**

Use this anonymous repository for the server source and its setup instructions.
The old identifying GitHub submodule is not included in this artifact.
Download the source through the anonymous repository and place the repository
contents directly in a directory named `MCP-server` at this checkout's root:

```text
MCP-server/
├── mcp_server.py
├── api/
└── service-graph/
```

The runtime imports the server's `api` modules and loads its service graphs;
it therefore needs this source directory even when connecting to an MCP server
over HTTP. A server URL alone is not a replacement for the source directory.

**Do not install or start the MCP server for offline analysis.**

### Runtime prerequisites

New experimental executions additionally require:

- The MCP source and its dependencies, configured using the anonymous repository.
- Poetry and the runtime dependencies in [`pyproject.toml`](pyproject.toml).
- A separately configured AIOpsLab installation, Docker, KinD, Kubernetes
  tooling, and the application's observability services.
- Neo4j, the local registry mirrors, and the application-specific topology.
- OpenAI credentials for the diagnostic model and usage tracking; credentials
  and tracing configuration are not included.

Prepare [`.env.example`](.env.example) as `.env`, review the
[agent configurations](sre-agent/experiments_runner/agent-configurations/) and
[fault scenarios](sre-agent/experiments_runner/fault-scenarios/), and choose
which entries have `execute: true`. Set `RESULTS_PATH` to a new destination
outside both bundled raw-data directories.

Install the runtime dependencies from the repository root with `poetry install`.
The [preflight helper](scripts/preflight.sh) checks the experimental environment;
the [registry instructions](registry/REGISTRY_SETUP.md) describe the mirrors.
The experiment entry point is
[`sre-agent/automated_experiment.py`](sre-agent/automated_experiment.py).
These helpers can start containers, provision or delete a cluster, and launch
paid API calls: inspect their configuration before running them. They are not
part of the offline reproduction commands.