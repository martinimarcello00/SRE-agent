# Use this only if the MCP server uses http streamable protocol

import os
import re
import sys
import time
import signal
import logging
from pathlib import Path

import pexpect


_MCP_CHILD = None  # Global handle to the spawned MCP server (pexpect.spawn)


logger = logging.getLogger(__name__)


def _detect_ready_patterns(child, timeout=60):
    """
    Wait for common HTTP server readiness signals printed by FastMCP/uvicorn.

    Returns:
        tuple[bool, str|None]: (ready, detected_url)
    """
    # Patterns chosen to cover typical uvicorn and generic HTTP server messages
    patterns = [
        r"Uvicorn running on (http[s]?://[^\s]+)",
        r"Application startup complete",
        r"Running on (http[s]?://[^\s]+)",
        r"listening on (http[s]?://[^\s]+)",
        r"http[s]?://[^\s]+",
    ]

    try:
        idx = child.expect(patterns, timeout=timeout)
        # Try to extract URL when available
        m = re.search(r"http[s]?://[^\s]+", child.after or "")
        return True, m.group(0) if m else None
    except pexpect.TIMEOUT:
        return False, None


def start_mcp_server(
    python_executable: str | None = None,
    server_path: str | Path | None = None,
    cwd: str | Path | None = None,
    ready_timeout: int = 60,
    silence_on_ready: bool = True,
    stream_output: bool = False,
):
    """
    Start the MCP server as a background process, wait until it's listening,
    then optionally silence its output so the experiment can continue cleanly.

    Args:
        python_executable: Absolute path to Python interpreter. Defaults to current interpreter.
        server_path: Path to MCP server script (mcp_server.py). Defaults to repo's MCP-server/mcp_server.py.
        cwd: Working directory to run from. Defaults to repo root.
        ready_timeout: Seconds to wait for readiness logs.
    silence_on_ready: If True, stop streaming server output once ready.
    stream_output: If True, stream server stdout to this process until ready.

    Returns:
        tuple[pexpect.spawn, str|None]: (child process handle, detected server URL if any)

    Raises:
        RuntimeError: If the server fails to start within timeout.
    """
    global _MCP_CHILD

    repo_root = Path(__file__).resolve().parents[2]  # .../SRE-agent
    if cwd is None:
        cwd = repo_root
    else:
        cwd = Path(cwd).resolve()

    if server_path is None:
        server_path = repo_root / "MCP-server" / "mcp_server.py"
    else:
        server_path = Path(server_path).resolve()

    if python_executable is None:
        python_executable = sys.executable

    if not server_path.exists():
        raise FileNotFoundError(f"MCP server script not found at: {server_path}")

    cmd = f"{python_executable} {server_path}"

    logger.info("Starting MCP server")
    logger.info("Working directory: %s", cwd)
    logger.info("Command: %s", cmd)

    # Spawn the process attached to a PTY so we can read logs and later silence them
    child = pexpect.spawn(
        cmd,
        encoding="utf-8",
        timeout=ready_timeout,
        cwd=str(cwd),
    )

    if stream_output:
        child.logfile_read = sys.stdout

    ready, url = _detect_ready_patterns(child, timeout=ready_timeout)
    if not ready:
        # Dump any trailing buffer for diagnostics
        tail = (child.before or "").splitlines()[-5:]
        try:
            child.close(force=True)
        except Exception:
            pass
        if tail:
            logger.error("MCP server failed to start. Last lines before timeout:\n%s", "\n".join(tail))
        else:
            logger.error("MCP server failed to start and produced no output")
        raise RuntimeError(
            "Failed to detect MCP server readiness within timeout.\n" +
            ("Last lines:\n" + "\n".join(tail) if tail else "No output captured.")
        )

    if silence_on_ready:
        # Stop streaming output to the console, keep process running in background
        child.logfile_read = None

    _MCP_CHILD = child

    logger.info("MCP server is listening%s", f" at {url}" if url else "")
    if silence_on_ready and stream_output:
        logger.info("Output silenced; continuing your experiment...")

    return child, url


def cleanup_mcp_server(grace_period: float = 5.0) -> bool:
    """
    Terminate the MCP server process and its terminal.

    Args:
        grace_period: Seconds to wait after SIGTERM before forcing SIGKILL.

    Returns:
        bool: True if the process is no longer alive, False otherwise.
    """
    global _MCP_CHILD

    if _MCP_CHILD is None:
        # Nothing to clean up
        return True

    child = _MCP_CHILD
    _MCP_CHILD = None

    logger.info("Stopping MCP server")

    try:
        # Try graceful termination first
        pid = getattr(child, "pid", None)
        if isinstance(pid, int):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        # Wait a bit to allow graceful shutdown
        t0 = time.time()
        while time.time() - t0 < grace_period:
            if not child.isalive():
                break
            time.sleep(0.2)

        if child.isalive():
            # Force kill if still alive
            pid = getattr(child, "pid", None)
            if isinstance(pid, int):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        # Ensure the PTY is closed
        try:
            child.close(force=True)
        except Exception:
            pass

        alive = child.isalive() if hasattr(child, "isalive") else False
        if not alive:
            logger.info("MCP server terminated")
            return True
        else:
            logger.warning("MCP server may still be running")
            return False

    except Exception as e:
        logger.exception("Error while stopping MCP server: %s", e)
        return False


if __name__ == "__main__":
    # Simple manual test helper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    try:
        _, url = start_mcp_server(ready_timeout=90, stream_output=True)
        logger.info("MCP server ready: %s", url or "(url not detected)")
        logger.info("Sleeping for 5 seconds before cleanup...")
        time.sleep(5)
    finally:
        cleanup_mcp_server()
