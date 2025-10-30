"""Main SRE Agent workflow graph assembly."""
from langgraph.graph import START, END, StateGraph
from langgraph.types import Send
import logging

from models import SreParentState
from agents import (
    triage_agent_graph,
    planner_agent_graph,
    rca_agent_graph,
    supervisor_agent_graph
)


def rca_router(state: SreParentState) -> list[Send]:
    """Route to RCA agents for parallel execution, or skip to supervisor if no tasks.
    
    Args:
        state: Current parent state with RCA tasks
        
    Returns:
        List of Send commands for parallel RCA agents or supervisor
    """
    rca_tasks = state.get("rca_tasks", [])

    if not rca_tasks:
        # No RCA tasks, go directly to supervisor with current symptoms
        supervisor_input = {
            "app_name": state.get("app_name"),
            "app_summary": state.get("app_summary"),
            "symptoms": state.get("symptoms", []),
            "rca_analyses_list": []
        }
        return [Send("supervisor_agent", supervisor_input)]

    # Create parallel RCA investigations
    parallel_rca_calls = []
    for task in rca_tasks:
        # Pass renamed fields to avoid InvalidUpdateError with parent state
        rca_input_state = {
            "rca_task": task,
            "rca_app_summary": state.get("app_summary", ""),  # Renamed field
            "rca_target_namespace": state.get("target_namespace", ""),  # Renamed field
            "messages": [],
            "insights": [],
            "prev_steps": [],
            "rca_analyses_list": []
        }
        parallel_rca_calls.append(Send("rca_agent", rca_input_state))

    logging.info(f"Starting {len(parallel_rca_calls)} parallel RCA agent workers")

    return parallel_rca_calls


def build_parent_graph():
    """Build and compile the complete SRE agent workflow graph.
    
    Returns:
        Compiled parent graph with all agents
    """
    builder = StateGraph(SreParentState)

    # Add agent nodes
    builder.add_node("triage_agent", triage_agent_graph)
    builder.add_node("planner_agent", planner_agent_graph)
    builder.add_node("rca_agent", rca_agent_graph)
    builder.add_node("supervisor_agent", supervisor_agent_graph)

    # Build workflow
    builder.add_edge(START, "triage_agent")
    builder.add_edge("triage_agent", "planner_agent")

    # Use rca_router to dynamically send tasks to parallel RCA agents
    # or skip to supervisor if no tasks
    builder.add_conditional_edges(
        "planner_agent",
        rca_router,
        ["rca_agent", "supervisor_agent"]
    )

    # After RCA agents complete, go to supervisor
    # (rca_analyses_list is automatically aggregated via operator.add)
    builder.add_edge("rca_agent", "supervisor_agent")
    builder.add_edge("supervisor_agent", END)

    return builder.compile()


# Export the compiled parent graph
parent_graph = build_parent_graph()
