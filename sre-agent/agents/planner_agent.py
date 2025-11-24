"""Planner Agent - Creates RCA investigation tasks from symptoms."""
import json
import sys
import os
from pathlib import Path
from langgraph.graph import START, END, StateGraph
import logging
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

# Add MCP-server to path for api imports
mcp_server_path = str(Path(__file__).parent.parent.parent / "MCP-server")
if mcp_server_path not in sys.path:
    sys.path.insert(0, mcp_server_path)

from api.k8s_api import K8sAPI
from api.datagraph import DataGraph

from models import PlannerAgentState, RCATaskList, Symptom
from prompts import PLANNER_SYSTEM_PROMPT, PLANNER_HUMAN_PROMPT
from config import GPT5_MINI


def get_resource_dependencies(symptom: Symptom) -> dict:
    """Get dependencies for a symptom's affected resource.
    
    Args:
        symptom: Symptom with affected resource information
        
    Returns:
        Dictionary with data and infrastructure dependencies
    """
    result: dict = {
        "resource_name": symptom.affected_resource,
        "resource_type": symptom.resource_type
    }

    service = ""
    k8s_api = K8sAPI()

    if symptom.resource_type == "pod":
        services = k8s_api.get_services_from_pod(symptom.affected_resource)
        service = services["services"][0]["service_name"]
    else:
        service = symptom.affected_resource

    datagraph = DataGraph()
    
    data_dependencies = datagraph.get_services_used_by(service)
    infra_dependencies = datagraph.get_dependencies(service)

    if len(data_dependencies) > 0:
        result["data_dependencies"] = []
        for dep in data_dependencies:
            temp = {
                "service": dep,
                "pods": []
            }
            pods = k8s_api.get_pods_from_service(dep)
            for pod in pods["pods"]:
                temp["pods"].append(pod["pod_name"])
            result["data_dependencies"].append(temp)

    if isinstance(infra_dependencies, dict) and len(infra_dependencies) > 0:
        result["infra_dependencies"] = []
        for dep_name, dep_type in infra_dependencies.items():
            dep = {
                "service": dep_name,
                "dependency_type": dep_type,
                "pods": []
            }
            pods = k8s_api.get_pods_from_service(dep_name)
            for pod in pods["pods"]:
                dep["pods"].append(pod["pod_name"])
            result["infra_dependencies"].append(dep)
    
    return result


def planner_agent(state: PlannerAgentState) -> dict:
    """Create RCA investigation tasks from symptoms and their dependencies.
    
    Args:
        state: Current planner agent state with identified symptoms
        
    Returns:
        Dictionary with list of RCA tasks
    """
    symptoms = state["symptoms"]
    
    if not symptoms:
        return {"rca_tasks": []}
    
    # Enrich symptoms with dependencies
    enriched_symptoms = []
    for symptom in symptoms:
        enriched = {
            "symptom": symptom.model_dump(),
            "dependencies": get_resource_dependencies(symptom)
        }
        enriched_symptoms.append(enriched)
    
    # Build human prompt with all symptom information in markdown format
    symptoms_info_parts = []
    
    for i, enriched in enumerate(enriched_symptoms, 1):
        symptom_dict = enriched["symptom"]
        deps = enriched["dependencies"]
        
        symptoms_info_parts.extend([
            f"## Symptom {i}\n\n",
            f"**Type**: {symptom_dict['potential_symptom']}\n\n",
            f"**Resource**: `{symptom_dict['affected_resource']}` (`{symptom_dict['resource_type']}`)\n\n",
            f"**Evidence**:\n{symptom_dict['evidence']}\n\n"
        ])
        
        # Add dependencies if they exist
        if "data_dependencies" in deps and deps["data_dependencies"]:
            symptoms_info_parts.append(f"**Data Dependencies**:\n```json\n{json.dumps(deps['data_dependencies'], indent=2)}\n```\n\n")
        else:
            symptoms_info_parts.append(f"**Data Dependencies**:\nNo data dependencies found for the affected resource\n\n")
        
        if "infra_dependencies" in deps and deps["infra_dependencies"]:
            symptoms_info_parts.append(f"**Infrastructure Dependencies**:\n```json\n{json.dumps(deps['infra_dependencies'], indent=2)}\n```\n\n")
        else:
            symptoms_info_parts.append(f"**Infrastructure Dependencies**:\nNo infrastructure dependencies found for the affected resource\n\n")

        if "data_dependencies" not in deps and "infra_dependencies" not in deps:
            symptoms_info_parts.append("**Dependencies**: None found\n\n")

        
        symptoms_info_parts.append("---\n\n")
    
    symptoms_info = "".join(symptoms_info_parts)
    
    # Create and invoke chain
    llm_for_tasks = GPT5_MINI.with_structured_output(RCATaskList)

    logger.info("Planner Agent: Finding investigation plan (RCA task list)")

    planner_prompt_template = ChatPromptTemplate.from_messages(
    [
        ("system", PLANNER_SYSTEM_PROMPT),
        ("human", PLANNER_HUMAN_PROMPT),
    ]
    )

    planner_chain = planner_prompt_template | llm_for_tasks
    
    task_list = planner_chain.invoke({
        "app_name": state["app_name"],
        "target_namespace": state["target_namespace"],
        "app_summary": state["app_summary"],
        "symptoms_info": symptoms_info
    })

    # order the task_list.rca_tasks by priority number (ascending)
    tasks_list = sorted(task_list.rca_tasks, key=lambda t: t.priority)  # type: ignore
    
    return {"rca_tasks": tasks_list}


def build_planner_graph():
    """Build and compile the planner agent graph.
    
    Returns:
        Compiled planner agent graph
    """
    builder = StateGraph(PlannerAgentState)
    builder.add_node("planner", planner_agent)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", END)
    
    return builder.compile().with_config(run_name="Planner Agent")


# Export the compiled graph
planner_agent_graph = build_planner_graph()
