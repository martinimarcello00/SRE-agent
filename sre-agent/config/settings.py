"""Configuration and environment settings for SRE Agent."""
import logging
import os
import threading

import openai
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from typing import Any, Mapping

# Get the path to the root directory of the repository
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

# Load environment variables from .env file in the root directory
load_dotenv(os.path.join(root_dir, '.env'), verbose=True)

# Add MCP-server to path
import sys
mcp_server_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../MCP-server'))
sys.path.insert(0, mcp_server_path)

logger = logging.getLogger(__name__)


class ChatOpenAIWithFallback(ChatOpenAI):
    """ChatOpenAI that switches to the fallback key and retries once when the quota is exhausted mid-run."""

    # note: only the sync path (_generate) is covered, all agents use .invoke(); override _agenerate if they move to .ainvoke()
    def _generate(self, *args: Any, **kwargs: Any):
        key_used = self.openai_api_key
        try:
            return super()._generate(*args, **kwargs)
        except openai.RateLimitError as exc:
            if exc.code != "insufficient_quota":
                raise
            use_fallback_openai_key()
            # Another parallel worker may have already switched: retry if the key changed
            if self.openai_api_key == key_used:
                raise
            logger.warning("OpenAI quota exhausted mid-run: retrying %s with the fallback key.", self.model_name)
            return super()._generate(*args, **kwargs)


# LLM Configuration
GPT5_MINI = ChatOpenAIWithFallback(model="gpt-5-mini")

GPT5_1 = ChatOpenAIWithFallback(model="gpt-5.1")

_fallback_lock = threading.Lock()


def use_fallback_openai_key() -> bool:
    """Switch LLM calls to OPENAI_API_KEY_FALLBACK and usage tracking to OPENAI_ADMIN_API_KEY_FALLBACK.

    Returns False if the fallback keys are not configured or already in use.
    """
    key = os.environ.get("OPENAI_API_KEY_FALLBACK")
    admin_key = os.environ.get("OPENAI_ADMIN_API_KEY_FALLBACK")
    with _fallback_lock:
        if not key or not admin_key or os.environ.get("OPENAI_API_KEY") == key:
            return False

        os.environ["OPENAI_API_KEY"] = key
        os.environ["OPENAI_ADMIN_API_KEY"] = admin_key
        # Swap the clients in place (agents hold references to these objects),
        # never leaving them None while parallel workers may be calling them.
        for llm in (GPT5_MINI, GPT5_1):
            fresh = ChatOpenAI(model=llm.model_name, api_key=SecretStr(key))
            llm.root_client, llm.root_async_client = fresh.root_client, fresh.root_async_client
            llm.client, llm.async_client = fresh.client, fresh.async_client
            llm.openai_api_key = fresh.openai_api_key
        return True

# Investigation Budget
MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", 8))

# RCA tasks per iteration
RCA_TASKS_PER_ITERATION = int(os.environ.get("RCA_TASKS_PER_ITERATION", 3))

# Trace service starting point for investigations
TRACE_SERVICE_STARTING_POINT = os.environ.get("TRACE_SERVICE_STARTING_POINT", "frontend")

# Daily OpenAI token limit
MAX_DAILY_OPENAI_TOKEN_LIMIT = int(os.environ.get("MAX_DAILY_OPENAI_TOKEN_LIMIT", 2_250_000))

AIOPSLAB_DIR = os.environ.get("AIOPSLAB_DIR")

def apply_config_overrides(overrides: Mapping[str, Any]) -> None:
    """Update runtime knobs (called before launching each agent run)."""
    global MAX_TOOL_CALLS, RCA_TASKS_PER_ITERATION, TRACE_SERVICE_STARTING_POINT

    if "MAX_TOOL_CALLS" in overrides:
        os.environ["MAX_TOOL_CALLS"] = str(overrides["MAX_TOOL_CALLS"])
    if "RCA_TASKS_PER_ITERATION" in overrides:
        os.environ["RCA_TASKS_PER_ITERATION"] = str(overrides["RCA_TASKS_PER_ITERATION"])
    if "TRACE_SERVICE_STARTING_POINT" in overrides:
        os.environ["TRACE_SERVICE_STARTING_POINT"] = str(overrides["TRACE_SERVICE_STARTING_POINT"])

    MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", MAX_TOOL_CALLS))
    RCA_TASKS_PER_ITERATION = int(os.environ.get("RCA_TASKS_PER_ITERATION", RCA_TASKS_PER_ITERATION))
    TRACE_SERVICE_STARTING_POINT = os.environ.get("TRACE_SERVICE_STARTING_POINT", TRACE_SERVICE_STARTING_POINT)

# MCP Server Configuration - Stdio-based (automatically spawned by client)
_MCP_SERVER_PATH = os.path.join(root_dir, "MCP-server", "mcp_server.py")
_MCP_SERVER_DIR = os.path.join(root_dir, "MCP-server")

def get_mcp_config() -> dict:
    """Get MCP configuration with current environment variables.
    
    This function builds the MCP config dynamically, ensuring all relevant
    environment variables are passed to the MCP server subprocess.
    """
    # Collect environment variables to pass to MCP server
    env_vars = {
        "ALLOW_ONLY_NON_DESTRUCTIVE_TOOLS": "true"
    }
    
    # Add all observability-related environment variables
    env_keys_to_pass = [
        "TARGET_NAMESPACE",
        "PROMETHEUS_SERVER_URL",
        "JAEGER_URL",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "TRACE_SERVICE_STARTING_POINT",
    ]
    
    for key in env_keys_to_pass:
        value = os.environ.get(key)
        if value is not None:
            env_vars[key] = value
    
    return {
        "kubernetes": {
            "command": "npx",
            "args": ["mcp-server-kubernetes@4.1.7"],  # pinned: hide_flagd relies on its tool argument names
            "transport": "stdio",
            "env": {
                "ALLOW_ONLY_NON_DESTRUCTIVE_TOOLS": "true"
            }
        },
        "cluster_api": {
            "command": "poetry",
            "args": ["run", "python", _MCP_SERVER_PATH],
            "transport": "stdio",
            "env": env_vars
        }
    }

# Initial MCP config - will be updated when creating client
MCP_CONFIG = get_mcp_config()

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
