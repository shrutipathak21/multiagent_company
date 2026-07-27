"""
Sandboxed Execution
====================
THE reason this can't go public without changes: repo.py's Workspace
runs the user's test command via `subprocess.run(cmd, shell=True)`
directly on the server. That's fine on your own laptop. It is NOT fine
once a stranger on the internet controls both the uploaded code and
the command string — that's arbitrary remote code execution on your
server.

This module replaces that local subprocess call with a request to
E2B (e2b.dev): each run gets a fresh, isolated, network-restricted,
time-limited sandbox VM that is destroyed afterward. Nothing the user
uploads or runs can touch your actual server or other users' data.

Setup (one-time):
    pip install e2b-code-interpreter
    Get a free API key at e2b.dev -> export E2B_API_KEY=...

This mirrors repo.py's Workspace interface (materialize-equivalent +
run()) so webapp_cloud.py can swap it in without restructuring the
scheduler/company logic at all — only WHERE code executes changes.
"""

from __future__ import annotations

import os

class SandboxUnavailable(Exception):
    """Raised when E2B isn't configured or reachable — callers should
    treat this as 'cannot safely run this request', not silently fall
    back to local execution."""

class RemoteWorkspace:
    """Same role as repo.py's Workspace, but file writes and test
    execution happen inside an ephemeral E2B sandbox instead of on
    this server's disk.
    """

    MAX_RUNTIME_SECONDS = 90

    def __init__(self, file_store):
        """file_store: an ai_company.conflict.FileStore already loaded
        with the repo's contents (reuse repo.py's Workspace to build
        this part — only execution moves to the sandbox)."""
        self.file_store = file_store
        self._sandbox = None

    def _get_sandbox(self):
        if self._sandbox is not None:
            return self._sandbox
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:
            raise SandboxUnavailable(
                "e2b_code_interpreter not installed. Run: "
                "pip install e2b-code-interpreter"
            ) from exc
        if not os.environ.get("E2B_API_KEY"):
            raise SandboxUnavailable(
                "E2B_API_KEY not set. Get a free key at e2b.dev"
            )
        self._sandbox = Sandbox.create()
        return self._sandbox

    def push_files(self) -> None:
        """Write the current FileStore contents into the sandbox."""
        sbx = self._get_sandbox()
        for path, vf in self.file_store.files.items():
            sbx.files.write(path, vf.content)

    def run(self, cmd: str, timeout: int | None = None) -> tuple[int, str]:
        """Run the test command INSIDE the isolated sandbox, not locally.

        A non-zero exit code (failing tests) is a normal, expected result
        we need to report -- NOT a crash. The E2B SDK's .run() raises
        CommandExitException on any non-zero exit rather than returning
        it, unlike Python's subprocess.run(). That exception object
        conveniently carries .exit_code/.stdout/.stderr itself, so we
        catch it and extract those instead of letting it propagate.
        """
        sbx = self._get_sandbox()
        from e2b.sandbox.commands.command_handle import CommandExitException
        timeout = min(timeout or self.MAX_RUNTIME_SECONDS, self.MAX_RUNTIME_SECONDS)
        try:
            result = sbx.commands.run(cmd, timeout=timeout)
        except CommandExitException as exc:
            result = exc
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return result.exit_code, output.strip()

    def pull_files(self) -> dict[str, str]:
        """Read back whatever the sandbox has after execution, in case
        the test command itself modified files (rare, but possible)."""
        sbx = self._get_sandbox()
        out = {}
        for path in self.file_store.files:
            try:
                out[path] = sbx.files.read(path)
            except Exception:
                out[path] = self.file_store.files[path].content
        return out

    def close(self) -> None:
        if self._sandbox is not None:
            try:
                self._sandbox.kill()
            except Exception:
                pass
            self._sandbox = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
