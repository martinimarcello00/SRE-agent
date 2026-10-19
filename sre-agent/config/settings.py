"""Configuration and environment settings for SRE Agent."""
import logging
import os
import threading

import openai
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from typing import Any, Mapping

from utils.openai_usage import get_today_model_usage

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
    """ChatOpenAI that switches to another key and retries once when the quota is exhausted mid-run."""

    # note: only the sync path (_generate) is covered, all agents use .invoke(); override _agenerate if they move to .ainvoke()
    def _generate(self, *args: Any, **kwargs: Any):
        key_used, index_used = self.openai_api_key, _active_key_index
        try:
            return super()._generate(*args, **kwargs)
        except openai.RateLimitError as exc:
            if exc.code != "insufficient_quota":
                raise
            select_openai_key(index_used)
            # Another parallel worker may have already switched: retry if the key changed
            if self.openai_api_key == key_used:
                raise
            logger.warning("OpenAI quota exhausted mid-run: retrying %s with another key.", self.model_name)
            return super()._generate(*args, **kwargs)


# LLM Configuration
GPT5_MINI = ChatOpenAIWithFallback(model="gpt-5-mini")

GPT5_1 = ChatOpenAIWithFallback(model="gpt-5.1")

_key_lock = threading.Lock()
_exhausted_keys: set[int] = set()  # pairs that got insufficient_quota: out of credit, which does not reset at 00:00 UTC


def select_openai_key(exhausted_index: int | None = None) -> bool:
    """Activate the first key pair of OPENAI_KEY_CHAIN, in priority order, under its daily gpt-5-mini token limit.

    The usage is read with each pair's admin key (both keys required) and restarts at 00:00 UTC, so a pair over its
    limit becomes usable again the next day.
    exhausted_index: the pair that got insufficient_quota mid-run, skipped for the rest of the execution (a parallel
    worker hitting the same error just finds the next pair already active).
    Returns False if no pair qualifies (the active one is left as it is).
    """
    global _active_key_index
    with _key_lock:
        if exhausted_index is not None:
            _exhausted_keys.add(exhausted_index)
        for index, (name, key, admin_key, limit) in enumerate(OPENAI_KEY_CHAIN):
            if not key or not admin_key or index in _exhausted_keys:
                continue
            used = get_today_model_usage(model_name="gpt-5-mini", admin_api_key=admin_key)["total_tokens"]
            logger.info("OpenAI key pair %d/%d (%s): %d/%d gpt-5-mini tokens used today.",
                        index + 1, len(OPENAI_KEY_CHAIN), name, used, limit)
            if used < limit:
                break
        else:
            return False
        if index == _active_key_index:
            return True

        os.environ["OPENAI_API_KEY"] = key
        os.environ["OPENAI_ADMIN_API_KEY"] = admin_key
        # Swap the clients in place (agents hold references to these objects),
        # never leaving them None while parallel workers may be calling them.
        for llm in (GPT5_MINI, GPT5_1):
            fresh = ChatOpenAI(model=llm.model_name, api_key=SecretStr(key))
            llm.root_client, llm.root_async_client = fresh.root_client, fresh.root_async_client
            llm.client, llm.async_client = fresh.client, fresh.async_client
            llm.openai_api_key = fresh.openai_api_key
        _active_key_index = index
        logger.warning("Switched to OpenAI key pair %d/%d (%s).", index + 1, len(OPENAI_KEY_CHAIN), name)
        return True

# Investigation Budget
MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", 8))

# Triage budget (agentic_triage architecture only), defaults to the RCA budget
TRIAGE_MAX_TOOL_CALLS = int(os.environ.get("TRIAGE_MAX_TOOL_CALLS", MAX_TOOL_CALLS))

# RCA tasks per iteration
RCA_TASKS_PER_ITERATION = int(os.environ.get("RCA_TASKS_PER_ITERATION", 3))

# Trace service starting point for investigations
TRACE_SERVICE_STARTING_POINT = os.environ.get("TRACE_SERVICE_STARTING_POINT", "frontend")

# Daily OpenAI token limit (gpt-5-mini) of the primary and first fallback key, and of the second fallback key
MAX_DAILY_OPENAI_TOKEN_LIMIT = int(os.environ.get("MAX_DAILY_OPENAI_TOKEN_LIMIT", 2_250_000))
MAX_DAILY_OPENAI_TOKEN_LIMIT_FALLBACK_2 = int(os.environ.get("MAX_DAILY_OPENAI_TOKEN_LIMIT_FALLBACK_2", 3_000_000))

# OpenAI key pairs in priority order: (name, LLM key, admin key for usage tracking, daily token limit).
# Read once: switching overwrites OPENAI_API_KEY / OPENAI_ADMIN_API_KEY.
OPENAI_KEY_CHAIN = [
    (f"OPENAI_API_KEY{suffix}", os.environ.get(f"OPENAI_API_KEY{suffix}"), os.environ.get(f"OPENAI_ADMIN_API_KEY{suffix}"), limit)
    for suffix, limit in (("", MAX_DAILY_OPENAI_TOKEN_LIMIT),
                          ("_FALLBACK", MAX_DAILY_OPENAI_TOKEN_LIMIT),
                          ("_FALLBACK_2", MAX_DAILY_OPENAI_TOKEN_LIMIT_FALLBACK_2))
]
_active_key_index = 0

AIOPSLAB_DIR = os.environ.get("AIOPSLAB_DIR")

def apply_config_overrides(overrides: Mapping[str, Any]) -> None:
    """Update runtime knobs (called before launching each agent run)."""
    global MAX_TOOL_CALLS, TRIAGE_MAX_TOOL_CALLS, RCA_TASKS_PER_ITERATION, TRACE_SERVICE_STARTING_POINT

    if "MAX_TOOL_CALLS" in overrides:
        os.environ["MAX_TOOL_CALLS"] = str(overrides["MAX_TOOL_CALLS"])
    if "TRIAGE_MAX_TOOL_CALLS" in overrides:
        os.environ["TRIAGE_MAX_TOOL_CALLS"] = str(overrides["TRIAGE_MAX_TOOL_CALLS"])
    if "RCA_TASKS_PER_ITERATION" in overrides:
        os.environ["RCA_TASKS_PER_ITERATION"] = str(overrides["RCA_TASKS_PER_ITERATION"])
    if "TRACE_SERVICE_STARTING_POINT" in overrides:
        os.environ["TRACE_SERVICE_STARTING_POINT"] = str(overrides["TRACE_SERVICE_STARTING_POINT"])

    MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", MAX_TOOL_CALLS))
    TRIAGE_MAX_TOOL_CALLS = int(os.environ.get("TRIAGE_MAX_TOOL_CALLS", MAX_TOOL_CALLS))
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
