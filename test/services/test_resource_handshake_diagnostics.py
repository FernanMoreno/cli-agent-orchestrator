"""Safe diagnostics for failures before an owned-child binding is issued."""

import importlib
import importlib.util
import json
import os
import signal
import socket
import struct
import sys
import traceback
from typing import Literal, get_args, get_origin, get_type_hints

import pytest


def api():
    name = "cli_agent_orchestrator.services.resource_process_supervisor"
    assert importlib.util.find_spec(name) is not None, "resource process supervisor is missing"
    return importlib.import_module(name)


def _literal_values(annotation):
    parts = get_args(annotation)
    assert type(None) in parts, "diagnostic fields must be optional"
    literal = next(part for part in parts if get_origin(part) is Literal)
    return frozenset(get_args(literal))


def test_diagnostic_fields_are_lazy_typed_and_allowlisted():
    error_type = api().ResourceProcessUnproven
    annotations = get_type_hints(error_type)
    assert _literal_values(annotations["failure_stage"]) == frozenset(
        {
            "channel_setup",
            "recvmsg",
            "ancillary",
            "packet_frame",
            "sender_credentials",
            "packet_decode",
            "packet_schema",
            "binding_decode",
            "kernel_validation",
            "subject_issue",
        }
    )
    assert _literal_values(annotations["failure_type"]) == frozenset(
        {
            "os_error",
            "value_error",
            "type_error",
            "index_error",
            "key_error",
            "process_error",
            "protocol_error",
        }
    )
    existing = error_type("existing generic failure")
    assert existing.failure_stage is None and existing.failure_type is None


@pytest.mark.parametrize(
    ("fault", "expected_stage", "expected_type"),
    [
        ("recvmsg", "recvmsg", "os_error"),
        ("packet_schema", "packet_schema", "protocol_error"),
    ],
)
@pytest.mark.parametrize("notes_supported", [True, False])
def test_pre_subject_rejection_keeps_generic_error_and_never_acknowledges(
    fault, expected_stage, expected_type, notes_supported, monkeypatch
):
    from test.services.test_resource_process_supervisor import child

    module = api()
    if notes_supported:
        if not callable(getattr(module.ResourceProcessUnproven, "add_note", None)):
            pytest.skip("this Python runtime has no Exception.add_note support")
    else:
        monkeypatch.setattr(module.ResourceProcessUnproven, "add_note", None, raising=False)
    with child() as owner:
        original_channel = owner._channel

        class InjectedChannel:
            def __init__(self):
                self.acknowledgments = []

            def settimeout(self, value):
                pass

            def recvmsg(self, *args):
                if fault == "recvmsg":
                    raise TimeoutError("private-packet-payload-must-not-escape")
                credentials = struct.pack("3i", owner._process.pid, os.getuid(), 0)
                return (
                    b"{}",
                    [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, credentials)],
                    0,
                    None,
                )

            def send(self, payload):
                self.acknowledgments.append(payload)
                return len(payload)

            def close(self):
                original_channel.close()

        channel = InjectedChannel()
        owner._channel = channel
        before_ack_calls = []

        with pytest.raises(module.ResourceProcessUnproven) as rejected:
            owner._receive_binding(
                timeout_s=2,
                before_ack=lambda subject: before_ack_calls.append(subject),
            )

        error = rejected.value
        assert str(error) == "resource process binding could not be verified"
        assert error.args == ("resource process binding could not be verified",)
        assert error.__suppress_context__ is True and error.__cause__ is None
        assert error.failure_stage == expected_stage
        assert error.failure_type == expected_type
        diagnostic = {
            "failure_stage": expected_stage,
            "failure_type": expected_type,
            "schema": "caos.resource-binding-rejection.v1",
        }
        canonical_note = json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
        if notes_supported:
            assert error.__notes__ == [canonical_note]
            assert json.loads(error.__notes__[0]) == diagnostic
            assert set(json.loads(error.__notes__[0])) == {
                "schema",
                "failure_stage",
                "failure_type",
            }
            assert set(vars(error)) == {"failure_stage", "failure_type", "__notes__"}
        else:
            assert getattr(error, "__notes__", []) == []
            assert set(vars(error)) == {"failure_stage", "failure_type"}
        assert "private-packet-payload-must-not-escape" not in repr(error)
        assert "private-packet-payload-must-not-escape" not in repr(vars(error))
        notes = getattr(error, "__notes__", [""])
        assert "private-packet-payload-must-not-escape" not in notes[0]
        formatted_traceback = "".join(traceback.format_exception(error))
        assert (canonical_note in formatted_traceback) is notes_supported
        assert "private-packet-payload-must-not-escape" not in formatted_traceback
        assert before_ack_calls == []
        assert channel.acknowledgments == []
        assert owner._binding is None
        assert owner.sql_application_name is None
        assert owner._receiving is False


def test_popen_failure_without_add_note_preserves_original_exception(monkeypatch):
    module = api()

    class NoNotesRuntimeError(RuntimeError):
        add_note = None

    original = NoNotesRuntimeError("controlled Popen failure")

    def fail_popen(*args, **kwargs):
        raise original

    monkeypatch.setattr(module.subprocess, "Popen", fail_popen)
    with pytest.raises(NoNotesRuntimeError) as rejected:
        module.ResourceProcessSupervisor.launch(
            [sys.executable, "-c", "pass"],
            expected_store_id="d18bcbac-9c97-4408-baf0-24f7e2331486",
            expected_target_identity_sha256="a" * 64,
        )

    # A Popen failure does not establish whether a process started; retain the
    # failure itself without adding a false no-start result or note.
    assert rejected.value is original
    assert str(rejected.value) == "controlled Popen failure"
    assert getattr(rejected.value, "__notes__", []) == []


def test_post_popen_identity_failure_without_add_note_keeps_exact_join_owner(
    monkeypatch,
):
    module = api()

    class NoNotesRuntimeError(RuntimeError):
        add_note = None

    original = NoNotesRuntimeError("controlled child identity failure")
    started = []
    owner = None
    real_popen = module.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    def fail_identity(*args):
        raise original

    monkeypatch.setattr(module.subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(module, "_identity", fail_identity)
    try:
        with pytest.raises(NoNotesRuntimeError) as rejected:
            module.ResourceProcessSupervisor.launch(
                [sys.executable, "-c", "import time; time.sleep(600)"],
                expected_store_id="d18bcbac-9c97-4408-baf0-24f7e2331486",
                expected_target_identity_sha256="a" * 64,
            )
        assert rejected.value is original
        owner = getattr(rejected.value, "resource_process_owner", None)
        assert owner is not None
        assert len(started) == 1
        assert owner._process is started[0]
        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout_s=10) is None
        assert owner.observation is None
        assert owner._process.returncode is not None
        assert getattr(rejected.value, "__notes__", []) == []
    finally:
        exact_owner = owner or getattr(original, "resource_process_owner", None)
        if exact_owner is not None:
            if exact_owner.observation is None:
                exact_owner.send_signal(signal.SIGTERM)
                exact_owner.wait(timeout_s=10)
            exact_owner.close()
        for process in started:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
