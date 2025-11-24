"""Triage Agent - Gathers cluster health data and identifies symptoms."""
import json
import sys
from pathlib import Path
from langgraph.graph import START, END, StateGraph
import logging
from langchain_core.prompts import ChatPromptTemplate


logger = logging.getLogger(__name__)

# Add MCP-server to path for api imports
mcp_server_path = str(Path(__file__).parent.parent.parent / "MCP-server")
if mcp_server_path not in sys.path:
    sys.path.insert(0, mcp_server_path)

from api.jaeger_api import JaegerAPI
from api.k8s_api import K8sAPI
from api.prometheus_api import PrometheusAPI

from models import TriageAgentState, SymptomList
from prompts import TRIAGE_SYSTEM_PROMPT, TRIAGE_HUMAN_PROMPT
from config import GPT5_MINI
from utils import get_system_prompt


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
    # Helper to format data or return info message
    def format_data(data, label):
        if "info" in data:
            return data["info"]
        if "error" in data:
            return f"Error retrieving {label}: {data['error']}"
        return f"```json\n{json.dumps(data, indent=2)}\n```"

    problematic_pods_str = format_data(state["problematic_pods"], "pods")
    problematic_metrics_str = format_data(state["problematic_metrics"], "metrics")
    slow_traces_str = format_data(state["slow_traces"], "slow traces")
    
    # Check if we have any primary problems (pods, metrics, slow traces)
    has_problems = (
        "info" not in state["problematic_pods"] and "error" not in state["problematic_pods"]
    ) or (
        "info" not in state["problematic_metrics"] and "error" not in state["problematic_metrics"]
    ) or (
        "info" not in state["slow_traces"] and "error" not in state["slow_traces"]
    )
    
    # Only include error traces if no other problems found (fallback)
    if has_problems:
        problematic_traces_str = "No error traces analyzed (other problems detected)."
    else:
        logger.warning("No primary problems detected (pods, metrics, slow traces); falling back to analyzing error traces.")
        problematic_traces_str = format_data(state["problematic_traces"], "error traces")

    # Determine which system prompt to use
    triage_system_prompt = get_system_prompt(state, "triage_agent", TRIAGE_SYSTEM_PROMPT) #type: ignore

    triage_prompt_template = ChatPromptTemplate.from_messages(
        [
            ("system", triage_system_prompt),
            ("human", TRIAGE_HUMAN_PROMPT),
        ]
    )

    llm_for_symptoms = GPT5_MINI.with_structured_output(SymptomList)
    triage_chain = triage_prompt_template | llm_for_symptoms

    logger.info("Triage agent is analyzing triage data to identify symptoms.")

    symptom_list = triage_chain.invoke({
        "app_name": state["app_name"],
        "app_summary": state["app_summary"],
        "problematic_pods": problematic_pods_str,
        "problematic_metrics": problematic_metrics_str,
        "slow_traces": slow_traces_str,
        "problematic_traces": problematic_traces_str
    })

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
