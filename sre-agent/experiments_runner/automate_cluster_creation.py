import sys
import time
import logging
import pexpect
import subprocess
from pathlib import Path


logger = logging.getLogger(__name__)

# Local registry configuration - matches kind official documentation
LOCAL_REGISTRY_NAME = "kind-registry"
LOCAL_REGISTRY_PORT = 5001


def run_command_with_wait(command, timeout=600, encoding='utf-8', stream_output=False, wait_for_string=None):
    """
    Run a command and wait for it to complete or for a specific string to appear.
    
    Args:
        command: The command to execute
        timeout: Maximum time to wait for command completion (in seconds)
        encoding: Character encoding for output
        stream_output: If True, stream the subprocess output to stdout
        wait_for_string: Optional string pattern to wait for before considering command complete
        
    Returns:
        tuple: (exit_code, output)
    """
    logger.info("Running command: %s", command)

    child = pexpect.spawn(command, encoding=encoding, timeout=timeout)
    if stream_output:
        child.logfile_read = sys.stdout
    
    try:
        if wait_for_string:
            # Wait for the specific string to appear
            logger.info("Waiting for string pattern: %s", wait_for_string)
            child.expect(wait_for_string)
            output = child.before
            logger.info("Found expected string pattern: %s", wait_for_string)
            # Continue waiting for EOF to ensure process completes
            try:
                child.expect(pexpect.EOF, timeout=10)
            except pexpect.TIMEOUT:
                logger.debug("Process still running after string appeared, closing...")
        else:
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


