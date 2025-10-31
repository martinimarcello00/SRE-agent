"""TypedDict state definitions for LangGraph agents."""
from typing import TypedDict, List, Annotated
import operator
from langgraph.graph.message import add_messages, AnyMessage
from .schemas import Symptom, RCATask


class TriageAgentState(TypedDict):
    """State for the Triage Agent"""
    app_name: str
    app_summary: str
    target_namespace: str
    trace_service_starting_point: str
    problematic_pods: dict
    slow_traces: dict
    problematic_metrics: dict
    symptoms: List[Symptom]


class PlannerAgentState(TypedDict):
    """State for the Planner Agent"""
    app_name: str
    app_summary: str
    target_namespace: str
    symptoms: List[Symptom]
    rca_tasks: List[RCATask]


class RcaAgentState(TypedDict):
    """State for the RCA Worker Agent"""
    messages: Annotated[list[AnyMessage], add_messages]
    rca_app_summary: str
    rca_target_namespace: str
    rca_task: RCATask
    insights: Annotated[list[str], operator.add]
    prev_steps: Annotated[list[str], operator.add]
    rca_output: dict
    rca_analyses_list: list[dict]


class SupervisorAgentState(TypedDict):
    """State for the Supervisor Agent"""
    app_name: str
    app_summary: str
    symptoms: List[Symptom]
    rca_analyses_list: List[dict]
    final_report: dict


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
    symptoms: List[Symptom]

    # Planner agent
    rca_tasks: List[RCATask]

    # RCA Worker agent
    rca_analyses_list: Annotated[list[dict], operator.add]

    # Supervisor agent
    final_report: dict
