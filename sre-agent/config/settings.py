"""Configuration and environment settings for SRE Agent."""
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# Get the path to the root directory of the repository
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

# Load environment variables from .env file in the root directory
load_dotenv(os.path.join(root_dir, '.env'), verbose=True)

# Add MCP-server to path
import sys
mcp_server_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../MCP-server'))
sys.path.insert(0, mcp_server_path)

# LLM Configuration
GPT5_MINI = ChatOpenAI(model="gpt-5-mini")

# Investigation Budget
MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", 8))

# RCA tasks per iteration
RCA_TASKS_PER_ITERATION = int(os.environ.get("RCA_TASKS_PER_ITERATION", 3))

# MCP Server Configuration
MCP_CONFIG = {
    "kubernetes": {
        "command": "npx",
        "args": ["mcp-server-kubernetes"],
        "transport": "stdio",
        "env": {
            "ALLOW_ONLY_NON_DESTRUCTIVE_TOOLS": "true"
        }
    },
    "cluster_api": {
        "url": "http://localhost:8000/mcp",
        "transport": "streamable_http"
    }
}

# Tool Configuration
K8S_TOOLS_ALLOWED = [
    "kubectl_get", 
    "kubectl_describe", 
    "explain_resource", 
    "list_api_resources", 
    "ping"
]

CUSTOM_TOOLS_ALLOWED = [
    "get_metrics", 
    "get_metrics_range", 
    "get_pods_from_service", 
    "get_cluster_pods_and_services", 
    "get_services_used_by", 
    "get_dependencies", 
    "get_logs", 
    "get_traces", 
    "get_trace"
]

TOOLS_ALLOWED = K8S_TOOLS_ALLOWED + CUSTOM_TOOLS_ALLOWED
