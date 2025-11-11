"""Pydantic model schemas for SRE Agent."""
from pydantic import BaseModel, Field
from typing import List, Literal, Optional


class Symptom(BaseModel):
    """A symptom observed in the Kubernetes cluster"""
    potential_symptom: str = Field(..., description="Type of symptom observed")
    resource_type: Literal["pod", "service"] = Field(..., description="Type of resource experiencing the issue")
    affected_resource: str = Field(..., description="Exact name of the resource experiencing the issue (no namespace or decorators)")
    evidence: str = Field(..., description="Evidence supporting this symptom identification")


class SymptomList(BaseModel):
    """A list of symptoms observed in the Kubernetes cluster"""
    symptoms: List[Symptom] = Field(default_factory=list, description="List of symptoms observed in the cluster")


class RCATask(BaseModel):
    """A RCA task to be performed by the RCA agent"""
    priority: int = Field(..., description="Order of execution for this RCA task")
    status: Literal["pending", "in_progress", "completed"] = Field(default="pending", description="Status of the RCA task")
    investigation_goal: str = Field(..., description="Goal of the investigation")
    target_resource: str = Field(..., description="Name of the resource to investigate")
    resource_type: Literal["pod", "service"] = Field(..., description="Type of resource being investigated")
    suggested_tools: List[str] = Field(default_factory=list, description="List of tools suggested for the investigation")


class RCATaskList(BaseModel):
    """A list of RCA tasks to be performed by the RCA agent in parallel"""
    rca_tasks: List[RCATask] = Field(default_factory=list, description="List of RCA tasks to be performed")


class RCAAgentExplaination(BaseModel):
    """Aggregates all reasoning steps and insights extracted by the RCA agent at the end of the investigation."""
    steps: List[str] = Field(..., description="Chronological list of all actions or analyses performed by the agent during the investigation")
    insights: List[str] = Field(..., description="Comprehensive list of key findings or insights discovered throughout the investigation")


class FinalReport(BaseModel):
    """The Final report created by the supervisor agent"""
    root_cause: str = Field(..., description="The identified root cause of the incident")
    affected_resources: List[str] = Field(..., description="List of all resources affected by the incident")
    evidence_summary: str = Field(..., description="Summary of evidence from all RCA workers")
    investigation_summary: str = Field(..., description="Overview of the investigation process and findings")

class SupervisorDecision(BaseModel):
    """The supervisor's decision to either conclude the investigation or request more data."""
    tasks_to_be_executed: List[int] = Field(
        default_factory=list,
        description="A list of task priorities to execute next. Provide this ONLY if the investigation is INCOMPLETE and more data is strictly necessary."
    )
    final_report: Optional[FinalReport] = Field(
        default=None,
        description="The final root cause report. Provide this ONLY if the investigation is COMPLETE and the evidence is sufficient."
    )