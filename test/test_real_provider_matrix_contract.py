"""Fast, credential-free contracts for the real-provider matrix harness."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from test.e2e import test_real_provider_matrix as matrix
from test.fixtures import cao_server as server_fixture
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_contract_auth_home(monkeypatch, tmp_path):
    """Keep readiness contracts explicit without touching a runner login."""
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(tmp_path))


def _provider_names(count: int = 1) -> list[str]:
    """Use registered provider ids without turning the contract into a fixed list."""

    assert len(matrix.PROVIDERS) >= count
    return list(matrix.PROVIDERS[:count])


def _provider_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "model": "matrix-test-model",
        "binary": "matrix-test-cli",
        "auth_env": ["CAO_MATRIX_TEST_AUTH"],
        "auth_files": [".matrix/auth.json"],
        "capabilities": ["native_children"],
    }
    config.update(overrides)
    return config


def _set_manifest(
    monkeypatch: pytest.MonkeyPatch,
    providers: dict[str, dict[str, object]],
) -> None:
    monkeypatch.setenv(matrix._PROVIDERS_ENV, json.dumps(providers))


def _skip_reason(parameter: pytest.ParameterSet) -> str:
    assert parameter.values
    cell = parameter.values[0]
    assert isinstance(cell, matrix.MatrixCell)
    assert cell.skip_reason is not None
    return cell.skip_reason


def test_manifest_loads_reviewed_registered_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config()})

    loaded = matrix._load_matrix_providers()

    assert loaded[provider] == matrix.MatrixProvider(
        name=provider,
        model="matrix-test-model",
        binary="matrix-test-cli",
        auth_env=("CAO_MATRIX_TEST_AUTH",),
        auth_files=(Path(".matrix/auth.json"),),
        capabilities=frozenset({"native_children"}),
    )


@pytest.mark.parametrize(
    ("requested", "returned", "expected"),
    (
        ("matrix-session", "cao-matrix-session", "cao-matrix-session"),
        ("cao-existing-session", "cao-existing-session", "cao-existing-session"),
    ),
)
def test_created_session_name_matches_terminal_service_namespace(
    requested: str, returned: object, expected: str
) -> None:
    assert matrix._validate_created_session_name(requested, returned) == expected


@pytest.mark.parametrize("returned", (None, 3, "matrix-session", "cao-other-session"))
def test_created_session_name_rejects_noncanonical_response(returned: object) -> None:
    with pytest.raises(ValueError, match="^created_session_name_mismatch$"):
        matrix._validate_created_session_name("matrix-session", returned)


def test_create_parent_returns_canonical_name_and_cleans_bad_response_by_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    server = SimpleNamespace(url="http://matrix.test", home_dir=tmp_path)

    def request(method: str, url: str, **kwargs: object) -> SimpleNamespace:
        calls.append((method, url, kwargs))
        if method == "POST":
            return _response(201, {"id": "parent-123", "session_name": "unexpected"})
        if method == "DELETE":
            return _response(200, {"deleted": True})
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(matrix, "_request", request)
    monkeypatch.setattr(matrix, "_wait_for_terminal_ready", lambda *_args: None)
    with pytest.raises(ValueError, match="^created_session_name_mismatch$"):
        matrix._create_parent(server, "claude_code", "matrix-profile")

    post_method, _post_url, post_kwargs = calls[0]
    requested_name = post_kwargs["params"]["session_name"]
    assert post_method == "POST"
    assert isinstance(requested_name, str)
    assert requested_name.startswith("real-e2e-")
    delete_method, delete_url, _delete_kwargs = calls[1]
    assert delete_method == "DELETE"
    assert delete_url == f"{server.url}/sessions/{requested_name}"


def test_create_parent_cleans_requested_name_after_malformed_json_and_preserves_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    server = SimpleNamespace(url="http://matrix.test", home_dir=tmp_path)
    parse_error = ValueError("malformed response")

    class MalformedResponse:
        status_code = 201

        def json(self) -> object:
            raise parse_error

    def request(method: str, url: str, **kwargs: object) -> object:
        calls.append((method, url, kwargs))
        if method == "POST":
            return MalformedResponse()
        if method == "DELETE":
            return _response(200, {"deleted": True})
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(matrix, "_request", request)
    with pytest.raises(ValueError) as raised:
        matrix._create_parent(server, "claude_code", "matrix-profile")

    assert raised.value is parse_error
    requested_name = calls[0][2]["params"]["session_name"]
    assert calls[1][0] == "DELETE"
    assert calls[1][1] == f"{server.url}/sessions/{requested_name}"


@pytest.mark.parametrize("terminal_id", ("", None, 7))
def test_create_parent_cleans_requested_name_after_invalid_terminal_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, terminal_id: object
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    server = SimpleNamespace(url="http://matrix.test", home_dir=tmp_path)

    def request(method: str, url: str, **kwargs: object) -> object:
        calls.append((method, url, kwargs))
        if method == "POST":
            requested = kwargs["params"]["session_name"]
            return _response(
                201,
                {"id": terminal_id, "session_name": f"cao-{requested}"},
            )
        if method == "DELETE":
            return _response(200, {"deleted": True})
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(matrix, "_request", request)
    with pytest.raises(ValueError, match="^parent_session_response_invalid$"):
        matrix._create_parent(server, "claude_code", "matrix-profile")

    requested_name = calls[0][2]["params"]["session_name"]
    assert calls[1][0] == "DELETE"
    assert calls[1][1] == f"{server.url}/sessions/{requested_name}"


def test_create_parent_cleans_requested_name_after_readiness_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    server = SimpleNamespace(url="http://matrix.test", home_dir=tmp_path)
    readiness_error = RuntimeError("readiness failed")

    def request(method: str, url: str, **kwargs: object) -> object:
        calls.append((method, url, kwargs))
        if method == "POST":
            requested = kwargs["params"]["session_name"]
            return _response(201, {"id": "parent-123", "session_name": f"cao-{requested}"})
        if method == "DELETE":
            return _response(200, {"deleted": True})
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(matrix, "_request", request)
    monkeypatch.setattr(
        matrix, "_wait_for_terminal_ready", lambda *_args: (_ for _ in ()).throw(readiness_error)
    )
    with pytest.raises(RuntimeError) as raised:
        matrix._create_parent(server, "claude_code", "matrix-profile")

    assert raised.value is readiness_error
    requested_name = calls[0][2]["params"]["session_name"]
    assert calls[1][0] == "DELETE"
    assert calls[1][1] == f"{server.url}/sessions/{requested_name}"


def test_create_parent_keeps_primary_contract_error_when_requested_name_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = SimpleNamespace(url="http://matrix.test", home_dir=tmp_path)
    requested_names: list[str] = []
    deleted_urls: list[str] = []

    def request(method: str, url: str, **kwargs: object) -> object:
        if method == "POST":
            requested = kwargs["params"]["session_name"]
            requested_names.append(requested)
            return _response(201, {"id": "parent-123", "session_name": "bad-response-name"})
        if method == "DELETE":
            deleted_urls.append(url)
            raise RuntimeError("cleanup transport failed")
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(matrix, "_request", request)
    with pytest.raises(ValueError) as raised:
        matrix._create_parent(server, "claude_code", "matrix-profile")

    assert str(raised.value) == "created_session_name_mismatch"
    assert requested_names
    assert deleted_urls == [f"{server.url}/sessions/{requested_names[0]}"]
    assert any(
        "created session cleanup by requested name unconfirmed" in note
        for note in getattr(raised.value, "__notes__", ())
    )


def test_create_parent_cleanup_note_falls_back_when_add_note_is_not_callable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class LegacyNoteError(ValueError):
        add_note = None

    server = SimpleNamespace(url="http://matrix.test", home_dir=tmp_path)

    def request(method: str, url: str, **kwargs: object) -> object:
        if method == "POST":
            requested = kwargs["params"]["session_name"]
            return _response(201, {"id": "parent-123", "session_name": f"cao-{requested}"})
        if method == "DELETE":
            raise RuntimeError("cleanup transport failed")
        raise AssertionError(f"unexpected request: {method} {url}")

    def fail_validation(*_args: object) -> str:
        raise LegacyNoteError("primary validation error")

    monkeypatch.setattr(matrix, "_request", request)
    monkeypatch.setattr(matrix, "_validate_created_session_name", fail_validation)
    with pytest.raises(LegacyNoteError) as raised:
        matrix._create_parent(server, "claude_code", "matrix-profile")

    assert str(raised.value) == "primary validation error"
    assert raised.value.__notes__ == ["created session cleanup by requested name unconfirmed"]


def test_matrix_fixture_owns_tmux_root_and_closes_it_before_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    root = tmp_path / "private-tmux"
    home = tmp_path / "live_provider_cao_home"
    auth_key = "CAO_MATRIX_AUTH_SENTINEL"
    auth_environment = {auth_key: "fixture-value"}
    provider = matrix.MatrixProvider(
        name="claude_code",
        model="matrix-test-model",
        binary="claude",
        auth_env=(),
        auth_files=(),
        capabilities=frozenset({"native_children"}),
    )
    cell = matrix.MatrixCell(parent=provider, child=provider)
    server = SimpleNamespace(home_dir=home, stop=lambda: events.append("server_stop"))
    observed: dict[str, object] = {}

    def create_root() -> Path:
        events.append("tmux_root_create")
        root.mkdir(mode=0o700)
        return root

    def start_server(
        passed_home: Path,
        _port: int,
        *,
        extra_env: dict[str, str],
        deadline: float,
        allowed_external_credentials: frozenset[str],
    ) -> SimpleNamespace:
        events.append("server_start")
        passed_home.mkdir(mode=0o700)
        observed.update(
            {
                "home": passed_home,
                "extra_env": extra_env,
                "deadline": deadline,
                "allowed": allowed_external_credentials,
            }
        )
        return server

    def cleanup_root(
        passed_root: Path,
        passed_home: Path,
        _tmux_module: object,
        *,
        owner_stopped: bool,
    ) -> bool:
        events.append("tmux_root_cleanup")
        assert passed_root == root
        assert passed_home == home
        assert owner_stopped is True
        return True

    monkeypatch.setattr(matrix, "_configured_auth_home", lambda: tmp_path)
    monkeypatch.setattr(
        matrix,
        "_matrix_server_auth_environment",
        lambda _cell: (auth_environment, frozenset({auth_key})),
    )
    monkeypatch.setattr(matrix.owned_tmux, "create_private_tmux_tmpdir", create_root)
    monkeypatch.setattr(matrix.owned_tmux, "cleanup_private_tmux_root", cleanup_root)
    monkeypatch.setattr(matrix, "_start_cao_server", start_server)
    monkeypatch.setattr(matrix, "_pick_free_port", lambda: 42342)
    monkeypatch.setattr(matrix, "_fixture_health_timeout", lambda: 5.0)
    monkeypatch.setattr(matrix, "_registered_cao_server_for_home", lambda _home: None)
    monkeypatch.setattr(
        matrix,
        "_remove_fixture_home_after_confirmed_stop",
        lambda passed_home, _tmp_path: (events.append("home_remove"), shutil.rmtree(passed_home)),
    )
    request = SimpleNamespace(node=SimpleNamespace(callspec=SimpleNamespace(params={"cell": cell})))

    fixture = matrix.live_provider_cao_server.__wrapped__(tmp_path, request)
    assert next(fixture) is server
    with pytest.raises(StopIteration):
        next(fixture)

    assert observed["home"] == home
    assert observed["allowed"] == frozenset({auth_key})
    passed_env = observed["extra_env"]
    assert isinstance(passed_env, dict)
    assert passed_env[auth_key] == "fixture-value"
    assert passed_env["TMUX"] == ""
    assert passed_env["TMUX_TMPDIR"] == str(root)
    assert passed_env["TMPDIR"] == str(root)
    assert events == [
        "tmux_root_create",
        "server_start",
        "server_stop",
        "tmux_root_cleanup",
        "home_remove",
    ]
    assert not home.exists()


def test_matrix_fixture_preserves_home_when_private_tmux_cleanup_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "private-tmux"
    home = tmp_path / "live_provider_cao_home"
    provider = matrix.MatrixProvider(
        name="claude_code",
        model="matrix-test-model",
        binary="claude",
        auth_env=(),
        auth_files=(),
        capabilities=frozenset({"native_children"}),
    )
    cell = matrix.MatrixCell(parent=provider, child=provider)
    server = SimpleNamespace(home_dir=home, stop=lambda: None)

    def create_root() -> Path:
        root.mkdir(mode=0o700)
        return root

    def start_server(passed_home: Path, *_args: object, **_kwargs: object) -> SimpleNamespace:
        passed_home.mkdir(mode=0o700)
        return server

    monkeypatch.setattr(matrix, "_configured_auth_home", lambda: tmp_path)
    monkeypatch.setattr(matrix, "_matrix_server_auth_environment", lambda _cell: ({}, frozenset()))
    monkeypatch.setattr(matrix.owned_tmux, "create_private_tmux_tmpdir", create_root)
    monkeypatch.setattr(
        matrix.owned_tmux, "cleanup_private_tmux_root", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(matrix, "_start_cao_server", start_server)
    monkeypatch.setattr(matrix, "_pick_free_port", lambda: 42342)
    monkeypatch.setattr(matrix, "_fixture_health_timeout", lambda: 5.0)
    monkeypatch.setattr(matrix, "_registered_cao_server_for_home", lambda _home: None)
    monkeypatch.setattr(
        matrix,
        "_remove_fixture_home_after_confirmed_stop",
        lambda *_args: pytest.fail("HOME removed"),
    )
    request = SimpleNamespace(node=SimpleNamespace(callspec=SimpleNamespace(params={"cell": cell})))

    fixture = matrix.live_provider_cao_server.__wrapped__(tmp_path, request)
    assert next(fixture) is server
    with pytest.raises(RuntimeError, match="^live provider cleanup incomplete$"):
        fixture.close()
    assert root.is_dir()
    assert home.is_dir()


_QUOTA_TERMINAL_ID = "quota-terminal-123"
_QUOTA_CHILD_ID = "quota-child-456"
_QUOTA_PARENT_ID = "parent-terminal-789"
_OUTPUT_SENTINEL = "PRIVATE_OUTPUT_SENTINEL"


def _response(status: int, body: object, text: str = "<redacted>") -> SimpleNamespace:
    return SimpleNamespace(status_code=status, json=lambda: body, text=text)


def _quota_receipt(
    *,
    state: str = "reconcile",
    child_id: str = _QUOTA_CHILD_ID,
    terminal_id: str = _QUOTA_TERMINAL_ID,
) -> dict[str, object]:
    return {
        "id": child_id,
        "parent_terminal_id": _QUOTA_PARENT_ID,
        "terminal_id": terminal_id,
        "provider": "claude_code",
        "agent_profile": "matrix-profile",
        "state": state,
        "error_kind": "quota_wait",
        "settled_at": "2026-09-24T10:00:00Z" if state == "succeeded" else None,
    }


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _run_quota_observation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider_may_resume: bool = True,
    delivery_may_have_occurred: bool = True,
    receipts: list[dict[str, object]] | None = None,
    terminal_states: list[str] | None = None,
    output_status: int = 200,
    output_marker_present: bool = True,
    terminal_cleanup_failure: bool = False,
    quota_observation_seconds: str | None = "1",
) -> tuple[object, list[tuple[str, str]], list[tuple[str, str, dict[str, object]]]]:
    """Run the quota branch through `_request` doubles and a monotonic fake clock."""
    marker_holder: list[str] = []
    calls: list[tuple[str, str, dict[str, object]]] = []
    receipt_snapshots = list(receipts or [_quota_receipt()])
    terminal_deleted = False
    last_receipt_state: str | None = None
    marker_observed = False
    observed_terminal_states = list(terminal_states or ["waiting_quota"])
    server = SimpleNamespace(url="http://matrix.test")
    clock = _FakeClock()
    monkeypatch.setattr(matrix.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(matrix.time, "sleep", clock.sleep)
    monkeypatch.setattr(matrix, "_RECEIPT_WAIT_SECONDS", 1.0)
    if quota_observation_seconds is None:
        monkeypatch.delenv("CAO_REAL_PROVIDER_E2E_QUOTA_OBSERVATION_SECONDS", raising=False)
    else:
        monkeypatch.setenv(
            "CAO_REAL_PROVIDER_E2E_QUOTA_OBSERVATION_SECONDS",
            quota_observation_seconds,
        )

    def next_receipt() -> dict[str, object]:
        if len(receipt_snapshots) > 1:
            return receipt_snapshots.pop(0)
        return receipt_snapshots[0]

    def next_terminal_state() -> str:
        if len(observed_terminal_states) > 1:
            return observed_terminal_states.pop(0)
        return observed_terminal_states[0]

    def request(method: str, url: str, **kwargs: object) -> SimpleNamespace:
        nonlocal last_receipt_state, marker_observed, terminal_deleted
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/terminals/run-step"):
            posted_body = kwargs["json"]
            assert isinstance(posted_body, dict)
            assert posted_body["prompt_redelivery"] is False
            prompt = posted_body["prompt"]
            marker_holder.append(
                next(
                    part.rstrip(".")
                    for part in prompt.split()
                    if part.startswith("CAO_REAL_PROVIDER_MATRIX_")
                )
            )
            detail = {
                "kind": "quota_wait",
                "terminal_id": _QUOTA_TERMINAL_ID,
                "native_child_id": _QUOTA_CHILD_ID,
                "action": "wait_for_quota",
                "delivery_may_have_occurred": delivery_may_have_occurred,
                "retryable": False,
                "provider_may_resume": provider_may_resume,
            }
            return _response(409, {"detail": detail})
        if method == "GET" and url.endswith(f"/terminals/{_QUOTA_TERMINAL_ID}"):
            return _response(200, {"id": _QUOTA_TERMINAL_ID, "status": next_terminal_state()})
        if method == "GET" and url.endswith(f"/native-children/{_QUOTA_CHILD_ID}"):
            receipt = dict(next_receipt())
            last_receipt_state = str(receipt.get("state"))
            if terminal_deleted:
                receipt["cleanup_completed_at"] = "2026-09-24T10:01:00Z"
            return _response(200, receipt)
        if method == "DELETE" and url.endswith(f"/terminals/{_QUOTA_TERMINAL_ID}"):
            assert last_receipt_state == "succeeded"
            assert marker_observed
            if terminal_cleanup_failure:
                return _response(500, {"detail": "cleanup failed"})
            terminal_deleted = True
            return _response(200, {"deleted": True})
        if method == "GET" and url.endswith(f"/terminals/{_QUOTA_PARENT_ID}/children"):
            return _response(200, [next_receipt()])
        if method == "GET" and url.endswith(f"/terminals/{_QUOTA_TERMINAL_ID}/output"):
            output = (
                f"{marker_holder[0]}\n{_OUTPUT_SENTINEL}" if marker_holder else _OUTPUT_SENTINEL
            )
            if not output_marker_present:
                output = _OUTPUT_SENTINEL
            marker_observed = output_status == 200 and marker_holder[0] in output
            return _response(
                output_status,
                {"output": output, "mode": "last"},
            )
        raise AssertionError(f"unexpected local harness request: {method} {url}")

    properties: list[tuple[str, str]] = []
    monkeypatch.setattr(matrix, "_request", request)
    outcome: object = None
    try:
        outcome = matrix._run_cross_provider_step(
            server,
            cell_id="claude_code->claude_code",
            parent_id=_QUOTA_PARENT_ID,
            session_name="session123",
            provider="claude_code",
            profile="matrix-profile",
            record_property=lambda name, value: properties.append((name, value)),
        )
    except BaseException as exc:
        outcome = exc
    return outcome, properties, calls


def _evidence(properties: list[tuple[str, str]]) -> dict[str, object]:
    assert len(properties) == 1
    property_name, serialized = properties[0]
    assert property_name == "matrix_evidence"
    return json.loads(serialized)


def _scenario(evidence: dict[str, object], name: str) -> dict[str, object]:
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, dict)
    value = scenarios[name]
    assert isinstance(value, dict)
    return value


_MATRIX_SCENARIO_NAMES = (
    "successful_provider_turn",
    "sibling_message_delivery",
    "sibling_cancellation_cleanup",
    "timeout_reconciliation",
    "quota_wait_reconciliation",
    "quota_continuation",
)


def _run_complete_cell_with_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failure_at: str | None = None,
    quota_skip: bool = False,
    cleanup_failure: bool = False,
    junit_record_property: Callable[[str, str], None] | None = None,
) -> tuple[object, list[tuple[str, str]]]:
    """Call the actual matrix cell with only its CAO/provider boundaries doubled."""
    provider = matrix.MatrixProvider(
        name="claude_code",
        model="reviewed-test-model",
        binary="claude",
        auth_env=(),
        auth_files=(),
        capabilities=frozenset({"native_children"}),
    )
    cell = matrix.MatrixCell(parent=provider, child=provider)
    server = SimpleNamespace(url="http://matrix.test", home_dir=Path("/tmp/matrix-test-home"))
    properties: list[tuple[str, str]] = []
    created_children = iter(("left-terminal", "right-terminal"))
    sent_message: list[str] = []
    receipt_reads = 0

    def maybe_fail(scenario: str) -> None:
        if failure_at == scenario:
            raise AssertionError("controlled local scenario failure")

    def link_auth(*_args: object) -> None:
        maybe_fail("setup")

    monkeypatch.setattr(matrix, "_link_auth_material", link_auth)
    monkeypatch.setattr(matrix, "_write_profile", lambda *_args: None)
    monkeypatch.setattr(
        matrix,
        "_create_parent",
        lambda *_args: ("parent-terminal", "matrix-session"),
    )

    def run_step(*_args: object, **_kwargs: object) -> dict[str, object]:
        if quota_skip:
            pytest.skip("synthetic provider quota")
        maybe_fail("successful_provider_turn")
        return _quota_receipt(state="succeeded")

    monkeypatch.setattr(matrix, "_run_cross_provider_step", run_step)
    monkeypatch.setattr(
        matrix,
        "_create_native_child",
        lambda *_args, **_kwargs: next(created_children),
    )

    def request(method: str, url: str, **kwargs: object) -> SimpleNamespace:
        nonlocal receipt_reads
        if method == "POST" and url.endswith("/inbox/messages"):
            params = kwargs["params"]
            assert isinstance(params, dict)
            sent_message.append(params["message"])
            return _response(200, {"message_id": "message-123"})
        if method == "POST" and url.endswith("/terminals/run-step"):
            return _response(
                504,
                {
                    "detail": {
                        "kind": "timeout",
                        "terminal_id": "timeout-terminal",
                        "native_child_id": "timeout-child",
                    }
                },
            )
        if method == "GET" and url.endswith("/native-children/timeout-child"):
            receipt_reads += 1
            if failure_at == "timeout_reconciliation":
                return _response(404, {"detail": "not found"})
            body = _quota_receipt(
                state="reconcile",
                child_id="timeout-child",
                terminal_id="timeout-terminal",
            )
            body["cleanup_completed_at"] = "2026-09-24T10:05:00Z" if receipt_reads > 1 else None
            return _response(200, body)
        raise AssertionError(f"unexpected synthetic request: {method} {url}")

    monkeypatch.setattr(matrix, "_request", request)

    def wait_for_delivery(*_args: object, **_kwargs: object) -> dict[str, str]:
        maybe_fail("sibling_message_delivery")
        return {
            "id": "message-123",
            "sender_id": "left-terminal",
            "receiver_id": "right-terminal",
            "message": sent_message[0],
            "status": "delivered",
        }

    monkeypatch.setattr(matrix, "_wait_for_message_delivery", wait_for_delivery)

    def delete_terminal(_server: object, terminal_id: str) -> None:
        if terminal_id == "left-terminal":
            maybe_fail("sibling_cancellation_cleanup")

    monkeypatch.setattr(matrix, "_delete_terminal", delete_terminal)

    def receipt_for_terminal(
        _server: object,
        _parent_id: str,
        terminal_id: str,
    ) -> dict[str, object]:
        return _quota_receipt(
            state="cancelled",
            child_id=f"child-for-{terminal_id}",
            terminal_id=terminal_id,
        ) | {"cleanup_completed_at": "2026-09-24T10:04:00Z"}

    monkeypatch.setattr(matrix, "_receipt_for_terminal", receipt_for_terminal)

    def delete_session(*_args: object) -> None:
        if cleanup_failure:
            raise AssertionError("controlled local cleanup failure")

    monkeypatch.setattr(matrix, "_delete_session", delete_session)

    def record_property(name: str, value: str) -> None:
        properties.append((name, value))
        if junit_record_property is not None:
            junit_record_property(name, value)

    outcome: object = None
    try:
        matrix.test_real_provider_native_child_matrix(
            server,
            cell,
            record_property,
        )
    except BaseException as exc:
        outcome = exc
    return outcome, properties


def test_complete_cell_publishes_six_explicit_scenario_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties = _run_complete_cell_with_doubles(monkeypatch)

    assert outcome is None
    evidence = _evidence(properties)
    assert evidence["status"] == "validated"
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, dict)
    assert set(scenarios) == set(_MATRIX_SCENARIO_NAMES)
    assert {name: scenarios[name]["status"] for name in _MATRIX_SCENARIO_NAMES} == {
        "successful_provider_turn": "validated",
        "sibling_message_delivery": "validated",
        "sibling_cancellation_cleanup": "validated",
        "timeout_reconciliation": "validated",
        "quota_wait_reconciliation": "skipped",
        "quota_continuation": "skipped",
    }
    assert evidence["cleanup"]["session"]["status"] == "validated"
    assert evidence["cleanup"]["quota_terminal"] == {
        "reason": "not_applicable",
        "status": "skipped",
    }


def test_complete_cell_quota_skip_keeps_cleanup_reason_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties = _run_complete_cell_with_doubles(monkeypatch, quota_skip=True)

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    assert evidence["status"] == "skipped"
    assert _scenario(evidence, "successful_provider_turn")["status"] == "not_observed"
    assert evidence["cleanup"]["quota_terminal"] == {
        "reason": "continuation_not_observed",
        "status": "skipped",
    }


@pytest.mark.parametrize(
    ("failure_at", "active_scenario"),
    [
        ("successful_provider_turn", "successful_provider_turn"),
        ("sibling_message_delivery", "sibling_message_delivery"),
        ("sibling_cancellation_cleanup", "sibling_cancellation_cleanup"),
        ("timeout_reconciliation", "timeout_reconciliation"),
    ],
)
def test_complete_cell_records_prior_and_failed_scenarios_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
    failure_at: str,
    active_scenario: str,
) -> None:
    outcome, properties = _run_complete_cell_with_doubles(monkeypatch, failure_at=failure_at)

    assert isinstance(outcome, AssertionError)
    evidence = _evidence(properties)
    assert evidence["status"] == "failed"
    assert len(properties) == 1
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, dict)
    active_index = _MATRIX_SCENARIO_NAMES.index(active_scenario)
    for index, name in enumerate(_MATRIX_SCENARIO_NAMES):
        expected = (
            "validated"
            if index < active_index
            else "failed" if index == active_index else "not_observed"
        )
        assert scenarios[name]["status"] == expected


def test_setup_failure_still_records_initial_not_observed_scenarios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties = _run_complete_cell_with_doubles(monkeypatch, failure_at="setup")

    assert isinstance(outcome, AssertionError)
    evidence = _evidence(properties)
    assert evidence["status"] == "failed"
    assert all(
        _scenario(evidence, name)["status"] == "not_observed" for name in _MATRIX_SCENARIO_NAMES
    )
    assert evidence["cleanup"] == {
        "session": {"reason": "setup_not_started", "status": "skipped"},
        "quota_terminal": {"reason": "not_applicable", "status": "skipped"},
    }


def test_session_cleanup_failure_overrides_validated_cell_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties = _run_complete_cell_with_doubles(
        monkeypatch,
        cleanup_failure=True,
    )

    assert isinstance(outcome, AssertionError)
    evidence = _evidence(properties)
    assert evidence["status"] == "failed"
    assert evidence["reason"] == "cleanup_failed"
    assert evidence["cleanup"] == {
        "session": {"reason": "cleanup_failed", "status": "failed"},
        "quota_terminal": {"reason": "not_applicable", "status": "skipped"},
    }
    assert len(properties) == 1


def test_quota_cleanup_failure_keeps_continuity_evidence_and_fails_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties, calls = _run_quota_observation(
        monkeypatch,
        receipts=[_quota_receipt(), _quota_receipt(state="succeeded")],
        terminal_states=["waiting_quota", "completed"],
        terminal_cleanup_failure=True,
    )

    assert isinstance(outcome, AssertionError)
    evidence = _evidence(properties)
    assert evidence["status"] == "failed"
    assert evidence["reason"] == "cleanup_failed"
    assert _scenario(evidence, "quota_continuation")["status"] == "validated"
    assert evidence["cleanup"]["quota_terminal"] == {
        "reason": "cleanup_failed",
        "status": "failed",
    }
    assert sum(method == "DELETE" for method, _url, _kwargs in calls) == 1
    assert calls[-1][0] == "DELETE"
    assert (
        sum(
            method == "POST" and url.endswith("/terminals/run-step")
            for method, url, _kwargs in calls
        )
        == 1
    )


def test_matrix_evidence_is_serialized_to_junit_for_pass_skip_and_failure(
    tmp_path: Path,
) -> None:
    synthetic_test = tmp_path / "test_matrix_junit_fixture.py"
    synthetic_test.write_text(
        """
