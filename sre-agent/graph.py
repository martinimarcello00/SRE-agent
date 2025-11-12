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

logger = logging.getLogger(__name__)

def update_rca_task_status(state: SreParentState) -> dict:
    """Update the status of RCA tasks from 'pending' to 'in_progress' before sending to RCA agents.
    
    This function implements a divide-and-conquer strategy for RCA task execution:
    - First iteration: Marks the first RCA_TASKS_PER_ITERATION tasks as 'in_progress'
    - Subsequent iterations: Marks only the tasks selected by the supervisor agent (via tasks_to_be_executed) as 'in_progress'
    
    The same number of tasks is always maintained in rca_tasks, only their status is updated.
    Tasks not yet scheduled remain with status 'pending'.
    
    Args:
        state: Current parent state containing:
            - rca_tasks: List of all RCATask objects
            - tasks_to_be_executed: List of task priorities to execute (empty on first iteration)
        
    Returns:
    Dictionary with updated rca_tasks list where selected tasks have status='in_progress'
    """
    rca_tasks = state.get("rca_tasks", [])
    rca_tasks_to_be_executed = state.get("tasks_to_be_executed", [])

    if not rca_tasks:
        return {}

    # Determine which priorities already have completed analyses so they are never rescheduled
    completed_priorities: set[int] = set()
    for analysis in state.get("rca_analyses_list", []):
        task_info = analysis.get("task") if isinstance(analysis, dict) else None
        priority = None
        if isinstance(task_info, dict):
            priority = task_info.get("priority")
        elif hasattr(task_info, "priority"):
            priority = getattr(task_info, "priority")
        if isinstance(priority, int):
            completed_priorities.add(priority)

    # Normalise task statuses so previously scheduled work is marked as completed
    normalised_tasks = []
    for task in rca_tasks:
        if task.priority in completed_priorities and task.status != "completed":
            normalised_tasks.append(task.model_copy(update={"status": "completed"}))
        else:
            normalised_tasks.append(task)

    rca_tasks = normalised_tasks

    selected_tasks: list = []
    priorities_to_execute = {priority for priority in rca_tasks_to_be_executed if isinstance(priority, int)}

    if priorities_to_execute:
        # Subsequent iterations: execute exactly the tasks requested by the supervisor
        logger.info(f"Updating status for tasks: {priorities_to_execute}")
        selected_tasks = [task for task in rca_tasks if task.priority in priorities_to_execute and task.status != "completed"]
        missing_priorities = priorities_to_execute.difference({task.priority for task in selected_tasks})
        if missing_priorities:
            logger.warning(f"Requested RCA tasks already completed or missing: {sorted(missing_priorities)}")
    else:
        # First iteration: select first pending tasks for parallel execution
        logger.info(f"Updating status for first {RCA_TASKS_PER_ITERATION} tasks.")
        pending_tasks = [task for task in rca_tasks if task.status == "pending"]
        selected_tasks = pending_tasks[:RCA_TASKS_PER_ITERATION]

    # Mark selected tasks as "in_progress" and persist other status transitions
    updated_tasks = []
    selected_priorities = {task.priority for task in selected_tasks}

    for task in rca_tasks:
        if task.priority in selected_priorities and task.status != "in_progress":
            updated_tasks.append(task.model_copy(update={"status": "in_progress"}))
        elif task.priority in completed_priorities and task.status != "completed":
            updated_tasks.append(task.model_copy(update={"status": "completed"}))
        elif task.priority not in selected_priorities and task.status == "in_progress":
            updated_tasks.append(task.model_copy(update={"status": "pending"}))
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
            - rca_tasks: List of all RCATask objects (marked as 'pending', 'in_progress', or 'completed')
            - tasks_to_be_executed: List of task priorities selected by supervisor (empty on first iteration)
            - app_name, app_summary: Application metadata
            - symptoms: List of identified symptoms
        
    Returns:
        List of Send commands for parallel RCA agent execution, or single Send to supervisor if no tasks
    """
    rca_tasks = state.get("rca_tasks", [])
    tasks_to_be_executed = [priority for priority in state.get("tasks_to_be_executed", []) if isinstance(priority, int)]
    
    if not rca_tasks:
        # No RCA tasks, go directly to supervisor with current symptoms
        logger.info("RCA Router: No RCA tasks found. Routing to supervisor.")
        supervisor_input = {
            "app_name": state.get("app_name"),
            "app_summary": state.get("app_summary"),
            "symptoms": state.get("symptoms", []),
            "rca_tasks": [],
            "rca_analyses_list": []
        }
        return [Send("supervisor_agent", supervisor_input)]

    # Determine tasks to run this iteration
    if tasks_to_be_executed:
        # Subsequent iterations: respect supervisor instructions explicitly
        requested_priorities = set(tasks_to_be_executed)
        selected_tasks = [task for task in rca_tasks if task.priority in requested_priorities and task.status != "completed"]
        missing_priorities = requested_priorities.difference({task.priority for task in selected_tasks})
        if missing_priorities:
            logger.warning(f"RCA Router: Requested tasks already completed or unavailable: {sorted(missing_priorities)}")
    else:
        # First iteration fallback: execute tasks marked as "in_progress"
        selected_tasks = [task for task in rca_tasks if task.status == "in_progress"]

    # Check if all tasks have already been completed in previous iterations
    pending_tasks = [task for task in rca_tasks if task.status == "pending"]
    
    if not selected_tasks and not pending_tasks:
        # All tasks are done, but router was called. Go to supervisor.
        logger.info("RCA Router: All tasks previously completed. Routing to supervisor for final report.")
        supervisor_input = {
            "app_name": state.get("app_name"),
            "app_summary": state.get("app_summary"),
            "symptoms": state.get("symptoms", []),
            "rca_tasks": rca_tasks, # <-- Pass all tasks
            "rca_analyses_list": state.get("rca_analyses_list", []) # Pass existing analyses
        }
        return [Send("supervisor_agent", supervisor_input)]
    
    if not selected_tasks:
        # This can happen if tasks_to_be_executed was empty and all tasks were already pending
        # This is the entry point for the very first iteration
        logger.info("RCA Router: No 'in_progress' tasks found. Selecting pending tasks for first iteration.")
        selected_tasks = pending_tasks[:RCA_TASKS_PER_ITERATION]
        if not selected_tasks:
            logger.warning("RCA Router: No tasks to execute. Routing to supervisor.")
            supervisor_input = {
                "app_name": state.get("app_name"),
                "app_summary": state.get("app_summary"),
                "symptoms": state.get("symptoms", []),
                "rca_tasks": rca_tasks,
                "rca_analyses_list": state.get("rca_analyses_list", [])
            }
            return [Send("supervisor_agent", supervisor_input)]


    # Create parallel RCA investigations for selected tasks
    parallel_rca_calls = []
    for task in selected_tasks:
        # Pass renamed fields to avoid InvalidUpdateError with parent state
        rca_input_state = {
            "rca_task": task,
            "rca_app_summary": state.get("app_summary", ""), 
            "rca_target_namespace": state.get("target_namespace", ""),
            "messages": [],
            "insights": [],
            "prev_steps": [],
            "rca_analyses_list": []
        }
        parallel_rca_calls.append(Send("rca_agent", rca_input_state))

    logger.info(f"RCA Router: Starting {len(parallel_rca_calls)} parallel RCA agent workers for tasks: {[t.priority for t in selected_tasks]}")

    return parallel_rca_calls


def supervisor_router(state: SreParentState) -> str:
    """Determine next step after supervisor agent.
    
    Args:
        state: Current parent state
        
    Returns:
        Name of next node to execute
    """
    tasks_to_be_executed = state.get("tasks_to_be_executed", [])
    
    if len(tasks_to_be_executed) > 0:
        # Supervisor requested more tasks
        logger.info(f"Supervisor Router: Re-routing to 'schedule_rca_tasks' for tasks: {tasks_to_be_executed}")
        return "schedule_rca_tasks"
    else:
        # No more tasks, investigation is complete
        logger.info("Supervisor Router: Investigation complete. Ending graph.")
        return END


def build_parent_graph():
    """Build and compile the complete SRE agent workflow graph.
    
    Returns:
        Compiled parent graph with all agents
    """
    builder = StateGraph(SreParentState)

    # Add agent nodes
    builder.add_node(
        "triage_agent",
        triage_agent_graph,
        metadata={
            "name": "Triage Agent",
            "description": "Aggregates telemetry and extracts symptoms to seed the investigation."
        },
    )
    builder.add_node(
        "planner_agent",
        planner_agent_graph,
        metadata={
            "name": "Planner Agent",
            "description": "Scores symptoms and proposes RCA tasks for the current incident."
        },
    )
    builder.add_node(
        "schedule_rca_tasks",
        update_rca_task_status,
        metadata={
            "name": "Schedule RCA Tasks",
            "description": "Updates task execution status and selects work for the next RCA iteration."
        },
    )
    builder.add_node(
        "rca_agent",
        rca_agent_graph,
        metadata={
            "name": "RCA Agent",
            "description": "Runs focused RCA workflows in parallel for each scheduled task."
        },
    )
    builder.add_node(
        "supervisor_agent",
        supervisor_agent_graph,
        metadata={
            "name": "Supervisor Agent",
            "description": "Reviews RCA findings, requests follow-up tasks, or finalizes the report."
        },
    )

    # Build workflow
    builder.add_edge(START, "triage_agent")
    builder.add_edge("triage_agent", "planner_agent")
    builder.add_edge("planner_agent", "schedule_rca_tasks")

    # Use rca_router to dynamically send tasks to parallel RCA agents
    # or skip to supervisor if no tasks
    builder.add_conditional_edges(
        "schedule_rca_tasks",
        rca_router,
        ["rca_agent", "supervisor_agent"]
    )

    # After RCA agents complete, go to supervisor
    # (rca_analyses_list is automatically aggregated via operator.add)
    builder.add_edge("rca_agent", "supervisor_agent")
    
    # Add conditional edge after supervisor to loop or end
    builder.add_conditional_edges(
        "supervisor_agent",
        supervisor_router,
        {
            "schedule_rca_tasks": "schedule_rca_tasks",
            END: END
        }
    )

    return builder.compile()

parent_graph = build_parent_graph()