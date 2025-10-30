"""Pydantic model schemas for SRE Agent."""
from pydantic import BaseModel, Field
from typing import List, Literal


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
    investigation_goal: str = Field(..., description="Goal of the investigation")
    target_resource: str = Field(..., description="Name of the resource to investigate")
    resource_type: Literal["pod", "service"] = Field(..., description="Type of resource being investigated")
    suggested_tools: List[str] = Field(default_factory=list, description="List of tools suggested for the investigation")


class RCATaskList(BaseModel):
    """A list of RCA tasks to be performed by the RCA agent in parallel"""
    rca_tasks: List[RCATask] = Field(default_factory=list, description="List of RCA tasks to be performed")


class UpdateAgentData(BaseModel):
    """Represents a step performed by the SRE agent."""
    insight: str = Field(..., description="Most important new finding")
    prev_step: str = Field(..., description="Concise description of the most recent action taken")


class FinalReport(BaseModel):
    """The Final report created by the supervisor agent"""
    root_cause: str = Field(..., description="The identified root cause of the incident")
    affected_resources: List[str] = Field(..., description="List of all resources affected by the incident")
    evidence_summary: str = Field(..., description="Summary of evidence from all RCA workers")
    investigation_summary: str = Field(..., description="Overview of the investigation process and findings")