def configure_kind_registry(
    cluster_name="kind",
    registry_port: int = LOCAL_REGISTRY_PORT,
) -> bool:
    """
    Configure the kind cluster to use the local registry.
    
    Based on official kind documentation:
    https://kind.sigs.k8s.io/docs/user/local-registry/
    
    This:
    1. Creates the registry config directory on each node
    2. Configures containerd to use the local registry
    3. Connects the registry to the kind network
    4. Adds a ConfigMap for registry discovery
    
    Args:
        cluster_name: Name of the kind cluster (default: "kind")
        registry_port: Port the registry is running on (default: 5001)
        
    Returns:
        bool: True if configuration successful, False otherwise
    """
    logger.info("Configuring kind cluster '%s' to use local registry", cluster_name)
    
    registry_dir = f"/etc/containerd/certs.d/localhost:{registry_port}"
    
    # Step 0: Connect registry to kind network
    logger.info("Connecting registry to kind network")
    try:
        # Connect registry container to kind network if it exists
        cmd = f"docker network connect {cluster_name} {LOCAL_REGISTRY_NAME} 2>/dev/null || true"
        subprocess.run(cmd, shell=True, capture_output=True)
        logger.info("Registry connected to kind network")
    except Exception as e:
        logger.warning("Could not connect registry to kind network: %s", e)
    
    # Step 1: Add registry config to all nodes
    logger.info("Configuring containerd on cluster nodes")
    try:
        # Get list of nodes
        get_nodes_cmd = f"kind get nodes --name {cluster_name}"
        result = subprocess.run(get_nodes_cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error("Failed to get cluster nodes: %s", result.stderr)
            return False
        
        nodes = [n for n in result.stdout.strip().split('\n') if n]
        
        for worker in nodes:
            logger.info("Configuring registry on worker node: %s", worker)
            
            # 1. Configure localhost:5001 (for explicit usage like localhost:5001/image)
            cmd = f"docker exec {worker} mkdir -p /etc/containerd/certs.d/localhost:{registry_port}"
            subprocess.run(cmd, shell=True, check=True)
            
            # Use registry container hostname which Docker DNS resolves automatically
            hosts_config = (
                f"[host.\\\"http://{LOCAL_REGISTRY_NAME}:5000\\\"]\n"
            )
            
            cmd = (
                f"docker exec {worker} "
                f"bash -c \"cat > /etc/containerd/certs.d/localhost:{registry_port}/hosts.toml << 'EOF'\n"
                f"{hosts_config}"
                f"EOF\""
            )
            subprocess.run(cmd, shell=True, check=True)

            # 2. Configure docker.io mirror (for transparent caching of standard images)
            cmd = f"docker exec {worker} mkdir -p /etc/containerd/certs.d/docker.io"
            subprocess.run(cmd, shell=True, check=True)

            mirror_config = (
                f"server = \\\"https://registry-1.docker.io\\\"\n"
                f"\n"
                f"[host.\\\"http://{LOCAL_REGISTRY_NAME}:5000\\\"]\n"
                f"  capabilities = [\\\"pull\\\", \\\"resolve\\\"]\n"
            )

            cmd = (
                f"docker exec {worker} "
                f"bash -c \"cat > /etc/containerd/certs.d/docker.io/hosts.toml << 'EOF'\n"
                f"{mirror_config}"
                f"EOF\""
            )
            subprocess.run(cmd, shell=True, check=True)
            
            logger.info("Worker node %s configured with registry mirror", worker)
        
    except subprocess.CalledProcessError as e:
        logger.warning("Issue configuring worker nodes: %s", e)
    
    # Step 2: Document the local registry
    logger.info("Documenting local registry in ConfigMap")
    try:
        config_map_yaml = (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: local-registry-hosting\n"
            "  namespace: kube-public\n"
            "data:\n"
            "  localRegistryHosting.v1: |\n"
            f"    host: \"localhost:{registry_port}\"\n"
            "    help: \"https://kind.sigs.k8s.io/docs/user/local-registry/\"\n"
        )
        
        cmd = f"echo '{config_map_yaml}' | kubectl apply -f -"
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        logger.info("Local registry documented in kube-public/local-registry-hosting ConfigMap")
    except subprocess.CalledProcessError as e:
        logger.warning("Failed to create local registry ConfigMap: %s", e)

    logger.info("Registry configuration complete - local registry enabled with fallback to default registries")
    return True


def setup_cluster_and_aiopslab(
    problem_id,
    kind_config_path=None,
    aiopslab_dir=None,
    cluster_timeout=600,
    setup_timeout=900,
    stream_cluster_output=False,
    stream_cli_output=False,
    enable_local_registry=True,
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
        enable_local_registry: If True, configure the cluster to use the local registry
        
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
    logger.info("=== STEP 1: Create kind cluster ===")
    
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
    logger.info("Waiting 30 seconds for cluster to stabilize...")
    time.sleep(30)
    
    # Step 2: Optional - Configure registry with cluster
    if enable_local_registry:
        logger.info("=== STEP 2: Configure local registry ===")
        if not configure_kind_registry("kind", LOCAL_REGISTRY_PORT):
            logger.warning("Failed to configure registry; continuing without it")
    
    # Step 3: Start AIOpsLab CLI in background
    logger.info("=== STEP 3: Start AIOpsLab CLI ===")

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
    Delete the kind cluster and wait for confirmation of node deletion.
    
    Args:
        cluster_timeout: Timeout for cluster deletion in seconds
        stream_output: If True, stream the deletion command output to stdout
        
    Returns:
        bool: True if successful, False otherwise
    """
    logger.info("Deleting kind cluster")
    
    delete_command = "kind delete cluster"
    # Wait for the "Deleted nodes:" string to confirm nodes were deleted
    delete_exit_code, output = run_command_with_wait(
        delete_command,
        timeout=cluster_timeout,
        stream_output=stream_output,
        wait_for_string=r"Deleted nodes:\s*\[.*\]",
    )
    
    if delete_exit_code != 0:
        logger.warning(
            "Failed to delete kind cluster (exit code: %s)",
            delete_exit_code,
        )
        return False
    else:
        logger.info("Kind cluster deleted successfully")
        if output:
            logger.debug("Cluster deletion output:\n%s", output)
        return True
