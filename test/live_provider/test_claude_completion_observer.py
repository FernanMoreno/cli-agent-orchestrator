"""Strict observer packet and filesystem contracts; no CLI or completion CAS."""

import io
import json
from test.live_provider import claude_completion_observer as observer

import pytest

pytestmark = pytest.mark.offline_database


def stop():
    return dict(
        hook_event_name="Stop",
        session_id="native-session",
        stop_hook_active=False,
        last_assistant_message="Completed\nCAO-TURN-RECEIPT-" + "a" * 32,
        background_tasks=[],
        session_crons=[],
    )


def packet(value):
    return io.BytesIO(json.dumps(value, ensure_ascii=False).encode())


def test_structured_native_text_is_preserved_without_terminal_parsing(tmp_path):
    value = stop()
    value["last_assistant_message"] = "Respuesta terminada 🧪\n" + value["last_assistant_message"]
    parsed = observer.read_payload(packet(value))
    path = observer.write_payload(tmp_path, parsed)
    assert json.loads(path.read_text())["payload"] == value
    assert path.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stop.json"]


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(hook_event_name="SubagentStop"),
        lambda x: x.update(session_id=None),
        lambda x: x.update(session_id=""),
        lambda x: x.update(stop_hook_active=1),
        lambda x: x.pop("last_assistant_message"),
        lambda x: x.update(last_assistant_message={"text": "not a string"}),
        lambda x: x.update(background_tasks="not a list"),
        lambda x: x.update(session_crons={}),
    ],
)
def test_wrong_event_identity_or_completion_shape_is_rejected(change):
    value = stop()
    change(value)
    with pytest.raises(ValueError):
        observer.read_payload(packet(value))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b'{"hook_event_name":"Stop","hook_event_name":"SessionStart"}',
        b'{"a":NaN}',
        b"x" * (observer.MAX_BYTES + 1),
    ],
)
def test_empty_duplicate_invalid_or_oversized_json_is_rejected(raw):
    with pytest.raises(ValueError):
        observer.read_payload(io.BytesIO(raw))


def test_first_event_is_retained_and_unsafe_directory_is_rejected(tmp_path):
    value = stop()
    path = observer.write_payload(tmp_path, value)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        observer.write_payload(tmp_path, value)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stop.json"]
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="directory"):
        observer.write_payload(tmp_path, value)


def test_start_event_needs_no_assistant_message():
    value = dict(hook_event_name="SessionStart", session_id="native-session", source="startup")
    assert observer.read_payload(packet(value)) == value


def test_unhashable_event_and_symlink_directory_fail_closed(tmp_path):
    value = stop()
    value["hook_event_name"] = {}
    with pytest.raises(ValueError):
        observer.read_payload(packet(value))
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        observer.write_payload(link, stop())


def test_inspection_settings_only_install_observers_in_the_private_home(tmp_path):
    import shlex
    import sys
    from test.live_provider.native_cancel_adapters import CLAUDE_INSPECT

    CLAUDE_INSPECT.configure(tmp_path)
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert set(settings["hooks"]) == {"SessionStart", "Stop", "StopFailure"}
    for groups in settings["hooks"].values():
        assert len(groups) == 1 and len(groups[0]["hooks"]) == 1
        hook = groups[0]["hooks"][0]
        assert hook["type"] == "command"
        assert shlex.split(hook["command"]) == [
            sys.executable,
            "-m",
            "test.live_provider.claude_completion_observer",
            "/tmp/evidence",
        ]
    assert not (tmp_path / ".claude/.credentials.json").exists()


def test_inspection_rejects_unmounted_interpreter_before_writing_settings(tmp_path, monkeypatch):
    from test.live_provider import native_cancel_adapters as adapters

    binary = tmp_path / "unmounted-python"
    binary.write_bytes(b"not executed")
    monkeypatch.setattr(adapters.sys, "executable", str(binary))
    with pytest.raises(AssertionError, match="namespace"):
        adapters.CLAUDE_INSPECT.configure(tmp_path)
    assert not (tmp_path / ".claude").exists()
