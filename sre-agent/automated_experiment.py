
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
import requests

# Configure logging for the SRE Agent script
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)

logger = logging.getLogger("automated_experiment")

def get_experiment_dir_path(dir_name: str, experiment_path: Optional[str] = None):
    """
    Returns the directory path for storing experiment results.
    Creates the directory if it does not exist.

    Args:
        dir_name (str): Name of the experiment batch directory.
        experiment_path (Optional[str]): Custom base path for results. If None, uses RESULTS_PATH env var or 'results'.

    Returns:
        Path: Path object for the experiment directory.
    """
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

import datetime
import requests

def get_today_completions_usage(
    bucket_width: str = "1d",
) -> dict:
    """
    Fetches today's OpenAI organization completions usage (UTC-based).

    Args:
        bucket_width (str): Width of usage bucket, default is "1d".

    Returns:
        dict: Dictionary with input_tokens, output_tokens, and total_tokens.
    """
    import os

    api_key = os.getenv("OPENAI_ADMIN_API_KEY")

    if not api_key:
        logger.error("OPENAI_ADMIN_API_KEY environment variable is missing.")

    start_dt = datetime.datetime.now(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + datetime.timedelta(days=1)

    params: list[tuple[str, int | str]] = [
        ("start_time", int(start_dt.timestamp())),
        ("end_time", int(end_dt.timestamp())),
        ("bucket_width", bucket_width),
    ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.get(
        "https://api.openai.com/v1/organization/usage/completions",
        params=params,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()
    json_response = response.json()

    input_tokens = json_response["data"][0]["results"][0]["input_tokens"]
    output_tokens = json_response["data"][0]["results"][0]["output_tokens"]

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens
    }

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
    date_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
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

    ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=ENV_PATH)

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

    default_batch_name = datetime.datetime.now().strftime("batch-%Y%m%d-%H%M%S")
    batch_dir_input = input(
        f"Enter directory name for this experiment batch [{default_batch_name}]: "
    ).strip()
    batch_dir_name = batch_dir_input or default_batch_name

    results_group_path = get_experiment_dir_path(batch_dir_name, None)
    logger.info("Results will be stored under: %s", results_group_path)

    for i in fault_scenarios:

        # Check today's token usage before running each experiment
        usage = get_today_completions_usage()
        logger.info(
            "Current token usage: input=%d, output=%d, total=%d",
            usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]
        )
        if usage["total_tokens"] >= 2000000:
            logger.error("Token usage exceeded limit (2,000,000). Aborting experiment.")
            sys.exit(1)
        
        logger.info("========= Experiment %d/%d =========", fault_scenarios.index(i) + 1, len(fault_scenarios))
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
            logger.info("Pausing briefly before launching the next experiment...")
            time.sleep(10)
            
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