"""Terminal backend abstract base class.

Defines the contract that all terminal backends (tmux, herdr, etc.) must satisfy.
Core services depend only on this ABC, never on a concrete backend directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.models.work_contract import ExecutableIdentity


class TerminalBackendError(Exception):
    """Base exception for terminal backend operations."""

    pass


class TerminalNotFoundError(TerminalBackendError):
    """Raised when a terminal/pane cannot be found or resolved."""

    def __init__(self, terminal_id: str, message: Optional[str] = None):
        self.terminal_id = terminal_id
        super().__init__(message or f"Terminal not found: {terminal_id}")


@dataclass(frozen=True)
class ProcessRestrictionContract:
    """Required process-boundary restrictions, not authorization or a sandbox.

    ``paths`` retains its historical name for the reserved writable paths.
    ``read_paths`` carries the effective read-only path ceiling and
    ``checkout_root`` its canonical work directory. Every dimension is
    explicit: empty tuples deny all; None is invalid. These are frozen
    requirements for an enforcing backend, never prompt hints.
    """

    paths: tuple[str, ...]
    commands: tuple[str, ...]
    network: tuple[str, ...]
    read_paths: tuple[str, ...] = ()
    checkout_root: str | None = None
    executable_identities: tuple[ExecutableIdentity, ...] = ()
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.executable_identities) is not tuple or any(
            not isinstance(identity, ExecutableIdentity) for identity in self.executable_identities
        ):
            raise ValueError("executable identities require an explicit immutable tuple")
        if (
            self.executable_identities
            and tuple(identity.command_token for identity in self.executable_identities)
            != self.commands
        ):
            raise ValueError("executable identities must match permitted commands")
        for values in (self.paths, self.commands, self.network, self.tools, self.read_paths):
            if type(values) is not tuple or any(
                type(value) is not str or not value.strip() or "\x00" in value for value in values
            ):
                raise ValueError(
                    "restriction dimensions require explicit tuples of nonempty strings"
                )
        if self.checkout_root is not None and (
            type(self.checkout_root) is not str
            or not self.checkout_root
            or not self.checkout_root.strip()
            or "\x00" in self.checkout_root
            or not Path(self.checkout_root).is_absolute()
        ):
            raise ValueError("checkout_root must be a nonempty absolute path or None")

    @property
    def enforcement_level(self) -> str:
        return "process_boundary"


class UnsupportedWorkEnforcement(TerminalBackendError):
    """Stable pre-effect rejection; no claimed isolation is available."""

    error_kind = "unsupported_enforcement"
    required_level = "process_boundary"

    def __init__(self, backend_name: str, reason: str | None = None):
        self.backend_name = backend_name
        self.reason = reason or "required process-boundary enforcement is unavailable"
        super().__init__(
            f"{backend_name} cannot verify required process-boundary enforcement: {self.reason}"
        )


class WorkEffectAuthorizationRequired(TerminalBackendError):
    """No server guard freshly authorized this protected external effect."""

    error_kind = "work_effect_authorization_required"


class TerminalBackend(ABC):
    """Abstract base class defining the terminal backend contract.

    All terminal operations CAO requires are declared here. Concrete backends
    (TmuxBackend, HerdrBackend) implement these methods using their respective
    multiplexer APIs.
    """

    def preflight_work(self, contract: ProcessRestrictionContract) -> None:
        """Reject protected execution unless a backend truly enforces restrictions.

        Capability dictionaries, allowed_tools, grant allowlists, working directory
        and prompts are NOT enforcement evidence. Existing terminal transports
        (including herdr) inherit this rejection. A future backend must implement
        verified isolation at its effect boundary, not just override a flag here.
        Legacy methods below retain their historical, unisolated behavior.
        """
        raise UnsupportedWorkEnforcement(type(self).__name__)

    def _before_work_effect(
        self, contract: ProcessRestrictionContract, before_effect: Callable[[], None] | None
    ) -> None:
        """Preflight, then fresh server authority; never accept a client boolean.

        The trusted coordinator's synchronous guard must revalidate current
        grant/resources/snapshot and close its database transaction before
        returning None. A guard exception or any other result blocks the effect.
        This callback is not a public request parameter or sandbox capability.
        """
        self.preflight_work(contract)
        if not callable(before_effect):
            raise WorkEffectAuthorizationRequired("server before-effect guard required")
        if before_effect() is not None:
            raise WorkEffectAuthorizationRequired("server guard must authorize without a result")

    def create_work_session(
        self,
        contract: ProcessRestrictionContract,
        *args,
        before_effect: Callable[[], None] | None = None,
        **kwargs,
    ) -> str:
        """Protected counterpart of create_session; preflight precedes all effects."""
        self._before_work_effect(contract, before_effect)
        return self.create_session(*args, **kwargs)

    def create_work_window(
        self,
        contract: ProcessRestrictionContract,
        *args,
        before_effect: Callable[[], None] | None = None,
        **kwargs,
    ) -> str:
        """Protected counterpart of create_window; no fallback on rejection."""
        self._before_work_effect(contract, before_effect)
        return self.create_window(*args, **kwargs)

    def send_work_keys(
        self,
        contract: ProcessRestrictionContract,
        *args,
        before_effect: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        """Protected send/continuation, checked anew before transport access."""
        self._before_work_effect(contract, before_effect)
        return self.send_keys(*args, **kwargs)

    # --- Session lifecycle ---

    @abstractmethod
    def create_session(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> str:
        """Create a new terminal session with an initial window.

        Args:
            session_name: Name for the session (e.g., "cao-my-project")
            window_name: Name for the initial window/tab
            terminal_id: Unique terminal identifier to inject into the environment
            working_directory: Optional starting directory

        Returns:
            The actual window name assigned by the backend

        Raises:
            TerminalBackendError: If session creation fails
            ValueError: If working_directory is invalid
        """
        ...

    @abstractmethod
    def session_exists(self, session_name: str) -> bool:
        """Check if a session exists.

        Args:
            session_name: Session name to check

        Returns:
            True if the session exists
        """
        ...

    def session_exists_strict(self, session_name: str) -> bool:
        """Check if a session exists, RAISING when the lookup cannot be answered.

        Semantics differ from ``session_exists`` only in the error case:
        ``session_exists`` collapses a lookup error into False ("assume
        absent"), whereas this must distinguish a confirmed absence (return
        False) from an inability to tell (raise). Teardown confirmation MUST
        use this so a transient backend error is never misread as "session
        gone" (#498).

        Implementations must query in a way that keeps the two apart. That is a
        real constraint, not a formality: a client library that swallows its own
        transport errors and reports an empty result set makes a lookup failure
        LOOK like an absence, and a strict check layered over it fails OPEN no
        matter what it does with its own exceptions. ``TmuxBackend`` therefore
        issues its own ``list-sessions`` and classifies the exit status
        (``clients/tmux.py``) rather than using libtmux's session collection.

        The default implementation delegates to ``session_exists`` for backends
        that cannot yet make the distinction; those backends therefore retain the
        old lenient, fail-OPEN behavior, and the teardown guarantee is only as
        strong as this method. HerdrBackend is in that position — tracked as a
        follow-up.
        """
        return self.session_exists(session_name)

    @abstractmethod
    def list_sessions(self) -> List[Dict[str, str]]:
        """List all sessions managed by this backend.

        Returns:
            List of dicts with keys: id, name, status
        """
        ...

    @abstractmethod
    def kill_session(self, session_name: str) -> bool:
        """Kill/destroy a session.

        Implementations MUST NOT return True on a merely-dispatched kill: session
        teardown treats True as proof the session is gone and only then drops the
        matching registry rows, so an optimistic True is what lets tmux and the
        registry diverge (#498). Confirm the session is actually gone — poll if
        the kill is asynchronous — before returning True.

        Args:
            session_name: Session to kill

        Returns:
            True once the session is confirmed gone; False if it was not found
            OR the kill could not be confirmed within the backend's bound. A
            caller that must tell those two apart re-checks existence itself via
            ``session_exists_strict``.
        """
        ...

    # --- Window/tab lifecycle ---

    @abstractmethod
    def create_window(
        self,
        session_name: str,
        window_name: str,
        terminal_id: str,
        working_directory: Optional[str] = None,
        window_shell: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> str:
        """Create a new window/tab in an existing session.

        Args:
            session_name: Session to add the window to
            window_name: Name for the new window
            terminal_id: Unique terminal identifier to inject into the environment
            working_directory: Optional starting directory
            window_shell: Optional shell command to run instead of default shell

        Returns:
            The actual window name assigned by the backend

        Raises:
            TerminalBackendError: If window creation fails
            ValueError: If session not found or working_directory is invalid
        """
        ...

    @abstractmethod
    def kill_window(self, session_name: str, window_name: str) -> bool:
        """Kill a specific window within a session.

        Args:
            session_name: Session containing the window
            window_name: Window to kill

        Returns:
            True if window was killed, False if not found
        """
        ...

    # --- Input ---

    @abstractmethod
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
        """Send text input to a window.

        Args:
            session_name: Target session
            window_name: Target window
            keys: Text to send
            enter_count: Number of Enter keys to send after the text
            force_bracketed_paste: If True, request bracketed-paste delivery.
                The herdr backend wraps content in \\x1b[200~...\\x1b[201~
                itself (it writes raw bytes to the pty, no sanitization). The
                tmux backend hand-crafts the same wrap on tmux < 3.7 but must
                delegate to ``paste-buffer -p`` on >= 3.7, where pasted
                buffers are vis(3)-sanitized and raw ESC bytes would arrive
                as literal "^[[200~" (issue #413); -p emits markers only when
                the pane enabled DECSET 2004.
            submit_delay: Seconds to wait after pasting before sending Enter, so
                a TUI (e.g. Claude Code's Ink renderer) finishes processing the
                paste before submission. Backends without a paste step may ignore.
            plain_shell: An explicit shell-command launch. It must be sent without
                bracketed-paste framing even if a stale terminal mode says otherwise.
                It is a transport property, not a permission to resend a prompt.
        """
        ...

    @abstractmethod
    def send_special_key(self, session_name: str, window_name: str, key: str) -> None:
        """Send a special key (e.g., C-c, C-d, Enter) to a window.

        Unlike send_keys(), this sends the key as a control/special key name
        and does not append a carriage return.

        Args:
            session_name: Target session
            window_name: Target window
            key: Key name (e.g., "C-d", "C-c", "Escape", "Enter", "")
        """
        ...

    # --- Output ---

    @abstractmethod
    def get_history(
        self,
        session_name: str,
        window_name: str,
        tail_lines: Optional[int] = None,
        strip_escapes: bool = False,
        full_history: bool = False,
        visible_only: bool = False,
    ) -> str:
        """Get terminal output/history from a window.

        Args:
            session_name: Target session
            window_name: Target window
            tail_lines: Number of lines from the end (None = backend default).
                On tmux this INCLUDES the visible pane plus N lines of scrollback
                above it — it is not a viewport bounded to N rows.
            strip_escapes: If True, strip ANSI escape sequences
            full_history: If True, capture entire scrollback
            visible_only: If True, capture only the currently rendered viewport,
                nothing from scrollback (overrides tail_lines/full_history). A
                backend without a viewport concept may approximate with its
                closest bounded recent read.

        Returns:
            Terminal output as a string
        """
        ...

    @abstractmethod
    def get_pane_working_directory(self, session_name: str, window_name: str) -> Optional[str]:
        """Get the current working directory of a pane.

        Args:
            session_name: Target session
            window_name: Target window

        Returns:
            Working directory path, or None if unavailable
        """
        ...

    @abstractmethod
    def get_pane_current_command(self, session_name: str, window_name: str) -> Optional[str]:
        """Get the current foreground command running in a pane.

        Args:
            session_name: Target session
            window_name: Target window

        Returns:
            Command name, or None if unavailable
        """
        ...

    # --- Attach ---

    @abstractmethod
    def attach_session(self, session_name: str) -> None:
        """Attach to a session (for interactive use).

        Args:
            session_name: Session to attach to
        """
        ...

    @abstractmethod
    def prepare_web_attach(self, session_name: str, window_name: str) -> List[str]:
        """Prepare a browser PTY attachment and return its subprocess argv.

        Backends may perform routing work before returning, such as focusing a
        Herdr workspace/tab. The caller owns the PTY and subprocess lifecycle.

        Args:
            session_name: Target session
            window_name: Target window

        Returns:
            Subprocess argv for the interactive backend client

        Raises:
            TerminalBackendError: If the backend cannot prepare the attachment
        """
        ...

    # --- Pipe-pane (logging) ---

    @abstractmethod
    def pipe_pane(self, session_name: str, window_name: str, file_path: str) -> None:
        """Start piping pane output to a file.

        For backends that don't support pipe-pane (e.g., herdr), this is a no-op
        since inbox delivery uses a different mechanism.

        Args:
            session_name: Target session
            window_name: Target window
            file_path: Absolute path to the log file
        """
        ...

    @abstractmethod
    def stop_pipe_pane(self, session_name: str, window_name: str) -> None:
        """Stop piping pane output.

        For backends that don't support pipe-pane, this is a no-op.

        Args:
            session_name: Target session
            window_name: Target window
        """
        ...

    # --- Capability queries ---

    def supports_event_inbox(self) -> bool:
        """Whether this backend uses event-based inbox delivery (e.g., socket events).

        When True, terminals should be registered with an event-based inbox service
        instead of using pipe-pane file watching.

        Default is False (pipe-pane based delivery).
        """
        return False

    def get_pane_id(self, terminal_id: str, session_name: str = "", window_name: str = "") -> str:
        """Resolve terminal_id to backend-specific pane identifier.

        Only meaningful for backends that use event-based inbox delivery.
        Default raises NotImplementedError.

        Args:
            terminal_id: CAO terminal identifier
            session_name: Optional session name for window-based fallback lookup
            window_name: Optional window name for window-based fallback lookup

        Returns:
            Backend-specific pane identifier

        Raises:
            NotImplementedError: If backend does not support pane ID resolution
        """
        raise NotImplementedError(f"{type(self).__name__} does not support get_pane_id()")

    def get_native_status(self, session_name: str, window_name: str) -> Optional[TerminalStatus]:
        """Query native agent status if the backend has agent awareness.

        Returns None if unsupported — caller falls back to pane content parsing.
        """
        return None
