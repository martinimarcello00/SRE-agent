"""RCA Agent Worker - Performs focused root cause analysis investigations."""
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import tools_condition, ToolNode
from langchain_core.messages import HumanMessage, AIMessage

from models import RcaAgentState, UpdateAgentData
from prompts import RCA_AGENT_PROMPT, SUMMARISE_PROMPT
from tools import TOOLS, submit_final_diagnosis
from utils import get_insights_str, get_prev_steps_str, count_tool_calls, count_non_submission_tool_calls
from config import GPT5_MINI, MAX_TOOL_CALLS


# Combine MCP tools with submission tool
tools_with_completion = TOOLS + [submit_final_diagnosis]

# LLM with structured output for summarization
llm_with_struct_output = GPT5_MINI.with_structured_output(UpdateAgentData)


async def summarise(state: RcaAgentState) -> dict:
    """Summarize the latest tool call results into insights and steps.
    
    Args:
        state: Current RCA agent state with messages
        
    Returns:
        Dictionary with new insights and previous steps
    """
    messages = state["messages"]
    last_ai_idx = None
    
    # Find the last AI message (which contains tool calls)
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            last_ai_idx = i
            break
    
    # Collect messages from last AI message onwards (to capture all parallel responses)
    if last_ai_idx is not None:
        last_messages = messages[last_ai_idx:]
    else:
        last_messages = messages[-2:]  # Fallback to last 2 messages

    insights_str = get_insights_str(state)
    prev_step_str = get_prev_steps_str(state)

    prompt = HumanMessage(content=SUMMARISE_PROMPT.format(
        prev_steps=prev_step_str,
        insights=insights_str,
        last_messages=last_messages
    ))

    data = llm_with_struct_output.invoke([prompt])

    return {"insights": [data.insight], "prev_steps": [data.prev_step]}  # type: ignore


async def rcaAgent(state: RcaAgentState) -> dict:
    """Main RCA agent that investigates based on assigned task.
    
    Args:
        state: Current RCA agent state
        
    Returns:
        Dictionary with AI message containing next action
    """
    # Count tool calls (excluding submit_final_diagnosis)
    tool_call_count = count_non_submission_tool_calls(state["messages"])

    insights_str = get_insights_str(state)
    prev_step_str = get_prev_steps_str(state)
    
    # Extract task details
    task = state["rca_task"]
    suggested_tools_str = ", ".join(task.suggested_tools) if task.suggested_tools else "Use your best judgment"

    # Build budget status message
    budget_status = ""
    if tool_call_count >= MAX_TOOL_CALLS:
        budget_status = f"""
⚠️ **BUDGET EXCEEDED**: You have made {tool_call_count}/{MAX_TOOL_CALLS} tool calls.

You MUST now call submit_final_diagnosis with your best conclusion based on the evidence gathered so far.
Do NOT make any more tool calls. Submit your diagnosis immediately.
"""
    elif tool_call_count >= MAX_TOOL_CALLS - 2:
        budget_status = f"""
⚠️ **BUDGET WARNING**: You have made {tool_call_count}/{MAX_TOOL_CALLS} tool calls. You should prepare to submit your diagnosis soon.
"""

    prompt = HumanMessage(content=RCA_AGENT_PROMPT.format(
        prev_steps=prev_step_str,
        insights=insights_str,
        app_summary=state["rca_app_summary"],
        target_namespace=state["rca_target_namespace"],
        investigation_goal=task.investigation_goal,
        resource_type=task.resource_type,
        target_resource=task.target_resource,
        suggested_tools=suggested_tools_str,
        investigation_budget=MAX_TOOL_CALLS,
        tool_calls_count=tool_call_count,
        budget_status=budget_status
    ))

    llm_with_completion_tools = GPT5_MINI.bind_tools(tools_with_completion, parallel_tool_calls=False)
    return {"messages": [llm_with_completion_tools.invoke([prompt])]}


async def format_response(state: RcaAgentState) -> dict:
    """Format the final RCA analysis with metadata.
    
    Args:
        state: Current RCA agent state with completed analysis
        
    Returns:
        Dictionary with formatted analysis list
    """
    final_report = state["rca_output"]
    
    task = state["rca_task"]
    final_report["task"] = {
        "investigation_goal": task.investigation_goal,
        "target_resource": task.target_resource,
        "resource_type": task.resource_type,
        "suggested_tools": task.suggested_tools
    }
    
    final_report["insights"] = state["insights"]
    final_report["steps_performed"] = state["prev_steps"]
    final_report["tools_stats"] = count_tool_calls(state["messages"])

    return {"rca_analyses_list": [final_report]}


def after_tools_condition(state: RcaAgentState) -> str:
    """Determine next step after tool execution.
    
    Args:
        state: Current RCA agent state
        
    Returns:
        Name of next node to execute
    """
    if state.get("rca_output"):
        # Investigation complete, format and end
        return "format-output"
    return "summarise"


def build_rca_graph():
    """Build and compile the RCA agent graph.
    
    Returns:
        Compiled RCA agent graph
    """
    builder = StateGraph(RcaAgentState)

    # Add nodes
    builder.add_node("rca-agent", rcaAgent)
    builder.add_node("tools", ToolNode(tools_with_completion))
    builder.add_node("summarise", summarise)
    builder.add_node("format-output", format_response)

    # Add edges
    builder.add_edge(START, "rca-agent")

    # Conditional edge from rca-agent
    builder.add_conditional_edges(
        "rca-agent",
        tools_condition,
    )

    # After tools, decide whether to summarise or end
    builder.add_conditional_edges(
        "tools",
        after_tools_condition,
        {
            "summarise": "summarise",
            "format-output": "format-output"
        }
    )

    # After summarise, continue investigation
    builder.add_edge("summarise", "rca-agent")
    builder.add_edge("format-output", END)

    # Compile with output keys to only return the analysis
    return builder.compile().with_config(
        run_name="RCA Agent",
        output_keys=["rca_analyses_list"]
    )


# Export the compiled graph
rca_agent_graph = build_rca_graph()
