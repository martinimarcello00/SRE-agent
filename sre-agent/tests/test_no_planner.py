# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_no_planner.py
# No cluster / API key needed: only `tools` is stubbed (importing it would connect to the MCP servers), and any LLM call
# or dependency lookup in the planner fails the test.
import importlib
import os
import sys
from types import ModuleType, SimpleNamespace

from langchain_core.tools import tool

os.environ.update(OPENAI_API_KEY="dummy", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false", LANGSMITH_API_KEY="")


@tool
def submit_final_diagnosis(diagnosis: str, reasoning: str) -> str:
    """Fake tool."""
    return "x"


fake_tools = ModuleType("tools")
fake_tools.TOOLS, fake_tools.submit_final_diagnosis, fake_tools.get_mcp_tools = [], submit_final_diagnosis, None
sys.modules["tools"] = fake_tools

planner = importlib.import_module("agents.planner_agent")  # not `import ... as`: the package re-exports the function
from config import settings  # noqa: E402
from graph import rca_router, update_rca_task_status  # noqa: E402
from models import Symptom  # noqa: E402


def fail(*_, **__):
    raise AssertionError("no_planner must not plan with the LLM or look up dependencies")


planner.GPT5_MINI = SimpleNamespace(with_structured_output=fail)
planner.get_resource_dependencies = fail

symptoms = [Symptom(potential_symptom=f"symptom {i}", resource_type=kind, affected_resource=f"res-{i}", evidence=f"evidence {i}")
            for i, kind in enumerate(["pod", "service", "pod"], 1)]
state = dict(architecture="no_planner", app_name="hotel", app_summary="hotel app", target_namespace="ns",
             symptoms=symptoms, rca_tasks=[], prompts_config={})

# one task per symptom, in triage order, built from the symptom alone (the graph: `architecture` must survive its state)
tasks = planner.planner_agent_graph.invoke(state)["rca_tasks"]
assert [(t.priority, t.target_resource, t.resource_type) for t in tasks] == [(1, "res-1", "pod"), (2, "res-2", "service"), (3, "res-3", "pod")]
assert all(t.suggested_tools == [] and t.status == "pending" for t in tasks)
assert "symptom 2" in tasks[1].investigation_goal and "evidence 2" in tasks[1].investigation_goal
assert planner.planner_agent_graph.invoke({**state, "symptoms": []})["rca_tasks"] == []

# every symptom starts in the first batch, whatever RCA_TASKS_PER_ITERATION says; the other modes keep the batch size
settings.RCA_TASKS_PER_ITERATION = 2
schedule = dict(rca_tasks=tasks, tasks_to_be_executed=[], rca_analyses_list=[], app_summary="hotel app", target_namespace="ns")
scheduled = update_rca_task_status({**schedule, "architecture": "no_planner"})["rca_tasks"]
assert [t.status for t in scheduled] == ["in_progress"] * 3
assert len(rca_router({**schedule, "rca_tasks": scheduled})) == 3
assert [t.status for t in update_rca_task_status(schedule)["rca_tasks"]] == ["in_progress", "in_progress", "pending"]
print("ok")
