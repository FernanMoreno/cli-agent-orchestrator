"""Private native-hook observation only; never a runtime completion authority."""

import json
import os
import stat
import sys
import uuid
from pathlib import Path

MAX_BYTES = 1024 * 1024
EVENT_FILES = {
    "SessionStart": "session-start.json",
    "Stop": "stop.json",
    "StopFailure": "stop-failure.json",
}


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate_hook_key")
        result[name] = value
    return result


def _invalid_number(value):
    raise ValueError("hook_number_invalid")


def read_payload(source):
    raw = source.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("hook_body_limit")
    payload = json.loads(
        raw,
        object_pairs_hook=_object,
        parse_constant=_invalid_number,
    )
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("hook_event_name"), str)
        or payload["hook_event_name"] not in EVENT_FILES
    ):
        raise ValueError("hook_event_invalid")
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid or len(sid) > 256:
        raise ValueError("hook_session_invalid")
    if payload["hook_event_name"] == "Stop":
        if type(payload.get("stop_hook_active")) is not bool:
            raise ValueError("hook_stop_state_invalid")
        text = payload.get("last_assistant_message")
        if not isinstance(text, str) or not text:
            raise ValueError("hook_final_text_missing")
        for key in ("background_tasks", "session_crons"):
            if key in payload and not isinstance(payload[key], list):
                raise ValueError("hook_background_shape_invalid")
    return payload


def write_payload(directory, payload):
    directory = Path(directory)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("hook_directory_invalid")
    target = directory / EVENT_FILES[payload["hook_event_name"]]
    temporary = directory / (".hook-" + uuid.uuid4().hex + ".tmp")
    value = {"schema": "caos.claude-hook-observation.v1", "payload": payload}
    body = (json.dumps(value, ensure_ascii=False) + "\n").encode()
    if len(body) > MAX_BYTES + 1024:
        raise ValueError("hook_record_limit")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(body)
        # Atomic exclusive publication; an earlier event is never overwritten.
        os.link(temporary, target, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("hook_output_directory_required")
        write_payload(sys.argv[1], read_payload(sys.stdin.buffer))
    except Exception:
        print("native_hook_observation_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
