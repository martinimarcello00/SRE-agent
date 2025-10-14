from dotenv import load_dotenv
from PrometheusAPI import PrometheusAPI
from logAPI import LogAPI
from mcp.server.fastmcp import FastMCP
import sys
import os
from pydantic import Field
from typing_extensions import Annotated
import logging

#TODO: Remove this and make a single package
# Add the parent directory to sys.path to import from graphs folder
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from graphs.datagraph import DataGraph

load_dotenv("../.env")

prometheus_api = None
datagraph = None
log_api = None

def get_prometheus_api():
    """Get or create the PrometheusAPI instance"""
    import os
    prometheus_url = os.environ.get("PROMETHEUS_SERVER_URL", "http://localhost:9090")
    namespace = os.environ.get("TARGET_NAMESPACE", "default")
    global prometheus_api
    if prometheus_api is None:
        prometheus_api = PrometheusAPI(prometheus_url, namespace)
    return prometheus_api

def get_datagraph():
    """Get or create the DataGraph instance"""
    import os
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "neo4j")
    global datagraph
    if datagraph is None:
        datagraph = DataGraph(neo4j_uri, neo4j_user, neo4j_password)
    return datagraph

def get_log_api():
    """Get or create the LogAPI instance"""
    import os
    namespace = os.environ.get("TARGET_NAMESPACE", "default")
    global log_api
    if log_api is None:
        log_api = LogAPI(namespace)
    return log_api

mcp = FastMCP("Cluster API MCP")

@mcp.tool(
        title="get_pod_metrics",
        description="Get all the instant prometheus metrics associated to a pod"
)
def get_pod_metrics(pod_name: str):
    """Get all the metrics associated to a pod"""
    api = get_prometheus_api()
    return api.get_pod_metrics(pod_name)

@mcp.tool(
    title="get_pods_from_service",
    description="Get all the pods associated to a service"
)
def get_pods_from_service(service_name: str):
    "Get the pods associated to a service"
    api = get_prometheus_api()
    return api.get_pods_from_service(service_name)

@mcp.tool(
    title="get_pods_and_services",
    description="Get the list of all pods and services in the kubernetes cluster"
)
def get_cluster_pods_and_services():
    """Get the list of all pods and services in the kubernetes cluster"""
    api = get_prometheus_api()
    pods = api.get_pods_list()
    services = api.get_services_list()
    return {
        "pods": pods,
        "services": services,
        "summary": f"Found {len(pods)} pods and {len(services)} services in the cluster"
    }

@mcp.tool(
        title="get_services_used_by",
        description="Return all the services that are used by the given service to complete its tasks"
)
def get_services_used_by(service: str):
    """Return all the services that are used by the given service to complete its tasks"""
    datagraph = get_datagraph()
    connected_services = datagraph.get_services_used_by(service)
    return ", ".join(connected_services)

@mcp.tool(
        title="get_dependencies",
        description="Retrieves all dependencies for a specified service from kubernetes cluster."
)
def get_dependencies(service: str):
    """Retrieves all dependencies for a specified service from kubernetes cluster."""
    datagraph = get_datagraph()
    return datagraph.get_dependencies(service)

@mcp.tool(
        title="get_pod_logs",
        description="Retrieve logs from a Kubernetes pod with optional filtering for important messages."
)
def get_pod_logs(
    pod_name: Annotated[str, Field(description="The name of the Kubernetes pod to retrieve logs from.")],
    tail: Annotated[int, Field(description="Number of recent log lines to retrieve.", ge=1)] = 100,
    important: Annotated[bool, Field(description="If True, filter logs to only include lines with ERROR, WARN, or CRITICAL keywords.")] = True,
) -> str:
    """Retrieves the last log entries of a pod"""
    log_api = get_log_api()
    return log_api.get_pod_logs(pod_name, tail, important)

if __name__ == "__main__":
    logging.info(f"Target namespace: {os.environ.get("TARGET_NAMESPACE", "default")}")
    mcp.run(transport="streamable-http")