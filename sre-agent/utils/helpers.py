"""Utility helper functions for SRE Agent."""
from langchain_core.messages import AIMessage
from collections import Counter
from typing import Optional
import logging


logger = logging.getLogger(__name__)


def get_insights_str(state) -> str:
    """Return a formatted string of insights gathered during exploration.
    
    Args:
        state: Agent state containing 'insights' list
        
    Returns:
        Formatted string of insights
    """
    if len(state["insights"]) > 0:
        return "\n- ".join([""] + state["insights"])
    else:
        return "No insights yet"


def get_prev_steps_str(state) -> str:
    """Return a formatted string of previous steps performed during exploration.
    
    Args:
        state: Agent state containing 'prev_steps' list
        
    Returns:
        Formatted string of previous steps
    """
    if len(state["prev_steps"]) > 0:
        return "\n- ".join([""] + state["prev_steps"])
    else:
        return "No previous steps yet"


def count_tool_calls(messages) -> dict:
    """Count tool call occurrences by tool name from state messages.
    
    Args:
        messages: List of messages from agent state
        
    Returns:
        Dictionary mapping tool names to call counts
    """
    tool_calls = []
    for msg in messages:
        if isinstance(msg, AIMessage):
            if hasattr(msg, 'additional_kwargs'):
                if "tool_calls" in msg.additional_kwargs:
                    for call in msg.additional_kwargs['tool_calls']:
                        if "function" in call:
                            if "name" in call["function"]:
                                tool_calls.append(call["function"]["name"])

    counts = Counter(tool_calls)
    return dict(counts)


def count_non_submission_tool_calls(messages) -> int:
    """Count tool calls excluding submit_final_diagnosis.
    
    Args:
        messages: List of messages from agent state
        
    Returns:
        Number of tool calls (excluding submission tool)
    """
    tool_call_count = 0
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, 'additional_kwargs'):
            if "tool_calls" in msg.additional_kwargs:
                for call in msg.additional_kwargs.get('tool_calls', []):
                    if "function" in call:
                        tool_name = call.get("function", {}).get("name", "")
                        if tool_name != "submit_final_diagnosis":
                            tool_call_count += 1
    return tool_call_count


def get_system_prompt(state: dict, agent_name: str, default_prompt: str, state_key: Optional[str] = "prompts_config") -> str:
    """Determine which system prompt to use (default or custom from config).
    
    Args:
        state: Agent state containing potential 'prompts_config'
        agent_name: Key name for the agent in the config (e.g., 'triage_agent')
        default_prompt: The default system prompt string to use if no override exists
        state_key (Optional[str], optional): The key in `state` where prompt configurations are stored. Defaults to "prompts_config".
        
    Returns:
        The selected system prompt string
    """
    system_prompt = default_prompt
    prompt_configs = state.get(state_key, {})
    
    if isinstance(prompt_configs, dict):
        custom_prompt = prompt_configs.get(agent_name)
        if custom_prompt:
            system_prompt = custom_prompt
            logger.info(f"Using custom system prompt for {agent_name}.")
        else:
            logger.info(f"No custom system prompt found for {agent_name}; using default.")
    
    return system_prompt