import pytest
from test.test_real_provider_matrix_contract import _run_complete_cell_with_doubles


def _finish(monkeypatch, record_property, **kwargs):
    outcome, _properties = _run_complete_cell_with_doubles(
        monkeypatch, junit_record_property=record_property, **kwargs
    )
    if isinstance(outcome, BaseException):
        raise outcome


def test_pass(monkeypatch, record_property):
    _finish(monkeypatch, record_property)


def test_skip(monkeypatch, record_property):
    _finish(monkeypatch, record_property, quota_skip=True)


def test_failure(monkeypatch, record_property):
    _finish(monkeypatch, record_property, failure_at='sibling_message_delivery')
""",
        encoding="utf-8",
    )
    junit_path = tmp_path / "matrix-results.xml"
    env = os.environ.copy()
    env.pop("CAO_RUN_LIVE_PROVIDER_TESTS", None)
    # The child owns its test selection and temporary directory. Inheriting
    # --basetemp can delete the parent's active fixtures before collection.
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(_REPO_ROOT / "pyproject.toml"),
            "-p",
            "no:cov",
            "-o",
            "addopts=",
            "-q",
            f"--junitxml={junit_path}",
            str(synthetic_test),
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert junit_path.is_file(), "pytest must write JUnit even when one synthetic cell fails"
    junit = ET.parse(junit_path).getroot()
    cases = {case.attrib["name"]: case for case in junit.findall(".//testcase")}
    assert set(cases) == {"test_pass", "test_skip", "test_failure"}
    for name, status in (
        ("test_pass", "validated"),
        ("test_skip", "skipped"),
        ("test_failure", "failed"),
    ):
        properties = cases[name].findall("./properties/property")
        assert len(properties) == 1
        assert properties[0].attrib["name"] == "matrix_evidence"
        evidence = json.loads(properties[0].attrib["value"])
        assert evidence["status"] == status
        assert _scenario(evidence, "sibling_message_delivery")["status"] == (
            "failed"
            if status == "failed"
            else "not_observed" if status == "skipped" else "validated"
        )
        serialized = json.dumps(evidence)
        assert "PRIVATE_OUTPUT_SENTINEL" not in serialized
        assert "CAO_REAL_PROVIDER_MATRIX_" not in serialized


def test_real_provider_workflow_writes_manual_junit_artifact() -> None:
    import shlex

    import yaml

    workflow_text = (_REPO_ROOT / ".github" / "workflows" / "real-provider-e2e.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    dispatch = workflow["on"]["workflow_dispatch"]
    quota_window = dispatch.get("inputs", {}).get("quota_observation_seconds")
    assert quota_window is not None, "manual dispatch must expose the quota observation window"
    assert quota_window.get("type") == "number"
    assert quota_window.get("required") == "true"

    job = workflow["jobs"]["native-provider-matrix"]
    steps = job["steps"]
    pytest_step = next(
        step for step in steps if step.get("name") == "Run selected real-provider matrix cells"
    )
    quota_env_name = "CAO_REAL_PROVIDER_E2E_QUOTA_OBSERVATION_SECONDS"
    assert quota_env_name not in job.get("env", {}), "quota window must be step-scoped"
    env_bindings = [step for step in steps if quota_env_name in step.get("env", {})]
    assert env_bindings == [pytest_step]
    assert pytest_step["env"][quota_env_name] == "${{ inputs.quota_observation_seconds }}"
    pytest_args = shlex.split(pytest_step["run"])
    junit_args = [arg for arg in pytest_args if arg.startswith("--junitxml=")]
    assert len(junit_args) == 1
    junit_path = junit_args[0].split("=", maxsplit=1)[1]
    assert junit_path and not Path(junit_path).is_absolute()

    uploads = [
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
        and junit_path in step.get("with", {}).get("path", "")
    ]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["if"] == "always()"
    assert junit_path in upload["with"]["path"]
    assert upload["with"]["if-no-files-found"] == "error"
    assert 1 <= int(upload["with"]["retention-days"]) <= 7


def test_quota_resume_requires_same_receipt_success_and_turn_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties, calls = _run_quota_observation(
        monkeypatch,
        receipts=[_quota_receipt(), _quota_receipt(state="succeeded")],
        terminal_states=["waiting_quota", "completed"],
    )

    assert not isinstance(
        outcome, pytest.skip.Exception
    ), "the harness stopped at quota before observing the original receipt and marker"
    assert isinstance(outcome, dict), f"quota continuation failed: {outcome!r}"
    assert outcome.get("id") == _QUOTA_CHILD_ID
    assert outcome.get("terminal_id") == _QUOTA_TERMINAL_ID
    assert outcome.get("state") == "succeeded"
    run_step_posts = [
        call for call in calls if call[0] == "POST" and call[1].endswith("/terminals/run-step")
    ]
    assert len(run_step_posts) == 1
    assert calls[0][0:2] == ("POST", "http://matrix.test/terminals/run-step")
    delete_indices = [index for index, call in enumerate(calls) if call[0] == "DELETE"]
    assert len(delete_indices) == 1
    delete_index = delete_indices[0]
    assert all(method == "GET" for method, _url, _kwargs in calls[1:delete_index])
    assert all(method == "GET" for method, _url, _kwargs in calls[delete_index + 1 :])
    assert calls[delete_index][1] == f"http://matrix.test/terminals/{_QUOTA_TERMINAL_ID}"
    assert calls[-1][1] == f"http://matrix.test/native-children/{_QUOTA_CHILD_ID}"
    assert {url for method, url, _kwargs in calls if method == "GET" and "/terminals/" in url} == {
        f"http://matrix.test/terminals/{_QUOTA_TERMINAL_ID}",
        f"http://matrix.test/terminals/{_QUOTA_TERMINAL_ID}/output",
    }
    assert any(
        method == "GET" and url.endswith(f"/native-children/{_QUOTA_CHILD_ID}")
        for method, url, _kwargs in calls[:delete_index]
    )
    assert any(
        method == "GET" and url.endswith(f"/terminals/{_QUOTA_TERMINAL_ID}/output")
        for method, url, _kwargs in calls[:delete_index]
    )
    assert {
        url for method, url, _kwargs in calls if method == "GET" and "/native-children/" in url
    } == {f"http://matrix.test/native-children/{_QUOTA_CHILD_ID}"}

    evidence = _evidence(properties)
    assert _scenario(evidence, "quota_wait_reconciliation")["status"] == "validated"
    assert _scenario(evidence, "quota_continuation")["status"] == "validated"
    assert evidence["cleanup"]["quota_terminal"]["status"] == "validated"
    serialized = json.dumps(evidence)
    assert "CAO_REAL_PROVIDER_MATRIX_" not in serialized
    assert _OUTPUT_SENTINEL not in serialized
    posted_prompt = run_step_posts[0][2]["json"]["prompt"]
    assert posted_prompt not in serialized


@pytest.mark.parametrize(
    ("provider_may_resume", "delivery_may_have_occurred", "reason"),
    [
        (False, True, "auto_resume_unsupported"),
        (True, False, "delivery_not_confirmed"),
    ],
)
def test_quota_without_supported_resume_is_a_typed_skip(
    monkeypatch: pytest.MonkeyPatch,
    provider_may_resume: bool,
    delivery_may_have_occurred: bool,
    reason: str,
) -> None:
    outcome, properties, calls = _run_quota_observation(
        monkeypatch,
        provider_may_resume=provider_may_resume,
        delivery_may_have_occurred=delivery_may_have_occurred,
    )

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    assert evidence["status"] == "skipped"
    assert _scenario(evidence, "quota_wait_reconciliation")["status"] == "validated"
    continuation = _scenario(evidence, "quota_continuation")
    assert continuation["status"] == "not_observed"
    assert continuation["reason"] == reason
    assert not any("/output" in url for _method, url, _kwargs in calls)
    assert (
        sum(
            method == "POST" and url.endswith("/terminals/run-step")
            for method, url, _kwargs in calls
        )
        == 1
    )
    assert all(method == "GET" for method, _url, _kwargs in calls[1:])


def test_quota_observation_limit_is_not_a_success_or_product_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties, calls = _run_quota_observation(
        monkeypatch,
        receipts=[_quota_receipt()],
        terminal_states=["waiting_quota", "completed"],
    )

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    assert evidence["status"] == "skipped"
    continuation = _scenario(evidence, "quota_continuation")
    assert continuation["status"] == "not_observed"
    assert continuation["reason"] == "observation_limit"
    assert (
        sum(
            method == "POST" and url.endswith("/terminals/run-step")
            for method, url, _kwargs in calls
        )
        == 1
    )
    assert all(method == "GET" for method, _url, _kwargs in calls[1:])


def test_quota_without_observation_window_is_typed_skip_without_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties, calls = _run_quota_observation(
        monkeypatch,
        quota_observation_seconds=None,
    )

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    assert evidence["status"] == "skipped"
    assert _scenario(evidence, "quota_wait_reconciliation")["status"] == "validated"
    assert _scenario(evidence, "quota_continuation") == {
        "reason": "observation_window_unconfigured",
        "status": "not_observed",
    }
    assert evidence["quota_observation"] == {
        "elapsed_seconds": None,
        "limit_seconds": None,
        "started_at_utc": None,
    }
    assert not any(url.endswith("/output") for _method, url, _kwargs in calls)
    assert sum(method == "DELETE" for method, _url, _kwargs in calls) == 0
    assert [(method, url) for method, url, _kwargs in calls] == [
        ("POST", "http://matrix.test/terminals/run-step"),
        ("GET", f"http://matrix.test/terminals/{_QUOTA_TERMINAL_ID}"),
        ("GET", f"http://matrix.test/native-children/{_QUOTA_CHILD_ID}"),
    ]


@pytest.mark.parametrize(
    "quota_observation_seconds",
    ["", "not-a-number", "0", "-1", "NaN", "Infinity", "1e9999", "1e-9999"],
)
def test_quota_with_invalid_observation_window_is_typed_skip_without_polling(
    monkeypatch: pytest.MonkeyPatch,
    quota_observation_seconds: str,
) -> None:
    outcome, properties, calls = _run_quota_observation(
        monkeypatch,
        quota_observation_seconds=quota_observation_seconds,
    )

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    assert evidence["status"] == "skipped"
    assert _scenario(evidence, "quota_continuation") == {
        "reason": "observation_window_unconfigured",
        "status": "not_observed",
    }
    assert evidence["quota_observation"]["limit_seconds"] is None
    assert not any(url.endswith("/output") for _method, url, _kwargs in calls)
    assert sum(method == "DELETE" for method, _url, _kwargs in calls) == 0
    assert [(method, url) for method, url, _kwargs in calls] == [
        ("POST", "http://matrix.test/terminals/run-step"),
        ("GET", f"http://matrix.test/terminals/{_QUOTA_TERMINAL_ID}"),
        ("GET", f"http://matrix.test/native-children/{_QUOTA_CHILD_ID}"),
    ]


def test_valid_observation_window_controls_fake_clock_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties, calls = _run_quota_observation(
        monkeypatch,
        receipts=[_quota_receipt()],
        terminal_states=["waiting_quota", "completed"],
        quota_observation_seconds="0.25",
    )

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    assert _scenario(evidence, "quota_continuation")["status"] == "not_observed"
    observation = evidence["quota_observation"]
    assert observation["limit_seconds"] == 0.25
    assert observation["started_at_utc"]
    assert observation["elapsed_seconds"] == 0.25
    assert sum(url.endswith("/output") for _method, url, _kwargs in calls) == 1
    assert sum(method == "DELETE" for method, _url, _kwargs in calls) == 0


def test_quota_continuation_is_not_observed_when_output_verification_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties, _calls = _run_quota_observation(
        monkeypatch,
        receipts=[_quota_receipt()],
        output_status=404,
    )

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    continuation = _scenario(evidence, "quota_continuation")
    assert continuation["status"] == "not_observed"
    assert continuation["reason"] == "verification_unavailable"


def test_succeeded_receipt_without_the_turn_marker_is_not_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, properties, _calls = _run_quota_observation(
        monkeypatch,
        receipts=[_quota_receipt(), _quota_receipt(state="succeeded")],
        terminal_states=["waiting_quota", "completed"],
        output_marker_present=False,
    )

    assert isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    continuation = _scenario(evidence, "quota_continuation")
    assert continuation["status"] == "not_observed"
    assert continuation["reason"] == "observation_limit"


@pytest.mark.parametrize(
    ("receipts", "failed_scenario"),
    [
        ([_quota_receipt(child_id="different-child")], "quota_wait_reconciliation"),
        ([_quota_receipt(terminal_id="different-terminal")], "quota_wait_reconciliation"),
        ([_quota_receipt(), _quota_receipt(state="failed")], "quota_continuation"),
        ([_quota_receipt(), _quota_receipt(state="cancelled")], "quota_continuation"),
    ],
    ids=["wrong-receipt-id", "wrong-terminal-id", "failed", "cancelled"],
)
def test_quota_receipt_identity_or_terminal_failure_fails_the_cell(
    monkeypatch: pytest.MonkeyPatch,
    receipts: list[dict[str, object]],
    failed_scenario: str,
) -> None:
    outcome, properties, _calls = _run_quota_observation(monkeypatch, receipts=receipts)

    assert isinstance(outcome, BaseException)
    assert not isinstance(outcome, pytest.skip.Exception)
    evidence = _evidence(properties)
    assert evidence["status"] == "failed"
    assert _scenario(evidence, failed_scenario)["status"] == "failed"


@pytest.mark.parametrize(
    "manifest",
    (
        "",
        "[]",
        "{",
        "{}",
    ),
)
def test_manifest_requires_a_nonempty_json_object(
    monkeypatch: pytest.MonkeyPatch,
    manifest: str,
) -> None:
    monkeypatch.setenv(matrix._PROVIDERS_ENV, manifest)

    with pytest.raises(pytest.fail.Exception, match="Invalid .* manifest"):
        matrix._load_matrix_providers()


def test_manifest_rejects_unknown_enabled_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_manifest(
        monkeypatch,
        {"unregistered_matrix_provider": _provider_config()},
    )

    with pytest.raises(pytest.fail.Exception, match="has no adapter in this CAO checkout"):
        matrix._load_matrix_providers()


def test_manifest_keeps_unknown_excluded_provider_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_manifest(
        monkeypatch,
        {"gemini_cli": {"exclude_reason": "adapter not installed on this runner"}},
    )

    loaded = matrix._load_matrix_providers()

    assert loaded["gemini_cli"].exclude_reason == "adapter not installed on this runner"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model", "\nnot-a-model"),
        ("binary", ""),
        ("auth_env", "CAO_MATRIX_TEST_AUTH"),
        ("auth_files", ["../outside.json"]),
        ("capabilities", "native_children"),
    ),
)
def test_manifest_validates_endpoint_fields(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config(**{field: value})})

    with pytest.raises(pytest.fail.Exception, match="Invalid .* manifest"):
        matrix._load_matrix_providers()


def test_missing_endpoint_metadata_yields_visible_skip_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    _set_manifest(monkeypatch, {provider: {}})

    reason = _skip_reason(matrix._matrix_cells()[0])

    assert "no reviewed model configured" in reason
    assert "no reviewed CLI binary configured" in reason
    assert "no authentication probe configured" in reason
    assert "missing required capabilities: native_children" in reason


def test_strict_mode_fails_before_fixtures_for_selected_unavailable_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    _set_manifest(monkeypatch, {provider: {}})

    with pytest.raises(
        pytest.fail.Exception,
        match="CAO_REAL_PROVIDER_E2E_STRICT=1 refuses to start real providers",
    ) as error:
        matrix._matrix_cells()

    assert "no reviewed model configured" in str(error.value)
    assert "no reviewed CLI binary configured" in str(error.value)
    assert "no authentication probe configured" in str(error.value)
    assert "missing required capabilities: native_children" in str(error.value)


def test_strict_mode_only_checks_enabled_providers_in_selected_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_provider, unavailable_provider = _provider_names(2)
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, f"{ready_provider}->{ready_provider}")
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    monkeypatch.setenv("CAO_MATRIX_TEST_AUTH", "test-only")
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")
    _set_manifest(
        monkeypatch,
        {
            ready_provider: _provider_config(),
            unavailable_provider: {},
        },
    )

    cells = matrix._matrix_cells()

    assert len(cells) == 1
    assert cells[0].values[0].skip_reason is None


def test_strict_mode_requires_an_executable_cell_beyond_intentional_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, f"{provider}->gemini_cli")
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    monkeypatch.setenv("CAO_MATRIX_TEST_AUTH", "test-only")
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")
    _set_manifest(
        monkeypatch,
        {
            provider: _provider_config(),
            "gemini_cli": {"exclude_reason": "adapter has not shipped"},
        },
    )

    with pytest.raises(
        pytest.fail.Exception,
        match="requires at least one selected fully executable parent->child cell",
    ) as error:
        matrix._matrix_cells()

    assert "child 'gemini_cli'" in str(error.value)
    assert "adapter exclusion: adapter has not shipped" in str(error.value)


def test_strict_mode_keeps_intentional_exclusions_as_skips_when_another_cell_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(
        matrix._PAIRS_ENV,
        f"{provider}->{provider},{provider}->gemini_cli",
    )
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    monkeypatch.setenv("CAO_MATRIX_TEST_AUTH", "test-only")
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")
    _set_manifest(
        monkeypatch,
        {
            provider: _provider_config(),
            "gemini_cli": {"exclude_reason": "adapter has not shipped"},
        },
    )

    cells = matrix._matrix_cells()

    assert len(cells) == 2
    assert cells[0].values[0].skip_reason is None
    assert "adapter exclusion: adapter has not shipped" in _skip_reason(cells[1])


@pytest.mark.parametrize("value", ("true", "yes", "2"))
def test_strict_mode_rejects_ambiguous_enablement_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(matrix._STRICT_ENV, value)

    with pytest.raises(pytest.fail.Exception, match="must be '1'"):
        matrix._strict_mode_enabled()


def test_manifest_rejects_payload_larger_than_64_kib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(matrix._PROVIDERS_ENV, " " * (64 * 1024 + 1))

    with pytest.raises(pytest.fail.Exception, match="must not exceed 65536 UTF-8 bytes"):
        matrix._load_matrix_providers()


def test_pair_selection_all_generates_cartesian_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")

    pairs = matrix._pair_selection(matrix._load_matrix_providers())

    assert pairs == [
        (parent, parent),
        (parent, child),
        (child, parent),
        (child, child),
    ]


def test_pair_selection_accepts_a_focused_explicit_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.setenv(matrix._PAIRS_ENV, f"{parent}->{child},{child}->{parent}")

    assert matrix._pair_selection(matrix._load_matrix_providers()) == [
        (parent, child),
        (child, parent),
    ]


def test_test_filter_alias_selects_matrix_cells_without_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clearer filter name is an E2E selection alias, not pair admission."""
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.delenv(matrix._PAIRS_ENV, raising=False)
    monkeypatch.setenv(matrix._TEST_FILTER_ENV, f"{parent}->{child}")

    assert matrix._pair_selection(matrix._load_matrix_providers()) == [(parent, child)]


