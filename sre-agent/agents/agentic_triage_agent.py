"""Agentic triage ablation: a ReAct agent gathers the symptoms with the MCP tools instead of the heuristic checks."""
from typing import Annotated, List, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages, AnyMessage
from langgraph.prebuilt import tools_condition, ToolNode
from langgraph.types import Command

from models import Symptom
from prompts import AGENTIC_TRIAGE_SYSTEM_PROMPT, AGENTIC_TRIAGE_HUMAN_PROMPT
from tools import TOOLS
from utils import get_system_prompt
from config import GPT5_MINI, settings as config_settings

SUBMIT_TOOL = "submit_symptoms"


class AgenticTriageState(TypedDict):
    """Triage input/output keys plus the message history and the submission flag."""
    messages: Annotated[list[AnyMessage], add_messages]
    app_name: str
    app_summary: str
    target_namespace: str
    trace_service_starting_point: str
    symptoms: List[Symptom]
    submitted: bool
    triage_analysis: dict
    prompts_config: dict[str, str]


@tool
def submit_symptoms(
    symptoms: List[Symptom],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Submit the symptoms found in the cluster when the triage is complete.

    Args:
        symptoms: The symptoms observed, at most one per pod or service
    """
    return Command(update={
        "symptoms": symptoms,
        "submitted": True,
        "messages": [ToolMessage(content="Symptoms submitted successfully.", tool_call_id=tool_call_id)],
    })


tools_with_completion = TOOLS + [submit_symptoms]


def _tool_names(messages) -> list[str]:
    return [c["name"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]


async def triage_agent(state: AgenticTriageState) -> dict:
    """One ReAct step; may produce tool calls or the symptoms submission."""
    count = sum(n != SUBMIT_TOOL for n in _tool_names(state["messages"]))
    max_tool_calls = config_settings.TRIAGE_MAX_TOOL_CALLS

    budget_status = ""
    if count >= max_tool_calls:
        budget_status = f"""
⚠️ **BUDGET EXCEEDED**: You have made {count}/{max_tool_calls} tool calls.

You MUST now call {SUBMIT_TOOL} with the symptoms found so far.
Do NOT make any more tool calls. Submit the symptoms immediately.
"""
    elif count >= max_tool_calls - 2:
        budget_status = f"""
⚠️ **BUDGET WARNING**: You have made {count}/{max_tool_calls} tool calls. You should prepare to submit the symptoms soon.
"""

    system_message = SystemMessage(content=get_system_prompt(
        state, "agentic_triage", AGENTIC_TRIAGE_SYSTEM_PROMPT))  # type: ignore
    human_message = HumanMessage(content=AGENTIC_TRIAGE_HUMAN_PROMPT.format(
        app_name=state["app_name"],
        app_summary=state["app_summary"],
        target_namespace=state["target_namespace"],
        trace_service_starting_point=state["trace_service_starting_point"],
        investigation_budget=max_tool_calls,
        tool_calls_count=count,
        budget_status=budget_status,
    ))

    llm = GPT5_MINI.bind_tools(tools_with_completion, parallel_tool_calls=True)
    return {"messages": [llm.invoke([system_message, human_message] + state["messages"])]}


async def format_response(state: AgenticTriageState) -> dict:
    """Return the symptoms (none if never submitted) and the triage history for the audit."""
    history = []
    for msg in state["messages"]:
        entry = {"type": msg.__class__.__name__, "content": msg.content}
        if isinstance(msg, AIMessage) and msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        history.append(entry)

    tools_stats: dict[str, int] = {}
    for name in _tool_names(state["messages"]):
        tools_stats[name] = tools_stats.get(name, 0) + 1

    return {
        "symptoms": state.get("symptoms") or [],
        "triage_analysis": {
            "submitted": state.get("submitted", False),
            "tools_stats": tools_stats,
            "message_history": history,
        },
    }


def after_tools_condition(state: AgenticTriageState) -> str:
    """Finish once the symptoms are submitted, otherwise keep investigating."""
    return "format-output" if state.get("submitted") else "triage-agent"


def build_agentic_triage_graph():
    """Construct and compile the agentic triage graph."""
    builder = StateGraph(AgenticTriageState)

    builder.add_node("triage-agent", triage_agent)
    builder.add_node("tools", ToolNode(tools_with_completion))
    builder.add_node("format-output", format_response)

    builder.add_edge(START, "triage-agent")
    # no tool call at all -> END would skip the formatting, so route it there explicitly
    builder.add_conditional_edges("triage-agent", tools_condition, {"tools": "tools", END: "format-output"})
    builder.add_conditional_edges("tools", after_tools_condition,
                                  {"triage-agent": "triage-agent", "format-output": "format-output"})
    builder.add_edge("format-output", END)

    return builder.compile().with_config(run_name="Agentic Triage Agent")


agentic_triage_graph = build_agentic_triage_graph()
