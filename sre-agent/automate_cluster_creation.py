import sys
import time
import pexpect
from pathlib import Path

def run_command_with_wait(command, timeout=600, encoding='utf-8'):
    """
    Run a command and wait for it to complete.
    
    Args:
        command: The command to execute
        timeout: Maximum time to wait for command completion (in seconds)
        encoding: Character encoding for output
        
    Returns:
        tuple: (exit_code, output)
    """
    print(f"\n{'='*60}")
    print(f"Running: {command}")
    print(f"{'='*60}\n")
    
    child = pexpect.spawn(command, encoding=encoding, timeout=timeout)
    child.logfile_read = sys.stdout
    
    try:
        # Wait for the process to finish
        child.expect(pexpect.EOF)
        child.close()
        return child.exitstatus, child.before
    except pexpect.TIMEOUT:
        print(f"\n\nCommand timed out after {timeout} seconds")
        child.close(force=True)
        return -1, None
    except Exception as e:
        print(f"\n\nError running command: {e}")
        child.close(force=True)
        return -1, None


def setup_cluster_and_aiopslab(problem_id, kind_config_path=None, aiopslab_dir=None, 
                                cluster_timeout=600, setup_timeout=300):
    """
    Set up the experiment environment: create kind cluster and initialize AIOpsLab.
    
    Args:
        problem_id: The problem/experiment ID to start
        kind_config_path: Path to kind config file (optional, uses default if not provided)
        aiopslab_dir: Directory where AIOpsLab is located (where cli.py and pyproject.toml are)
                      If None, uses the directory of this script
        cluster_timeout: Timeout for cluster creation in seconds
        setup_timeout: Timeout for problem setup in seconds
        
    Returns:
        bool: True if setup successful, False otherwise
    """
    script_dir = Path(__file__).parent
    
    # Determine AIOpsLab directory
    if aiopslab_dir is None:
        aiopslab_dir = script_dir
    else:
        aiopslab_dir = Path(aiopslab_dir).resolve()
    
    print(f"AIOpsLab directory: {aiopslab_dir}")
    
    # Step 1: Create kind cluster
    print("\n" + "="*60)
    print("Creating kind cluster")
    print("="*60)
    
    if kind_config_path is None:
        kind_config_path = aiopslab_dir / "kind" / "kind-config-x86.yaml"
    else:
        kind_config_path = Path(kind_config_path)
    
    if not kind_config_path.exists():
        print(f"❌ Error: Kind config file not found at {kind_config_path}")
        return False
    
    cluster_command = f"kind create cluster --config {kind_config_path}"
    exit_code, _ = run_command_with_wait(cluster_command, timeout=cluster_timeout)
    
    if exit_code != 0:
        print(f"\n❌ Failed to create kind cluster (exit code: {exit_code})")
        return False
    
    print("\n✅ Kind cluster created successfully")
    
    # Give cluster a moment to stabilize
    print("\nWaiting 5 seconds for cluster to stabilize...")
    time.sleep(5)
    
    # Step 2: Start AIOpsLab CLI in background
    print("\n" + "="*60)
    print("Starting AIOpsLab CLI")
    print("="*60)
    
    # Change to AIOpsLab directory and run poetry from there
    command = f"cd {aiopslab_dir} && poetry run python cli.py"
    print(f"\nStarting: {command}")
    
    # Use bash to execute the command with cd and &&
    child = pexpect.spawn('/bin/bash', ['-c', command], encoding='utf-8', timeout=setup_timeout)
    child.logfile_read = sys.stdout
    
    try:
        # Wait for the first prompt
        print("Waiting for CLI to start...")
        child.expect('aiopslab>', timeout=30)
        print("✓ CLI started")
        
        # Send the start command
        start_cmd = f"start {problem_id}"
        print(f"\n[Sending]: {start_cmd}")
        child.sendline(start_cmd)
        
        print(f"\nWaiting for problem initialization (timeout: {setup_timeout}s)...")
        print("(This includes loading the problem, displaying context, and first env message)")
        
        child.expect('aiopslab>', timeout=setup_timeout)
        print("✓ Context displayed")
        
        child.expect('aiopslab>', timeout=setup_timeout)
        print("✓ Problem fully initialized and ready for actions")
        
        print("\n✅ Environment ready")
        print(f"✅ Problem '{problem_id}' initialized")
        print("\n" + "="*60)
        print("CLI is running in background. Run your experiments now.")
        print("Call cleanup_cluster() when done.")
        print("="*60 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Failed to initialize AIOpsLab: {e}")
        try:
            child.close(force=True)
        except:
            pass
        return False


def cleanup_cluster(cluster_timeout=120):
    """
    Delete the kind cluster.
    
    Args:
        cluster_timeout: Timeout for cluster deletion in seconds
        
    Returns:
        bool: True if successful, False otherwise
    """
    print("\n" + "="*60)
    print("Deleting kind cluster")
    print("="*60)
    
    delete_command = "kind delete cluster"
    delete_exit_code, _ = run_command_with_wait(delete_command, timeout=cluster_timeout)
    
    if delete_exit_code != 0:
        print(f"\n⚠️  Failed to delete kind cluster (exit code: {delete_exit_code})")
        return False
    else:
        print("\n✅ Kind cluster deleted successfully")
        return True
