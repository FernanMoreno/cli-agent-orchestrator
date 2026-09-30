"""Task-local view of a backend for one live work dispatch capability."""

from cli_agent_orchestrator.backends.base import WorkEffectAuthorizationRequired
from cli_agent_orchestrator.constants import FIFO_DIR


class WorkBackendView:
    """Explicit surface for one attempt's durable terminal target."""

    _READS = frozenset(
        {
            "session_exists",
            "session_exists_strict",
            "list_sessions",
            "get_history",
            "get_pane_working_directory",
            "get_pane_current_command",
            "supports_event_inbox",
            "get_pane_id",
            "get_native_status",
        }
    )
    _EFFECTS = frozenset(
        {
            "create_session",
            "create_window",
            "send_keys",
            "send_special_key",
            "pipe_pane",
            "stop_pipe_pane",
            "kill_session",
            "kill_window",
        }
    )
    _TARGET_EFFECTS = frozenset(
        {"send_keys", "send_special_key", "pipe_pane", "stop_pipe_pane", "kill_window"}
    )

    def __init__(
        self,
        backend,
        restriction,
        before_effect,
        terminal_id=None,
        expected_target=None,
        assert_open=None,
    ):
        self._backend = backend
        self._restriction = restriction
        self._guard = before_effect
        self._terminal_id = terminal_id
        if expected_target is not None and (
            not isinstance(expected_target, tuple)
            or len(expected_target) != 2
            or any(not isinstance(value, str) or not value for value in expected_target)
        ):
            raise WorkEffectAuthorizationRequired("invalid planned terminal target")
        self._expected_target = expected_target
        self._expected_pipe_path = (
            str(FIFO_DIR / f"{terminal_id}.fifo")
            if isinstance(terminal_id, str) and terminal_id
            else None
        )
        self._assert_open = assert_open or (lambda: None)
        self._authorized_targets = set()

    @staticmethod
    def _argument(args, kwargs, position, *names):
        if len(args) > position:
            return args[position]
        for name in names:
            if name in kwargs:
                return kwargs[name]
        return None

    def _require_attempt_terminal(self, terminal_id):
        if not isinstance(self._terminal_id, str) or not self._terminal_id:
            raise WorkEffectAuthorizationRequired("durable attempt terminal binding required")
        if terminal_id != self._terminal_id:
            raise WorkEffectAuthorizationRequired("effect terminal differs from admitted attempt")

    def _guard_target(
        self, effect, terminal_id, session_name=None, window_name=None, file_path=None
    ):
        self._require_attempt_terminal(terminal_id)
        if (
            effect in self._TARGET_EFFECTS
            and (session_name, window_name) not in self._authorized_targets
        ):
            raise WorkEffectAuthorizationRequired("effect target is not bound to this work view")
        return self._guard(effect, terminal_id, session_name, window_name, file_path)

    def _before(self, effect, terminal_id, session_name=None, window_name=None, file_path=None):
        self._backend._before_work_effect(
            self._restriction,
            lambda: self._guard_target(effect, terminal_id, session_name, window_name, file_path),
        )

    def bind_terminal_target(self, terminal_id, session_name, window_name):
        """Bind an existing pane only after the durable Work guard validates it."""
        self._assert_open()
        self._require_attempt_terminal(terminal_id)
        if not isinstance(session_name, str) or not session_name:
            raise WorkEffectAuthorizationRequired("terminal session is required")
        if not isinstance(window_name, str) or not window_name:
            raise WorkEffectAuthorizationRequired("terminal window is required")
        if (
            self._expected_target is not None
            and (session_name, window_name) != self._expected_target
        ):
            raise WorkEffectAuthorizationRequired("terminal target differs from the planned target")
        self._before("bind_terminal_target", terminal_id, session_name, window_name)
        self._authorized_targets.add((session_name, window_name))

    def _revalidate_before_effect(self, terminal_id=None, session_name=None, window_name=None):
        """Run the fresh guard before delivery preparation or a later effect."""
        self._assert_open()
        target_id = self._terminal_id if terminal_id is None else terminal_id
        self._require_attempt_terminal(target_id)
        if session_name is None and window_name is None:
            self._before("revalidate", target_id)
            return
        if (session_name, window_name) not in self._authorized_targets:
            raise WorkEffectAuthorizationRequired("effect target is not bound to this work view")
        self._before("revalidate_target", target_id, session_name, window_name)

    def __getattr__(self, name):
        if name in self._READS:
            return getattr(self._backend, name)
        if name not in self._EFFECTS:
            raise AttributeError(name)

        def invoke(*args, **kwargs):
            self._assert_open()
            if name in {"create_session", "create_window"}:
                session_name = self._argument(args, kwargs, 0, "session_name")
                window_name = self._argument(args, kwargs, 1, "window_name")
                terminal_id = self._argument(args, kwargs, 2, "terminal_id")
                self._require_attempt_terminal(terminal_id)
                if not isinstance(session_name, str) or not session_name:
                    raise WorkEffectAuthorizationRequired("terminal session is required")
                if not isinstance(window_name, str) or not window_name:
                    raise WorkEffectAuthorizationRequired("terminal window is required")
                if name == "create_session":
                    if (
                        self._expected_target is None
                        or (session_name, window_name) != self._expected_target
                    ):
                        raise WorkEffectAuthorizationRequired(
                            "create_session requires the exact planned target"
                        )
                    result = self._backend.create_work_session(
                        self._restriction,
                        *args,
                        before_effect=lambda: self._guard_target(
                            "create_session", terminal_id, session_name, window_name
                        ),
                        **kwargs,
                    )
                    if result != window_name:
                        raise WorkEffectAuthorizationRequired(
                            "backend created a window other than the planned target"
                        )
                    self._authorized_targets.add((session_name, result))
                    return result

                raise WorkEffectAuthorizationRequired(
                    "create_window requires a durable Work window reservation"
                )

            session_name = self._argument(args, kwargs, 0, "session_name")
            window_name = self._argument(args, kwargs, 1, "window_name")
            terminal_id = self._terminal_id
            if name in self._TARGET_EFFECTS:
                if not isinstance(session_name, str) or not session_name:
                    raise WorkEffectAuthorizationRequired("terminal session is required")
                if not isinstance(window_name, str) or not window_name:
                    raise WorkEffectAuthorizationRequired("terminal window is required")
                if name == "send_keys":
                    return self._backend.send_work_keys(
                        self._restriction,
                        *args,
                        before_effect=lambda: self._guard_target(
                            name, terminal_id, session_name, window_name
                        ),
                        **kwargs,
                    )
                file_path = None
                if name == "pipe_pane":
                    file_path = self._argument(args, kwargs, 2, "file_path")
                    if file_path != self._expected_pipe_path:
                        raise WorkEffectAuthorizationRequired(
                            "pipe_pane requires the terminal's exact FIFO path"
                        )
                self._before(name, terminal_id, session_name, window_name, file_path)
                if name == "kill_window":
                    raise WorkEffectAuthorizationRequired(
                        "Work window teardown requires a server-instance-fenced identity"
                    )
            elif name == "kill_session":
                session_name = self._argument(args, kwargs, 0, "session_name")
                if not isinstance(session_name, str) or not session_name:
                    raise WorkEffectAuthorizationRequired("terminal session is required")
                self._backend._before_work_effect(
                    self._restriction,
                    lambda: self._guard_target(name, terminal_id, session_name),
                )
                # The durable registry binds a reusable name, not a tmux
                # server/session incarnation. A fresh row guard therefore
                # cannot make a name-based teardown safe after replacement.
                raise WorkEffectAuthorizationRequired(
                    "Work session teardown requires a server-instance-fenced identity"
                )
            else:
                raise WorkEffectAuthorizationRequired("unsupported work backend effect")
            return getattr(self._backend, name)(*args, **kwargs)

        return invoke
