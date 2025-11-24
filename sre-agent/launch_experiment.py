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
import logging
from dotenv import load_dotenv
from pathlib import Path

# Configure logging for the SRE Agent script
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)

# Import the compiled parent graph
from graph import parent_graph

async def run_sre_agent(
    app_name: str,
    fault_name: str,
    app_summary: str,
    target_namespace: str,
    trace_service_starting_point: str,
    trace_name: Optional[str] = None,
    agent_configuration_name: Optional[str] = None,
    agent_id: Optional[str] = None
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
        problematic_traces={},
        problematic_metrics={},
        tasks_to_be_executed=[],
        symptoms=[],
        rca_tasks=[],
        rca_analyses_list=[],
        final_report={}
    )

    if not agent_configuration_name:
        agent_configuration_name = "Default"

    if not agent_id:
        agent_id = "Z"
    
    start_time = time.time()

    config = {
        "recursion_limit": 100,
        "metadata": {
            "app_name": app_name,
            "namespace": target_namespace,
            "starting_service": trace_service_starting_point,
            "experiment_name": trace_name or app_name,
            "fault_name" : fault_name,
            "agent_configuration": agent_configuration_name,
            "agent_id" : agent_id,
            "parallel_rca_tasks": os.environ.get("RCA_TASKS_PER_ITERATION","Unknown"),
            "max_tool_calls": os.environ.get("MAX_TOOL_CALLS","Unknown")
        }
    }
    if trace_name:
        config["run_name"] = trace_name  # type: ignore

    result = await parent_graph.ainvoke(initial_state, config) #type: ignore
    
    execution_time = time.time() - start_time
    
    return result, execution_time

def get_experiment_metrics(experiment_name: str, exec_time: float | int) -> dict:
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
    execution_time = (run.end_time - run.start_time).total_seconds() if run.end_time else exec_time
    
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
    
    run_url = getattr(run, "url", None)

    # Build final metrics dictionary
    return {
        "run_id": str(run.id),
        "experiment_name": run.name,
        "status": run.status,
        "execution_time_seconds": execution_time,
        "total_tokens": run.total_tokens or 0,
        "total_cost": sum(s["cost"] for s in agent_stats.values()),
        "langsmith_url": run_url,
        "agent_stats": agent_stats
    }

def export_json_results(
        result: dict,
        experiment_name: str,
        exec_time: float | int,
        fault_name: str,
        application_name: str,
        target_namespace: str,
        trace_service_starting_point: str,
        agent_configuration_name: Optional[str] = None,
        agent_id: Optional[str] = None
        ) -> dict:

    export = result

    export["experiment_name"] = experiment_name

    if agent_id:
        export["agent_id"] = agent_id
    
    if agent_configuration_name:
        export["agent_configuration_name"] = agent_configuration_name

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

    export["stats"] = get_experiment_metrics(experiment_name, exec_time)

    testbed = {}
    testbed["application_name"] = application_name,
    testbed["fault_name"] = fault_name
    testbed["target_namespace"] = target_namespace
    testbed["trace_service_starting_point"] = trace_service_starting_point
    # Add divide and conquer parameter
    testbed["rca_tasks_per_iteration"] = os.environ.get("RCA_TASKS_PER_ITERATION", "")
    # Add tool calls budgeting parameter
    testbed["max_tool_calls"] = os.environ.get("MAX_TOOL_CALLS", "")

    export["testbed"] = testbed
    
    return export

async def main():

    load_dotenv(dotenv_path="../.env")

    # Get experiment name
    experiment_name = input("Enter experiment name (press Enter for default): ").strip()
    if not experiment_name:
        experiment_name = "SRE Agent Test"

    # Prompt for fault name (AIOpsLab experiment name)
    fault_name = ""
    while not fault_name:
        fault_name = input("Enter fault name (AIOpsLab experiment name): ").strip()

    # Prompt for agent id (AIOpsLab experiment name)
    agent_id = ""
    while not agent_id:
        agent_id = input("Enter agent configuration ID: ").strip()
    
    # Application configuration
    app_summary = """
        The application implements a hotel reservation service, built with Go and gRPC. 
        The initial project is extended in several ways, including adding back-end 
        in-memory and persistent databases, adding a recommender system for obtaining 
        hotel recommendations, and adding the functionality to place a hotel reservation.
    """
    target_namespace = "test-hotel-reservation"
    service_starting_point = "frontend"
    app_name = "Hotel Reservation"
    
    print(f"\n🚀 Starting SRE Agent: {experiment_name}")
    print(f"📦 Application: {app_name}")
    print(f"🎯 Namespace: {target_namespace}")
    print(f"🔍 Starting service: {service_starting_point}\n")
    
    # Run the agent
    result, exec_time = await run_sre_agent(
        app_name=app_name,
        fault_name=fault_name,
        app_summary=app_summary,
        target_namespace=target_namespace,
        trace_service_starting_point=service_starting_point,
        trace_name=experiment_name,
        agent_configuration_name="Plain ReAct",
        agent_id=agent_id
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
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_experiment_name = experiment_name.replace(" ", "-")
    output_file = f"{date_str}_{safe_experiment_name}.json"

    enriched_result = export_json_results(
        result=result,
        experiment_name=experiment_name,
        exec_time = exec_time,
        fault_name=fault_name,
        application_name=app_name,
        target_namespace=target_namespace,
        trace_service_starting_point=service_starting_point,
        agent_id=agent_id
    )

    output_dir = os.environ.get("RESULTS_PATH", "results")
    output_dir_path = Path(output_dir)
    if not output_dir_path.is_absolute():
        output_dir_path = Path.cwd() / output_dir_path
    output_dir_path.mkdir(parents=True, exist_ok=True)
    output_file_path = output_dir_path / output_file

    with open(output_file_path, "w") as f:
        json.dump(enriched_result, f, indent=2, default=str)
    
    print(f"\n💾 Results saved to: {output_file_path}")
    
    return result

if __name__ == "__main__":
    asyncio.run(main())