from dotenv import load_dotenv
from PrometheusAPI import PrometheusAPI
from mcp.server.fastmcp import FastMCP

load_dotenv("../.env")

prometheus_api = None

def get_prometheus_api():
    """Get or create the PrometheusAPI instance"""
    import os
    prometheus_url = os.environ.get("PROMETHEUS_SERVER_URL", "http://localhost:9090")
    namespace = os.environ.get("TARGET_NAMESPACE", "default")
    global prometheus_api
    if prometheus_api is None:
        prometheus_api = PrometheusAPI(prometheus_url, namespace)
    return prometheus_api

mcp = FastMCP("Metrics API MCP")

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

@mcp.resource(
    uri="cluster://pods-and-services",
    name="Cluster Pods and Services",
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

if __name__ == "__main__":
    mcp.run(transport="streamable-http")