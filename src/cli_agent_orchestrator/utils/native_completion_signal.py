"""Bounded ephemeral native completion evidence; task authority stays in PostgreSQL.

One private directory per CLI epoch holds one atomic snapshot and one lock.
Observation readers never wait. Dispatch admission and retirement retry only
within fixed short bounds. A dispatch must arm a newer sequence;
provider-specific live-state and receipt witnesses remain mandatory.
"""

import contextlib
import fcntl
import json
import os
import secrets
import stat
import tempfile
import time
from pathlib import Path

MAX_BYTES = 1024 * 1024
SCHEMA = "caos.native-completion.v1"


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate native event key")
        value[key] = item
    return value


def _nonfinite(_value):
    raise ValueError("invalid native event number")


def decode(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("native event size")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    if type(value) is not dict:
        raise ValueError("native event object required")
    return value


def _safe(info, directory=False):
    expected = 0o700 if directory else 0o600
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not kind(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != expected
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("unsafe native channel ownership")


@contextlib.contextmanager
def _directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _safe(os.fstat(fd), directory=True)
        yield fd
    finally:
        os.close(fd)


def _read(fd, name):
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        _safe(os.fstat(source))
        with os.fdopen(source, "rb", closefd=False) as stream:
            return decode(stream.read(MAX_BYTES + 1))
    finally:
        os.close(source)


def _write(fd, name, value):
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("native snapshot size")
    # Under the channel lock: a single fixed temporary name bounds storage.
    target = ".snapshot.tmp"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(target, dir_fd=fd)
    output = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=fd,
    )
    try:
        with os.fdopen(output, "wb") as stream:
            stream.write(raw)
        os.replace(target, name, src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(target, dir_fd=fd)


@contextlib.contextmanager
def _locked(path, exclusive=False, wait_seconds=0.0):
    with _directory(path) as fd:
        lock = os.open("channel.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            _safe(os.fstat(lock))
            deadline = time.monotonic() + wait_seconds
            while True:
                try:
                    fcntl.flock(
                        lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
                    )
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    time.sleep(min(0.01, remaining))
            yield fd
        finally:
            os.close(lock)


def _state(fd, binding):
    value = _read(fd, "state.json")
    if (
        value.get("schema") != SCHEMA
        or value.get("binding") != binding
        or type(value.get("sequence")) is not int
        or not 0 <= value["sequence"] < 2**63
        or not (
            value.get("session") is None
            or (type(value["session"]) is str and 0 < len(value["session"]) <= 256)
        )
        or not (value.get("text") is None or type(value["text"]) is str)
    ):
        raise ValueError("invalid native channel snapshot")
    return value


def publish_event(directory, binding, event, session, text=None):
    """Best-effort normalized adapter event. Missing/unsafe channels stay closed."""
    try:
        if (
            event not in {"start", "stop"}
            or type(session) is not str
            or not 0 < len(session) <= 256
            or (event == "stop" and (type(text) is not str or not text))
        ):
            return
        with _locked(directory, exclusive=True, wait_seconds=1.0) as fd:
            state = _state(fd, binding)
            if event == "start":
                if state["session"] is not None:
                    return
                state["session"] = session
            else:
                if state["session"] != session:
                    return
                state["sequence"] += 1
                state["text"] = text
            _write(fd, "state.json", state)
    except (OSError, ValueError, TypeError, OverflowError):
        return


class NativeCompletionChannel:
    """One API-owned epoch and an in-memory active-dispatch fence."""

    def __init__(self, directory, binding):
        self.directory = Path(directory)
        self.binding = binding
        self._armed = None
        self._retired = False

    @classmethod
    def create(cls, parent, provider, terminal):
        directory = Path(tempfile.mkdtemp(prefix="native-completion-", dir=parent))
        try:
            binding = {"provider": provider, "terminal": terminal, "epoch": secrets.token_hex(32)}
            channel = cls(directory, binding)
            with _directory(directory) as fd:
                lock = os.open(
                    "channel.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd
                )
                os.close(lock)
                _write(
                    fd,
                    "state.json",
                    {
                        "schema": SCHEMA,
                        "binding": binding,
                        "session": None,
                        "sequence": 0,
                        "text": None,
                    },
                )
        except BaseException:
            # No callback has been launched. Remove only this partial epoch's
            # known entries; preserve an unsafe or unexpectedly nonempty path.
            with contextlib.suppress(OSError, ValueError):
                with _directory(directory) as fd:
                    for name in ("state.json", "channel.lock", ".snapshot.tmp"):
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(name, dir_fd=fd)
                directory.rmdir()
            raise
        return channel

    def arm(self, generation, receipt_hash):
        self._armed = None
        if self._retired:
            return
        try:
            with _locked(self.directory, wait_seconds=0.25) as fd:
                state = _state(fd, self.binding)
            self._armed = (generation, receipt_hash, state["session"], state["sequence"])
        except (OSError, ValueError, TypeError, RecursionError):
            pass

    def result(self, generation: str | None, receipt_hash: str | None) -> str | None:
        armed = self._armed
        if self._retired or armed is None or armed[:2] != (generation, receipt_hash):
            return None
        try:
            with _locked(self.directory) as fd:
                state = _state(fd, self.binding)
            if (
                state["session"] is not None
                and (armed[2] is None or state["session"] == armed[2])
                and state["sequence"] > armed[3]
                and isinstance(state["text"], str)
            ):
                return state["text"]
        except (OSError, ValueError, TypeError, RecursionError):
            pass
        return None

    def close(self):
        """Retire only known files; preserve unexpected contents for inspection."""
        self._armed = None
        self._retired = True
        try:
            with _locked(self.directory, exclusive=True, wait_seconds=1.0) as fd:
                for name in ("state.json", "settings.json", ".snapshot.tmp", "channel.lock"):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(name, dir_fd=fd)
            self.directory.rmdir()
            return True
        except (OSError, ValueError, TypeError, RecursionError):
            return False
