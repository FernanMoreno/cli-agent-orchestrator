"""Bounded newline-framed reads for the T097 ready handshake."""

from __future__ import annotations

import os
import select
import time
from typing import BinaryIO


def read_newline_frame(
    stream: BinaryIO, *, timeout: float = 30.0, max_bytes: int = 4096
) -> bytes | None:
    """Return one newline-terminated frame, or ``None`` by the fixed deadline."""
    if timeout < 0 or max_bytes <= 0:
        raise ValueError("timeout must be non-negative and max_bytes positive")

    deadline = time.monotonic() + timeout
    frame = bytearray()
    fd = stream.fileno()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            return None
        chunk = os.read(fd, 1)
        if not chunk:
            return bytes(frame)
        frame.extend(chunk)
        if len(frame) > max_bytes:
            raise ValueError("ready frame exceeded its fixed byte limit")
        if chunk == b"\n":
            return bytes(frame)
