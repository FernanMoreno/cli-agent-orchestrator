"""Ordinary remote native effects require the current durable dispatch owner."""

from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement


class OrdinaryRemoteEffectFence:
    """A private bridge adapter; provides no Work process enforcement."""

    def __init__(self, native, store, op_id, epoch):
        self._native, self._store, self._op_id, self._epoch = native, store, op_id, epoch

    def preflight_work(self, contract):
        raise UnsupportedWorkEnforcement("RemoteRuntime")

    def _before_effect(self):
        if not self._store.effect_is_current(self._op_id, connection_epoch=self._epoch):
            from cli_agent_orchestrator.runtime_channel.registry import RemoteOutcomeUnknownError

            raise RemoteOutcomeUnknownError(
                "Remote dispatch authority expired or changed before native effect"
            )

    def __getattr__(self, name):
        value = getattr(self._native, name)
        if not callable(value):
            return value

        def fenced(*args, **kwargs):
            # Reads may observe late proof. Every physical write remains fenced.
            if name not in {
                "get_history",
                "capture_pane",
                "get_pane_current_command",
                "get_pane_working_directory",
                "session_exists",
                "session_exists_strict",
                "list_sessions",
                "list_windows",
                "supports_event_inbox",
                "get_backend_name",
                "has_session",
            }:
                self._before_effect()
            return value(*args, **kwargs)

        return fenced
