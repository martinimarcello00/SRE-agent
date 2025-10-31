"""Supervisor Agent - Synthesizes RCA findings into final diagnosis."""
import json
from langgraph.graph import START, END, StateGraph

from models import SupervisorAgentState, FinalReport
from prompts import supervisor_prompt_template
from config import GPT5_MINI
import logging


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

    logging.info("Supervisor Agent is synthesizing findings to produce the final RCA diagnosis.")
    
    if not rca_analyses and not symptoms:
        return {
            "final_report": FinalReport(
                root_cause="No analysis data available",
                affected_resources=[],
                evidence_summary="No symptoms or RCA analysis provided",
                investigation_summary="Investigation incomplete - insufficient data"
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
        for i, analysis in enumerate(rca_analyses, 1):
            # Create a copy excluding message_history for the prompt
            analysis_for_prompt = {k: v for k, v in analysis.items() if k != 'message_history'}
            human_parts.extend([
                f"## Investigation {i}\n\n",
                f"```json\n{json.dumps(analysis_for_prompt, indent=2)}\n```\n\n"
            ])
        human_parts.append("---\n\n")
    
    human_input = "".join(human_parts)
    human_input += "\n\nBased on all the above information, provide a comprehensive root cause diagnosis."
    
    # Create and invoke chain
    llm_for_final_report = GPT5_MINI.with_structured_output(FinalReport)
    supervisor_chain = supervisor_prompt_template | llm_for_final_report
    final_report = supervisor_chain.invoke({"human_input": human_input})
    
    return {"final_report": final_report.model_dump()}  # type: ignore


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