def test_legacy_parent_child_selects_one_manifest_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.delenv(matrix._PAIRS_ENV, raising=False)
    monkeypatch.setenv(matrix._LEGACY_PARENT_ENV, parent)
    monkeypatch.setenv(matrix._LEGACY_CHILD_ENV, child)

    assert matrix._pair_selection(matrix._load_matrix_providers()) == [(parent, child)]


def test_one_legacy_endpoint_without_a_pair_selector_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config()})
    monkeypatch.delenv(matrix._PAIRS_ENV, raising=False)
    monkeypatch.setenv(matrix._LEGACY_PARENT_ENV, provider)
    monkeypatch.delenv(matrix._LEGACY_CHILD_ENV, raising=False)

    with pytest.raises(pytest.fail.Exception, match="must be set together"):
        matrix._pair_selection(matrix._load_matrix_providers())


@pytest.mark.parametrize("selection", ("missing->missing", "not-a-pair", ""))
def test_pair_selection_rejects_unknown_or_malformed_cells(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config()})
    monkeypatch.setenv(matrix._PAIRS_ENV, selection)
    monkeypatch.delenv(matrix._LEGACY_PARENT_ENV, raising=False)
    monkeypatch.delenv(matrix._LEGACY_CHILD_ENV, raising=False)

    with pytest.raises(pytest.fail.Exception):
        matrix._pair_selection(matrix._load_matrix_providers())


