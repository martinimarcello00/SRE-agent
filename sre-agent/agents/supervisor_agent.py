"""Supervisor Agent - Synthesizes RCA findings into final diagnosis."""
import json
from langgraph.graph import START, END, StateGraph

from models import SupervisorAgentState, SupervisorDecision, FinalReport
from prompts import supervisor_prompt_template
from config import GPT5_MINI
import logging

logger = logging.getLogger(__name__)

def supervisor_agent(state: SupervisorAgentState) -> dict:
    """Analyze all RCA findings and produce final root cause diagnosis.
    
    Args:
        state: Current supervisor agent state with symptoms and RCA analyses
        
    Returns:
        Dictionary with final report
    """
    symptoms = state.get("symptoms", [])
    rca_analyses = state.get("rca_analyses_list", [])
    app_summary = state.get("app_summary", "")
    app_name = state.get("app_name", "")
    rca_tasks = state.get("rca_tasks", [])

    logger.info("Supervisor Agent is synthesizing findings to produce the final RCA diagnosis.")
    
    if not rca_analyses and not symptoms:
        return {
            "final_report": FinalReport(
                root_cause="No analysis data available",
                affected_resources=[],
                evidence_summary="No symptoms or RCA analysis provided",
                investigation_summary="Investigation incomplete - insufficient data",
                detection=False,
                localization=None
            ).model_dump()
        }
    
    # Build human prompt with all investigation data in markdown format
    human_parts = [
        "# Incident Analysis Summary\n\n",
        f"- **Application**: {app_name}\n",
        f"- **Summary**: {app_summary}\n\n",
        "---\n\n"
    ]
    
    # Add symptoms
    if symptoms:
        human_parts.append("# Symptoms Identified\n\n")
        for i, symptom in enumerate(symptoms, 1):
            human_parts.extend([
                f"## Symptom {i}\n\n",
                f"**Type**: {symptom.potential_symptom}\n\n",
                f"**Resource**: `{symptom.affected_resource}` ({symptom.resource_type})\n\n",
                f"**Evidence**: {symptom.evidence}\n\n"
            ])
        human_parts.append("---\n\n")
    
    # Add RCA analysis findings
    if rca_analyses:
        human_parts.append("# RCA Investigation Findings\n\n")
        for analysis in rca_analyses:
            # Create a copy excluding message_history for the prompt
            analysis_for_prompt = {k: v for k, v in analysis.items() if k != 'message_history'}
            human_parts.extend([
                f"## Investigation (priority #{analysis["task"]["priority"]})\n\n",
                f"```json\n{json.dumps(analysis_for_prompt, indent=2)}\n```\n\n"
            ])
        human_parts.append("---\n\n")
    
    # Add pending RCA tasks
    if rca_tasks:
        human_parts.append("# Pending RCA Tasks\nThese are the tasks planned but NOT yet completed:\n\n")
        pending_tasks = [task for task in rca_tasks if task.status in ("pending", "in_progress")]
        if not pending_tasks:
            human_parts.append("All planned RCA tasks have been completed.\n")
        else:
            for task in pending_tasks:
                human_parts.extend([
                    f"- **Priority #{task.priority}**: {task.investigation_goal}",
                    f"  - **Target**: {task.resource_type} `{task.target_resource}`",
                    f"  - **Suggested Tools**: {', '.join(task.suggested_tools)}\n"
                ])
    
    human_input = "".join(human_parts)
    human_input += "\n\nBased on all the above information, provide a comprehensive root cause diagnosis."

    # Create and invoke chain
    llm_with_decision = GPT5_MINI.with_structured_output(SupervisorDecision)
    supervisor_chain = supervisor_prompt_template | llm_with_decision
    decision = supervisor_chain.invoke({"human_input": human_input})

    # Evaluate the decision
    if decision.final_report: # type: ignore
        logger.info("Supervisor Decision: Investigation COMPLETE. Generating final report.")
        # Return final report and clear tasks list
        return {
            "final_report": decision.final_report.model_dump(), # type: ignore
            "tasks_to_be_executed": []
        }
    elif decision.tasks_to_be_executed: # type: ignore
        logger.info(f"Supervisor Decision: Investigation INCOMPLETE. Requesting tasks: {decision.tasks_to_be_executed}") # type: ignore
        # Return tasks to be executed and clear final report
        return {
            "final_report": {}, # Ensure final_report is empty
            "tasks_to_be_executed": decision.tasks_to_be_executed # type: ignore
        }
    else:
        # Fallback: If LLM returns neither, assume investigation is done
        logger.warning("Supervisor Warning: LLM returned no decision. Defaulting to incomplete report.")
        final_report = FinalReport(
            root_cause="Investigation Inconclusive",
            affected_resources=[],
            evidence_summary="Supervisor agent failed to make a clear decision.",
            investigation_summary="Incomplete",
            detection=False,
            localization=None
        )
        return {"final_report": final_report.model_dump(), "tasks_to_be_executed": []}

def build_supervisor_graph():
    """Build and compile the supervisor agent graph.
    
    Returns:
        Compiled supervisor agent graph
    """
    builder = StateGraph(SupervisorAgentState)
    builder.add_node("supervisor", supervisor_agent)
    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", END)
    
    return builder.compile().with_config(run_name="Supervisor Agent")


# Export the compiled graph
supervisor_agent_graph = build_supervisor_graph()
