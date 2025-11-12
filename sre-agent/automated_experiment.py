
from experiments_runner import (
    setup_cluster_and_aiopslab,
    cleanup_cluster,
    load_fault_scenarios,
    start_mcp_server,
    cleanup_mcp_server,
)
import time
import logging
from typing import Optional
from datetime import datetime
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import sys
import asyncio

# Configure logging for the SRE Agent script
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)


async def run_experiment(
    app_name: str,
    fault_name: str,
    app_summary: str,
    target_namespace: str,
    trace_service_starting_point: str,
    wait_time_before_running_agent: int,
    agent_configuration_name: str,
    run_sre_agent_func,
    export_json_results_func,
    trace_name: Optional[str] = None,
):
    # Functions are passed as parameters to avoid importing before MCP server starts
    
    logging.info(f"Waiting {wait_time_before_running_agent} before running the SRE agent")
    time.sleep(wait_time_before_running_agent)

    experiment_name = f"{agent_configuration_name} - {app_name} - {fault_name}"
    logging.info(f"Launching experiment: {experiment_name}")

    result, exec_time = await run_sre_agent_func(
        app_name=app_name,
        fault_name=fault_name,
        app_summary=app_summary,
        target_namespace=target_namespace,
        trace_service_starting_point=trace_service_starting_point,
        trace_name=experiment_name,
        agent_configuration_name=agent_configuration_name
    )

    # Save results
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_experiment_name = experiment_name.replace(" ", "-")
    output_file = f"{date_str}_{safe_experiment_name}.json"

    enriched_result = export_json_results_func(
        result=result,
        experiment_name=experiment_name,
        exec_time = exec_time,
        fault_name=fault_name,
        application_name=app_name,
        target_namespace=target_namespace,
        trace_service_starting_point=trace_service_starting_point,
        agent_configuration_name=agent_configuration_name
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
    print("\n✅ Experiment completed")

def main():

    load_dotenv(dotenv_path="../.env")

    AIOPSLAB_DIR = "/home/vm-kubernetes/AIOpsLab"

    fault_scenarios = load_fault_scenarios()

    for i in fault_scenarios:

        print("="*60)
        print(f"Scenario: {i['scenario']}\nFault: {i['fault_type']}")
        print("="*60)

        try:
            # Step 1: Setup cluster and AIOpsLab
            print("="*60)
            print(f"STEP 1: Setup kind and aiopslab")
            print("="*60)
            
            success = setup_cluster_and_aiopslab(
                problem_id=i["aiopslab_command"],
                aiopslab_dir=AIOPSLAB_DIR
            )
            
            if not success:
                print("\n❌ Setup failed")
                sys.exit(1)

            # Start MCP server (silence output when ready)
            print("\n" + "="*60)
            print("Starting MCP Server")
            print("="*60)
            try:
                start_mcp_server(ready_timeout=90, silence_on_ready=True, print_output=True)
            except Exception as mcp_err:
                print(f"\n❌ MCP Server failed to start: {mcp_err}")
                cleanup_cluster()
                sys.exit(1)
            
            # Import AFTER MCP server is running, before starting any event loop
            from launch_experiment import run_sre_agent, export_json_results
            
            # Step 2: Run your experiment (new event loop for async execution)
            print("\n" + "="*60)
            print("STEP 2: Run SRE Agent")
            print("="*60)

            asyncio.run(
                run_experiment(
                    fault_name=i["fault_type"],
                    app_name=i["app_name"],
                    app_summary=i["app_summary"],
                    target_namespace=i["target_namespace"],
                    trace_service_starting_point=i["service_starting_point"],
                    wait_time_before_running_agent=i["wait_before_launch_agent"],
                    agent_configuration_name="Plain ReAct",
                    run_sre_agent_func=run_sre_agent,
                    export_json_results_func=export_json_results
                )
            )
            
            # Step 3: Cleanup
            print("\n" + "="*60)
            print("STEP 3: Cleanup")
            print("="*60)
            
            # Cleanup MCP server first then cluster
            cleanup_mcp_server()
            cleanup_cluster()
            
            print("\n✅ All done!")
            
        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user")
            cleanup_mcp_server()
            cleanup_cluster()
            sys.exit(130)
        except Exception as e:
            print(f"\n\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            cleanup_mcp_server()
            cleanup_cluster()
            sys.exit(1)


if __name__ == "__main__":
    main()