def test_disabled_matrix_never_parses_an_ambient_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAO_RUN_LIVE_PROVIDER_TESTS", raising=False)
    monkeypatch.setenv(matrix._PROVIDERS_ENV, "not valid JSON")

    cells = matrix._matrix_cells()

    assert len(cells) == 1
    assert cells[0].id == "live-provider-tests-disabled"


def test_excluded_provider_emits_visible_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    _set_manifest(monkeypatch, {provider: {"exclude_reason": "adapter not installed"}})

    cells = matrix._matrix_cells()

    assert len(cells) == 1
    reason = _skip_reason(cells[0])
    assert f"parent {provider!r}: adapter exclusion: adapter not installed" in reason
    assert f"child {provider!r}: adapter exclusion: adapter not installed" in reason


def test_missing_binary_emits_visible_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    _set_manifest(monkeypatch, {provider: _provider_config(binary="matrix-absent-cli")})
    monkeypatch.setattr(matrix.shutil, "which", lambda _: None)

    cells = matrix._matrix_cells()

    assert "required binary 'matrix-absent-cli' is not on PATH" in _skip_reason(cells[0])


def test_missing_auth_emits_visible_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(tmp_path))
    monkeypatch.delenv("CAO_MATRIX_TEST_AUTH", raising=False)
    _set_manifest(monkeypatch, {provider: _provider_config()})
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")

    cells = matrix._matrix_cells()

    assert "no reusable auth" in _skip_reason(cells[0])
    assert "CAO_MATRIX_TEST_AUTH" in _skip_reason(cells[0])


