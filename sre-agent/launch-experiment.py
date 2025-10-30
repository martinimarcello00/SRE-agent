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
    from langchain_core.runnables import RunnableConfig

    initial_state = SreParentState(
        app_name=app_name,
        app_summary=app_summary,
        target_namespace=target_namespace,
        trace_service_starting_point=trace_service_starting_point,
        problematic_pods={},
        problematic_traces={},
        slow_traces={},
        problematic_metrics={},
        symptoms=[],
        rca_tasks=[],
        rca_analyses_list=[],
        final_report={}
    )
    
    start_time = time.time()
    
    config = RunnableConfig(recursion_limit=100)
    if trace_name:
        config.run_name = trace_name #type: ignore

    result = await parent_graph.ainvoke(initial_state, config)
    
    execution_time = time.time() - start_time
    
    return result, execution_time


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
    output_file = "sre_agent_result.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"\n💾 Results saved to: {output_file}")
    
    return result


if __name__ == "__main__":
    asyncio.run(main())
