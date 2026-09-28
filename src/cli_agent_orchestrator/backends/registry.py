"""Backend registry — module-level backend singleton management.

This module provides get_backend() and set_backend() as the central access point
for the configured TerminalBackend. It has no dependencies on providers or services,
breaking the circular import chain.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from cli_agent_orchestrator.backends.base import TerminalBackend

# Module-level backend instance. Initialized lazily via get_backend().
_backend: Optional[TerminalBackend] = None
_dispatch_backend = ContextVar("work_dispatch_backend", default=None)


def get_backend() -> TerminalBackend:
    """Return the configured terminal backend (lazy-initialized via BackendFactory)."""
    scoped = _dispatch_backend.get()
    if scoped is not None:
        return scoped
    global _backend
    if _backend is None:
        from cli_agent_orchestrator.backends.factory import BackendFactory

        _backend = BackendFactory.create()
    return _backend


def set_backend(backend: TerminalBackend) -> None:
    """Set the terminal backend (called at application startup)."""
    global _backend
    _backend = backend


@contextmanager
def work_backend_scope(backend):
    """Trusted dispatch scope, inherited by its tasks/threads, never a global swap."""
    from cli_agent_orchestrator.backends.work_backend import WorkBackendView

    if not isinstance(backend, WorkBackendView):
        raise TypeError("protected work backend view required")
    token = _dispatch_backend.set(backend)
    try:
        yield
    finally:
        _dispatch_backend.reset(token)