def test_missing_native_child_capability_keeps_all_skip_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(tmp_path))
    monkeypatch.delenv("CAO_MATRIX_TEST_AUTH", raising=False)
    _set_manifest(
        monkeypatch,
        {
            provider: _provider_config(
                binary="matrix-absent-cli",
                capabilities=["ordinary_terminal"],
            )
        },
    )
    monkeypatch.setattr(matrix.shutil, "which", lambda _: None)

    reason = _skip_reason(matrix._matrix_cells()[0])

    assert "missing required capabilities: native_children" in reason
    assert "required binary 'matrix-absent-cli' is not on PATH" in reason
    assert "no reusable auth" in reason


def test_profile_persists_explicit_model_as_frontmatter(tmp_path) -> None:
    server = SimpleNamespace(home_dir=tmp_path, url="http://matrix.test")

    with patch.object(matrix, "_request", return_value=_response(200, {})) as request:
        matrix._write_profile(
            server,
            "matrix-child",
            "opencode_cli",
            "opencode/mimo-v2.5-free",
        )
    request.assert_called_once_with(
        "POST",
        "http://matrix.test/agents/profiles/install",
        json={"source": "matrix-child", "provider": "opencode_cli"},
    )

    profile = (
        tmp_path / ".aws" / "cli-agent-orchestrator" / "agent-store" / "matrix-child.md"
    ).read_text(encoding="utf-8")
    assert 'model: "opencode/mimo-v2.5-free"' in profile


