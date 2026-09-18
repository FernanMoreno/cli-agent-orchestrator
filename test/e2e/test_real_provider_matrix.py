"""Controlled real-provider matrix for CAO's native child lifecycle.

This module intentionally does not use a fake provider.  Each selected case
launches a real parent and real child, then proves the child lifecycle from its
durable receipt rather than from a terminal viewport:

* a cross-provider ``run-step`` returns output and settles ``succeeded``;
* sibling inbox transport records a delivered message;
* deletion of unfinished siblings records ``cancelled`` plus cleanup; and
* a deliberately short post-send deadline records ``reconcile`` rather than a
  guessed success.

It is expensive and requires logged-in local CLIs, so it is disabled unless
``CAO_RUN_LIVE_PROVIDER_TESTS=1`` and an explicit parent/child pair are set.
The manual GitHub Actions workflow supplies those values on a protected
self-hosted runner.  See ``docs/real-provider-e2e.md``.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Final, Iterator

import pytest
import requests

from test.fixtures.cao_server import CaoServer, _fixture_health_timeout, _pick_free_port, _start_cao_server

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.live_provider,
    pytest.mark.skipif(
        os.environ.get("CAO_RUN_LIVE_PROVIDER_TESTS") != "1",
        reason="Real provider matrix disabled; set CAO_RUN_LIVE_PROVIDER_TESTS=1.",
    ),
]


_PARENT_ENV: Final = "CAO_REAL_PROVIDER_E2E_PARENT"
_CHILD_ENV: Final = "CAO_REAL_PROVIDER_E2E_CHILD"
_PARENT_MODEL_ENV: Final = "CAO_REAL_PROVIDER_E2E_PARENT_MODEL"
_CHILD_MODEL_ENV: Final = "CAO_REAL_PROVIDER_E2E_CHILD_MODEL"
_AUTH_HOME_ENV: Final = "CAO_REAL_PROVIDER_E2E_AUTH_HOME"
_PROVIDER_BINARIES: Final = {
    "codex": "codex",
    "claude_code": "claude",
    "opencode_cli": "opencode",
}
_PROVIDER_AUTH_ENV: Final = {
    "codex": ("OPENAI_API_KEY",),
    "claude_code": ("ANTHROPIC_API_KEY",),
    # OpenCode can delegate to different upstreams.  A local auth record is
    # preferred, but these are the common non-interactive credentials.
    "opencode_cli": ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
}
_PROVIDER_AUTH_FILES: Final = {
    "codex": (Path(".codex") / "auth.json",),
    "claude_code": (Path(".claude") / ".credentials.json", Path(".claude.json")),
    "opencode_cli": (
        Path(".local") / "share" / "opencode" / "auth.json",
        Path(".config") / "opencode" / "auth.json",
    ),
}
_READY_STATES: Final = {"idle", "completed"}
_RECEIPT_WAIT_SECONDS: Final = 30.0
_STEP_TIMEOUT_SECONDS: Final = 180.0


def _configured_auth_home() -> Path:
    """Return the operator home which owns the reusable CLI logins."""
    configured_home = os.environ.get(_AUTH_HOME_ENV, "").strip()
    return Path(configured_home).expanduser() if configured_home else Path.home()


@pytest.fixture
def live_provider_cao_server(tmp_path: Path) -> Iterator[CaoServer]:
    """Start one fresh, isolated CAO server for the selected matrix cell.

    Provider login files are linked after startup by ``_link_auth_material``.
    In particular, do not convert the access token inside a renewable OAuth
    receipt into ``CLAUDE_CODE_OAUTH_TOKEN``: those are distinct auth inputs.
    """
    server = _start_cao_server(
        tmp_path / "live_provider_cao_home",
        _pick_free_port(),
        deadline=_fixture_health_timeout(),
    )
    try:
        yield server
    finally:
        server.stop()


def _selected_provider(name: str) -> str:
    provider = os.environ.get(name, "").strip()
    if provider not in _PROVIDER_BINARIES:
        valid = ", ".join(sorted(_PROVIDER_BINARIES))
        pytest.fail(f"{name} must be one of {valid}; got {provider or '<unset>'!r}")
    binary = _PROVIDER_BINARIES[provider]
    if shutil.which(binary) is None:
        pytest.fail(f"Selected provider {provider!r} requires {binary!r} on PATH")
    return provider


def _selected_model(name: str, provider: str) -> str:
    """Require an explicit, provider-valid model for each matrix endpoint.

    Provider defaults are operator-local state. They can silently point to an
    unavailable or retired model (as a real OpenCode run demonstrated), making
    a lifecycle failure indistinguishable from a model-routing failure. A
    protected runner supplies a reviewed model per matrix side instead.
    """
    raw_model = os.environ.get(name, "")
    if any(character in raw_model for character in "\r\n"):
        pytest.fail(f"{name} must be a single-line model identifier")
    model = raw_model.strip()
    if not model:
        pytest.fail(
            f"{name} is required for {provider!r}; set an installed, currently "
            "available model accepted by that provider"
        )
    return model


def _link_auth_material(cao_server: CaoServer, provider: str) -> None:
    """Expose only the selected provider's auth file to CAO's isolated HOME.

    The managed server deliberately redirects HOME to keep its database,
    profiles and logs isolated.  Copying a complete provider directory would
    both leak unrelated state into artifacts and let a test mutate it.  A
    Symlink every documented auth record for the selected provider, while
    keeping all unrelated provider state out of the test HOME.  Some CLIs
    split renewable credentials and account/session metadata across more than
    one file; linking only the first one can make a logged-in CLI reopen an
    interactive browser flow.  API-key based setups need no filesystem link.
    """

    if any(os.environ.get(key) for key in _PROVIDER_AUTH_ENV[provider]):
        return

    auth_home = _configured_auth_home()
    linked_any = False
    for relative_path in _PROVIDER_AUTH_FILES[provider]:
        source = auth_home / relative_path
        if not source.is_file():
            continue
        target = cao_server.home_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if target.resolve() != source.resolve():
                pytest.fail(f"Refusing to replace existing isolated auth path {target}")
            linked_any = True
            continue
        target.symlink_to(source)
        linked_any = True

    if linked_any:
        return

    searched = ", ".join(str(auth_home / path) for path in _PROVIDER_AUTH_FILES[provider])
    variables = " or ".join(_PROVIDER_AUTH_ENV[provider])
    pytest.fail(
        f"Selected provider {provider!r} has no reusable auth. Set {variables} "
        f"or place its login record under {_AUTH_HOME_ENV} (searched: {searched})."
    )


def _write_profile(cao_server: CaoServer, name: str, provider: str, model: str) -> None:
    store = cao_server.home_dir / ".aws" / "cli-agent-orchestrator" / "agent-store"
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Dedicated real-provider matrix profile for {provider}\n"
        f"provider: {provider}\n"
        f"model: {json.dumps(model)}\n"
        "role: developer\n"
        "---\n\n"
        "Follow the user's request exactly. Do not edit files, invoke tools, "
        "delegate work, or start background work unless the request explicitly requires it.\n",
        encoding="utf-8",
    )


def _request(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", _STEP_TIMEOUT_SECONDS + 60.0)
    response = requests.request(method, url, **kwargs)
    return response


def _wait_for_terminal_ready(base_url: str, terminal_id: str) -> None:
    deadline = time.monotonic() + _STEP_TIMEOUT_SECONDS
    last_status = "unknown"
    while time.monotonic() < deadline:
        response = _request("GET", f"{base_url}/terminals/{terminal_id}")
        if response.status_code == 200:
            last_status = response.json().get("status", "unknown")
            if last_status in _READY_STATES:
                return
            if last_status == "error":
                break
        time.sleep(1.0)
    pytest.fail(f"Terminal {terminal_id} did not become ready; last status={last_status!r}")


def _create_parent(cao_server: CaoServer, provider: str, profile: str) -> tuple[str, str]:
    session_name = f"real-e2e-{provider[:5]}-{uuid.uuid4().hex[:10]}"
    response = _request(
        "POST",
        f"{cao_server.url}/sessions",
        params={
            "provider": provider,
            "agent_profile": profile,
            "session_name": session_name,
        },
    )
    assert response.status_code in (200, 201), response.text
    data = response.json()
    terminal_id = data["id"]
    _wait_for_terminal_ready(cao_server.url, terminal_id)
    return terminal_id, data["session_name"]


def _create_native_child(
    cao_server: CaoServer,
    *,
    session_name: str,
    parent_id: str,
    provider: str,
    profile: str,
) -> str:
    response = _request(
        "POST",
        f"{cao_server.url}/sessions/{session_name}/terminals",
        params={
            "provider": provider,
            "agent_profile": profile,
            "caller_id": parent_id,
        },
    )
    assert response.status_code == 201, response.text
    terminal_id = response.json()["id"]
    _wait_for_terminal_ready(cao_server.url, terminal_id)
    return terminal_id


def _children(cao_server: CaoServer, parent_id: str) -> list[dict]:
    response = _request("GET", f"{cao_server.url}/terminals/{parent_id}/children")
    assert response.status_code == 200, response.text
    return response.json()


def _receipt_for_terminal(cao_server: CaoServer, parent_id: str, terminal_id: str) -> dict:
    deadline = time.monotonic() + _RECEIPT_WAIT_SECONDS
    while time.monotonic() < deadline:
        for receipt in _children(cao_server, parent_id):
            if receipt["terminal_id"] == terminal_id:
                return receipt
        time.sleep(0.25)
    pytest.fail(f"Native receipt for terminal {terminal_id} was never persisted")


def _delete_terminal(cao_server: CaoServer, terminal_id: str) -> None:
    response = _request("DELETE", f"{cao_server.url}/terminals/{terminal_id}")
    assert response.status_code == 200, response.text


def _delete_session(cao_server: CaoServer, session_name: str) -> None:
    response = _request("DELETE", f"{cao_server.url}/sessions/{session_name}")
    assert response.status_code in (200, 404), response.text


def _wait_for_message_delivery(cao_server: CaoServer, receiver_id: str, message_id: str) -> dict:
    deadline = time.monotonic() + _RECEIPT_WAIT_SECONDS
    while time.monotonic() < deadline:
        response = _request(
            "GET",
            f"{cao_server.url}/terminals/{receiver_id}/inbox/messages",
            params={"limit": 100},
        )
        assert response.status_code == 200, response.text
        for message in response.json():
            if message["id"] == message_id:
                if message["status"] == "delivered":
                    return message
                if message["status"] == "failed":
                    pytest.fail(f"Sibling message {message_id} failed delivery")
        time.sleep(0.25)
    pytest.fail(f"Sibling message {message_id} was never delivered")


def _run_cross_provider_step(
    cao_server: CaoServer,
    *,
    parent_id: str,
    session_name: str,
    provider: str,
    profile: str,
) -> dict:
    marker = f"CAO_REAL_PROVIDER_MATRIX_{uuid.uuid4().hex}"
    response = _request(
        "POST",
        f"{cao_server.url}/terminals/run-step",
        json={
            "provider": provider,
            "agent": profile,
            "prompt": (
                f"Return only this exact marker and then finish your turn: {marker}. "
                "Do not call tools, edit files, delegate, or add any other text."
            ),
            "session_name": session_name,
            "caller_id": parent_id,
            "teardown": True,
            "timeout": _STEP_TIMEOUT_SECONDS,
            "prompt_redelivery": False,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert marker in data["last_message"], data["last_message"]
    receipt = _receipt_for_terminal(cao_server, parent_id, data["terminal_id"])
    assert receipt["provider"] == provider
    assert receipt["state"] == "succeeded"
    assert receipt["settled_at"] is not None
    assert receipt["cleanup_completed_at"] is not None

    joined = _request("POST", f"{cao_server.url}/native-children/{receipt['id']}/join")
    assert joined.status_code == 200, joined.text
    joined_data = joined.json()
    assert joined_data["settled"] is True
    assert joined_data["child"]["state"] == "succeeded"
    return receipt


def test_real_provider_native_child_matrix(live_provider_cao_server: CaoServer) -> None:
    """Exercise one explicit parent × child cell of the live provider matrix.

    The workflow schedules all nine cells serially.  Keeping the test itself
    to one cell makes a local repro cheap and associates any failure with an
    exact provider pair instead of an ambiguous large suite.
    """

    cao_server = live_provider_cao_server
    parent_provider = _selected_provider(_PARENT_ENV)
    child_provider = _selected_provider(_CHILD_ENV)
    parent_model = _selected_model(_PARENT_MODEL_ENV, parent_provider)
    child_model = _selected_model(_CHILD_MODEL_ENV, child_provider)
    _link_auth_material(cao_server, parent_provider)
    if child_provider != parent_provider:
        _link_auth_material(cao_server, child_provider)

    suffix = uuid.uuid4().hex[:10]
    parent_profile = f"real_matrix_parent_{suffix}"
    child_profile = f"real_matrix_child_{suffix}"
    _write_profile(cao_server, parent_profile, parent_provider, parent_model)
    _write_profile(cao_server, child_profile, child_provider, child_model)

    parent_id: str | None = None
    session_name: str | None = None
    try:
        parent_id, session_name = _create_parent(cao_server, parent_provider, parent_profile)

        # Launch + prompt + cross-provider child + durable success/cleanup.
        _run_cross_provider_step(
            cao_server,
            parent_id=parent_id,
            session_name=session_name,
            provider=child_provider,
            profile=child_profile,
        )

        # Two siblings remain live only long enough to prove the inbox transport.
        # Their later deletion is the cancellation test: a terminal removal before
        # result must become ``cancelled`` rather than a false completion.
        left_id = _create_native_child(
            cao_server,
            session_name=session_name,
            parent_id=parent_id,
            provider=child_provider,
            profile=child_profile,
        )
        right_id = _create_native_child(
            cao_server,
            session_name=session_name,
            parent_id=parent_id,
            provider=child_provider,
            profile=child_profile,
        )
        sibling_marker = f"CAO_SIBLING_MESSAGE_{uuid.uuid4().hex}"
        sent = _request(
            "POST",
            f"{cao_server.url}/terminals/{right_id}/inbox/messages",
            params={"sender_id": left_id, "message": sibling_marker},
        )
        assert sent.status_code == 200, sent.text
        delivered = _wait_for_message_delivery(cao_server, right_id, sent.json()["message_id"])
        assert delivered["sender_id"] == left_id
        assert delivered["receiver_id"] == right_id
        assert delivered["message"] == sibling_marker

        _delete_terminal(cao_server, left_id)
        _delete_terminal(cao_server, right_id)
        for child_id in (left_id, right_id):
            receipt = _receipt_for_terminal(cao_server, parent_id, child_id)
            assert receipt["state"] == "cancelled"
            assert receipt["cleanup_completed_at"] is not None

        # A deadline after a successful send is uncertain work.  It must be
        # reported as ``reconcile`` and retain the child handle; it is never
        # promoted from a transient TUI status to a false success.
        uncertain = _request(
            "POST",
            f"{cao_server.url}/terminals/run-step",
            json={
                "provider": child_provider,
                "agent": child_profile,
                "prompt": "Do not produce a final answer; remain active until cancelled.",
                "session_name": session_name,
                "caller_id": parent_id,
                "teardown": True,
                "timeout": 0.1,
                "prompt_redelivery": False,
            },
        )
        assert uncertain.status_code == 504, uncertain.text
        detail = uncertain.json()["detail"]
        assert detail["kind"] == "timeout"
        assert detail["terminal_id"]
        assert detail["native_child_id"]
        receipt_response = _request(
            "GET", f"{cao_server.url}/native-children/{detail['native_child_id']}"
        )
        assert receipt_response.status_code == 200, receipt_response.text
        receipt = receipt_response.json()
        assert receipt["terminal_id"] == detail["terminal_id"]
        assert receipt["state"] == "reconcile"
        _delete_terminal(cao_server, detail["terminal_id"])
        after_cleanup = _request(
            "GET", f"{cao_server.url}/native-children/{detail['native_child_id']}"
        )
        assert after_cleanup.status_code == 200, after_cleanup.text
        assert after_cleanup.json()["state"] == "reconcile"
        assert after_cleanup.json()["cleanup_completed_at"] is not None
    finally:
        if session_name is not None:
            _delete_session(cao_server, session_name)
