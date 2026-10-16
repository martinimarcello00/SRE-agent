# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_planner_no_grounding.py
# No cluster / API key needed: `config` and the MCP-server `api` modules are stubbed, the LLM records its prompt.
import importlib.util
import sys
from types import ModuleType, SimpleNamespace

from models import RCATaskList, Symptom

prompts = []


def fake_llm(prompt_value):
    prompts.append(prompt_value.to_string())
    return RCATaskList()


fake_config = ModuleType("config")
fake_config.GPT5_MINI = SimpleNamespace(with_structured_output=lambda _schema: fake_llm)
fake_k8s, fake_datagraph = ModuleType("api.k8s_api"), ModuleType("api.datagraph")
fake_k8s.K8sAPI, fake_datagraph.DataGraph = None, None
sys.modules.update({"config": fake_config, "api": ModuleType("api"), "api.k8s_api": fake_k8s,
                    "api.datagraph": fake_datagraph})

# load the file directly: importing the `agents` package would pull in every other agent
spec = importlib.util.spec_from_file_location("planner_agent", "agents/planner_agent.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)
planner.get_resource_dependencies = lambda _symptom: {"data_dependencies": ["mongodb-geo-MARKER"]}

symptom = Symptom(potential_symptom="CrashLoopBackOff", resource_type="pod", affected_resource="geo-abc",
                  evidence="no reachable servers")
state = dict(app_name="hotel", app_summary="hotel app", target_namespace="ns", symptoms=[symptom],
             rca_tasks=[], prompts_config={})


def plan(**extra):
    prompts.clear()
    planner.planner_agent_graph.invoke({**state, **extra})  # the graph: `architecture` must survive its state schema
    return prompts[0]


# grounded (default): the DataGraph dependencies reach the prompt, with the grounded system prompt
grounded = plan()
assert "mongodb-geo-MARKER" in grounded and "Connections (non-negotiable)" in grounded

# no grounding: no dependencies and no dependency headers, with the no-grounding system prompt
ungrounded = plan(architecture="planner_no_grounding")
assert "mongodb-geo-MARKER" not in ungrounded and "Dependencies**" not in ungrounded
assert "No DataGraph topology is supplied" in ungrounded and "geo-abc" in ungrounded
print("ok")