def test_real_provider_workflow_uses_manifest_and_dynamic_test_filter() -> None:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "real-provider-e2e.yml").read_text(
        encoding="utf-8"
    )

    assert "providers_json:" in workflow
    assert "CAO_REAL_PROVIDER_E2E_PROVIDERS:" in workflow
    assert "CAO_REAL_PROVIDER_E2E_TEST_FILTER: ${{ inputs.pairs }}" in workflow
    assert 'CAO_REAL_PROVIDER_E2E_STRICT: "1"' in workflow
    assert "inputs.providers_json || vars.CAO_REAL_PROVIDER_E2E_PROVIDERS" in workflow
    assert workflow.index('if len(raw.encode("utf-8")) > 65_536:') < workflow.index(
        "if not raw.strip():"
    )
    assert "parent_provider:" not in workflow
    assert "child_provider:" not in workflow
    assert "parent_model:" not in workflow
    assert "child_model:" not in workflow


def test_real_provider_matrix_documentation_explains_dynamic_contract() -> None:
    documentation = (_REPO_ROOT / "docs" / "real-provider-e2e.md").read_text(encoding="utf-8")

    assert "CAO_REAL_PROVIDER_E2E_PROVIDERS" in documentation
    assert "CAO_REAL_PROVIDER_E2E_TEST_FILTER=all" in documentation
    assert "**not** a\nruntime compatibility policy" in documentation
    assert "deprecated alias" in documentation
    assert "CAO_REAL_PROVIDER_E2E_STRICT=1" in documentation
    assert "at least one\nselected parent -> child cell" in documentation
    assert "exclude_reason" in documentation
    assert "native_children" in documentation
    assert "64 KiB" in documentation


def test_auth_home_requires_explicit_isolated_directory(monkeypatch, tmp_path):
    monkeypatch.delenv(matrix._AUTH_HOME_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match="isolated"):
        matrix._configured_auth_home()
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, os.environ["HOME"])
    with pytest.raises(pytest.fail.Exception, match="isolated"):
        matrix._configured_auth_home()
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(tmp_path))
    assert matrix._configured_auth_home() == tmp_path


def test_auth_copy_cannot_mutate_reusable_login(monkeypatch, tmp_path):
    source = tmp_path / "auth"
    source.mkdir()
    (source / "record.json").write_text("private record")
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(source))
    provider = matrix.MatrixProvider(
        name="test",
        model="model",
        binary="test",
        auth_env=(),
        auth_files=(Path("record.json"),),
        capabilities=frozenset(),
    )
    home = tmp_path / "isolated"
    home.mkdir()
    matrix._link_auth_material(SimpleNamespace(home_dir=home), provider)
    (home / "record.json").write_text("renewed")
    assert (source / "record.json").read_text() == "private record"
    assert not (home / "record.json").is_symlink()


def _selected_provider_fixture_request() -> SimpleNamespace:
    """Build the selected-cell request shape required by the R4 fixture."""
    provider = matrix.MatrixProvider(
        name="fixture-contract",
        model="fixture-contract",
        binary="unused-test-value",
        auth_env=(),
        auth_files=(),
        capabilities=frozenset(),
    )
    cell = matrix.MatrixCell(parent=provider, child=provider)
    callspec = SimpleNamespace(params={"cell": cell})
    return SimpleNamespace(node=SimpleNamespace(callspec=callspec))


def _stub_fixture_tmux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "private-tmux-root"
    monkeypatch.setattr(matrix.owned_tmux, "create_private_tmux_tmpdir", lambda: root)
    monkeypatch.setattr(
        matrix.owned_tmux,
        "cleanup_private_tmux_root",
        lambda *_args, **_kwargs: True,
    )


@pytest.mark.parametrize("outcome", [None, RuntimeError("failure"), KeyboardInterrupt()])
def test_provider_fixture_cleans_auth_home_on_success_failure_cancel(
    monkeypatch, tmp_path, outcome
):
    _stub_fixture_tmux(monkeypatch, tmp_path)
    auth = tmp_path / "auth"
    auth.mkdir()
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(auth))
    home = tmp_path / "live_provider_cao_home"
    stopped = []

    def start(*args, **kwargs):
        home.mkdir()
        (home / "private.json").write_text("secret")
        return SimpleNamespace(home_dir=home, stop=lambda: stopped.append(True))

    monkeypatch.setattr(matrix, "_start_cao_server", start)
    fixture = matrix.live_provider_cao_server.__wrapped__(
        tmp_path, _selected_provider_fixture_request()
    )
    next(fixture)
    if outcome is None:
        with pytest.raises(StopIteration):
            next(fixture)
    else:
        with pytest.raises(type(outcome)):
            fixture.throw(outcome)
    assert stopped == [True]
    assert not home.exists()


