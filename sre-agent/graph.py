"""Main SRE Agent workflow graph assembly."""
from langgraph.graph import START, END, StateGraph
from langgraph.types import Send
import logging
from config import RCA_TASKS_PER_ITERATION

from models import SreParentState
from agents import (
    triage_agent_graph,
    planner_agent_graph,
    rca_agent_graph,
    supervisor_agent_graph
)

def update_rca_task_status(state: SreParentState) -> dict:
    """Update the status of RCA tasks from 'pending' to 'executed' before sending to RCA agents.
    
    This function implements a divide-and-conquer strategy for RCA task execution:
    - First iteration: Marks the first RCA_TASKS_PER_ITERATION tasks as 'executed'
    - Subsequent iterations: Marks only the tasks selected by the supervisor agent (via tasks_to_be_executed) as 'executed'
    
    The same number of tasks is always maintained in rca_tasks, only their status is updated.
    Tasks not yet executed remain with status 'pending'.
    
    Args:
        state: Current parent state containing:
            - rca_tasks: List of all RCATask objects
            - tasks_to_be_executed: List of task priorities to execute (empty on first iteration)
        
    Returns:
        Dictionary with updated rca_tasks list where selected tasks have status='executed'
    """
    rca_tasks = state.get("rca_tasks", [])
    rca_tasks_to_be_executed = state.get("tasks_to_be_executed", [])
    
    if not rca_tasks:
        return {}
    
    selected_tasks = []

    if len(rca_tasks_to_be_executed) > 0:
        # Subsequent iterations: Tasks chosen by the supervisor agent based on priority
        for task in rca_tasks:
            if task.priority in rca_tasks_to_be_executed:
                selected_tasks.append(task)
    else:
        # First iteration: Select first RCA_TASKS_PER_ITERATION tasks for parallel execution
        selected_tasks = rca_tasks[:RCA_TASKS_PER_ITERATION]
    
    # Mark selected tasks as "executed", keep unselected tasks as "pending"
    updated_tasks = []
    for task in rca_tasks:
        if task in selected_tasks:
            # Update status to executed using model_copy
            updated_task = task.model_copy(update={"status": "executed"})
            updated_tasks.append(updated_task)
        else:
            updated_tasks.append(task)
    
    return {"rca_tasks": updated_tasks}

def rca_router(state: SreParentState) -> list[Send]:
    """Route RCA tasks to parallel RCA agents or skip to supervisor based on task availability.
    
    This function implements divide-and-conquer RCA execution:
    - First iteration: Routes the first RCA_TASKS_PER_ITERATION tasks to parallel RCA agents
    - Subsequent iterations: Routes only supervisor-selected tasks (via tasks_to_be_executed priorities) to RCA agents
    - If no tasks remain, routes directly to supervisor agent for final diagnosis
    
    Each task is sent with renamed fields (rca_app_summary, rca_target_namespace) to avoid 
    conflicts with parent state keys and InvalidUpdateError exceptions.
    
    Args:
        state: Current parent state containing:
            - rca_tasks: List of all RCATask objects (already marked as 'executed' or 'pending')
            - tasks_to_be_executed: List of task priorities selected by supervisor (empty on first iteration)
            - app_name, app_summary: Application metadata
            - symptoms: List of identified symptoms
        
    Returns:
        List of Send commands for parallel RCA agent execution, or single Send to supervisor if no tasks
    """
    rca_tasks = state.get("rca_tasks", [])
    rca_tasks_to_be_executed = state.get("tasks_to_be_executed", [])

    if not rca_tasks:
        # No RCA tasks, go directly to supervisor with current symptoms
        supervisor_input = {
            "app_name": state.get("app_name"),
            "app_summary": state.get("app_summary"),
            "symptoms": state.get("symptoms", []),
            "rca_analyses_list": []
        }
        return [Send("supervisor_agent", supervisor_input)]

    selected_tasks = []

    if len(rca_tasks_to_be_executed) > 0:
        # Subsequent iterations: Tasks chosen by the supervisor agent based on priority
        for task in rca_tasks:
            if task.priority in rca_tasks_to_be_executed:
                selected_tasks.append(task)
    else:
        # First iteration: Select first RCA_TASKS_PER_ITERATION tasks for parallel execution
        selected_tasks = rca_tasks[:RCA_TASKS_PER_ITERATION]

    # Create parallel RCA investigations for selected tasks
    parallel_rca_calls = []
    for task in selected_tasks:
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
    builder.add_node("update_task_status", update_rca_task_status)
    builder.add_node("rca_agent", rca_agent_graph)
    builder.add_node("supervisor_agent", supervisor_agent_graph)

    # Build workflow
    builder.add_edge(START, "triage_agent")
    builder.add_edge("triage_agent", "planner_agent")
    builder.add_edge("planner_agent", "update_task_status")

    # Use rca_router to dynamically send tasks to parallel RCA agents
    # or skip to supervisor if no tasks
    builder.add_conditional_edges(
        "update_task_status",
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
