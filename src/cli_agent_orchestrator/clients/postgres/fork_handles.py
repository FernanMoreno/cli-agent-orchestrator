"""Detach inherited libpq sockets before child finalizers can affect parents."""

import os
import weakref
from typing import Any

# Weak references do not extend normal connection lifetimes. Fork callbacks
# must never acquire a lock which another parent thread might have held.
_HANDLES: dict[int, weakref.ReferenceType[Any]] = {}
_UNDETACHED: list[Any] = []


def track_storage_handle(raw: Any) -> None:
    key = id(raw)

    def forgotten(reference: weakref.ReferenceType[Any]) -> None:
        if _HANDLES.get(key) is reference:
            _HANDLES.pop(key, None)

    _HANDLES[key] = weakref.ref(raw, forgotten)


def _after_fork_child() -> None:
    global _HANDLES
    inherited = tuple(_HANDLES.values())
    _HANDLES = {}
    for reference in inherited:
        raw = reference()
        if raw is None:
            continue
        try:
            if raw.closed:
                continue
            socket_fd = raw.pgconn.socket
            if socket_fd < 0:
                continue
            replacement = os.open(os.devnull, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
            try:
                os.dup2(replacement, socket_fd, inheritable=False)
            finally:
                os.close(replacement)
        except BaseException:
            # Keep failed detachments alive instead of invoking libpq finalizers.
            # No admission owner, reservation or parent state is reset here.
            _UNDETACHED.append(raw)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_child)
