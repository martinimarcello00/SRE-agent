"""Triage Agent - Gathers cluster health data and identifies symptoms."""
import json
import sys
import os
from pathlib import Path
from langgraph.graph import START, END, StateGraph
import logging

# Add MCP-server to path for api imports
mcp_server_path = str(Path(__file__).parent.parent.parent / "MCP-server")
if mcp_server_path not in sys.path:
    sys.path.insert(0, mcp_server_path)

from api.jaeger_api import JaegerAPI
from api.k8s_api import K8sAPI
from api.prometheus_api import PrometheusAPI

from models import TriageAgentState, SymptomList
from prompts import triage_prompt_template
from config import GPT5_MINI


def get_triage_data(state: TriageAgentState) -> dict:
    """Gather triage data from cluster monitoring systems.
    
    Args:
        state: Current triage agent state
        
    Returns:
        Dictionary with problematic pods, traces, and metrics
    """
    jaeger_api = JaegerAPI()
    k8s_api = K8sAPI(state["target_namespace"])
    prometheus_api = PrometheusAPI(namespace=state["target_namespace"])
    
    # Get pods with problematic statuses
    problematic_pods = k8s_api.get_problematic_pods()

    # Traces which have errors
    problematic_traces = jaeger_api.get_processed_traces(
        service=state["trace_service_starting_point"], 
        only_errors=True
    )

    # Filter for traces which take more than 2 seconds
    slow_traces = jaeger_api.get_slow_traces(
        service=state["trace_service_starting_point"], 
        min_duration_ms=2000
    )

    # Metrics with anomalous values
    problematic_pods_metrics: dict = {
        "problematic_metrics": []
    }

    pods = k8s_api.get_pods_list()

    for pod in pods:
        triage_metric_report = prometheus_api.get_pod_triage_metrics(pod)
        if triage_metric_report["is_anomalous"]:
            problematic_pods_metrics["problematic_metrics"].append(triage_metric_report)
    
    if len(problematic_pods_metrics["problematic_metrics"]) > 0:
        problematic_pods_metrics["pods_count"] = len(problematic_pods_metrics["problematic_metrics"])
    else:
        problematic_pods_metrics["info"] = "All monitored metrics look healthy; no anomalous values detected."

    return {
        "problematic_pods": problematic_pods,
        "problematic_traces": problematic_traces,
        "slow_traces": slow_traces,
        "problematic_metrics": problematic_pods_metrics
    }


def triage_agent(state: TriageAgentState) -> dict:
    """Analyze triage data and identify symptoms.
    
    Args:
        state: Current triage agent state with gathered data
        
    Returns:
        Dictionary with identified symptoms
    """
    human_prompt_parts = [
        f"Please analyze the following triage data for the {state['app_name']} application.\n\n"
        f"### Application Summary\n{state['app_summary']}"
    ]

    if "info" not in state["problematic_pods"]:
        problematic_pods_str = json.dumps(state["problematic_pods"], indent=2)
        human_prompt_parts.append(f"### Problematic Pods\n```json\n{problematic_pods_str}\n```")

    if "info" not in state["problematic_metrics"]:
        problematic_metrics_str = json.dumps(state["problematic_metrics"], indent=2)
        human_prompt_parts.append(f"### Anomalous Pod Metrics\n```json\n{problematic_metrics_str}\n```")

    if "info" not in state["slow_traces"] and "error" not in state["slow_traces"]:
        slow_traces_str = json.dumps(state["slow_traces"], indent=2)
        human_prompt_parts.append(f"### Slow Traces\n```json\n{slow_traces_str}\n```")

    if "info" not in state["problematic_traces"] and "error" not in state["problematic_traces"] and len(human_prompt_parts) == 1:
        problematic_traces_str = json.dumps(state["problematic_traces"], indent=2)
        human_prompt_parts.append(f"### Traces that contains error\n```json\n{problematic_traces_str}\n```")

    # If no problems were found in any dataset, add a note.
    if len(human_prompt_parts) == 1:
        human_prompt_parts.append("No issues were found in pods, metrics, or traces.")

    human_input = "\n\n".join(human_prompt_parts)

    llm_for_symptoms = GPT5_MINI.with_structured_output(SymptomList)
    triage_chain = triage_prompt_template | llm_for_symptoms

    logging.info("Triage agent is analyzing triage data to identify symptoms.")

    symptom_list = triage_chain.invoke({"human_input": human_input})

    return {"symptoms": symptom_list.symptoms}  # type: ignore


def build_triage_graph():
    """Build and compile the triage agent graph.
    
    Returns:
        Compiled triage agent graph
    """
    builder = StateGraph(TriageAgentState)

    # Add nodes
    builder.add_node("gather-triage-data", get_triage_data)
    builder.add_node("triage-agent", triage_agent)

    # Add edges
    builder.add_edge(START, "gather-triage-data")
    builder.add_edge("gather-triage-data", "triage-agent")
    builder.add_edge("triage-agent", END)

    return builder.compile().with_config(run_name="Triage Agent")


# Export the compiled graph
triage_agent_graph = build_triage_graph()
