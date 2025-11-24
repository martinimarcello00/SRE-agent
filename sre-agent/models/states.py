"""TypedDict state definitions for LangGraph agents."""
from typing import TypedDict, List, Annotated, Dict
from langgraph.graph.message import add_messages, AnyMessage
from .schemas import Symptom, RCATask
from .reducers import merge_rca_analyses


class TriageAgentState(TypedDict):
    """State for the Triage Agent"""
    app_name: str
    app_summary: str
    target_namespace: str
    trace_service_starting_point: str
    problematic_pods: dict
    slow_traces: dict
    problematic_metrics: dict
    problematic_traces: dict
    symptoms: List[Symptom]
    prompts_config: Dict[str, str]


class PlannerAgentState(TypedDict):
    """State for the Planner Agent"""
    app_name: str
    app_summary: str
    target_namespace: str
    symptoms: List[Symptom]
    rca_tasks: List[RCATask]
    prompts_config: Dict[str, str]


class RcaAgentState(TypedDict):
    """State for the RCA Worker Agent"""
    messages: Annotated[list[AnyMessage], add_messages]
    rca_app_summary: str
    rca_target_namespace: str
    rca_task: RCATask
    insights: list[str]
    prev_steps: list[str]
    rca_output: dict
    rca_analyses_list: list[dict]
    rca_prompts_config: Dict[str, str]
    

class SupervisorAgentState(TypedDict):
    """State for the Supervisor Agent"""
    app_name: str
    app_summary: str
    symptoms: List[Symptom]
    rca_analyses_list: List[dict]
    final_report: dict
    rca_tasks: List[RCATask]
    tasks_to_be_executed: List[int]
    prompts_config: Dict[str, str]


class SreParentState(TypedDict):
    """Parent state for the complete SRE workflow"""
    app_name: str
    app_summary: str
    target_namespace: str
    trace_service_starting_point: str

    # Triage Agent
    problematic_pods: dict
    slow_traces: dict
    problematic_metrics: dict
    problematic_traces: dict
    symptoms: List[Symptom]

    # Planner agent
    rca_tasks: List[RCATask]

    # RCA Worker agent
    rca_analyses_list: Annotated[list[dict], merge_rca_analyses]

    # Tasks to be executed by the RCA agent
    tasks_to_be_executed: List[int]

    # Supervisor agent
    final_report: dict

    # Prompt configuration
    prompts_config: Dict[str, str]
