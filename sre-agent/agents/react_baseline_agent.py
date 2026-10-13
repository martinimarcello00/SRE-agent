"""Single-agent ReAct baseline: one agent with every MCP tool (datagraph included), no triage/planner/supervisor."""
from typing import Annotated, List, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages, AnyMessage
from langgraph.prebuilt import tools_condition, ToolNode
from langgraph.types import Command

from models import FinalReport
from prompts import REACT_BASELINE_SYSTEM_PROMPT, REACT_BASELINE_HUMAN_PROMPT
from tools import TOOLS
from utils import get_system_prompt
from config import GPT5_MINI, settings as config_settings

SUBMIT_TOOL = "submit_final_report"


class ReactBaselineState(TypedDict):
    """Same input/output keys as SreParentState (so export and evaluation are unchanged) plus the message history."""
    messages: Annotated[list[AnyMessage], add_messages]
    app_name: str
    app_summary: str
    target_namespace: str
    trace_service_starting_point: str
    symptoms: list
    rca_tasks: list
    rca_analyses_list: list[dict]
    final_report: dict
    prompts_config: dict[str, str]


@tool
def submit_final_report(
    root_cause: str,
    affected_resources: List[str],
    evidence_summary: str,
    investigation_summary: str,
    detection: bool,
    tool_call_id: Annotated[str, InjectedToolCallId],
    localization: Optional[List[str]] = None,
) -> Command:
    """Submit the final report when the investigation is complete (without fixing anything).

    Args:
        root_cause: The identified root cause of the incident
        affected_resources: All resources affected by the incident
        evidence_summary: Summary of the evidence gathered with the tools
        investigation_summary: Overview of the investigation process and findings
        detection: Whether a problem was detected in the cluster
        localization: ONLY the faulty components (service or pod names) that caused the incident
    """
    report = FinalReport(
        root_cause=root_cause,
        affected_resources=affected_resources,
        evidence_summary=evidence_summary,
        investigation_summary=investigation_summary,
        detection=detection,
        localization=localization,
    )
    return Command(update={
        "final_report": report.model_dump(),
        "messages": [ToolMessage(content="Final report submitted successfully.", tool_call_id=tool_call_id)],
    })


tools_with_completion = TOOLS + [submit_final_report]


def _tool_names(messages) -> list[str]:
    return [c["name"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]


def _investigation_calls(messages) -> int:
    return sum(n != SUBMIT_TOOL for n in _tool_names(messages))


async def react_agent(state: ReactBaselineState) -> dict:
    """One ReAct step; may produce tool calls or the final report submission."""
    count = _investigation_calls(state["messages"])
    max_tool_calls = config_settings.MAX_TOOL_CALLS

    budget_status = ""
    if count >= max_tool_calls:
        budget_status = f"""
⚠️ **BUDGET EXCEEDED**: You have made {count}/{max_tool_calls} tool calls.

You MUST now call {SUBMIT_TOOL} with your best conclusion based on the evidence gathered so far.
Do NOT make any more tool calls. Submit your report immediately.
"""
    elif count >= max_tool_calls - 2:
        budget_status = f"""
⚠️ **BUDGET WARNING**: You have made {count}/{max_tool_calls} tool calls. You should prepare to submit your report soon.
"""

    system_message = SystemMessage(content=get_system_prompt(
        state, "react_baseline", REACT_BASELINE_SYSTEM_PROMPT))  # type: ignore
    human_message = HumanMessage(content=REACT_BASELINE_HUMAN_PROMPT.format(
        app_summary=state["app_summary"],
        target_namespace=state["target_namespace"],
        trace_service_starting_point=state["trace_service_starting_point"],
        investigation_budget=max_tool_calls,
        tool_calls_count=count,
        budget_status=budget_status,
    ))

    llm = GPT5_MINI.bind_tools(tools_with_completion, parallel_tool_calls=True)
    return {"messages": [llm.invoke([system_message, human_message] + state["messages"])]}


async def format_response(state: ReactBaselineState) -> dict:
    """Package the output like the multi-agent workflow; fall back to an inconclusive report if none was submitted."""
    final_report = state.get("final_report") or FinalReport(
        root_cause="Investigation Inconclusive",
        affected_resources=[],
        evidence_summary="The agent stopped without submitting a final report.",
        investigation_summary="Incomplete",
        detection=False,
        localization=None,
    ).model_dump()

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
        "final_report": final_report,
        "symptoms": [],
        "rca_tasks": [],
        "rca_analyses_list": [{
            "diagnosis": final_report["root_cause"],
            "tools_stats": tools_stats,
            "message_history": history,
        }],
    }


def after_tools_condition(state: ReactBaselineState) -> str:
    """Finish once the report is submitted, otherwise keep reasoning."""
    return "format-output" if state.get("final_report") else "react-agent"


def build_react_baseline_graph():
    """Construct and compile the single-agent ReAct graph."""
    builder = StateGraph(ReactBaselineState)

    builder.add_node("react-agent", react_agent)
    builder.add_node("tools", ToolNode(tools_with_completion))
    builder.add_node("format-output", format_response)

    builder.add_edge(START, "react-agent")
    # no tool call at all -> END would skip the formatting, so route it there explicitly
    builder.add_conditional_edges("react-agent", tools_condition, {"tools": "tools", END: "format-output"})
    builder.add_conditional_edges("tools", after_tools_condition,
                                  {"react-agent": "react-agent", "format-output": "format-output"})
    builder.add_edge("format-output", END)

    return builder.compile().with_config(run_name="ReAct Baseline Agent")


react_baseline_graph = build_react_baseline_graph()
