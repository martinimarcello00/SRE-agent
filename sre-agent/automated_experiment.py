
from experiments_runner import (
    setup_cluster_and_aiopslab,
    cleanup_cluster,
    load_fault_scenarios,
    load_agent_configurations
)
import asyncio
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional
import string

from dotenv import load_dotenv

from utils import TelegramNotification, get_today_completions_usage
from config import apply_config_overrides

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
    batch_name: str,
    results_group_dir: Optional[Path] = None,
    trace_name: Optional[str] = None
) -> tuple[dict, Path]:
    
    logger.info(
        "Waiting %s seconds before running the SRE agent",
        wait_time_before_running_agent,
    )
    time.sleep(wait_time_before_running_agent)

    experiment_name = f"{agent_configuration_name} - {app_name} - {fault_name} ({batch_name})"
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

    logger.info("Waiting 15 seconds to allow LangSmith to import final experiment data before saving results...")
    time.sleep(15)

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

    return enriched_result, output_file_path

def main():

    ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=ENV_PATH)

    AIOPSLAB_DIR = "/home/vm-kubernetes/AIOpsLab"

    # Get the fault scenarios
    fault_scenarios = load_fault_scenarios()

    # Get the agent configurations
    agents_configurations = load_agent_configurations()

    if not fault_scenarios:
        logger.error("No fault scenarios found. Exiting.")
        return
    
    if not agents_configurations:
        logger.error("No agent configurations found. Exiting.")
        return

    total_runs = len(fault_scenarios) * len(agents_configurations)
    print(f"\n\nThe following experiments will be executed ({total_runs} experiments):")
    for scenario_idx, scenario in enumerate(fault_scenarios, start=1):
        for config_idx, agent in enumerate(agents_configurations, start=1):
            config_label = (
                string.ascii_uppercase[config_idx - 1]
                if config_idx <= len(string.ascii_uppercase)
                else str(config_idx)
            )
            scenario_name = scenario.get("scenario", "Unknown Scenario")
            fault_type = scenario.get("fault_type", "Unknown Fault")
            agent_conf_name = agent.get("name", "Unknown Agent Configuration")
            print(f"{scenario_idx}{config_label}. {agent_conf_name} - {scenario_name} — {fault_type}")

    proceed_input = input("\nProceed with these experiments? [y/N]: ").strip().lower()
    if proceed_input not in {"y", "yes"}:
        logger.info("Aborting execution at user request.")
        return

    default_batch_name = datetime.datetime.now().strftime("batch-%Y%m%d-%H%M%S")
    batch_dir_input = input(
        f"Enter directory name for this experiment batch [{default_batch_name}]: "
    ).strip()
    batch_dir_name = batch_dir_input or default_batch_name

    enable_notifications = False
    telegram_notifier: TelegramNotification | None = None
    telegram_handler: logging.Handler | None = None
    telegram_choice = input("Enable Telegram notifications? [y/N]: ").strip().lower()
    if telegram_choice in {"y", "yes"}:
        telegram_notifier = TelegramNotification()
        try:
            telegram_handler = telegram_notifier.create_log_handler()
            telegram_handler.setLevel(logging.ERROR)
            formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
            telegram_handler.setFormatter(formatter)
            logging.getLogger().addHandler(telegram_handler)

            telegram_notifier.send_telegram_message(
                f"✅ Telegram notifications enabled for SRE automated experiments ({batch_dir_name})"
            )
            enable_notifications = True
        except RuntimeError as exc:
            logger.error("Telegram notifications disabled: %s", exc)
            telegram_notifier = None
        except Exception as exc:
            logger.error("Failed to initialize Telegram notifications: %s", exc)
            if telegram_handler:
                logging.getLogger().removeHandler(telegram_handler)
            telegram_notifier = None

    results_group_path = get_experiment_dir_path(batch_dir_name, None)
    logger.info("Results will be stored under: %s", results_group_path)

    total_scenarios = len(fault_scenarios)
    total_configs = len(agents_configurations)

    for scenario_idx, scenario in enumerate(fault_scenarios, start=1):

        if enable_notifications and telegram_notifier:
            try:
                telegram_notifier.send_telegram_message(
                    f"🚀 Starting scenario {scenario_idx}/{total_scenarios}: {scenario.get('scenario', 'Unknown Scenario')} - {scenario.get('fault_type', 'Unknown fault type')}"
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to send Telegram start message: %s", exc)

        pre_run_usage = get_today_completions_usage()
        logger.info(
            "Current token usage before scenario %d: input=%d, output=%d, total=%d",
            scenario_idx,
            pre_run_usage["input_tokens"],
            pre_run_usage["output_tokens"],
            pre_run_usage["total_tokens"],
        )
        if pre_run_usage["total_tokens"] >= 2_000_000:
            logger.error("Token usage exceeded limit (2,000,000). Aborting experiment.")
            if enable_notifications and telegram_notifier:
                try:
                    telegram_notifier.send_telegram_message(
                        "❌ Experiment aborted: token usage exceeded budget."
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to send Telegram abort message: %s", exc)
            sys.exit(1)

        logger.info("========= Scenario %d/%d =========", scenario_idx, total_scenarios)
        logger.info("Scenario: %s", scenario.get("scenario", "Unknown Scenario"))
        logger.info("Fault: %s", scenario.get("fault_type", "Unknown Fault"))

        try:
            # Step 1: Setup cluster and AIOpsLab
            logger.info("=== STEP 1: Setup kind and aiopslab ===")

            success = setup_cluster_and_aiopslab(
                problem_id=scenario["aiopslab_command"],
                aiopslab_dir=AIOPSLAB_DIR,
                stream_cli_output=True,
            )

            if not success:
                logger.error("Setup failed; aborting run")
                sys.exit(1)

            # Import AFTER setup complete, before starting any event loop
            from launch_experiment import run_sre_agent, export_json_results

            # Step 2: Run your experiment (new event loop for async execution)

            logger.info("=== STEP 2: Run SRE Agent ===")

            for config_idx, agent_conf in enumerate(agents_configurations, start=1):
                agent_name = agent_conf.get("name", "Unknown Agent Configuration")
                config_label = (
                    string.ascii_uppercase[config_idx - 1]
                    if config_idx <= len(string.ascii_uppercase)
                    else str(config_idx)
                )
                logger.info(
                    "Running agent configuration %s (%d/%d) for scenario '%s': %s",
                    config_label,
                    config_idx,
                    total_configs,
                    scenario.get("scenario", "Unknown Scenario"),
                    agent_name,
                )

                experiment_label = f"{agent_name} - {scenario.get('app_name', scenario.get('scenario', 'Unknown App'))} - {scenario.get('fault_type', 'Unknown Fault')}"
                if enable_notifications and telegram_notifier:
                    try:
                        telegram_notifier.send_telegram_message(
                            f"🧪 Starting experiment '{experiment_label}' ({config_label})"
                        )
                    except Exception as exc:  # pragma: no cover
                        logger.warning("Failed to send Telegram experiment start message: %s", exc)

                overrides_to_log = {
                    key: agent_conf[key]
                    for key in ("MAX_TOOL_CALLS", "RCA_TASKS_PER_ITERATION")
                    if key in agent_conf
                }
                if overrides_to_log:
                    logger.info("Applying overrides: %s", overrides_to_log)
                else:
                    logger.info("No overrides specified; using existing runtime settings.")

                apply_config_overrides(agent_conf)

                usage = get_today_completions_usage()
                logger.info(
                    "Current token usage before run: input=%d, output=%d, total=%d",
                    usage["input_tokens"],
                    usage["output_tokens"],
                    usage["total_tokens"],
                )
                if usage["total_tokens"] >= 2_000_000:
                    logger.error("Token usage exceeded limit (2,000,000). Aborting experiment.")
                    if enable_notifications and telegram_notifier:
                        try:
                            telegram_notifier.send_telegram_message(
                                "❌ Experiment aborted mid-run: token usage exceeded budget."
                            )
                        except Exception as exc:  # pragma: no cover
                            logger.warning("Failed to send Telegram budget warning: %s", exc)
                    sys.exit(1)

                enriched_result, output_file_path = asyncio.run(
                    run_experiment(
                        fault_name=scenario["fault_type"],
                        app_name=scenario["app_name"],
                        app_summary=scenario["app_summary"],
                        target_namespace=scenario["target_namespace"],
                        trace_service_starting_point=scenario["service_starting_point"],
                        wait_time_before_running_agent=scenario["wait_before_launch_agent"],
                        agent_configuration_name=agent_name,
                        batch_name=batch_dir_name,
                        run_sre_agent_func=run_sre_agent,
                        export_json_results_func=export_json_results,
                        results_group_dir=results_group_path,
                    )
                )

                logger.info(
                    "Completed agent configuration %s (%d/%d) for scenario '%s'",
                    config_label,
                    config_idx,
                    total_configs,
                    scenario.get("scenario", "Unknown Scenario"),
                )

                if enable_notifications and telegram_notifier:
                    try:
                        final_report = enriched_result.get("final_report", {}) if isinstance(enriched_result, dict) else {}
                        root_cause = final_report.get("root_cause", "Unknown root cause")
                        evidence_summary = final_report.get("evidence_summary", "No summary available")
                        stats = enriched_result.get("stats", {}) if isinstance(enriched_result, dict) else {}
                        total_tokens = stats.get("total_tokens", "N/A")
                        exec_seconds = stats.get("execution_time_seconds", "N/A")
                        langsmith_url = stats.get("langsmith_url", "N/A")
                        telegram_notifier.send_telegram_message(
                            "\n".join(
                                [
                                    f"✅ Experiment '{enriched_result.get('experiment_name', experiment_label)}' completed.",
                                    f"Root cause: {root_cause}",
                                    f"Summary: {evidence_summary}",
                                    f"Execution time: {exec_seconds} seconds",
                                    f"Total tokens: {total_tokens}",
                                    f"LangSmith run: {langsmith_url}",
                                ]
                            )
                        )
                    except Exception as exc:  # pragma: no cover
                        logger.warning("Failed to send Telegram experiment completion message: %s", exc)

                logger.info("Pausing briefly before launching the next experiment...")
                time.sleep(10)

            logger.info(
                "All %d agent configurations executed for scenario '%s' without recreating the cluster",
                total_configs,
                scenario.get("scenario", "Unknown Scenario"),
            )

            if enable_notifications and telegram_notifier:
                try:
                    telegram_notifier.send_telegram_message(
                        f"✅ Scenario '{scenario.get('scenario', 'Unknown Scenario')} - {scenario.get('fault_type', 'Unknown fault type')}' completed."
                    )
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to send Telegram completion message: %s", exc)

            # Step 3: Cleanup
            logger.info("=== STEP 3: Cleanup ===")

            # Cleanup cluster (MCP server cleanup is handled automatically by MultiServerMCPClient)
            cleanup_cluster()

            logger.info("All cleanup steps completed")
            logger.info("Pausing briefly before launching the next scenario...")
            time.sleep(10)

        except KeyboardInterrupt:
            logger.warning("Interrupted by user; performing cleanup")
            cleanup_cluster()
            if enable_notifications and telegram_notifier:
                try:
                    telegram_notifier.send_telegram_message("⚠️ Experiment interrupted by user.")
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to send Telegram interrupt message: %s", exc)
            sys.exit(130)
        except Exception as e:
            logger.exception("Experiment execution failed: %s", e)
            cleanup_cluster()
            if enable_notifications and telegram_notifier:
                try:
                    telegram_notifier.send_telegram_message(
                        f"❌ Experiment error in scenario '{scenario.get('scenario', 'Unknown Scenario')}': {e}"
                    )
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to send Telegram exception message: %s", exc)
            sys.exit(1)

    if enable_notifications and telegram_notifier:
        try:
            telegram_notifier.send_telegram_message("🎉 Automated experiment batch completed successfully.")
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to send Telegram final message: %s", exc)

    if telegram_handler:
        logging.getLogger().removeHandler(telegram_handler)

if __name__ == "__main__":
    main()