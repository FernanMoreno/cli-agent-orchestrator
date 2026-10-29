"""Bound semantic progress to exact new FIFO bytes using the production compositor."""

import codecs
import os
from pathlib import Path

from cli_agent_orchestrator.services.status_monitor import StatusMonitor


class NativeOutputWitness:
    def __init__(self, request, terminal_id, log, offset):
        self.request = request
        self.terminal_id = terminal_id
        self.log = Path(log)
        info = self.log.stat()
        self.identity = (info.st_dev, info.st_ino)
        assert type(offset) is int and 0 <= offset <= info.st_size
        self.offset = 0
        self.baseline = offset
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.monitor = StatusMonitor()
        with self.monitor._lock:
            self.monitor._feed_screen_locked("witness", "")
            # Actual geometry is independently required by every gateway ledger.
            self.monitor._screens["witness"][0].resize(lines=50, columns=220)
        self._read_to(offset)
        self.previous_screen = self._screen()

    def _screen(self):
        lines, _ = self.monitor._screen_lines("witness")
        assert lines is not None
        return "\n".join(lines)

    def _read_to(self, end):
        with self.log.open("rb") as source:
            info = os.fstat(source.fileno())
            assert (info.st_dev, info.st_ino) == self.identity
            assert self.offset <= end <= info.st_size
            source.seek(self.offset)
            while self.offset < end:
                length = min(65536, end - self.offset)
                raw = source.read(length)
                assert len(raw) == length
                value = self.request(
                    "GET",
                    self.terminal_id,
                    "/output/range",
                    params={"offset": self.offset, "length": length},
                )
                assert value["terminal_id"] == self.terminal_id
                assert value["offset"] == self.offset and value["length"] == length
                assert value["data"] == raw.decode("utf-8", errors="replace")
                with self.monitor._lock:
                    self.monitor._feed_screen_locked("witness", self.decoder.decode(raw))
                self.offset += length

    def observe(self, marker):
        assert (
            marker and marker not in self.previous_screen
        ), "marker_already_visible_before_boundary"
        self._read_to(self.log.stat().st_size)
        count = self.offset - self.baseline
        return count if count > 0 and marker in self._screen() else False
