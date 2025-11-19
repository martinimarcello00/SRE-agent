import sys
import time
import logging
import pexpect
from pathlib import Path


logger = logging.getLogger(__name__)


def run_command_with_wait(command, timeout=600, encoding='utf-8', stream_output=False):
    """
    Run a command and wait for it to complete.
    
    Args:
        command: The command to execute
        timeout: Maximum time to wait for command completion (in seconds)
        encoding: Character encoding for output
        stream_output: If True, stream the subprocess output to stdout
        
    Returns:
        tuple: (exit_code, output)
    """
    logger.info("Running command: %s", command)

    child = pexpect.spawn(command, encoding=encoding, timeout=timeout)
    if stream_output:
        child.logfile_read = sys.stdout
    
    try:
        # Wait for the process to finish
        child.expect(pexpect.EOF)
        output = child.before
        child.close()
        if output:
            logger.debug("Command output for '%s':\n%s", command, output.strip())
        return child.exitstatus, output
    except pexpect.TIMEOUT:
        logger.error("Command timed out after %s seconds: %s", timeout, command)
        child.close(force=True)
        return -1, None
    except Exception as e:
        logger.exception("Error running command '%s': %s", command, e)
        child.close(force=True)
        return -1, None


def setup_cluster_and_aiopslab(
    problem_id,
    kind_config_path=None,
    aiopslab_dir=None,
    cluster_timeout=600,
    setup_timeout=300,
    stream_cluster_output=False,
    stream_cli_output=False,
):
    """
    Set up the experiment environment: create kind cluster and initialize AIOpsLab.
    
    Args:
        problem_id: The problem/experiment ID to start
        kind_config_path: Path to kind config file (optional, uses default if not provided)
        aiopslab_dir: Directory where AIOpsLab is located (where cli.py and pyproject.toml are)
                      If None, uses the directory of this script
        cluster_timeout: Timeout for cluster creation in seconds
        setup_timeout: Timeout for problem setup in seconds
    stream_cluster_output: If True, stream kind cluster command output to stdout
    stream_cli_output: If True, stream AIOpsLab CLI output to stdout
        
    Returns:
        bool: True if setup successful, False otherwise
    """
    script_dir = Path(__file__).parent
    
    # Determine AIOpsLab directory
    if aiopslab_dir is None:
        aiopslab_dir = script_dir
    else:
        aiopslab_dir = Path(aiopslab_dir).resolve()
    
    logger.info("AIOpsLab directory: %s", aiopslab_dir)
    
    # Step 1: Create kind cluster
    logger.info("Creating kind cluster")
    
    if kind_config_path is None:
        kind_config_path = aiopslab_dir / "kind" / "kind-config-x86.yaml"
    else:
        kind_config_path = Path(kind_config_path)
    
    if not kind_config_path.exists():
        logger.error("Kind config file not found at %s", kind_config_path)
        return False
    
    cluster_command = f"kind create cluster --config {kind_config_path}"
    exit_code, _ = run_command_with_wait(
        cluster_command,
        timeout=cluster_timeout,
        stream_output=stream_cluster_output,
    )
    
    if exit_code != 0:
        logger.error("Failed to create kind cluster (exit code: %s)", exit_code)
        return False
    
    logger.info("Kind cluster created successfully")
    
    # Give cluster a moment to stabilize
    logger.info("Waiting 5 seconds for cluster to stabilize...")
    time.sleep(5)
    
    # Step 2: Start AIOpsLab CLI in background
    logger.info("Starting AIOpsLab CLI")

    # Change to AIOpsLab directory and run poetry from there
    command = f"cd {aiopslab_dir} && poetry run python cli.py"
    logger.info("Starting CLI command: %s", command)
    
    # Use bash with login shell (-l) to properly source environment in tmux
    # This ensures proper PATH, environment variables, and shell initialization
    full_command = f"bash -l -c '{command}'"
    logger.info("Starting CLI with environment: %s", full_command)
    
    child = pexpect.spawn('/bin/bash', ['-l', '-c', command], encoding='utf-8', timeout=setup_timeout)
    if stream_cli_output:
        child.logfile_read = sys.stdout
    
    try:
        # Wait for the first prompt
        logger.info("Waiting for CLI to start...")
        child.expect('aiopslab>', timeout=30)
        logger.info("CLI started")
        if child.before:
            logger.debug("CLI initial output:\n%s", child.before.strip())
        
        # Send the start command
        start_cmd = f"start {problem_id}"
        logger.info("Sending CLI command: %s", start_cmd)
        child.sendline(start_cmd)
        
        logger.info(
            "Waiting for problem initialization (timeout: %s s)...",
            setup_timeout,
        )
        logger.debug(
            "Initialization includes loading the problem, displaying context, and the first env message",
        )
        
        child.expect('aiopslab>', timeout=setup_timeout)
        logger.info("Context displayed")
        if child.before:
            logger.debug("CLI output before context prompt:\n%s", child.before.strip())
        
        child.expect('aiopslab>', timeout=setup_timeout)
        logger.info("Problem fully initialized and ready for actions")
        if child.before:
            logger.debug("CLI output before ready prompt:\n%s", child.before.strip())
        
        logger.info("Environment ready")
        logger.info("Problem '%s' initialized", problem_id)
        logger.info("CLI is running in background. Run your experiments now.")
        logger.info("Call cleanup_cluster() when done.")
        
        return True
        
    except Exception as e:
        logger.exception("Failed to initialize AIOpsLab: %s", e)
        try:
            child.close(force=True)
        except:
            pass
        return False


def cleanup_cluster(cluster_timeout=120, stream_output=False):
    """
    Delete the kind cluster.
    
    Args:
        cluster_timeout: Timeout for cluster deletion in seconds
        stream_output: If True, stream the deletion command output to stdout
        
    Returns:
        bool: True if successful, False otherwise
    """
    logger.info("Deleting kind cluster")
    
    delete_command = "kind delete cluster"
    delete_exit_code, _ = run_command_with_wait(
        delete_command,
        timeout=cluster_timeout,
        stream_output=stream_output,
    )
    
    if delete_exit_code != 0:
        logger.warning(
            "Failed to delete kind cluster (exit code: %s)",
            delete_exit_code,
        )
        return False
    else:
        logger.info("Kind cluster deleted successfully")
        return True
