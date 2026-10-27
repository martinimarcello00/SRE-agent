# SRE-agent implementation

This directory contains the diagnostic system and experimental runner. It is
retained for inspection and new executions; the paper's offline reproduction
commands are in the [artifact README](../README.md).

## Modules

| Path | Purpose |
|---|---|
| [`graph.py`](graph.py) | Multi-agent workflow assembly. |
| [`agents/`](agents/) | Triage, planning, Workers, Supervisor, and workflow alternatives. |
| [`prompts/`](prompts/) | Diagnostic and evaluator prompts. |
| [`models/`](models/) | Shared state and structured output schemas. |
| [`config/`](config/) | Environment, model, budgets, and MCP configuration. |
| [`tools/`](tools/) | MCP client, tool filtering, and submission tools. |
| [`evaluation/`](evaluation/) | Detection, localization, and secondary judge evaluation. |
| [`experiments_runner/`](experiments_runner/) | Agent/scenario configuration, testbed lifecycle, topology, and readiness. |
| [`automated_experiment.py`](automated_experiment.py) | Batch experiment entry point. |
| [`launch_experiment.py`](launch_experiment.py) | Diagnostic invocation and export. |
| [`tests/`](tests/) | Workflow and infrastructure regression tests. |

## MCP dependency

The MCP source is available at
**<https://anonymous.4open.science/r/mcp-k8s-obs-cain27>**.

Follow the [external MCP setup](../README.md#external-mcp-server) before importing
or running the system. The source must be placed at `../MCP-server/`, including
`mcp_server.py`, `api/`, and `service-graph/`. The agent imports its API modules
and uses its topology files. The anonymous source link is not an MCP transport
endpoint, and the artifact no longer includes a GitHub submodule.

Some runtime imports initialize models, graph clients, or MCP tools. Do not
import the system just to reproduce saved analyses: use the independent
[main](../analysis/README.md) and
[exploratory](../exploratory-analysis/README.md) analysis scripts instead.

## Architecture selection

The experiment runner reads `architecture` from each
[agent configuration](experiments_runner/agent-configurations/):

| Value | Workflow |
|---|---|
| Omitted or `"multi_agent"` | Multi-agent workflow with dependency-enriched planning. |
| `"react_baseline"` | One ReAct investigator, nominal budget 14 in configuration M. |
| `"planner_no_grounding"` | Planner without injected dependency context; Worker graph tools remain available. |
| `"agentic_triage"` | LLM-directed initial evidence collection, configuration O. |
| `"no_planner"` | One task per symptom, with all generated tasks dispatched initially, configuration P. |

Configuration F uses initial fan-out two, Worker budget guidance seven, and
non-binding tool suggestions. Prompt guidance is not an enforced equality of
total work or cost. A--L definitions are retained for the historical exploration.

In the topology alternative, the Planner skips service/data and infrastructure
dependency lookups. Omitted topology is not represented as "no dependencies
found". Symptoms, application context, model knowledge, and Worker graph queries
may still reveal topology. Custom overrides use
`prompts_config["planner_no_grounding"]` rather than the normal Planner prompt.

O replaces the programmed telemetry checks with a ReAct collector that submits
the same symptom schema. Its `triage_analysis` records tool calls and messages;
the programmed snapshot fields remain empty. If it never submits symptoms,
the symptom list is empty. P generates one task per symptom without a Planner
LLM call or dependency lookup; its first batch is not limited by
`RCA_TASKS_PER_ITERATION`.

The saved exports, rather than today's configuration defaults, define the
observations analyzed in the artifact. The author confirms that N used the
same experimental environment, tools, and execution protocol as the other
configurations. Its evaluated alternative is the absence of dependency context
in the Planner, with Worker graph-query tools retained. The analysis treats
all 29 N fault runs and three controls as one configuration.

## New executions

Install runtime dependencies with `poetry install` from the repository root.
Configure `.env`, the external MCP source, AIOpsLab, Neo4j, registry mirrors,
and credentials as described in the [artifact README](../README.md).

Before launching a batch, review all `execute` flags and the output directory.
Then run from this directory:

```bash
poetry run python automated_experiment.py
```

This is a new, infrastructure-changing experiment with potential API charges,
not the offline analysis. LangGraph's development server is also available
through [`langgraph.json`](langgraph.json), after runtime configuration:

```bash
poetry run langgraph dev
```

For programmatic invocation, use
[`run_sre_agent`](launch_experiment.py), which validates workflow selection.
Direct `parent_graph.ainvoke(initial_state)` calls bypass launcher validation.

## Runtime regression tests

These selected tests stub model/MCP access and do not require a live cluster
or API calls. They still require the installed runtime Python dependencies.
Run each separately from this directory:

```bash
PYTHONPATH=. poetry run python tests/test_planner_no_grounding.py
PYTHONPATH=. poetry run python tests/test_react_baseline.py
PYTHONPATH=. poetry run python tests/test_agentic_triage.py
PYTHONPATH=. poetry run python tests/test_no_planner.py
```

The offline analysis tests require only the smaller analysis environment;
do not use generic discovery of runtime modules to reproduce paper outputs.
