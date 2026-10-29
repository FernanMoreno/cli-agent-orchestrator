"""Normalize Claude native hooks into the shared ephemeral evidence channel."""

import sys
from pathlib import Path

from cli_agent_orchestrator.utils.native_completion_signal import MAX_BYTES, decode, publish_event


def normalize(payload):
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        return "start", payload.get("session_id"), None
    if (
        event == "Stop"
        and payload.get("stop_hook_active") is False
        and type(payload.get("background_tasks")) is list
        and payload["background_tasks"] == []
        and type(payload.get("session_crons")) is list
        and payload["session_crons"] == []
    ):
        return "stop", payload.get("session_id"), payload.get("last_assistant_message")
    return None


def main():
    # A telemetry failure must not block/alter the user's native hook lifecycle.
    try:
        if len(sys.argv) != 5:
            return 0
        directory, terminal, epoch, provider = sys.argv[1:]
        payload = decode(sys.stdin.buffer.read(MAX_BYTES + 1))
        event = normalize(payload)
        if event is not None:
            publish_event(
                Path(directory),
                {
                    "terminal": terminal,
                    "epoch": epoch,
                    "provider": provider,
                },
                *event,
            )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
