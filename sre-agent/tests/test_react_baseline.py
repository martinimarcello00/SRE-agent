# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_react_baseline.py
# No cluster / API key needed: `tools` and `config` are stubbed (importing them would connect to the MCP servers).
import asyncio
import importlib.util
import sys
from types import ModuleType, SimpleNamespace

from langchain_core.messages import AIMessage
from langchain_core.tools import tool


@tool
def kubectl_get(resource: str) -> str:
    """Fake tool."""
    return f"{resource}: ok"


class ScriptedLLM:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def bind_tools(self, *_, **__):
        return self

    def invoke(self, _messages):
        self.calls += 1
        return self.script.pop(0)


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


REPORT = dict(root_cause="geo misconfigured", affected_resources=["geo"], evidence_summary="e",
              investigation_summary="s", detection=True, localization=["geo"])
llm = ScriptedLLM([])
fake_config = ModuleType("config")
fake_config.GPT5_MINI = llm
fake_config.settings = SimpleNamespace(MAX_TOOL_CALLS=2)
fake_tools = ModuleType("tools")
fake_tools.TOOLS = [kubectl_get]
sys.modules.update(config=fake_config, tools=fake_tools)

# load the file directly: importing the `agents` package would pull in every other agent
spec = importlib.util.spec_from_file_location("react_baseline_agent", "agents/react_baseline_agent.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
react_baseline_graph = module.react_baseline_graph

state = dict(messages=[], app_name="hotel", app_summary="hotel app", target_namespace="ns",
             trace_service_starting_point="frontend", symptoms=[], rca_tasks=[],
             rca_analyses_list=[], final_report={}, prompts_config={})
run = lambda: asyncio.run(react_baseline_graph.ainvoke(dict(state)))  # noqa: E731

# happy path: 2 investigation calls, then the structured report; output has the multi-agent shape
llm.script = [call("kubectl_get", {"resource": "pods"}, 1), call("kubectl_get", {"resource": "svc"}, 2),
              call("submit_final_report", REPORT, 3)]
out = run()
assert out["final_report"]["localization"] == ["geo"] and out["final_report"]["detection"] is True
assert out["symptoms"] == [] and out["rca_tasks"] == []
assert out["rca_analyses_list"][0]["tools_stats"] == {"kubectl_get": 2, "submit_final_report": 1}  # submit not counted in budget

# agent stops without calling any tool -> inconclusive fallback, not a crash
llm.script = [AIMessage(content="no tools, no report")]
out = run()
assert out["final_report"]["root_cause"] == "Investigation Inconclusive" and out["final_report"]["detection"] is False

# budget messages reach the model: nothing at 0/4, warning at 2/4, forced submission at 4/4
fake_config.settings.MAX_TOOL_CALLS = 4
prompts, script = [], [call("kubectl_get", {"resource": str(i)}, i) for i in range(4)] + [call("submit_final_report", REPORT, 9)]
llm.invoke = lambda msgs: (prompts.append(msgs[1].content), script.pop(0))[1]
run()
assert "⚠️" not in prompts[0] and "⚠️" not in prompts[1]
assert "BUDGET WARNING" in prompts[2] and "BUDGET EXCEEDED" in prompts[4]
print("ok")
