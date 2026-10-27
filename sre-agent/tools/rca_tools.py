"""RCA-specific tools including submission tool."""
from typing import Annotated
from langgraph.types import Command
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId


@tool
def submit_final_diagnosis(
    diagnosis: str,
    reasoning: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command:
    """Submit the final diagnosis when investigation is complete.
    
    Args:
        diagnosis: The issue you have identified (without fixing it)
        reasoning: Your reasoning and thought process behind the diagnosis (keep it concise)
        tool_call_id: Injected tool call ID from LangChain
    
    Returns:
        Command to update state and end workflow
    """
    final_response = {
        "diagnosis": diagnosis,
        "reasoning": reasoning
    }
    
    return Command(
        update={
            "rca_output": final_response,
            "messages": [
                ToolMessage(
                    content="Final diagnosis submitted successfully. Investigation complete.",
                    tool_call_id=tool_call_id
                )
            ]
        },
        goto="format-output"  # End the loop cycle
    )
