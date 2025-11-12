
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

logger = logging.getLogger(__name__)

def get_experiment_dir_path(dir_name: str, experiment_path: Optional[str] = None):

    load_dotenv(dotenv_path="../.env")

    if experiment_path:
        output_dir = experiment_path
    else:
        output_dir = os.environ.get("RESULTS_PATH", "results")
    output_dir_path = Path(output_dir)

    if not output_dir_path.is_absolute():
        output_dir_path = Path.cwd() / output_dir_path
    output_dir_path.mkdir(parents=True, exist_ok=True)

    # Create the experiment subdirectory
    experiment_dir_path = output_dir_path / dir_name
    experiment_dir_path.mkdir(parents=True, exist_ok=True)

    return experiment_dir_path

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
    results_group_dir: Optional[Path] = None,
    trace_name: Optional[str] = None,
):
    
    logger.info(
        "Waiting %s seconds before running the SRE agent",
        wait_time_before_running_agent,
    )
    time.sleep(wait_time_before_running_agent)

    experiment_name = f"{agent_configuration_name} - {app_name} - {fault_name}"
    logger.info("Launching experiment: %s", experiment_name)

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

    if results_group_dir is not None:
        output_dir_path = results_group_dir
    else:
        output_dir = os.environ.get("RESULTS_PATH", "results")
        output_dir_path = Path(output_dir)
        if not output_dir_path.is_absolute():
            output_dir_path = Path.cwd() / output_dir_path
    output_dir_path.mkdir(parents=True, exist_ok=True)
    output_file_path = output_dir_path / output_file

    with open(output_file_path, "w") as f:
        json.dump(enriched_result, f, indent=2, default=str)

    logger.info("Results saved to %s", output_file_path)
    logger.info("Experiment completed successfully")

def main():

    AIOPSLAB_DIR = "/home/vm-kubernetes/AIOpsLab"

    fault_scenarios = load_fault_scenarios()

    if not fault_scenarios:
        logger.error("No fault scenarios found. Exiting.")
        return

    print("\n\nThe following experiments will be executed:")
    for idx, scenario in enumerate(fault_scenarios, start=1):
        scenario_name = scenario.get("scenario", "Unknown Scenario")
        fault_type = scenario.get("fault_type", "Unknown Fault")
        print(f"{idx}. {scenario_name} — {fault_type}")

    proceed_input = input("\nProceed with these experiments? [y/N]: ").strip().lower()
    if proceed_input not in {"y", "yes"}:
        logger.info("Aborting execution at user request.")
        return

    default_batch_name = datetime.now().strftime("batch-%Y%m%d-%H%M%S")
    batch_dir_input = input(
        f"Enter directory name for this experiment batch [{default_batch_name}]: "
    ).strip()
    batch_dir_name = batch_dir_input or default_batch_name

    results_group_path = get_experiment_dir_path(batch_dir_name, None)
    logger.info("Results will be stored under: %s", results_group_path)

    for i in fault_scenarios:
        logger.info("Scenario: %s", i["scenario"])
        logger.info("Fault: %s", i["fault_type"])

        try:
            # Step 1: Setup cluster and AIOpsLab
            logger.info("=== STEP 1: Setup kind and aiopslab ===")
            
            success = setup_cluster_and_aiopslab(
                problem_id=i["aiopslab_command"],
                aiopslab_dir=AIOPSLAB_DIR,
                stream_cli_output=False,
            )
            
            if not success:
                logger.error("Setup failed; aborting run")
                sys.exit(1)

            # Start MCP server (silence output when ready)
            logger.info("=== Starting MCP Server ===")
            try:
                start_mcp_server(ready_timeout=90, silence_on_ready=True, stream_output=False)
            except Exception as mcp_err:
                logger.error("MCP Server failed to start: %s", mcp_err)
                cleanup_cluster()
                sys.exit(1)
            
            # Import AFTER MCP server is running, before starting any event loop
            from launch_experiment import run_sre_agent, export_json_results
            
            # Step 2: Run your experiment (new event loop for async execution)

            logger.info("=== STEP 2: Run SRE Agent ===")

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
                    export_json_results_func=export_json_results,
                    results_group_dir=results_group_path
                )
            )
            
            # Step 3: Cleanup
            logger.info("=== STEP 3: Cleanup ===")
            
            # Cleanup MCP server first then cluster
            cleanup_mcp_server()
            cleanup_cluster()
            
            logger.info("All cleanup steps completed")
            
        except KeyboardInterrupt:
            logger.warning("Interrupted by user; performing cleanup")
            cleanup_mcp_server()
            cleanup_cluster()
            sys.exit(130)
        except Exception as e:
            logger.exception("Experiment execution failed: %s", e)
            cleanup_mcp_server()
            cleanup_cluster()
            sys.exit(1)


if __name__ == "__main__":
    main()