def test_provider_fixture_removes_auth_after_startup_failure(monkeypatch, tmp_path):
    _stub_fixture_tmux(monkeypatch, tmp_path)
    home = tmp_path / "live_provider_cao_home"

    def fail_start(*args, **kwargs):
        home.mkdir()
        (home / "credential.json").write_text("private")
        raise RuntimeError("startup failed")

    monkeypatch.setattr(matrix, "_start_cao_server", fail_start)
    with pytest.raises(RuntimeError, match="startup failed"):
        next(
            matrix.live_provider_cao_server.__wrapped__(
                tmp_path, _selected_provider_fixture_request()
            )
        )
    assert not home.exists()


def test_provider_fixture_missing_isolation_blocks_before_launch(monkeypatch, tmp_path):
    monkeypatch.delenv(matrix._AUTH_HOME_ENV, raising=False)
    launched = []
    monkeypatch.setattr(matrix, "_start_cao_server", lambda *args, **kwargs: launched.append(True))
    with pytest.raises(pytest.fail.Exception, match="isolated"):
        next(
            matrix.live_provider_cao_server.__wrapped__(
                tmp_path, _selected_provider_fixture_request()
            )
        )
    assert not launched


def _register_fixture_server_owner(home, stop):
    owner = matrix.CaoServer(
        url="http://127.0.0.1:1",
        port=1,
        home_dir=home,
        database_targets=None,
        database_config_path=home / "private-config.json",
        log_path=home / "server.log",
        stop=stop,
    )
    server_fixture._LIVE_SERVERS.add(owner)
    return owner


def test_provider_fixture_preserves_home_when_stop_is_unconfirmed(monkeypatch, tmp_path):
    _stub_fixture_tmux(monkeypatch, tmp_path)
    home = tmp_path / "live_provider_cao_home"
    stop_error = RuntimeError("stop not confirmed")
    owners = []

    def start(home_dir, *args, **kwargs):
        home_dir.mkdir()
        (home_dir / "owner-marker").write_text("private test sentinel")
        owner = _register_fixture_server_owner(home_dir, lambda: (_ for _ in ()).throw(stop_error))
        owners.append(owner)
        return owner

    monkeypatch.setattr(matrix, "_start_cao_server", start)
    fixture = matrix.live_provider_cao_server.__wrapped__(
        tmp_path, _selected_provider_fixture_request()
    )
    next(fixture)
    try:
        with pytest.raises(RuntimeError) as raised:
            next(fixture)
        assert raised.value is stop_error
        assert owners[0] in server_fixture._LIVE_SERVERS
        assert home.is_dir()
    finally:
        for owner in owners:
            server_fixture._LIVE_SERVERS.discard(owner)


def test_provider_fixture_keeps_body_error_primary_when_stop_is_unconfirmed(monkeypatch, tmp_path):
    _stub_fixture_tmux(monkeypatch, tmp_path)
    home = tmp_path / "live_provider_cao_home"
    stop_error = RuntimeError("stop not confirmed")
    body_error = ValueError("body failed")
    owners = []

    def start(home_dir, *args, **kwargs):
        home_dir.mkdir()
        (home_dir / "owner-marker").write_text("private test sentinel")
        owner = _register_fixture_server_owner(home_dir, lambda: (_ for _ in ()).throw(stop_error))
        owners.append(owner)
        return owner

    monkeypatch.setattr(matrix, "_start_cao_server", start)
    fixture = matrix.live_provider_cao_server.__wrapped__(
        tmp_path, _selected_provider_fixture_request()
    )
    next(fixture)
    try:
        with pytest.raises(ValueError) as raised:
            fixture.throw(body_error)
        assert raised.value is body_error
        assert any("cleanup incomplete" in note for note in getattr(body_error, "__notes__", ()))
        assert owners[0] in server_fixture._LIVE_SERVERS
        assert home.is_dir()
    finally:
        for owner in owners:
            server_fixture._LIVE_SERVERS.discard(owner)


def test_provider_fixture_preserves_startup_error_and_registered_home(monkeypatch, tmp_path):
    _stub_fixture_tmux(monkeypatch, tmp_path)
    home = tmp_path / "live_provider_cao_home"
    startup_error = RuntimeError("startup failed")
    stop_error = RuntimeError("stop not confirmed")
    owners = []

    def fail_start(home_dir, *args, **kwargs):
        home_dir.mkdir()
        (home_dir / "owner-marker").write_text("private test sentinel")
        owner = _register_fixture_server_owner(home_dir, lambda: (_ for _ in ()).throw(stop_error))
        owners.append(owner)
        raise startup_error from stop_error

    monkeypatch.setattr(matrix, "_start_cao_server", fail_start)
    fixture = matrix.live_provider_cao_server.__wrapped__(
        tmp_path, _selected_provider_fixture_request()
    )
    try:
        with pytest.raises(RuntimeError) as raised:
            next(fixture)
        assert raised.value is startup_error
        assert raised.value.__cause__ is stop_error
        assert any("cleanup incomplete" in note for note in getattr(startup_error, "__notes__", ()))
        assert owners[0] in server_fixture._LIVE_SERVERS
        assert home.is_dir()
    finally:
        for owner in owners:
            server_fixture._LIVE_SERVERS.discard(owner)


def test_provider_fixture_partial_home_cleanup_note_is_truthful(monkeypatch, tmp_path):
    _stub_fixture_tmux(monkeypatch, tmp_path)
    auth = tmp_path / "auth"
    auth.mkdir()
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(auth))
    home = tmp_path / "live_provider_cao_home"
    marker = home / "owner-marker"
    body_error = ValueError("body failed before partial cleanup")

    def start(home_dir, *args, **kwargs):
        home_dir.mkdir()
        (home_dir / "owner-marker").write_text("private test sentinel")
        return SimpleNamespace(home_dir=home_dir, stop=lambda: None)

    def partially_remove(path, *, ignore_errors=False):
        assert path == home
        assert ignore_errors is False
        marker.unlink()
        raise OSError("controlled partial cleanup failure")

    monkeypatch.setattr(matrix, "_start_cao_server", start)
    # Rebind only the fixture module's namespace, so pytest's own shutil
    # cleanup functions remain untouched.
    monkeypatch.setattr(matrix, "shutil", SimpleNamespace(rmtree=partially_remove))
    fixture = matrix.live_provider_cao_server.__wrapped__(
        tmp_path, _selected_provider_fixture_request()
    )
    next(fixture)
    with pytest.raises(ValueError) as raised:
        fixture.throw(body_error)

    assert raised.value is body_error
    assert home.is_dir()
    assert not marker.exists()
    notes = getattr(body_error, "__notes__", ())
    assert "live provider fixture cleanup incomplete; isolated HOME cleanup unconfirmed" in notes
    assert all("HOME preserved" not in note for note in notes)


_SYNTHETIC_CLAUDE_CREDENTIAL = b'{"fixture":"synthetic-only"}\n'
_SYNTHETIC_CLAUDE_METADATA = b'{"fixture":"metadata-only"}\n'


def _identity_without_access_time(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _claude_archive_fixture_inputs(monkeypatch, tmp_path, pytest_request):
    auth_home = tmp_path / "isolated-auth"
    auth_home.mkdir(mode=0o700)
    auth_home.chmod(0o700)
    auth_claude = auth_home / ".claude"
    auth_claude.mkdir(mode=0o700)
    auth_claude.chmod(0o700)
    credential = auth_claude / ".credentials.json"
    credential.write_bytes(_SYNTHETIC_CLAUDE_CREDENTIAL)
    credential.chmod(0o600)
    (auth_home / ".claude.json").write_bytes(_SYNTHETIC_CLAUDE_METADATA)
    (auth_home / ".claude.json").chmod(0o600)

    evidence_owner = tempfile.TemporaryDirectory(
        prefix="caos-claude-archive-evidence-", dir=tempfile.gettempdir()
    )
    pytest_request.addfinalizer(evidence_owner.cleanup)
    evidence_root = Path(evidence_owner.name)
    assert stat.S_IMODE(evidence_root.stat().st_mode) == 0o700
    assert not evidence_root.resolve().is_relative_to(tmp_path.parent.resolve())
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(auth_home))
    monkeypatch.setenv(matrix._CLAUDE_ARCHIVE_ROOT_ENV, str(evidence_root))

    provider = matrix.MatrixProvider(
        name="claude_code",
        model="matrix-test-model",
        binary="claude",
        auth_env=(),
        auth_files=(Path(".claude/.credentials.json"), Path(".claude.json")),
        capabilities=frozenset({"native_children"}),
    )
    cell = matrix.MatrixCell(parent=provider, child=provider)
    request = SimpleNamespace(node=SimpleNamespace(callspec=SimpleNamespace(params={"cell": cell})))
    return auth_home, evidence_root, credential, provider, cell, request


def _start_private_claude_archive_fixture(monkeypatch, tmp_path, events, *, stop=None, tmux=True):
    auth_home = tmp_path / "isolated-auth"
    monkeypatch.setattr(matrix, "_configured_auth_home", lambda: auth_home)
    monkeypatch.setattr(matrix, "_matrix_server_auth_environment", lambda _cell: ({}, frozenset()))
    tmux_root = tmp_path / "fixture-tmux"

    def create_tmux_root():
        events.append("tmux_create")
        tmux_root.mkdir(mode=0o700)
        return tmux_root

    def cleanup_tmux_root(_root, _home, _client, *, owner_stopped):
        events.append("tmux_cleanup")
        assert owner_stopped is True
        return tmux

    def start_server(home, *_args, **_kwargs):
        events.append("server_start")
        assert home.is_dir()
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert stat.S_IMODE((home / ".claude").stat().st_mode) == 0o700
        return SimpleNamespace(
            home_dir=home,
            stop=stop or (lambda: events.append("server_stop")),
        )

    monkeypatch.setattr(matrix.owned_tmux, "create_private_tmux_tmpdir", create_tmux_root)
    monkeypatch.setattr(matrix.owned_tmux, "cleanup_private_tmux_root", cleanup_tmux_root)
    monkeypatch.setattr(matrix, "_start_cao_server", start_server)
    monkeypatch.setattr(matrix, "_pick_free_port", lambda: 42342)
    monkeypatch.setattr(matrix, "_fixture_health_timeout", lambda: 5.0)
    monkeypatch.setattr(matrix, "_registered_cao_server_for_home", lambda _home: None)


