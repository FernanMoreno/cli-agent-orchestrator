"""TmuxBackend — concrete TerminalBackend implementation wrapping TmuxClient.

This backend delegates all operations to the existing TmuxClient, preserving
identical behavior for all callers. It serves as the default backend when
no alternative is configured.
"""

import logging
from typing import Callable, Dict, List, Optional

from cli_agent_orchestrator.backends.base import (
    ProcessRestrictionContract,
    TerminalBackend,
    TerminalBackendError,
    UnsupportedWorkEnforcement,
)
from cli_agent_orchestrator.clients.tmux import TmuxClient, TmuxCreateUncertain

logger = logging.getLogger(__name__)


class WorkSessionCreateUncertain(TerminalBackendError):
    """tmux may hold a Work session whose creation receipt was lost."""

    error_kind = "work_session_create_uncertain"


class TmuxBackend(TerminalBackend):
    """TerminalBackend implementation backed by tmux via TmuxClient."""

    def __init__(self, client: Optional[TmuxClient] = None) -> None:
        """Initialize with an optional TmuxClient (defaults to module singleton)."""
        if client is None:
            from cli_agent_orchestrator.clients.tmux import tmux_client

            client = tmux_client
        self._client = client

    # --- Session lifecycle ---

    def preflight_work(self, contract: ProcessRestrictionContract) -> None:
        """tmux transports terminal input; it cannot isolate paths, commands or network."""
        raise UnsupportedWorkEnforcement(type(self).__name__)

    def create_work_session(
        self,
        contract: ProcessRestrictionContract,
        *args,
        before_effect: Callable[[], None] | None = None,
        **kwargs,
    ) -> str:
        self._before_work_effect(contract, before_effect)
        return self.create_session(*args, work_safe=True, **kwargs)

    def create_session(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
        *,
        work_safe: bool = False,
    ) -> str:
        try:
            if work_safe:
                return self._client.create_session(
                    session_name,
                    window_name,
                    terminal_id,
                    working_directory,
                    extra_env=extra_env,
                    work_safe=True,
                )
            return self._client.create_session(
                session_name, window_name, terminal_id, working_directory, extra_env=extra_env
            )
        except TmuxCreateUncertain as e:
            raise WorkSessionCreateUncertain(
                f"Work session '{session_name}' may remain; reconcile before retrying: {e}"
            ) from e
        except Exception as e:
            raise TerminalBackendError(f"Failed to create session '{session_name}': {e}") from e

    def session_exists(self, session_name: str) -> bool:
        return self._client.session_exists(session_name)

    def session_exists_strict(self, session_name: str) -> bool:
        return self._client.session_exists_strict(session_name)

    def list_sessions(self) -> List[Dict[str, str]]:
        return self._client.list_sessions()

    def kill_session(self, session_name: str) -> bool:
        return self._client.kill_session(session_name)

    # --- Window/tab lifecycle ---

    def create_window(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        window_shell: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> str:
        try:
            return self._client.create_window(
                session_name,
                window_name,
                terminal_id,
                working_directory,
                window_shell,
                extra_env=extra_env,
            )
        except Exception as e:
            raise TerminalBackendError(
                f"Failed to create window '{window_name}' in session '{session_name}': {e}"
            ) from e

    def kill_window(self, session_name: str, window_name: str) -> bool:
        return self._client.kill_window(session_name, window_name)

    # --- Input ---

    def send_keys(
        self,
        session_name: str,
        window_name: str,
        keys: str,
        enter_count: int = 1,
        force_bracketed_paste: bool = False,
        submit_delay: float = 0.3,
        plain_shell: bool = False,
    ) -> None:
        self._client.send_keys(
            session_name,
            window_name,
            keys,
            enter_count=enter_count,
            force_bracketed_paste=force_bracketed_paste,
            submit_delay=submit_delay,
            plain_shell=plain_shell,
        )

    def send_special_key(self, session_name: str, window_name: str, key: str) -> None:
        self._client.send_special_key(session_name, window_name, key)

    # --- Output ---

    def get_history(
        self,
        session_name: str,
        window_name: str,
        tail_lines: Optional[int] = None,
        strip_escapes: bool = False,
        full_history: bool = False,
        visible_only: bool = False,
    ) -> str:
        return self._client.get_history(
            session_name,
            window_name,
            tail_lines=tail_lines,
            strip_escapes=strip_escapes,
            full_history=full_history,
            visible_only=visible_only,
        )

    def get_pane_working_directory(self, session_name: str, window_name: str) -> Optional[str]:
        return self._client.get_pane_working_directory(session_name, window_name)

    def get_pane_current_command(self, session_name: str, window_name: str) -> Optional[str]:
        return self._client.get_pane_current_command(session_name, window_name)

    # --- Attach ---

    def attach_session(self, session_name: str) -> None:
        """Attach to tmux session via subprocess (replaces current process)."""
        import subprocess

        subprocess.run(["tmux", "attach-session", "-t", session_name], check=True)

    def prepare_web_attach(self, session_name: str, window_name: str) -> List[str]:
        """Return the tmux command used by the browser PTY WebSocket."""
        return ["tmux", "-u", "attach-session", "-t", f"{session_name}:{window_name}"]

    # --- Pipe-pane ---

    def pipe_pane(self, session_name: str, window_name: str, file_path: str) -> None:
        self._client.pipe_pane(session_name, window_name, file_path)

    def stop_pipe_pane(self, session_name: str, window_name: str) -> None:
        self._client.stop_pipe_pane(session_name, window_name)
