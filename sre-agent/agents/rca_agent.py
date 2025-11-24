"""RCA Agent Worker - Performs focused root cause analysis investigations."""
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import tools_condition, ToolNode
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from models import RcaAgentState, RCAAgentExplaination
from prompts import RCA_SYSTEM_PROMPT, RCA_HUMAN_PROMPT, EXPLAIN_ANALYSIS_PROMPT
from tools import TOOLS, submit_final_diagnosis
from utils import count_tool_calls, count_non_submission_tool_calls
from config import GPT5_MINI, settings as config_settings


# Combine MCP tools with submission tool
tools_with_completion = TOOLS + [submit_final_diagnosis]

async def rcaAgent(state: RcaAgentState) -> dict:
    """Run one RCA reasoning step; may produce tool calls or final submission."""

    # Count tool calls (excluding submit_final_diagnosis)
    tool_call_count = count_non_submission_tool_calls(state["messages"])
    
    # Extract task details
    task = state["rca_task"]
    suggested_tools_str = ", ".join(task.suggested_tools) if task.suggested_tools else "Use your best judgment"

    # Build budget status message
    budget_status = ""
    max_tool_calls = config_settings.MAX_TOOL_CALLS

    if tool_call_count >= max_tool_calls:
        budget_status = f"""
⚠️ **BUDGET EXCEEDED**: You have made {tool_call_count}/{max_tool_calls} tool calls.

You MUST now call submit_final_diagnosis with your best conclusion based on the evidence gathered so far.
Do NOT make any more tool calls. Submit your diagnosis immediately.
"""
    elif tool_call_count >= max_tool_calls - 2:
        budget_status = f"""
⚠️ **BUDGET WARNING**: You have made {tool_call_count}/{max_tool_calls} tool calls. You should prepare to submit your diagnosis soon.
"""

    system_message = SystemMessage(content=RCA_SYSTEM_PROMPT)
    human_message = HumanMessage(content=RCA_HUMAN_PROMPT.format(
        app_summary=state["rca_app_summary"],
        target_namespace=state["rca_target_namespace"],
        investigation_goal=task.investigation_goal,
        resource_type=task.resource_type,
        target_resource=task.target_resource,
        suggested_tools=suggested_tools_str,
        investigation_budget=max_tool_calls,
        tool_calls_count=tool_call_count,
        budget_status=budget_status
    ))

    llm_with_completion_tools = GPT5_MINI.bind_tools(tools_with_completion, parallel_tool_calls=True)
    return {"messages": [llm_with_completion_tools.invoke([system_message, human_message] + state["messages"])]}


async def explain_analysis(state: RcaAgentState) -> dict:
    """Summarize investigation into ordered steps and consolidated insights."""
    # LLM with structured output for summarization
    llm_explain_steps = GPT5_MINI.with_structured_output(RCAAgentExplaination)

    prompt = SystemMessage(content=EXPLAIN_ANALYSIS_PROMPT)

    explaination = llm_explain_steps.invoke([prompt] + state["messages"])

    result = explaination.model_dump() #type: ignore

    return {
        "prev_steps": result["steps"],
        "insights": result["insights"]
    }

async def format_response(state: RcaAgentState) -> dict:
    """Package final RCA output with task data, insights, steps, stats, and history."""

    final_report = state["rca_output"]
    
    task = state["rca_task"]
    final_report["task"] = {
        "priority": task.priority,
        "status": "completed",
        "investigation_goal": task.investigation_goal,
        "target_resource": task.target_resource,
        "resource_type": task.resource_type,
        "suggested_tools": task.suggested_tools
    }
    
    final_report["insights"] = state["insights"]
    final_report["steps_performed"] = state["prev_steps"]
    final_report["tools_stats"] = count_tool_calls(state["messages"])
    
    # Export complete message history as JSON
    message_history = []
    for msg in state["messages"]:
        message_dict = {
            "type": msg.__class__.__name__,
            "content": msg.content if hasattr(msg, 'content') else str(msg),
        }
        if isinstance(msg, AIMessage) and msg.tool_calls:
            message_dict["tool_calls"] = msg.tool_calls
        message_history.append(message_dict)
    
    final_report["message_history"] = message_history

    return {"rca_analyses_list": [final_report]}


def after_tools_condition(state: RcaAgentState) -> str:
    """Decide next node: summarize if diagnosis present else continue loop."""

    if state.get("rca_output"):
        # Investigation complete, summarise
        return "explain-analysis"
    return "rca-agent"


def build_rca_graph():
    """Construct and compile the RCA agent state graph."""

    builder = StateGraph(RcaAgentState)

    # Add nodes
    builder.add_node("rca-agent", rcaAgent)
    builder.add_node("tools", ToolNode(tools_with_completion))
    builder.add_node("explain-analysis", explain_analysis)
    builder.add_node("format-output", format_response)

    # Add edges
    builder.add_edge(START, "rca-agent")

    # Conditional edge from rca-agent
    builder.add_conditional_edges(
        "rca-agent",
        tools_condition,
    )

    # After tools, decide whether to continue the ReAct loop or summarise the entire analysis
    builder.add_conditional_edges(
        "tools",
        after_tools_condition,
        {
            "rca-agent": "rca-agent",
            "explain-analysis": "explain-analysis"
        }
    )
    
    builder.add_edge("explain-analysis", "format-output")

    builder.add_edge("format-output", END)

    # Compile with output keys to only return the analysis
    return builder.compile().with_config(
        run_name="RCA Agent",
        output_keys=["rca_analyses_list"]
    )


# Export the compiled graph
rca_agent_graph = build_rca_graph()