def test_claude_archive_runs_after_teardown_even_when_functional_test_fails(
    monkeypatch, tmp_path, request
):
    auth_home, evidence_root, credential, provider, cell, matrix_request = (
        _claude_archive_fixture_inputs(monkeypatch, tmp_path, request)
    )
    credential_identity = _identity_without_access_time(credential)
    credential_content = credential.read_bytes()
    metadata_source = auth_home / ".claude.json"
    metadata_identity = _identity_without_access_time(metadata_source)
    metadata_content = metadata_source.read_bytes()
    auth_claude_identity = _identity_without_access_time(auth_home / ".claude")
    events: list[str] = []
    _start_private_claude_archive_fixture(monkeypatch, tmp_path, events)
    real_archive = matrix.claude_auth_archive.archive_private_credential_copy
    original_remove = matrix._remove_archived_claude_workspace

    def archive_with_event(*args, **kwargs):
        events.append("archive")
        return real_archive(*args, **kwargs)

    def remove_with_event(state):
        assert (state.home / ".claude.json").read_bytes() == _SYNTHETIC_CLAUDE_METADATA
        events.append("workspace_remove")
        return original_remove(state)

    monkeypatch.setattr(
        matrix.claude_auth_archive,
        "archive_private_credential_copy",
        archive_with_event,
    )
    monkeypatch.setattr(matrix, "_remove_archived_claude_workspace", remove_with_event)
    monkeypatch.setattr(
        matrix, "_delete_session", lambda _server, _name: events.append("session_delete")
    )

    fixture = matrix.live_provider_cao_server.__wrapped__(tmp_path, matrix_request)
    server = next(fixture)
    matrix._link_auth_material(server, provider)
    matrix._delete_session_and_record_archive_eligibility(server, "cao-matrix-session", cell)
    body_error = AssertionError("synthetic functional assertion failure")
    with pytest.raises(AssertionError) as raised:
        fixture.throw(body_error)
    assert raised.value is body_error

    assert events == [
        "tmux_create",
        "server_start",
        "session_delete",
        "server_stop",
        "tmux_cleanup",
        "archive",
        "workspace_remove",
    ]
    archive_root = evidence_root / "archives"
    run_archives = list(archive_root.iterdir())
    assert len(run_archives) == 1
    archive_run = run_archives[0]
    archived = archive_run / "credentials.json"
    assert archived.read_bytes() == _SYNTHETIC_CLAUDE_CREDENTIAL
    assert stat.S_IMODE(archived.stat().st_mode) == 0o600
    assert stat.S_IMODE(archive_run.stat().st_mode) == 0o700
    assert list(archive_run.iterdir()) == [archived]
    assert not server.home_dir.parent.exists()
    assert credential.read_bytes() == credential_content
    assert _identity_without_access_time(credential) == credential_identity
    assert metadata_source.read_bytes() == metadata_content
    assert _identity_without_access_time(metadata_source) == metadata_identity
    assert _identity_without_access_time(auth_home / ".claude") == auth_claude_identity


@pytest.mark.parametrize("boundary", ["delete", "stop", "owner", "tmux", "archive", "setup"])
def test_claude_archive_retains_durable_workspace_on_uncertain_boundary(
    monkeypatch, tmp_path, boundary, request
):
    _auth_home, evidence_root, _credential, provider, cell, matrix_request = (
        _claude_archive_fixture_inputs(monkeypatch, tmp_path, request)
    )
    events: list[str] = []

    def stop():
        events.append("server_stop")
        if boundary == "stop":
            raise RuntimeError("synthetic stop uncertainty")

    _start_private_claude_archive_fixture(
        monkeypatch, tmp_path, events, stop=stop, tmux=boundary != "tmux"
    )
    if boundary == "owner":
        monkeypatch.setattr(
            matrix,
            "_registered_cao_server_for_home",
            lambda home: SimpleNamespace(home_dir=home),
        )
    if boundary == "archive":
        monkeypatch.setattr(
            matrix.claude_auth_archive,
            "archive_private_credential_copy",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic archive failure")),
        )
    if boundary == "delete":
        monkeypatch.setattr(
            matrix,
            "_delete_session",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic DELETE failure")),
        )

    fixture = matrix.live_provider_cao_server.__wrapped__(tmp_path, matrix_request)
    server = next(fixture)
    matrix._link_auth_material(server, provider)
    if boundary not in {"delete", "setup"}:
        monkeypatch.setattr(matrix, "_delete_session", lambda *_args: None)
        matrix._delete_session_and_record_archive_eligibility(server, "cao-matrix-session", cell)

    body_error = ValueError("synthetic body failure")
    if boundary == "setup":
        with pytest.raises(ValueError) as raised:
            fixture.throw(body_error)
        assert raised.value is body_error
    else:
        with pytest.raises((RuntimeError, ValueError)):
            if boundary == "delete":
                with pytest.raises(RuntimeError, match="synthetic DELETE failure"):
                    matrix._delete_session_and_record_archive_eligibility(
                        server, "cao-matrix-session", cell
                    )
                fixture.close()
            else:
                fixture.close()

    assert server.home_dir.is_dir()
    credential = server.home_dir / ".claude" / ".credentials.json"
    assert credential.read_bytes() == _SYNTHETIC_CLAUDE_CREDENTIAL
    archive_root = evidence_root / "archives"
    assert not archive_root.exists() or list(archive_root.iterdir()) == []


def test_claude_archive_invalid_root_fails_before_tmux_or_server(monkeypatch, tmp_path, request):
    _auth_home, evidence_root, _credential, _provider, _cell, matrix_request = (
        _claude_archive_fixture_inputs(monkeypatch, tmp_path, request)
    )
    invalid_root = tmp_path / "missing-parent" / "evidence"
    monkeypatch.setenv(matrix._CLAUDE_ARCHIVE_ROOT_ENV, str(invalid_root))
    events: list[str] = []
    _start_private_claude_archive_fixture(monkeypatch, tmp_path, events)
    fixture = matrix.live_provider_cao_server.__wrapped__(tmp_path, matrix_request)
    with pytest.raises(RuntimeError, match="^Claude credential archive preflight failed$"):
        next(fixture)
    assert events == []
    assert not invalid_root.exists()
    assert not (evidence_root / "archives").exists()


def test_claude_archive_rejects_pytest_temporary_base(monkeypatch, tmp_path, request):
    _auth_home, evidence_root, _credential, _provider, _cell, matrix_request = (
        _claude_archive_fixture_inputs(monkeypatch, tmp_path, request)
    )
    temporary_root = tmp_path / "durable-by-name-only"
    monkeypatch.setenv(matrix._CLAUDE_ARCHIVE_ROOT_ENV, str(temporary_root))
    events: list[str] = []
    _start_private_claude_archive_fixture(monkeypatch, tmp_path, events)
    fixture = matrix.live_provider_cao_server.__wrapped__(tmp_path, matrix_request)
    with pytest.raises(RuntimeError, match="^Claude credential archive preflight failed$"):
        next(fixture)
    assert events == []
    assert not temporary_root.exists()


def test_claude_archive_rejects_environment_auth_before_start(monkeypatch, tmp_path, request):
    _auth_home, evidence_root, _credential, _provider, _cell, matrix_request = (
        _claude_archive_fixture_inputs(monkeypatch, tmp_path, request)
    )
    events: list[str] = []
    _start_private_claude_archive_fixture(monkeypatch, tmp_path, events)
    monkeypatch.setattr(
        matrix,
        "_matrix_server_auth_environment",
        lambda _cell: ({"ANTHROPIC_API_KEY": "synthetic-only"}, frozenset()),
    )
    fixture = matrix.live_provider_cao_server.__wrapped__(tmp_path, matrix_request)
    with pytest.raises(RuntimeError, match="^Claude credential archive preflight failed$"):
        next(fixture)
    assert events == []
    assert not (evidence_root / "archives").exists()


def test_claude_archive_is_ignored_for_other_cells(monkeypatch, tmp_path):
    monkeypatch.setenv(matrix._CLAUDE_ARCHIVE_ROOT_ENV, str(tmp_path / "missing-root"))
    _stub_fixture_tmux(monkeypatch, tmp_path)
    auth_home = tmp_path / "auth"
    auth_home.mkdir(mode=0o700)
    home = tmp_path / "live_provider_cao_home"
    monkeypatch.setattr(matrix, "_configured_auth_home", lambda: auth_home)
    monkeypatch.setattr(matrix, "_matrix_server_auth_environment", lambda _cell: ({}, frozenset()))

    def start(home_dir, *_args, **_kwargs):
        home_dir.mkdir(mode=0o700)
        return SimpleNamespace(home_dir=home_dir, stop=lambda: None)

    monkeypatch.setattr(matrix, "_start_cao_server", start)
    fixture = matrix.live_provider_cao_server.__wrapped__(
        tmp_path, _selected_provider_fixture_request()
    )
    next(fixture)
    with pytest.raises(StopIteration):
        next(fixture)
    assert not home.exists()
    assert not (tmp_path / "missing-root").exists()
