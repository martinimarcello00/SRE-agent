# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_agentic_triage.py
# No cluster / API key needed: `tools`, `config` and the MCP-server `api` modules are stubbed (importing them would connect).
import asyncio
import importlib.util
import sys
from types import ModuleType, SimpleNamespace

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from models import Symptom


@tool
def kubectl_get(resource: str) -> str:
    """Fake tool."""
    return f"{resource}: ok"


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, *_, **__):
        return self

    def invoke(self, _messages):
        return self.script.pop(0)


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


class FakeK8sAPI:
    def get_services_from_pod(self, pod_name):
        return {"pod_name": pod_name, "services": [], "error": f"The pod {pod_name} does not exist."}


llm = ScriptedLLM([])
fake_config = ModuleType("config")
fake_config.GPT5_MINI = llm
fake_config.settings = SimpleNamespace(TRIAGE_MAX_TOOL_CALLS=2)
fake_tools = ModuleType("tools")
fake_tools.TOOLS = [kubectl_get]
fake_k8s, fake_datagraph = ModuleType("api.k8s_api"), ModuleType("api.datagraph")
fake_k8s.K8sAPI, fake_datagraph.DataGraph = FakeK8sAPI, None
sys.modules.update({"config": fake_config, "tools": fake_tools, "api": ModuleType("api"),
                    "api.k8s_api": fake_k8s, "api.datagraph": fake_datagraph})


def load(name):
    # load the file directly: importing the `agents` package would pull in every other agent
    spec = importlib.util.spec_from_file_location(name, f"agents/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agentic_triage_graph = load("agentic_triage_agent").agentic_triage_graph
state = dict(messages=[], app_name="hotel", app_summary="hotel app", target_namespace="ns",
             trace_service_starting_point="frontend", symptoms=[], prompts_config={})
run = lambda: asyncio.run(agentic_triage_graph.ainvoke(dict(state)))  # noqa: E731

SYMPTOM = dict(potential_symptom="CrashLoopBackOff", resource_type="pod", affected_resource="geo-abc",
               evidence="restarted 5 times")

# happy path: 2 investigation calls, then the symptoms as Symptom objects (export and planner call model_dump)
llm.script = [call("kubectl_get", {"resource": "pods"}, 1), call("kubectl_get", {"resource": "svc"}, 2),
              call("submit_symptoms", {"symptoms": [SYMPTOM]}, 3)]
out = run()
assert out["symptoms"] == [Symptom(**SYMPTOM)] and isinstance(out["symptoms"][0], Symptom)
assert out["triage_analysis"]["submitted"] is True
assert out["triage_analysis"]["tools_stats"] == {"kubectl_get": 2, "submit_symptoms": 1}  # submit not counted in budget

# healthy cluster: an empty submission ends the triage with no symptoms
llm.script = [call("kubectl_get", {"resource": "pods"}, 1), call("submit_symptoms", {"symptoms": []}, 2)]
out = run()
assert out["symptoms"] == [] and out["triage_analysis"]["submitted"] is True

# agent stops without submitting -> no symptoms, flagged as not submitted, not a crash
llm.script = [AIMessage(content="no tools, no symptoms")]
out = run()
assert out["symptoms"] == [] and out["triage_analysis"]["submitted"] is False

# budget messages reach the model: nothing at 0/4, warning at 2/4, forced submission at 4/4
fake_config.settings.TRIAGE_MAX_TOOL_CALLS = 4
prompts, script = [], [call("kubectl_get", {"resource": str(i)}, i) for i in range(4)] + [call("submit_symptoms", {"symptoms": []}, 9)]
llm.invoke = lambda msgs: (prompts.append(msgs[1].content), script.pop(0))[1]
run()
assert "⚠️" not in prompts[0] and "⚠️" not in prompts[1]
assert "BUDGET WARNING" in prompts[2] and "BUDGET EXCEEDED" in prompts[4]

# planner: a symptom on a pod that does not exist gets no dependencies instead of an IndexError
planner = load("planner_agent")
deps = planner.get_resource_dependencies(Symptom(**{**SYMPTOM, "affected_resource": "geo"}))
assert deps == {"resource_name": "geo", "resource_type": "pod"}
print("ok")
