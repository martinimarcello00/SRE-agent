"""
SRE Agent - Automated Root Cause Analysis for Kubernetes

This script is used to launch SRE agent experiments.
It serves as the main entry point for the SRE Agent workflow.
For detailed documentation, see README.md
"""

import asyncio
import time
import json
from typing import Optional
from langsmith import Client
import os
from datetime import datetime

# Import the compiled parent graph
from graph import parent_graph

async def run_sre_agent(
    app_name: str,
    app_summary: str,
    target_namespace: str,
    trace_service_starting_point: str,
    trace_name: Optional[str] = None
) -> tuple[dict, float]:
    """Execute the complete SRE agent workflow.
    
    Args:
        app_name: Name of the application being diagnosed
        app_summary: Brief description of the application architecture
        target_namespace: Kubernetes namespace to investigate
        trace_service_starting_point: Service name to start trace analysis
        trace_name: Optional name for the execution trace
        
    Returns:
        Tuple of (result dictionary, execution time in seconds)
    """
    
    from graph import SreParentState

    initial_state = SreParentState(
        app_name=app_name,
        app_summary=app_summary,
        target_namespace=target_namespace,
        trace_service_starting_point=trace_service_starting_point,
        problematic_pods={},
        slow_traces={},
        problematic_metrics={},
        tasks_to_be_executed=[],
        symptoms=[],
        rca_tasks=[],
        rca_analyses_list=[],
        final_report={}
    )
    
    start_time = time.time()
    
    config = {"recursion_limit": 100}
    if trace_name:
        config["run_name"] = trace_name #type: ignore

    result = await parent_graph.ainvoke(initial_state, config) #type: ignore
    
    execution_time = time.time() - start_time
    
    return result, execution_time

def get_experiment_metrics(experiment_name: str) -> dict:
    """
    Get comprehensive metrics for a LangSmith experiment.
    
    Args:
        experiment_name: Name of the experiment to retrieve
    
    Returns:
        Dictionary with experiment ID, execution time, total tokens, and token breakdown by agent
    """
    langsmith_client = Client()
    
    # Get the experiment run - search by session name first
    runs = langsmith_client.list_runs(
    project_name=os.environ.get("LANGSMITH_PROJECT"),
    filter=f'eq(name, "{experiment_name}")',
    limit=1
    )
    
    run = next(iter(runs), None)
    
    if not run:
        return {"error": f"Experiment '{experiment_name}' not found"}
    
    # Calculate execution time
    execution_time = (run.end_time - run.start_time).total_seconds() if run.end_time else 0
    
    # Get all child runs
    child_runs = list(langsmith_client.list_runs(parent_run_id=run.id))
    
    # Aggregate token usage by agent name
    agent_stats = {}
    
    for agent_run in child_runs:
        agent_name = agent_run.name
        
        if agent_name not in agent_stats:
            agent_stats[agent_name] = {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "runs_count": 0
            }
        
        agent_stats[agent_name]["total_tokens"] += agent_run.total_tokens or 0
        agent_stats[agent_name]["input_tokens"] += (agent_run.input_tokens or 0)
        agent_stats[agent_name]["output_tokens"] += (agent_run.output_tokens or 0)
        agent_stats[agent_name]["cost"] += float((agent_run.completion_cost or 0.0))
        agent_stats[agent_name]["runs_count"] += 1
    
    # Build final metrics dictionary
    return {
        "run_id": str(run.id),
        "experiment_name": run.name,
        "status": run.status,
        "execution_time_seconds": execution_time,
        "total_tokens": run.total_tokens or 0,
        "total_cost": sum(s["cost"] for s in agent_stats.values()),
        "agent_stats": agent_stats
    }

def export_json_results(result: dict, experiment_name: str) -> dict:

    export = result

    export["experiment_name"] = experiment_name

    # Convert symptom pydantic objects to dict
    symptoms = []
    for s in result["symptoms"]:
        symptoms.append(s.model_dump())
    export["symptoms"] = symptoms

    # Convert rca_task pydantic objects to dict
    rca_tasks = []
    for t in result["rca_tasks"]:
        rca_tasks.append(t.model_dump())
    export["rca_tasks"] = rca_tasks

    export["stats"] = get_experiment_metrics(experiment_name)
    
    return export

async def main():
    """Main execution function."""
    # Get experiment name
    experiment_name = input("Enter experiment name (press Enter for default): ").strip()
    if not experiment_name:
        experiment_name = "SRE Agent Test"
    
    # Application configuration
    app_summary = """
        The application implements a hotel reservation service, built with Go and gRPC. 
        The initial project is extended in several ways, including adding back-end 
        in-memory and persistent databases, adding a recommender system for obtaining 
        hotel recommendations, and adding the functionality to place a hotel reservation.
    """
    target_namespace = "test-hotel-reservation"
    service_starting_point = "frontend"
    
    print(f"\n🚀 Starting SRE Agent: {experiment_name}")
    print(f"📦 Application: Hotel Reservation")
    print(f"🎯 Namespace: {target_namespace}")
    print(f"🔍 Starting service: {service_starting_point}\n")
    
    # Run the agent
    result, exec_time = await run_sre_agent(
        app_name="Hotel Reservation",
        app_summary=app_summary,
        target_namespace=target_namespace,
        trace_service_starting_point=service_starting_point,
        trace_name=experiment_name
    )

    # Display results
    print(f"\n✅ Analysis Complete!")
    print(f"⏱️  Execution time: {exec_time:.2f} seconds\n")
    
    final_report = result.get("final_report", {})
    if final_report:
        print("📋 Final Report:")
        print(f"  Root Cause: {final_report.get('root_cause', 'N/A')}")
        print(f"  Affected Resources: {', '.join(final_report.get('affected_resources', []))}")
        print(f"\n  Evidence Summary:\n  {final_report.get('evidence_summary', 'N/A')}")
    
    # Save results
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_experiment_name = experiment_name.replace(" ", "_")
    output_file = f"result-{date_str}-{safe_experiment_name}.json"

    enriched_result = export_json_results(result, experiment_name)

    with open(output_file, "w") as f:
        json.dump(enriched_result, f, indent=2, default=str)
    
    print(f"\n💾 Results saved to: {output_file}")
    
    return result


if __name__ == "__main__":
    asyncio.run(main())