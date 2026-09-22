from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cli_agent_orchestrator.models.kiro_engine import KiroEngine
from cli_agent_orchestrator.models.provider import ProviderType

# Terminal ID validation (8 character hex string)
TerminalId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{8}$")]


class TerminalStatus(str, Enum):
    """Terminal status enumeration with provider-aware states."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    PROCESSING = "processing"
    COMPLETED = "completed"
    WAITING_USER_ANSWER = "waiting_user_answer"
    WAITING_QUOTA = "waiting_quota"
    ERROR = "error"


class TerminalInputBlockedError(Exception):
    """Stop an automated delivery without guessing a retry action.

    ``action`` is a small, stable recovery contract for callers that must
    decide what to tell a parent agent.  ``answer_user_prompt`` means a real
    human-owned dialog is visible.  ``reconcile`` means a durable task receipt
    may already describe work (including a post-paste persistence failure), so
    retrying or answering a dialog could duplicate work.  ``inspect`` means
    the terminal is not healthy enough to receive input. ``wait_for_quota``
    means the provider itself paused an already-delivered turn and may resume
    it without more input. Keeping this on the
    shared exception lets existing callers continue catching one type while
    preventing textual exception matching from deciding safety.

    Defined here (not in services/terminal_service.py, its original home) because
    providers/claude_code.py (and any other provider) needs to raise it from initialize() and
    services/terminal_service.py already imports providers/manager.py, which imports the concrete
    provider modules -- a provider importing back from terminal_service would be a circular
    import. models/terminal.py has no such dependency. terminal_service.py re-exports this name
    unchanged so every existing `from cli_agent_orchestrator.services.terminal_service import
    TerminalInputBlockedError` caller keeps working without edits.

    The original interactive-prompt paths keep the default
    ``answer_user_prompt`` action. Terminal delivery code must set a more
    conservative action explicitly whenever it cannot prove the task was not
    accepted. ``delivery_may_have_occurred`` is narrower than ``action``: it
    is true only when the current input was already handed to the terminal
    backend and the caller cannot prove that it was rejected. That includes a
    post-paste receipt-write failure *and* a transport error raised after a
    task-delivery paste begins. Queue consumers use it to durably stop
    automatic retry of that exact message.
    1. services/terminal_service.py's send_input(): the terminal's provider process has exited
       (status ERROR) -- refuse to type into what is now a bare shell, since queued input would
       execute as arbitrary commands.
    2. services/terminal_service.py's send_input(), separately: an orchestrated (assign/handoff)
       message would answer a prompt still showing WAITING_USER_ANSWER -- refuse, since only a
       real answer_user_prompt call (or the operator themselves) should resolve it. This guard
       only actually fires for a given provider once that provider's own
       ``blocks_orchestrated_input_while_waiting_user_answer`` property opts in (see
       providers/base.py) -- claude_code did not opt in until PR #539's round-2 fix, so before
       that an orchestrated message reaching a claude_code terminal parked on
       WAITING_USER_ANSWER was NOT refused here; it was pasted straight into the live prompt.
    3. providers/*.py's own initialize(): the CLI reached a real, alive, interactive state that
       isn't {IDLE, COMPLETED} but IS a recognized "something is asking a question" signal
       (WAITING_USER_ANSWER) -- or, for the outermost fallback, genuinely never resolved within
       the init timeout despite the terminal staying alive and producing output the whole time.
    Deferred initialization leaves all these workers alive. It communicates the
    action rather than deleting a possibly working terminal or recommending a
    duplicate delivery.
    """

    def __init__(
        self,
        message: str,
        *,
        action: Literal[
            "answer_user_prompt", "reconcile", "inspect", "wait_for_quota"
        ] = "answer_user_prompt",
        delivery_may_have_occurred: bool = False,
    ) -> None:
        super().__init__(message)
        self.action = action
        self.delivery_may_have_occurred = delivery_may_have_occurred


class TerminalLimitError(Exception):
    """Raised when creating a terminal would exceed this node's tracked-terminal cap.

    The cap comes from ``settings_service.get_max_terminals()`` (the
    ``CAO_MAX_TERMINALS`` env var / ``server.max_terminals`` setting; unset =
    unlimited, preserving pre-cap behavior). It exists for the one-agent-per-pod
    Kubernetes topology: worker pods set ``CAO_MAX_TERMINALS=1`` so each pod
    hosts exactly one agent and a second placement is rejected loudly instead
    of silently oversubscribing the pod. Deliberately NOT a ``ValueError``
    subclass — the API layer maps ValueError from terminal creation to 404
    ("session not found"), whereas a capacity rejection must surface as its own
    status (429) so a scheduler/supervisor can retry on a different node.
    """


class Terminal(BaseModel):
    """Terminal model - represents a tmux window."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(..., description="Unique terminal identifier")
    name: str = Field(..., description="Terminal/window name")
    provider: ProviderType = Field(..., description="CLI tool provider")
    session_name: str = Field(..., description="Session name")
    agent_profile: Optional[str] = Field(None, description="Agent profile")
    caller_id: Optional[str] = Field(
        None, description="Terminal that created this one via handoff/assign (callback target)"
    )
    allowed_tools: Optional[List[str]] = Field(None, description="Allowed CAO tools")
    engine: Optional[KiroEngine] = Field(None, description="Resolved Kiro engine")
    shell_command: Optional[str] = Field(
        None, description="Shell process name captured before kiro launch"
    )
    group: Optional[List[str]] = Field(
        None,
        description=(
            "Ordered, general-to-specific grouping array (e.g. "
            '["tenant_1", "project_5", "folder_12"]). CAO does ordered-prefix '
            "matching only; consumers own what the levels mean. None = this "
            "terminal participates in no group-based discovery (see "
            "list_siblings)."
        ),
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Free-form, consumer-defined JSON describing what this terminal is doing"
    )
    status: Optional[TerminalStatus] = Field(
        None, description="Current terminal status (live only)"
    )
    last_active: Optional[datetime] = Field(None, description="Last active timestamp")


class AgentStepResult(BaseModel):
    """Transient result of one agent step (issue #312, C3b). Not persisted.

    ``run_agent_step`` returns this ONLY on success (status COMPLETED); all
    failure modes raise narrow exceptions instead. It lives here in the terminal
    layer (not the workflow module) because it is the generic step substrate's
    return type and is conceptually workflow-independent — keeping it out of
    ``models/workflow.py`` lets ``services/agent_step.py`` avoid importing the
    workflow module (and its jsonschema/yaml deps).
    """

    terminal_id: str
    last_message: str
    status: TerminalStatus
