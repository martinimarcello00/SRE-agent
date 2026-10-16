# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_launch_experiment.py
# No cluster / API key needed: only `tools` is stubbed and the LLM is scripted. Catches NameErrors in run_sre_agent
# (e.g. a missing SreParentState import), which only blow up at call time, after ~12 min of cluster setup.
import asyncio
import os
import sys
from types import ModuleType

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

os.environ.update(OPENAI_API_KEY="dummy", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false", LANGSMITH_API_KEY="")


@tool
def kubectl_get(resource: str) -> str:
    """Fake tool."""
    return "ok"


@tool
def submit_final_diagnosis(diagnosis: str, reasoning: str) -> str:
    """Fake tool."""
    return "x"


fake_tools = ModuleType("tools")
fake_tools.TOOLS = [kubectl_get]
fake_tools.submit_final_diagnosis = submit_final_diagnosis
fake_tools.get_mcp_tools = None
sys.modules["tools"] = fake_tools

import agents.react_baseline_agent as react_baseline_agent  # noqa: E402
import launch_experiment  # noqa: E402


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, *_, **__):
        return self

    def invoke(self, _messages):
        return self.script.pop(0)


REPORT = dict(root_cause="rc", affected_resources=["a"], evidence_summary="e",
              investigation_summary="s", detection=True, localization=["a"])
react_baseline_agent.GPT5_MINI = ScriptedLLM([call("kubectl_get", {"resource": "pods"}, 1),
                                              call("submit_final_report", REPORT, 2)])

result, seconds = asyncio.run(launch_experiment.run_sre_agent(
    app_name="x", fault_name="f", app_summary="s", target_namespace="ns", trace_service_starting_point="frontend",
    trace_name="test", agent_configuration_name="M", agent_id="M", architecture="react_baseline"))
assert result["final_report"]["localization"] == ["a"]
assert "messages" not in result and result["architecture"] == "react_baseline"

# an unknown architecture fails before any graph runs, instead of silently running multi_agent
try:
    asyncio.run(launch_experiment.run_sre_agent(
        app_name="x", fault_name="f", app_summary="s", target_namespace="ns", trace_service_starting_point="frontend",
        trace_name="test", agent_configuration_name="N", agent_id="N", architecture="planner_no_grounding_typo"))
    raise AssertionError("unknown architecture accepted")
except ValueError:
    pass

launch_experiment.get_experiment_metrics = lambda *_, **__: {}  # needs LangSmith
exported = launch_experiment.export_json_results(
    result=result, experiment_name="test", exec_time=seconds, fault_name="f", application_name="x",
    target_namespace="ns", trace_service_starting_point="frontend", agent_configuration_name="M", agent_id="M")
assert exported
print("ok")
