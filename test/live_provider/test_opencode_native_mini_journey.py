"""Offline contracts for the OpenCode production-mini journey candidate."""

from __future__ import annotations

import ast
import asyncio
import errno
import json
import os
import shutil
import socket
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from test.live_provider import opencode_interaction_journey as journey
from types import SimpleNamespace
from typing import Any

import pytest

import cli_agent_orchestrator
import cli_agent_orchestrator.providers.opencode_cli as opencode_provider
import cli_agent_orchestrator.services.terminal_service as terminal_service
from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
from cli_agent_orchestrator.providers.mcp_interactions import (
    is_bundled_cao_mcp_entry,
    overlay_interaction_capability_file,
)
from cli_agent_orchestrator.utils import opencode_config, opencode_v2

pytestmark = pytest.mark.offline_database


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"function not found: {name}")


def _same_source_file(actual: Path, expected: Path) -> bool:
    try:
        return actual.samefile(expected)
    except OSError:
        return False


def test_agui_ledger_identity_allows_normal_answer_transition() -> None:
    expected = {
        "q-1": {
            "group_id": "g-1",
            "version": 1,
            "terminal_id": "t-1",
            "incarnation_id": "i-1",
        }
    }
    answered = [
        {
            **expected["q-1"],
            "question_id": "q-1",
            "lifecycle": "answered",
            "state": "accepted",
        }
    ]
    changed_incarnation = [
        {
            **answered[0],
            "incarnation_id": "i-2",
        }
    ]

    assert journey._question_ledger_identity_matches(answered, expected) is True
    assert journey._question_ledger_identity_matches(changed_incarnation, expected) is False


def test_harness_uses_initialized_production_terminal_then_normal_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    phases: list[str] = []
    terminal = SimpleNamespace(id="oc-private", session_name="cao-private", name="window-private")
    principal = object()

    class FakeProvider:
        async def initialize(self) -> bool:
            calls.append(("initialize", None))
            return True

    class FakeTerminalService:
        generate_terminal_id = staticmethod(lambda: "unexpected-generated-id")

        async def create_terminal(self, **kwargs: Any) -> Any:
            calls.append(("create_terminal", kwargs))
            await FakeProvider().initialize()
            return terminal

        def send_input(self, terminal_id: str, prompt: str) -> int:
            calls.append(("send_input", (terminal_id, prompt)))
            return 7

    service = FakeTerminalService()
    created: list[Any] = []
    result_terminal, sequence = asyncio.run(
        journey._create_and_send_native_terminal(
            service,
            terminal_id="oc-private",
            profile_name="profile-private",
            session_name="cao-private",
            working_directory=Path("/private/work"),
            model=journey.common.OPENCODE_INTERACTION_MODEL,
            creation_principal=principal,
            prompt="private prompt",
            on_created=created.append,
            on_phase=lambda phase: phases.append(phase),
        )
    )

    assert result_terminal is terminal
    assert created == [terminal]
    assert sequence == 7
    assert [name for name, _ in calls] == [
        "create_terminal",
        "initialize",
        "send_input",
    ]
    create_args = calls[0][1]
    assert create_args == {
        "provider": "opencode_cli",
        "agent_profile": "profile-private",
        "session_name": "cao-private",
        "new_session": True,
        "working_directory": "/private/work",
        "allowed_tools": ["@cao-mcp-server"],
        "model": journey.common.OPENCODE_INTERACTION_MODEL,
        "defer_init": False,
        "creation_principal": principal,
    }
    assert create_args["creation_principal"] is principal
    assert service.generate_terminal_id() == "unexpected-generated-id"
    assert calls[-1] == ("send_input", ("oc-private", "private prompt"))
    assert phases == ["native_create", "native_send_input"]

    # The harness helper itself cannot regress to the headless JSON command.
    helper = _function_source(Path(journey.__file__), "_create_and_send_native_terminal")
    assert "terminal_service.create_terminal" in helper
    assert "terminal_service.send_input" in helper
    assert "_headless_command" not in helper
    assert "subprocess.Popen" not in helper


def test_agui_deadline_publishes_after_initialization_before_one_input_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    now = [100.0]
    stale_precreate_deadline = now[0] + journey.RUN_BUDGET_SECONDS
    barrier = tmp_path / "barrier"
    barrier.mkdir(mode=0o700)
    monkeypatch.setattr(journey, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class FakeProvider:
        async def initialize(self) -> bool:
            events.append("provider_initialized")
            now[0] += 100.0
            return True

    terminal = SimpleNamespace(id="oc-private", session_name="cao-private", name="window-private")

    class FakeTerminalService:
        generate_terminal_id = staticmethod(lambda: "unused-generated-id")

        async def create_terminal(self, **kwargs: Any) -> Any:
            events.append("terminal_created")
            assert kwargs["defer_init"] is False
            await FakeProvider().initialize()
            return terminal

        def send_input(self, terminal_id: str, prompt: str) -> int:
            events.append("input_sent")
            assert terminal_id == terminal.id
            assert prompt == "private prompt"
            published = float((barrier / "journey-deadline").read_text(encoding="ascii"))
            assert published == now[0] + journey.RUN_BUDGET_SECONDS
            return 1

    published_deadlines: list[float] = []

    def on_phase(phase: str) -> None:
        events.append(phase)
        if phase == "native_send_input":
            deadline = journey._publish_agui_native_deadline_before_send(
                barrier_directory=barrier,
                existing_deadline=None,
            )
            published_deadlines.append(deadline)
            events.append("deadline_published")

    result, sequence = asyncio.run(
        journey._create_and_send_native_terminal(
            FakeTerminalService(),
            terminal_id=terminal.id,
            profile_name="profile-private",
            session_name=terminal.session_name,
            working_directory=tmp_path,
            model=journey.common.OPENCODE_INTERACTION_MODEL,
            creation_principal=object(),
            prompt="private prompt",
            on_created=lambda _: events.append("created_callback"),
            on_phase=on_phase,
        )
    )

    assert result is terminal
    assert sequence == 1
    assert events == [
        "native_create",
        "terminal_created",
        "provider_initialized",
        "created_callback",
        "native_send_input",
        "deadline_published",
        "input_sent",
    ]
    assert len(published_deadlines) == 1
    assert stale_precreate_deadline < now[0]
    assert published_deadlines == [now[0] + journey.RUN_BUDGET_SECONDS]


@pytest.mark.parametrize(
    ("failure_kind", "expected_error"),
    [
        ("already_published", "agui_restart_deadline_already_published"),
        ("missing_barrier", "mcp_restart_barrier_unavailable"),
        ("marker_exists", "mcp_restart_barrier_marker_publish_failed"),
    ],
)
def test_agui_deadline_publication_failure_prevents_native_input_send(
    tmp_path: Path,
    failure_kind: str,
    expected_error: str,
) -> None:
    barrier = tmp_path / "barrier"
    barrier.mkdir(mode=0o700)
    stale_marker = barrier / "journey-deadline"
    if failure_kind == "marker_exists":
        stale_marker.write_text("123.0", encoding="ascii")
        stale_marker.chmod(0o600)
    calls: list[str] = []
    terminal = SimpleNamespace(id="oc-private", session_name="cao-private", name="window-private")

    class FakeTerminalService:
        generate_terminal_id = staticmethod(lambda: "unused-generated-id")

        async def create_terminal(self, **kwargs: Any) -> Any:
            calls.append("created")
            return terminal

        def send_input(self, terminal_id: str, prompt: str) -> int:
            calls.append("sent")
            return 1

    def on_phase(phase: str) -> None:
        if phase == "native_send_input":
            journey._publish_agui_native_deadline_before_send(
                barrier_directory=None if failure_kind == "missing_barrier" else barrier,
                existing_deadline=123.0 if failure_kind == "already_published" else None,
            )

    with pytest.raises(RuntimeError, match=expected_error):
        asyncio.run(
            journey._create_and_send_native_terminal(
                FakeTerminalService(),
                terminal_id=terminal.id,
                profile_name="profile-private",
                session_name=terminal.session_name,
                working_directory=tmp_path,
                model=journey.common.OPENCODE_INTERACTION_MODEL,
                creation_principal=object(),
                prompt="private prompt",
                on_created=lambda _: None,
                on_phase=on_phase,
            )
        )
    assert calls == ["created"]
    if failure_kind == "marker_exists":
        assert stale_marker.read_text(encoding="ascii") == "123.0"
    else:
        assert not stale_marker.exists()


def test_agui_deadline_is_published_only_from_the_native_send_phase() -> None:
    run_source = _function_source(Path(journey.__file__), "run")
    phase_source = _function_source(Path(journey.__file__), "mark_phase")
    assert "set_journey_deadline(mcp_barrier_directory, deadline)" not in run_source
    assert "_publish_agui_native_deadline_before_send" in phase_source
    assert (
        'if agui_restart:\n                raise RuntimeError("agui_restart_deadline_not_published")'
        in run_source
    )


def test_create_response_barrier_returns_the_exact_real_result_after_release(
    tmp_path: Path,
) -> None:
    import threading
    import time
    from test.live_provider import interaction_opencode_mcp_gate as gate

    directory = tmp_path / "barrier"
    directory.mkdir(mode=0o700)
    result = object()
    original_calls: list[tuple[str, object]] = []

    class FakeMcpUtils:
        @staticmethod
        def interaction_post_json(path: str, body: object) -> object:
            original_calls.append((path, body))
            return result

    utils = FakeMcpUtils()
    restore = gate.install_create_response_barrier(utils, directory)
    gate.set_journey_deadline(directory, time.monotonic() + 2)
    outcomes: list[object] = []
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            outcomes.append(utils.interaction_post_json("/agent/interactions/groups", object()))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=invoke)
    try:
        thread.start()
        deadline = time.monotonic() + 2
        while not gate.barrier_reached(directory) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate.barrier_reached(directory)
        assert len(original_calls) == 1
        assert thread.is_alive()
        gate.release_barrier(directory)
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert failures == []
        assert len(outcomes) == 1 and outcomes[0] is result
    finally:
        if thread.is_alive():
            gate.release_barrier(directory, abort=True)
            thread.join(timeout=2)
        restore()


def test_create_response_barrier_propagates_the_original_exception_identity(
    tmp_path: Path,
) -> None:
    from test.live_provider import interaction_opencode_mcp_gate as gate

    directory = tmp_path / "barrier"
    directory.mkdir(mode=0o700)
    original_error = ValueError("private sentinel")

    class FakeMcpUtils:
        @staticmethod
        def interaction_post_json(path: str, body: object) -> object:
            raise original_error

    utils = FakeMcpUtils()
    restore = gate.install_create_response_barrier(utils, directory)
    try:
        with pytest.raises(ValueError) as caught:
            utils.interaction_post_json("/agent/interactions/groups", object())
        assert caught.value is original_error
        assert not gate.barrier_reached(directory)
    finally:
        restore()


def test_create_response_barrier_deadline_fails_closed_after_real_response(
    tmp_path: Path,
) -> None:
    import time
    from test.live_provider import interaction_opencode_mcp_gate as gate

    directory = tmp_path / "barrier"
    directory.mkdir(mode=0o700)
    result = object()

    class FakeMcpUtils:
        @staticmethod
        def interaction_post_json(path: str, body: object) -> object:
            return result

    utils = FakeMcpUtils()
    restore = gate.install_create_response_barrier(utils, directory)
    try:
        gate.set_journey_deadline(directory, time.monotonic() - 1)
        with pytest.raises(TimeoutError, match="mcp_restart_barrier_deadline_exhausted"):
            utils.interaction_post_json("/agent/interactions/groups", object())
        assert gate.barrier_reached(directory)
    finally:
        restore()


def test_create_response_barrier_rejects_missing_deadline_without_waiting(
    tmp_path: Path,
) -> None:
    import threading
    import time
    from test.live_provider import interaction_opencode_mcp_gate as gate

    directory = tmp_path / "barrier"
    directory.mkdir(mode=0o700)
    result = object()

    class FakeMcpUtils:
        @staticmethod
        def interaction_post_json(path: str, body: object) -> object:
            return result

    utils = FakeMcpUtils()
    restore = gate.install_create_response_barrier(utils, directory)
    outcomes: list[object] = []
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            outcomes.append(utils.interaction_post_json("/agent/interactions/groups", object()))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=invoke)
    try:
        thread.start()
        deadline = time.monotonic() + 1
        while not gate.barrier_reached(directory) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate.barrier_reached(directory)
        thread.join(timeout=0.25)
        if thread.is_alive():
            gate.release_barrier(directory, abort=True)
            thread.join(timeout=1)
        assert not thread.is_alive()
        assert outcomes == []
        assert len(failures) == 1
        assert isinstance(failures[0], RuntimeError)
        assert str(failures[0]) == "mcp_restart_barrier_deadline_missing"
    finally:
        if thread.is_alive():
            gate.release_barrier(directory, abort=True)
            thread.join(timeout=1)
        restore()


def test_final_agui_snapshot_checks_pending_to_acknowledged_identity_and_state() -> None:
    from test.live_provider.interaction_agui_restart import (
        parse_sse_frames,
        snapshot_from_frame,
        snapshot_identity_map,
    )
    from test.live_provider.test_interaction_agui_restart import _valid_interaction_item

    expected = {
        "q-1": {
            "group_id": "g-1",
            "version": 1,
            "terminal_id": "t-1",
            "incarnation_id": "i-1",
        },
        "q-2": {
            "group_id": "g-1",
            "version": 2,
            "terminal_id": "t-1",
            "incarnation_id": "i-1",
        },
    }

    def snapshot(*, acknowledged: bool, changed: tuple[str, object] | None = None):
        lifecycle = "answered" if acknowledged else "open"
        delivery_state = "agent_acknowledged" if acknowledged else None
        items = []
        for question_id, identity in expected.items():
            receipt = (
                {
                    "question_id": question_id,
                    "version": identity["version"],
                    "answer_id": f"answer-{question_id}",
                    "lifecycle": lifecycle,
                    "delivery_state": delivery_state,
                    "accepted_at": "2026-10-15T00:00:01Z",
                }
                if acknowledged
                else None
            )
            source = {
                "terminal_id": identity["terminal_id"],
                "incarnation_id": identity["incarnation_id"],
                "provider": "opencode_cli",
                "provider_version": "opencode-v2",
                "execution_mode": "interactive",
            }
            item = _valid_interaction_item(
                question_id=question_id,
                version=identity["version"],
                group_id=identity["group_id"],
                lifecycle=lifecycle,
                delivery_state=delivery_state,
                source=source,
                group_progress={"required_total": 2, "required_answered": 2 if acknowledged else 0},
                answer_receipt=receipt,
            )
            items.append(item)
        if changed is not None:
            field, value = changed
            if field in {"incarnation_id", "terminal_id"}:
                items[0]["source"][field] = value
            else:
                items[0][field] = value
        payload = {
            "snapshot": {
                "interactions": {
                    "available": True,
                    "items": items,
                    "next_cursor": None,
                    "truncated": False,
                    "event_sequence": 7,
                }
            }
        }
        frame = parse_sse_frames("event: STATE_SNAPSHOT\ndata: " + json.dumps(payload) + "\n\n")[0]
        return snapshot_from_frame(frame)

    pending_snapshot = snapshot(acknowledged=False)
    before_identity = snapshot_identity_map(pending_snapshot)
    final_snapshot = snapshot(acknowledged=True)
    rows = [
        {
            "question_id": question_id,
            **identity,
            "lifecycle": "answered",
            "state": "agent_acknowledged",
        }
        for question_id, identity in expected.items()
    ]

    final_identity, acknowledged, identity_stable, ledger_stable = (
        journey._final_agui_snapshot_checks(
            final_snapshot,
            expected,
            before_identity,
            rows,
        )
    )
    assert acknowledged is True
    assert identity_stable is True
    assert ledger_stable is True
    assert final_identity != before_identity
    assert {
        question_id: (identity["lifecycle"], identity["delivery_state"])
        for question_id, identity in final_identity.items()
    } == {"q-1": ("answered", "agent_acknowledged"), "q-2": ("answered", "agent_acknowledged")}

    for changed in (
        ("question_id", "q-other"),
        ("group_id", "g-other"),
        ("version", 9),
        ("incarnation_id", "i-other"),
        ("terminal_id", "t-other"),
    ):
        changed_snapshot = snapshot(acknowledged=True, changed=changed)
        _, changed_ack, changed_identity, _ = journey._final_agui_snapshot_checks(
            changed_snapshot,
            expected,
            before_identity,
            rows,
        )
        assert changed_ack is False
        assert changed_identity is False

    pending_rows = [
        {
            "question_id": question_id,
            **identity,
            "lifecycle": "open",
            "state": None,
        }
        for question_id, identity in expected.items()
    ]
    _, pending_ack, pending_identity, pending_ledger = journey._final_agui_snapshot_checks(
        pending_snapshot,
        expected,
        before_identity,
        pending_rows,
    )
    assert pending_ack is False
    assert pending_identity is True
    assert pending_ledger is False

    redacted_item = _valid_interaction_item(
        recipient={"kind": "human", "label": "secret", "reason": "human"}
    )
    redacted_payload = {
        "snapshot": {
            "interactions": {
                "available": True,
                "items": [redacted_item],
                "next_cursor": None,
                "truncated": False,
                "event_sequence": 8,
            }
        }
    }
    redacted_frame = parse_sse_frames(
        "event: STATE_SNAPSHOT\ndata: " + json.dumps(redacted_payload) + "\n\n"
    )[0]
    with pytest.raises(ValueError, match="unsafe_interaction_projection"):
        snapshot_from_frame(redacted_frame, forbidden_content=("secret",))


def test_success_requires_final_provider_state_and_result_before_deadline() -> None:
    assert journey._completion_allowed_before_deadline(
        success_conditions=True,
        timed_out=False,
        deadline=10.0,
        observed_at=9.99,
        status_observed_at=9.98,
    )
    assert not journey._completion_allowed_before_deadline(
        success_conditions=True,
        timed_out=False,
        deadline=10.0,
        observed_at=10.0,
        status_observed_at=9.99,
    )
    assert not journey._completion_allowed_before_deadline(
        success_conditions=True,
        timed_out=False,
        deadline=10.0,
        observed_at=9.99,
        status_observed_at=10.0,
    )
    assert not journey._completion_allowed_before_deadline(
        success_conditions=True,
        timed_out=True,
        deadline=10.0,
        observed_at=9.99,
        status_observed_at=9.98,
    )


def test_final_provider_state_uses_only_production_provider_status_enum() -> None:
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    class FakeProvider:
        def __init__(self, status: object) -> None:
            self.status = status
            self.buffers: list[str] = []

        def get_status(self, buffer: str) -> object:
            self.buffers.append(buffer)
            return self.status

    accepted = journey._provider_final_state
    for status in (TerminalStatus.IDLE, TerminalStatus.COMPLETED):
        provider = FakeProvider(status)
        assert accepted(provider, "private buffer") == status.name
        assert provider.buffers == ["private buffer"]
    for status in (
        TerminalStatus.PROCESSING,
        TerminalStatus.ERROR,
        TerminalStatus.UNKNOWN,
        None,
        "IDLE",
    ):
        expected = status.name if isinstance(status, TerminalStatus) else "UNKNOWN"
        assert accepted(FakeProvider(status), "private buffer") == expected
    assert not journey._provider_final_state_is_complete("PROCESSING")
    assert not journey._provider_final_state_is_complete("ERROR")
    assert not journey._provider_final_state_is_complete("UNKNOWN")
    assert journey._provider_final_state_is_complete("IDLE")
    assert journey._provider_final_state_is_complete("COMPLETED")


def test_real_production_source_awaits_opencode_initialize_and_builds_mini_command() -> None:
    source_root = journey.common._source_root()
    expected_modules = (
        (
            cli_agent_orchestrator,
            source_root / "src/cli_agent_orchestrator/__init__.py",
        ),
        (
            opencode_provider,
            source_root / "src/cli_agent_orchestrator/providers/opencode_cli.py",
        ),
        (
            terminal_service,
            source_root / "src/cli_agent_orchestrator/services/terminal_service.py",
        ),
        (
            journey.common,
            source_root / "test/live_provider/interaction_cli_journey.py",
        ),
    )
    assert all(
        _same_source_file(Path(module.__file__), expected_path)
        for module, expected_path in expected_modules
    )

    terminal_source = _function_source(
        source_root / "src/cli_agent_orchestrator/services/terminal_service.py",
        "create_terminal",
    )
    provider_source = _function_source(
        source_root / "src/cli_agent_orchestrator/providers/opencode_cli.py",
        "initialize",
    )
    command_source = _function_source(
        source_root / "src/cli_agent_orchestrator/providers/opencode_cli.py",
        "_build_launch_command",
    )

    assert "await provider_instance.initialize()" in terminal_source
    assert "await self._initialize_validated()" in provider_source
    assert 'cmd_parts += ["mini", "--standalone"]' in command_source
    assert '"--agent"' in command_source
    assert '"--model"' in command_source


def test_native_mcp_config_uses_private_home_and_contains_no_provider_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-root"
    mcp_home = private_root / "mcp-home"
    cao_home = private_root / "cao"
    xdg_data = private_root / "mcp-xdg-data"
    tmp_dir = private_root / "tmp"
    for path in (mcp_home, cao_home, xdg_data, tmp_dir):
        path.mkdir(mode=0o700, parents=True)
    monkeypatch.setenv("OPENCODE_API_KEY", "PARENT_SECRET_SENTINEL")
    monkeypatch.setenv("OPENAI_API_KEY", "OTHER_SECRET_SENTINEL")
    monkeypatch.setattr(journey.common, "_source_pythonpath", lambda: "/private/candidate/src")

    source_server = journey._native_mcp_server(
        cao_home=cao_home,
        mcp_home=mcp_home,
        mcp_xdg_data_home=xdg_data,
        tmp_dir=tmp_dir,
        api_port=43127,
        terminal_id="oc-private",
    )
    assert is_bundled_cao_mcp_entry("cao-mcp-server", source_server)
    config_path = private_root / "opencode-config" / "opencode.json"
    monkeypatch.setattr(opencode_config, "OPENCODE_CONFIG_FILE", config_path)
    opencode_config.write_config(
        {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "timeout": journey.NATIVE_MCP_TIMEOUT,
                "servers": {"cao-mcp-server": source_server},
            },
        }
    )
    persisted = opencode_config.read_config()
    assert persisted["mcp"]["servers"]["cao-mcp-server"] == source_server

    # The previous order isolated the command before CAO attached its protected
    # capability path. That wrapped command no longer qualifies as canonical.
    prematurely_isolated = journey._native_mcp_server(
        cao_home=cao_home,
        mcp_home=mcp_home,
        mcp_xdg_data_home=xdg_data,
        tmp_dir=tmp_dir,
        api_port=43127,
        terminal_id="oc-private",
    )
    journey._isolate_mcp_server(prematurely_isolated)
    with pytest.raises(RuntimeError, match="native_cao_mcp_server_not_canonical"):
        journey._overlay_native_mcp(
            {"cao-mcp-server": prematurely_isolated},
            str(private_root / "cao" / "tmp" / "capability.cap"),
            overlay_interaction_capability_file,
            interaction_pull_flow_only=True,
        )

    capability_file = str(private_root / "cao" / "tmp" / "capability.cap")
    overlaid = journey._overlay_native_mcp(
        persisted["mcp"]["servers"],
        capability_file,
        overlay_interaction_capability_file,
        interaction_pull_flow_only=True,
    )["cao-mcp-server"]
    assert is_bundled_cao_mcp_entry("cao-mcp-server", overlaid)
    assert overlaid["environment"]["CAO_INTERACTION_CAPABILITY_FILE"] == capability_file
    assert overlaid["environment"][_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV] == "1"
    assert (
        opencode_provider._find_cao_mcp_server_name(
            {"mcp": {"servers": {"cao-mcp-server": overlaid}}}
        )
        == "cao-mcp-server"
    )

    unrelated_server = {
        "type": "local",
        "command": ["/usr/bin/true"],
        "environment": {"HOME": str(mcp_home)},
    }
    source_config = {
        "mcp": {
            "timeout": journey.NATIVE_MCP_TIMEOUT,
            "servers": {
                "cao-mcp-server": overlaid,
                "unrelated-server": unrelated_server,
            },
        }
    }
    prepared = journey._isolate_native_mcp_for_v2_writer(source_config)
    config = prepared["mcp"]["servers"]["cao-mcp-server"]
    encoded = repr(prepared)
    assert source_config["mcp"]["servers"]["cao-mcp-server"] == overlaid
    assert source_config["mcp"]["servers"]["unrelated-server"] == unrelated_server
    assert prepared["mcp"]["servers"]["unrelated-server"] == unrelated_server

    written = journey._write_v2_configuration_with_isolated_native_mcp(
        opencode_v2.write_v2_configuration,
        private_root / "v2-config",
        source_agents=private_root / "agents",
        source_config=source_config,
        session_id="oc-private",
    )
    emitted = json.loads((written / "opencode.json").read_text(encoding="utf-8"))
    emitted_server = emitted["mcp"]["servers"]["cao-mcp-server"]
    command = emitted_server["command"]
    assert command[0].startswith("/")
    assert command[1] == "-i"
    assert command[-2:] == ["-m", "cli_agent_orchestrator.mcp_server.server"]
    assert emitted_server["environment"] == {}
    assert "HOME=" + str(mcp_home) in command
    assert _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV + "=1" in command
    assert "XDG_DATA_HOME=" + str(xdg_data) in command
    assert "PYTHONPATH=/private/candidate/src" in command
    assert "CAO_API_PORT=43127" in command
    assert "CAO_TERMINAL_ID=oc-private" in command
    assert "CAO_INTERACTION_CAPABILITY_FILE=" + capability_file in command
    assert emitted["mcp"]["servers"]["unrelated-server"] == unrelated_server
    assert not any(value.startswith("OPENCODE_API_KEY=") for value in command)
    assert "PARENT_SECRET_SENTINEL" not in encoded
    assert "OTHER_SECRET_SENTINEL" not in encoded
    assert os.environ["OPENCODE_API_KEY"] == "PARENT_SECRET_SENTINEL"
    assert os.environ["OPENAI_API_KEY"] == "OTHER_SECRET_SENTINEL"


def test_sitecustomize_restart_barrier_is_default_off_for_observer_sender(
    tmp_path: Path,
) -> None:
    import subprocess

    candidate_root = Path(journey.__file__).resolve().parents[2]
    sitecustomize_dir = Path(journey.__file__).resolve().parent
    source_root = candidate_root / "src"
    private_root = tmp_path / "observer-private"
    cao_home = private_root / "cao"
    private_home = private_root / "home"
    workdir = private_root / "work"
    for path in (cao_home, private_home, workdir):
        path.mkdir(mode=0o700, parents=True)
    env = {
        "HOME": str(private_home),
        "CAO_HOME_DIR": str(cao_home),
        "PYTHONPATH": os.pathsep.join(
            (str(sitecustomize_dir), str(candidate_root), str(source_root))
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; "
            "assert 'sitecustomize' in sys.modules; "
            "assert 'interaction_opencode_mcp_gate' not in sys.modules",
        ],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 0, child.returncode
    assert child.stderr == ""


def test_sitecustomize_installs_restart_barrier_with_exact_native_child_environment(
    tmp_path: Path,
) -> None:
    import subprocess

    private_root = tmp_path / "native-private"
    cao_home = private_root / "cao"
    mcp_home = private_root / "home"
    xdg_data = private_root / "xdg-data"
    tmp_dir = private_root / "tmp"
    workdir = private_root / "work"
    for path in (cao_home, mcp_home, xdg_data, tmp_dir, workdir):
        path.mkdir(mode=0o700, parents=True)
    child_config = journey._native_mcp_server(
        cao_home=cao_home,
        mcp_home=mcp_home,
        mcp_xdg_data_home=xdg_data,
        tmp_dir=tmp_dir,
        api_port=43127,
        terminal_id="oc-private",
        install_restart_barrier=True,
    )
    assert is_bundled_cao_mcp_entry("cao-mcp-server", child_config)
    env = dict(child_config["environment"])
    barrier_path = env.get("CAO_MCP_RESTART_BARRIER_DIR")
    assert barrier_path == str(private_root / "mcp-response-barrier")
    Path(barrier_path).mkdir(mode=0o700)
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; "
            "from cli_agent_orchestrator.mcp_server import utils; "
            "assert 'sitecustomize' in sys.modules; "
            "assert 'interaction_opencode_mcp_gate' in sys.modules; "
            "assert utils.interaction_post_json.__name__ == 'guarded'",
        ],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 0, child.returncode
    assert child.stderr == ""


def test_sitecustomize_rejects_invalid_enabled_restart_barrier_without_path_output(
    tmp_path: Path,
) -> None:
    import subprocess

    private_root = tmp_path / "invalid-private"
    sitecustomize_dir = Path(journey.__file__).resolve().parent
    candidate_root = Path(journey.__file__).resolve().parents[2]
    source_root = candidate_root / "src"
    workdir = private_root / "work"
    home = private_root / "home"
    for path in (workdir, home):
        path.mkdir(mode=0o700, parents=True)
    env = {
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join((str(sitecustomize_dir), str(source_root))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "CAO_MCP_RESTART_BARRIER_DIR": "relative-private-path",
    }
    child = subprocess.run(
        [sys.executable, "-B", "-c", "pass"],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert child.returncode != 0
    assert "mcp_restart_barrier_startup_rejected" in child.stderr
    assert str(private_root) not in child.stderr


def test_run_uses_production_create_path_not_headless_json_path() -> None:
    source = _function_source(Path(journey.__file__), "run")
    assert "_create_and_send_native_terminal" in source
    assert "_headless_command" not in source
    assert "subprocess.Popen" not in source
    assert '"execution_path": "terminal_service.create_terminal+send_input"' in source
    environment_index = source.index("_replace_process_environment(private_cli_environment)")
    client_index = source.index("tmux_client = TmuxClient()", environment_index)
    pin_index = source.index("_pin_tmux_client_to_private_server(", client_index)
    create_index = source.index("_create_and_send_native_terminal(", pin_index)
    phase_source = _function_source(Path(journey.__file__), "mark_phase")
    wait_deadline_index = source.index("if deadline is None:", create_index)
    assert environment_index < client_index < pin_index < create_index < wait_deadline_index
    assert "set_journey_deadline(mcp_barrier_directory, deadline)" not in source
    assert "_publish_agui_native_deadline_before_send" in phase_source
    assert 'next_phase == "native_send_input" and agui_restart' in phase_source


def test_provider_can_discover_canonical_server_after_environment_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root = tmp_path / "private-root"
    mcp_home = private_root / "mcp-home"
    cao_home = private_root / "cao"
    xdg_data = private_root / "mcp-xdg-data"
    tmp_dir = private_root / "tmp"
    for path in (mcp_home, cao_home, xdg_data, tmp_dir):
        path.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(journey.common, "_source_pythonpath", lambda: "/private/candidate/src")

    source_server = journey._native_mcp_server(
        cao_home=cao_home,
        mcp_home=mcp_home,
        mcp_xdg_data_home=xdg_data,
        tmp_dir=tmp_dir,
        api_port=43127,
        terminal_id="oc-private",
    )
    overlaid = journey._overlay_native_mcp(
        {"cao-mcp-server": source_server},
        str(private_root / "cao" / "tmp" / "capability.cap"),
        overlay_interaction_capability_file,
    )
    # OpenCode performs this lookup after CAO overlays the protected capability
    # and before the v2 writer serializes the isolated copy.
    assert (
        opencode_provider._find_cao_mcp_server_name({"mcp": {"servers": overlaid}})
        == "cao-mcp-server"
    )
    writer_input = journey._isolate_native_mcp_for_v2_writer({"mcp": {"servers": overlaid}})
    assert writer_input["mcp"]["servers"]["cao-mcp-server"]["command"][1] == "-i"
    assert overlaid["cao-mcp-server"]["command"] == [
        sys.executable,
        "-m",
        "cli_agent_orchestrator.mcp_server.server",
    ]


def test_restart_barrier_survives_native_mcp_isolation_and_v2_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    private_root = tmp_path / "restart-barrier-v2-writer"
    cao_home = private_root / "cao"
    mcp_home = private_root / "mcp-home"
    xdg_data = private_root / "xdg-data"
    tmp_dir = private_root / "tmp"
    workdir = private_root / "work"
    for path in (cao_home, mcp_home, xdg_data, tmp_dir, workdir):
        path.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(
        journey.common,
        "_source_pythonpath",
        lambda: str(Path(journey.__file__).resolve().parents[2] / "src"),
    )

    default_server = journey._native_mcp_server(
        cao_home=cao_home,
        mcp_home=mcp_home,
        mcp_xdg_data_home=xdg_data,
        tmp_dir=tmp_dir,
        api_port=43127,
        terminal_id="oc-default",
    )
    assert "CAO_MCP_RESTART_BARRIER_DIR" not in default_server["environment"]

    source_server = journey._native_mcp_server(
        cao_home=cao_home,
        mcp_home=mcp_home,
        mcp_xdg_data_home=xdg_data,
        tmp_dir=tmp_dir,
        api_port=43127,
        terminal_id="oc-private",
        install_restart_barrier=True,
    )
    capability_file = str(cao_home / "tmp" / "capability.cap")
    overlaid = journey._overlay_native_mcp(
        {"cao-mcp-server": source_server},
        capability_file,
        overlay_interaction_capability_file,
    )
    source_config = {"mcp": {"servers": overlaid}}
    written = journey._write_v2_configuration_with_isolated_native_mcp(
        opencode_v2.write_v2_configuration,
        private_root / "v2-config",
        source_agents=private_root / "agents",
        source_config=source_config,
        session_id="oc-private",
    )
    emitted = json.loads((written / "opencode.json").read_text(encoding="utf-8"))
    emitted_server = emitted["mcp"]["servers"]["cao-mcp-server"]
    command = emitted_server["command"]
    barrier_path = str(cao_home.parent / "mcp-response-barrier")
    Path(barrier_path).mkdir(mode=0o700)
    assert emitted_server["environment"] == {}
    assert "CAO_MCP_RESTART_BARRIER_DIR=" + barrier_path in command
    assert source_config["mcp"]["servers"]["cao-mcp-server"] == overlaid["cao-mcp-server"]

    # Execute the generated env -i prefix with a harmless Python probe instead
    # of launching the real MCP server or contacting any service.
    executable_index = command.index(sys.executable)
    child = subprocess.run(
        [
            *command[:executable_index],
            sys.executable,
            "-B",
            "-c",
            "import os, sys; "
            "import cli_agent_orchestrator.mcp_server.utils as utils; "
            "assert os.environ['CAO_MCP_RESTART_BARRIER_DIR'] == sys.argv[1]; "
            "assert 'sitecustomize' in sys.modules; "
            "assert 'interaction_opencode_mcp_gate' in sys.modules; "
            "assert utils.interaction_post_json.__name__ == 'guarded'",
            barrier_path,
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 0, child.returncode


def test_run_keeps_native_mcp_canonical_until_capability_overlay() -> None:
    source = _function_source(Path(journey.__file__), "run")
    write_index = source.index("opencode_config.write_config(")
    source_server_index = source.index("source_server = _native_mcp_server(")

    assert source_server_index < write_index
    assert "_isolate_mcp_server(source_server)" not in source
    overlay_index = source.index("opencode_provider_module.overlay_interaction_capability_file =")
    writer_index = source.index("opencode_v2.write_v2_configuration = isolated_v2_writer")
    assert overlay_index < writer_index
    assert "_overlay_native_mcp(" in source
    assert "_write_v2_configuration_with_isolated_native_mcp(" in source
    assert source.index("opencode_config.write_config(") < source.index(
        "_create_and_send_native_terminal(",
        source.index("opencode_config.write_config("),
    )
    provider_source = _function_source(Path(opencode_provider.__file__), "_initialize_validated")
    write_v2_index = provider_source.index("write_v2_configuration(")
    assert provider_source.index("self._v2_cao_mcp_server_name = _find_cao_mcp_server_name") < (
        write_v2_index
    )
    assert provider_source.index("self._v2_config_dir = v2_config_dir") > write_v2_index


def test_terminal_cleanup_failure_is_reported_and_success_is_confirmed() -> None:
    class DeleteService:
        def __init__(self, result: bool) -> None:
            self.result = result
            self.deleted: list[str] = []

        def delete_terminal(self, terminal_id: str) -> bool:
            self.deleted.append(terminal_id)
            return self.result

    successful = DeleteService(True)
    no_confirmation = DeleteService(False)
    failures: set[str] = set()

    assert journey._cleanup_native_terminal(successful, "oc-private", failures)
    assert successful.deleted == ["oc-private"]
    assert not failures

    assert not journey._cleanup_native_terminal(no_confirmation, "oc-private", failures)
    assert no_confirmation.deleted == ["oc-private"]
    assert failures == {"terminal_cleanup_failed"}


def test_private_tmux_cleanup_removes_only_the_owned_socket_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_identity = journey._capture_private_tmux_socket_identity(socket_path)
    calls: list[str] = []
    alive = True

    class Server:
        returncode = 0

        def cmd(self, *args: str) -> Any:
            nonlocal alive
            calls.extend(args)
            if self.returncode == 0:
                alive = False
                listener.close()
                socket_path.unlink()
            return SimpleNamespace(returncode=self.returncode)

    client = SimpleNamespace(server=Server())
    client.server.socket_path = str(socket_path.absolute())
    server_identity = journey._ProcessIdentity(123, os.getuid(), 500, 123, 123)
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: server_identity)
    monkeypatch.setattr(journey, "_owned_process_snapshot", lambda *args: set())
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: alive)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(journey, "_terminal_tagged_processes", lambda *args, **kwargs: set())
    failures: set[str] = set()

    assert journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=server_identity,
        owned_processes=set(),
        socket_identity=socket_identity,
    )
    assert calls == ["kill-server"]
    assert not socket_path.exists()
    assert not failures

    replacement_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement_listener.bind(str(socket_path))
    replacement_socket_identity = journey._capture_private_tmux_socket_identity(socket_path)
    alive = True
    client.server.returncode = 1
    ticks = iter((10.0, 20.0))
    monkeypatch.setattr(journey.time, "monotonic", lambda: next(ticks, 20.0))
    assert not journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=server_identity,
        owned_processes=set(),
        socket_identity=replacement_socket_identity,
    )
    assert "tmux_server_cleanup_failed" in failures
    assert socket_path.exists()
    replacement_listener.close()


def test_private_tmux_client_ignores_ambient_socket_and_tmux_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = journey._private_opencode_root()
    try:
        private_tmux = root / "owned-tmux"
        private_tmux.mkdir(mode=0o700)
        monkeypatch.setenv("TMUX", "/foreign/tmux/socket,909,0")
        monkeypatch.setenv("TMUX_TMPDIR", "/foreign/tmux")
        client = SimpleNamespace(server=None)

        socket_path = journey._pin_tmux_client_to_private_server(client, private_tmux)

        assert socket_path == (private_tmux / f"tmux-{os.getuid()}" / "default").absolute()
        assert client.server.socket_path == str(socket_path)
        assert client.server.config_file == "/dev/null"
        assert socket_path.parent.stat().st_mode & 0o077 == 0
        assert not socket_path.exists()
        assert len(os.fsencode(socket_path)) < 108
        assert os.environ["TMUX"] == "/foreign/tmux/socket,909,0"
    finally:
        failures: set[str] = set()
        assert journey.common._remove_private_run_root(
            root, process_stopped=True, failures=failures
        )
        assert not failures


def test_private_opencode_root_preserves_unverified_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    replacement = tmp_path / "replacement"
    replacement.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(journey.tempfile, "mkdtemp", lambda **kwargs: str(replacement))

    with pytest.raises(RuntimeError, match="private_directory_required"):
        journey._private_opencode_root()

    assert replacement.is_symlink()
    assert target.is_dir()
    replacement.unlink()


def test_private_opencode_root_socket_binds_and_legacy_long_path_is_rejected(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / ("legacy-" + "x" * 72)
    legacy_socket = legacy_root / "tmux-tmp" / f"tmux-{os.getuid()}" / "default"
    legacy_socket.parent.mkdir(mode=0o700, parents=True)
    assert len(os.fsencode(legacy_socket.absolute())) >= 108
    failed_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError) as error:
            failed_socket.bind(str(legacy_socket))
        assert error.value.errno in (None, errno.ENAMETOOLONG)
        assert "path too long" in str(error.value).lower()
    finally:
        failed_socket.close()

    root = journey._private_opencode_root()
    socket_parent = root / "tmux-tmp" / f"tmux-{os.getuid()}"
    socket_parent.mkdir(mode=0o700, parents=True)
    socket_path = socket_parent / "default"
    assert root.parent == Path("/tmp")
    assert root.stat().st_uid == os.getuid()
    assert root.stat().st_mode & 0o777 == 0o700
    assert len(os.fsencode(socket_path.absolute())) < 108
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        assert socket_path.exists()
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)
    failures: set[str] = set()
    assert journey.common._remove_private_run_root(root, process_stopped=True, failures=failures)
    assert not failures
    assert not root.exists()


def test_private_scope_attestation_survives_module_binding_restore(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned-run"
    cao_home = root / "cao"
    agent_store = cao_home / "agent-store"
    agent_store.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    cao_home.chmod(0o700)
    environment = {
        "CAO_HOME_DIR": str(cao_home),
        "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
    }

    assert (
        journey._private_scope_is_exact(
            root,
            cao_home,
            agent_store,
            bound_cao_home=cao_home,
            bound_local_agent_store=agent_store,
            bound_profile_store=agent_store,
            environment=environment,
        )
        is True
    )
    # A runner observing restored operator bindings must not be able to turn
    # that restoration into a false provider-scope failure.
    assert (
        journey._private_scope_is_exact(
            root,
            cao_home,
            agent_store,
            bound_cao_home=Path("/operator/cao-home"),
            bound_local_agent_store=Path("/operator/cao-home/agent-store"),
            bound_profile_store=Path("/operator/cao-home/agent-store"),
            environment=environment,
        )
        is False
    )


def test_private_runtime_paths_rebind_fifo_and_log_modules(tmp_path: Path) -> None:
    cao_home = tmp_path / "run" / "cao"
    operator_fifo = Path("/operator/cao-home/fifos")
    operator_logs = Path("/operator/cao-home/logs")
    operator_terminal_logs = operator_logs / "terminal"
    constants_module = SimpleNamespace(
        FIFO_DIR=operator_fifo,
        LOG_DIR=operator_logs,
        TERMINAL_LOG_DIR=operator_terminal_logs,
    )
    fifo_module = SimpleNamespace(FIFO_DIR=operator_fifo)
    terminal_module = SimpleNamespace(
        FIFO_DIR=operator_fifo,
        TERMINAL_LOG_DIR=operator_terminal_logs,
    )
    bindings: list[tuple[Any, str, Any]] = []

    private_logs, private_terminal_logs, private_fifo = journey._bind_private_runtime_paths(
        cao_home=cao_home,
        modules=[constants_module, fifo_module, terminal_module],
        module_bindings=bindings,
    )

    assert private_logs == cao_home / "logs"
    assert private_terminal_logs == cao_home / "logs" / "terminal"
    assert private_fifo == cao_home / "fifos"
    assert constants_module.FIFO_DIR == private_fifo
    assert constants_module.LOG_DIR == private_logs
    assert constants_module.TERMINAL_LOG_DIR == private_terminal_logs
    assert fifo_module.FIFO_DIR == private_fifo
    assert terminal_module.FIFO_DIR == private_fifo
    assert terminal_module.TERMINAL_LOG_DIR == private_terminal_logs
    assert private_fifo.stat().st_mode & 0o777 == 0o700
    assert private_terminal_logs.stat().st_mode & 0o777 == 0o700

    for module, name, value in reversed(bindings):
        setattr(module, name, value)
    assert constants_module.FIFO_DIR == operator_fifo
    assert constants_module.LOG_DIR == operator_logs
    assert constants_module.TERMINAL_LOG_DIR == operator_terminal_logs
    assert fifo_module.FIFO_DIR == operator_fifo
    assert terminal_module.FIFO_DIR == operator_fifo
    assert terminal_module.TERMINAL_LOG_DIR == operator_terminal_logs


def test_private_constants_rebase_all_operator_home_paths(tmp_path: Path) -> None:
    operator_home = tmp_path / "operator-cao"
    private_home = tmp_path / "run" / "cao"
    constants_module = SimpleNamespace(
        **{name: operator_home / name.lower() for name in journey._PRIVATE_CONSTANT_PATH_NAMES}
    )
    bindings: list[tuple[Any, str, Any]] = []

    bound = journey._bind_private_constants_paths(
        constants_module=constants_module,
        source_cao_home=operator_home,
        cao_home=private_home,
        module_bindings=bindings,
    )

    assert set(bound) == set(journey._PRIVATE_CONSTANT_PATH_NAMES)
    assert all(path == private_home / name.lower() for name, path in bound.items())
    assert all(private_home in path.parents for path in bound.values())

    for module, name, value in reversed(bindings):
        setattr(module, name, value)
    assert all(
        getattr(constants_module, name) == operator_home / name.lower()
        for name in journey._PRIVATE_CONSTANT_PATH_NAMES
    )


def test_exact_interaction_permissions_rejects_partial_or_reordered_grants() -> None:
    expected = [
        {"action": "*", "resource": "*", "effect": "deny"},
        *[
            {"action": name, "resource": "*", "effect": "allow"}
            for name in journey.OPENCODE_INTERACTION_TOOL_NAMES
        ],
    ]

    assert journey._exact_interaction_permissions(expected) is True
    assert (
        journey._exact_interaction_permissions(
            [*expected, {"action": "execute", "resource": "*", "effect": "allow"}]
        )
        is False
    )
    assert (
        journey._exact_interaction_permissions(
            [
                *expected[:2],
                {**expected[2], "resource": "private"},
                *expected[3:],
            ]
        )
        is False
    )
    assert (
        journey._exact_interaction_permissions(
            [expected[0], expected[2], expected[1], *expected[3:]]
        )
        is False
    )


def test_private_process_environment_is_allowlisted_and_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sentinels = {
        "AWS_ACCESS_KEY_ID": "AWS_SENTINEL",
        "AWS_PROFILE": "AWS_PROFILE_SENTINEL",
        "KIRO_API_KEY": "KIRO_SENTINEL",
        "MISE_ENV": "MISE_SENTINEL",
        "CAO_AUTH_LOCAL_TOKEN": "CAO_TOKEN_SENTINEL",
        "CAO_AUTH_LOCAL_TOKEN_FILE": "/private/operator/token",
        "CAO_AUTH_ISSUER": "https://operator.invalid",
        "CAO_AUTH_JWKS_URI": "https://operator.invalid/jwks",
        "CAO_AUTH_AUDIENCE": "operator-audience",
        "OPENCODE_API_KEY": "OPENCODE_SENTINEL",
        "TMUX": "/foreign/tmux/socket,909,0",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    original = dict(os.environ)
    root = tmp_path / "private"
    private_env = journey._private_cli_environment(
        root=root,
        cao_home=root / "cao",
        config_dir=root / "config",
        data_home=root / "data",
        api_port=43127,
        source_pythonpath="/private/source/src",
        path_value="/private/runtime/bin:/usr/bin:/bin",
        tmux_tmpdir=root / "tmux-tmp",
    )

    assert set(private_env) == {
        "HOME",
        "PATH",
        "LANG",
        "TERM",
        "TMPDIR",
        "TMUX_TMPDIR",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "CAO_HOME_DIR",
        "CAO_DATABASE_CONFIG_FILE",
        "CAO_API_HOST",
        "CAO_API_PORT",
        "CAO_AUTH_MODE",
        "CAO_TERMINAL_BACKEND",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "OPENCODE_DISABLE_AUTOUPDATE",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
    }
    assert "TMUX" not in private_env
    assert "CAO_AUTH_LOCAL_TOKEN" not in private_env
    assert "CAO_TERMINAL_ID" not in private_env
    assert not any(name.startswith(("AWS_", "KIRO_", "MISE_")) for name in private_env)
    assert not any(value in repr(private_env) for value in sentinels.values())

    replaced = journey._replace_process_environment(private_env)
    try:
        assert dict(os.environ) == private_env
        assert "TMUX" not in os.environ
        assert "AWS_ACCESS_KEY_ID" not in os.environ
        assert "CAO_AUTH_LOCAL_TOKEN" not in os.environ
    finally:
        journey._restore_process_environment(replaced)
    assert dict(os.environ) == original


def test_private_tmux_cleanup_is_idempotent_after_expected_server_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    socket_path_value = str(socket_path.absolute())
    server_identity = journey._ProcessIdentity(123, os.getuid(), 500, 123, 123)

    class Server:
        socket_path = socket_path_value

        def cmd(self, *args: str) -> Any:
            raise AssertionError("already-exited server must not be killed")

    client = SimpleNamespace(server=Server())
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: None)
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: False)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(journey, "_terminal_tagged_processes", lambda *args, **kwargs: set())
    failures: set[str] = set()

    for _ in range(2):
        assert journey._cleanup_private_tmux_server(
            client,
            private_tmux,
            failures,
            terminal_id="oc-private",
            server_identity=server_identity,
            owned_processes=set(),
            socket_identity=journey._TmuxSocketIdentity(1, 2, os.getuid(), 0o600),
        )
    assert not failures


def test_private_tmux_cleanup_unlinks_receipted_stale_socket_after_owner_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_identity = journey._capture_private_tmux_socket_identity(socket_path)
    listener.close()
    assert socket_path.exists()

    socket_path_value = str(socket_path.absolute())
    server_identity = journey._ProcessIdentity(123, os.getuid(), 500, 123, 123)

    class Server:
        socket_path = socket_path_value

        def cmd(self, *args: str) -> Any:
            raise AssertionError("already-exited server must not be killed")

    client = SimpleNamespace(server=Server())
    monkeypatch.setattr(
        journey,
        "_find_tmux_server_identity",
        lambda path: (_ for _ in ()).throw(RuntimeError("private_tmux_socket_owner_unknown")),
    )
    monkeypatch.setattr(journey, "_tmux_socket_path_registered", lambda path: False, raising=False)
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: False)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(journey, "_terminal_tagged_processes", lambda *args, **kwargs: set())
    failures: set[str] = set()

    for _ in range(2):
        assert journey._cleanup_private_tmux_server(
            client,
            private_tmux,
            failures,
            terminal_id="oc-private",
            server_identity=server_identity,
            owned_processes=set(),
            socket_identity=socket_identity,
        )
    assert not socket_path.exists()
    assert not failures


def test_private_tmux_cleanup_unlinks_receipted_socket_node_after_kill_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_identity = journey._capture_private_tmux_socket_identity(socket_path)
    server_identity = journey._ProcessIdentity(123, os.getuid(), 500, 123, 123)
    calls: list[str] = []
    socket_path_value = str(socket_path.absolute())
    alive = True

    class Server:
        socket_path = socket_path_value

        def cmd(self, *args: str) -> Any:
            nonlocal alive
            calls.extend(args)
            alive = False
            listener.close()
            return SimpleNamespace(returncode=0)

    client = SimpleNamespace(server=Server())
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: server_identity)
    monkeypatch.setattr(journey, "_owned_process_snapshot", lambda *args: set())
    monkeypatch.setattr(journey, "_tmux_socket_path_registered", lambda path: False)
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: alive)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(journey, "_terminal_tagged_processes", lambda *args, **kwargs: set())
    failures: set[str] = set()

    assert journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=server_identity,
        owned_processes=set(),
        socket_identity=socket_identity,
    )
    assert calls == ["kill-server"]
    assert not socket_path.exists()
    assert not failures


@pytest.mark.parametrize(
    "missing_proof",
    [
        "receipt",
        "device",
        "inode",
        "uid",
        "mode",
        "parent_owner",
        "parent_mode",
        "registered",
        "process_group",
        "terminal_tag",
    ],
)
def test_stale_socket_is_preserved_without_required_cleanup_proof(
    missing_proof: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    captured = journey._capture_private_tmux_socket_identity(socket_path)
    listener.close()
    server_identity = journey._ProcessIdentity(123, os.getuid(), 500, 123, 123)
    socket_identity = captured
    if missing_proof == "receipt":
        socket_identity = None
    elif missing_proof == "device":
        socket_identity = journey._TmuxSocketIdentity(
            captured.device + 1, captured.inode, captured.uid, captured.mode
        )
    elif missing_proof == "inode":
        socket_identity = journey._TmuxSocketIdentity(
            captured.device, captured.inode + 1, captured.uid, captured.mode
        )
    elif missing_proof == "uid":
        socket_identity = journey._TmuxSocketIdentity(
            captured.device, captured.inode, captured.uid + 1, captured.mode
        )
    elif missing_proof == "mode":
        socket_identity = journey._TmuxSocketIdentity(
            captured.device, captured.inode, captured.uid, captured.mode ^ 0o100
        )

    if missing_proof in {"parent_owner", "parent_mode"}:
        original_lstat = Path.lstat

        def alter_parent_receipt(path: Path) -> Any:
            status = original_lstat(path)
            if path != socket_path.parent:
                return status
            uid = status.st_uid + 1 if missing_proof == "parent_owner" else status.st_uid
            mode = (
                stat.S_IFDIR | stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                if missing_proof == "parent_mode"
                else status.st_mode
            )
            return SimpleNamespace(st_mode=mode, st_uid=uid)

        monkeypatch.setattr(Path, "lstat", alter_parent_receipt)

    client = SimpleNamespace(server=SimpleNamespace(socket_path=str(socket_path.absolute())))
    monkeypatch.setattr(
        journey,
        "_find_tmux_server_identity",
        lambda path: (_ for _ in ()).throw(RuntimeError("private_tmux_socket_owner_unknown")),
    )
    monkeypatch.setattr(
        journey,
        "_tmux_socket_path_registered",
        lambda path: missing_proof == "registered",
    )
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: False)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        journey,
        "_processes_in_groups",
        lambda groups: {server_identity} if missing_proof == "process_group" else set(),
    )
    monkeypatch.setattr(
        journey,
        "_terminal_tagged_processes",
        lambda *args, **kwargs: ({server_identity} if missing_proof == "terminal_tag" else set()),
    )
    failures: set[str] = set()

    assert not journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=server_identity,
        owned_processes=set(),
        socket_identity=socket_identity,
    )
    assert socket_path.exists()
    assert failures == {"tmux_process_identity_unavailable"}


def test_private_process_inventory_pins_socket_owner_and_terminal_descendants(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    (proc_root / "net").mkdir(mode=0o700, parents=True)
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    socket_path.parent.mkdir(mode=0o700, parents=True)
    socket_path.touch(mode=0o600)
    (proc_root / "net" / "unix").write_text(
        "Num RefCount Protocol Flags Type St Inode Path\n"
        f"00000000: 00000002 00000000 00010000 0001 01 4242 {socket_path}\n",
        encoding="utf-8",
    )

    def add_process(
        pid: int,
        *,
        parent_pid: int,
        process_group: int,
        session_id: int,
        start_time: int,
        comm: str,
        environment: bytes,
        socket_inode: int | None = None,
    ) -> None:
        process = proc_root / str(pid)
        (process / "fd").mkdir(mode=0o700, parents=True)
        fields = ["0"] * 20
        fields[0] = "S"
        fields[1] = str(parent_pid)
        fields[2] = str(process_group)
        fields[3] = str(session_id)
        fields[19] = str(start_time)
        (process / "stat").write_text(f"{pid} ({comm}) {' '.join(fields)}", encoding="ascii")
        (process / "comm").write_text(comm, encoding="utf-8")
        (process / "environ").write_bytes(environment)
        if socket_inode is not None:
            (process / "fd" / "3").symlink_to(f"socket:[{socket_inode}]")

    add_process(
        101,
        parent_pid=1,
        process_group=101,
        session_id=101,
        start_time=1001,
        comm="tmux: server",
        environment=b"HOME=/private\0",
        socket_inode=4242,
    )
    add_process(
        102,
        parent_pid=101,
        process_group=101,
        session_id=101,
        start_time=1002,
        comm="opencode",
        environment=b"CAO_TERMINAL_ID=oc-private\0",
    )
    add_process(
        103,
        parent_pid=1,
        process_group=103,
        session_id=103,
        start_time=1003,
        comm="cao-mcp-server",
        environment=b"CAO_TERMINAL_ID=oc-private\0",
    )
    add_process(
        104,
        parent_pid=1,
        process_group=104,
        session_id=104,
        start_time=1004,
        comm="unrelated",
        environment=b"CAO_TERMINAL_ID=other\0",
    )

    server_identity = journey._find_tmux_server_identity(
        socket_path,
        proc_root=proc_root,
        unix_socket_table=proc_root / "net" / "unix",
    )
    assert server_identity == journey._ProcessIdentity(101, os.getuid(), 1001, 101, 101)
    owned = journey._owned_process_snapshot(
        server_identity,
        "oc-private",
        socket_path,
        proc_root=proc_root,
    )
    assert owned == {
        journey._ProcessIdentity(102, os.getuid(), 1002, 101, 101),
        journey._ProcessIdentity(103, os.getuid(), 1003, 103, 103),
    }


def _add_fake_process(
    proc_root: Path,
    pid: int,
    *,
    parent_pid: int,
    start_time: int,
    comm: str = "worker",
    environment: bytes = b"HOME=/private\0",
) -> Path:
    process = proc_root / str(pid)
    process.mkdir(mode=0o700, parents=True)
    fields = ["0"] * 20
    fields[0] = "S"
    fields[1] = str(parent_pid)
    fields[2] = str(pid)
    fields[3] = str(pid)
    fields[19] = str(start_time)
    (process / "stat").write_text(f"{pid} ({comm}) {' '.join(fields)}", encoding="ascii")
    (process / "comm").write_text(comm, encoding="utf-8")
    (process / "environ").write_bytes(environment)
    return process / "environ"


def test_process_info_ignores_non_ascii_comm_bytes_and_uses_last_parenthesis(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    process = proc_root / "7311"
    _add_fake_process(proc_root, 7311, parent_pid=1, start_time=1001)
    stat_path = process / "stat"
    original = stat_path.read_bytes()
    suffix = original.rsplit(b")", 1)[1].split()
    comm = b"worker " + bytes((0xFF, 0xE2, 0x98, 0x83)) + b" ) inner"
    stat_path.write_bytes(b"7311 (" + comm + b") " + b" ".join(suffix) + b"\n")

    info = journey._process_info(7311, proc_root=proc_root)

    assert info == (
        journey._ProcessIdentity(7311, os.getuid(), 1001, 7311, 7311),
        1,
        "S",
    )


def test_process_info_rejects_malformed_required_numeric_stat_fields(
    tmp_path: Path,
) -> None:
    cases = ((1, b"12x"), (2, bytes((0xFF,))), (19, b"-1"))
    for offset, (field_index, value) in enumerate(cases, start=1):
        proc_root = tmp_path / f"proc-{offset}"
        _add_fake_process(proc_root, 7320 + offset, parent_pid=1, start_time=1001)
        stat_path = proc_root / str(7320 + offset) / "stat"
        prefix, suffix = stat_path.read_bytes().rsplit(b")", 1)
        fields = suffix.split()
        fields[field_index] = value
        stat_path.write_bytes(prefix + b") " + b" ".join(fields) + b"\n")

        with pytest.raises(RuntimeError, match="^process_stat_unreadable$"):
            journey._process_info(7320 + offset, proc_root=proc_root)


def test_process_info_treats_exit_before_stat_read_as_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc_root = tmp_path / "proc"
    _add_fake_process(proc_root, 7331, parent_pid=1, start_time=1001)
    stat_path = proc_root / "7331" / "stat"
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == stat_path:
            raise FileNotFoundError("synthetic process exit")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    assert journey._process_info(7331, proc_root=proc_root) is None


def _deny_fake_environments(monkeypatch: pytest.MonkeyPatch, paths: set[Path]) -> set[Path]:
    original_read_bytes = Path.read_bytes
    read_paths: set[Path] = set()

    def read_bytes(path: Path) -> bytes:
        if path in paths:
            raise PermissionError("fake process environment is unreadable")
        if path.name == "environ":
            read_paths.add(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return read_paths


def test_process_snapshot_skips_old_unreadable_process_and_captures_descendant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(mode=0o700)
    socket_path = tmp_path / "private-tmux" / "default"
    server_environment = _add_fake_process(
        proc_root, 101, parent_pid=1, start_time=100, comm="tmux: server"
    )
    old_environment = _add_fake_process(
        proc_root, 102, parent_pid=1, start_time=99, comm="unrelated"
    )
    descendant_environment = _add_fake_process(
        proc_root,
        103,
        parent_pid=101,
        start_time=101,
        comm="opencode",
        environment=b"HOME=/private\0",
    )
    later_environment = _add_fake_process(
        proc_root, 104, parent_pid=1, start_time=102, comm="unrelated"
    )
    server_identity = journey._ProcessIdentity(101, os.getuid(), 100, 101, 101)
    read_paths = _deny_fake_environments(monkeypatch, {old_environment, descendant_environment})

    owned = journey._owned_process_snapshot(
        server_identity, "oc-private", socket_path, proc_root=proc_root
    )

    assert owned == {journey._ProcessIdentity(103, os.getuid(), 101, 103, 103)}
    assert read_paths == {later_environment}
    assert server_environment not in read_paths
    assert old_environment not in read_paths
    assert descendant_environment not in read_paths


def test_terminal_tag_scan_skips_old_process_and_finds_new_terminal_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(mode=0o700)
    socket_path = (tmp_path / "private-tmux" / "default").absolute()
    old_environment = _add_fake_process(
        proc_root, 201, parent_pid=1, start_time=99, comm="unrelated"
    )
    tagged_environment = _add_fake_process(
        proc_root,
        202,
        parent_pid=1,
        start_time=101,
        comm="detached-worker",
        environment=b"CAO_TERMINAL_ID=oc-private\0",
    )
    server_identity = journey._ProcessIdentity(200, os.getuid(), 100, 200, 200)
    read_paths = _deny_fake_environments(monkeypatch, {old_environment})

    tagged = journey._terminal_tagged_processes(
        "oc-private", socket_path, proc_root=proc_root, server_identity=server_identity
    )

    assert tagged == {journey._ProcessIdentity(202, os.getuid(), 101, 202, 202)}
    assert read_paths == {tagged_environment}


@pytest.mark.parametrize("scanner", ["snapshot", "terminal_tags"])
@pytest.mark.parametrize("start_time", [100, 101])
def test_process_scans_fail_closed_for_equal_or_newer_unreadable_processes(
    scanner: str,
    start_time: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(mode=0o700)
    socket_path = tmp_path / "private-tmux" / "default"
    server_identity = journey._ProcessIdentity(301, os.getuid(), 100, 301, 301)
    if scanner == "snapshot":
        _add_fake_process(proc_root, 301, parent_pid=1, start_time=100, comm="tmux: server")
    target_environment = _add_fake_process(
        proc_root, 302, parent_pid=1, start_time=start_time, comm="unrelated"
    )
    _deny_fake_environments(monkeypatch, {target_environment})

    with pytest.raises(RuntimeError, match="private_process_environment_unreadable"):
        if scanner == "snapshot":
            journey._owned_process_snapshot(
                server_identity, "oc-private", socket_path, proc_root=proc_root
            )
        else:
            journey._terminal_tagged_processes(
                "oc-private",
                socket_path,
                proc_root=proc_root,
                server_identity=server_identity,
            )


def test_terminal_tag_scan_without_server_identity_fails_closed_for_old_unreadable_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(mode=0o700)
    socket_path = tmp_path / "private-tmux" / "default"
    old_environment = _add_fake_process(
        proc_root, 401, parent_pid=1, start_time=1, comm="unrelated"
    )
    _deny_fake_environments(monkeypatch, {old_environment})

    with pytest.raises(RuntimeError, match="private_process_environment_unreadable"):
        journey._terminal_tagged_processes(
            "oc-private", socket_path, proc_root=proc_root, server_identity=None
        )


def test_private_tmux_cleanup_fails_if_owned_descendant_remains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    server_identity = journey._ProcessIdentity(123, os.getuid(), 500, 123, 123)
    child_identity = journey._ProcessIdentity(124, os.getuid(), 501, 123, 123)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_identity = journey._capture_private_tmux_socket_identity(socket_path)
    server_alive = True

    socket_path_value = str(socket_path.absolute())

    class Server:
        socket_path = socket_path_value

        def cmd(self, *args: str) -> Any:
            nonlocal server_alive
            server_alive = False
            listener.close()
            socket_path.unlink()
            return SimpleNamespace(returncode=0)

    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: server_identity)
    monkeypatch.setattr(
        journey,
        "_owned_process_snapshot",
        lambda *args: {child_identity},
    )
    monkeypatch.setattr(
        journey,
        "_process_identity_alive",
        lambda identity: (
            server_alive if identity == server_identity else identity == child_identity
        ),
    )
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: False)
    client = SimpleNamespace(server=Server())
    failures: set[str] = set()

    assert not journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=server_identity,
        owned_processes={child_identity},
        socket_identity=socket_identity,
    )
    assert not socket_path.exists()
    assert failures == {"tmux_server_cleanup_failed"}


def _write_and_read_safe_evidence(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    raw: dict[str, Any],
    safe: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    monkeypatch.setattr(journey.common, "ORIGINAL_HOME", home)
    assert journey.common._append_sanitized_evidence(raw) is True

    evidence_path = home / ".cache" / "cao-live-provider" / "sanitized-evidence.jsonl"
    cache_dir = evidence_path.parent
    assert not cache_dir.is_symlink()
    assert not evidence_path.is_symlink()
    assert cache_dir.stat().st_uid == os.getuid()
    assert cache_dir.stat().st_mode & 0o777 == 0o700
    assert evidence_path.stat().st_uid == os.getuid()
    assert evidence_path.stat().st_mode & 0o777 == 0o600

    lines = evidence_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    projected = {
        key: value
        for key, value in entry.items()
        if key not in {"recorded_at_utc", "time_limit_seconds"}
    }
    assert projected == {key: value for key, value in safe.items() if key in projected}
    assert isinstance(entry.get("recorded_at_utc"), str)
    assert entry.get("time_limit_seconds") == 75
    return evidence_path, entry


@pytest.mark.parametrize(
    ("failure_phase", "expected_error"),
    [
        ("bootstrap_snapshot_helper_failure", "RuntimeError"),
        ("bootstrap_recheck_helper_failure", "RuntimeError"),
        ("native_create", "RuntimeError"),
        ("native_initialize", "ConnectionError"),
        ("native_send_input", "RuntimeError"),
    ],
)
def test_run_failure_phase_survives_safe_receipt_and_evidence_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_phase: str,
    expected_error: str,
) -> None:
    from test.postgres import fixtures as postgres_fixtures

    from cli_agent_orchestrator.clients import (
        database,
    )
    from cli_agent_orchestrator.clients import interaction_repository as repository_module
    from cli_agent_orchestrator.clients import tmux as tmux_module
    from cli_agent_orchestrator.providers import opencode_cli as provider_module
    from cli_agent_orchestrator.services.interactions import service as interaction_service_module

    source_auth = tmp_path / "source-auth.json"
    source_auth.write_text("{}", encoding="utf-8")
    source_auth.chmod(0o600)

    class DatabaseContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: Any) -> None:
            return None

    class FakeService:
        def __init__(self, repository: Any) -> None:
            self.repository = SimpleNamespace()

        def initialize(self) -> None:
            return None

    class FakeServer:
        started = True
        should_exit = False

        def __init__(self, config: Any) -> None:
            self.config = config

        async def serve(self, sockets: list[socket.socket]) -> None:
            return None

    fake_tmux_clients: list[Any] = []

    class FakeTmuxClient:
        def __init__(self) -> None:
            self.commands: list[tuple[Any, ...]] = []

            def command(*args: Any) -> Any:
                self.commands.append(args)
                return SimpleNamespace(returncode=0)

            self.server = SimpleNamespace(socket_path=None, cmd=command)
            fake_tmux_clients.append(self)

    async def original_initialize(self: Any) -> bool:
        if failure_phase == "native_initialize":
            data_home = tmp_path / "private-mcp-data"
            data_home.mkdir(mode=0o700)
            (data_home / "mcp-startup.log").write_text(
                'PRIVATE_MCP_LOG_SENTINEL message="mcp connected" server=cao-mcp-server\n',
                encoding="utf-8",
            )
            self._v2_data_home = data_home
            raise ConnectionError("PRIVATE_PROVIDER_ERROR_SENTINEL")
        return True

    async def fail_at_native_phase(
        terminal_service_arg: Any,
        *,
        terminal_id: str,
        profile_name: str,
        session_name: str,
        working_directory: Path,
        model: str,
        creation_principal: Any,
        prompt: str,
        on_created: Any,
        on_phase: Any | None = None,
    ) -> tuple[Any, int]:
        if on_phase is not None:
            on_phase("native_create")
        if failure_phase == "native_create":
            raise RuntimeError("PRIVATE_PROVIDER_ERROR_SENTINEL")
        instance = object.__new__(provider_module.OpenCodeCliProvider)
        await provider_module.OpenCodeCliProvider.initialize(instance)
        terminal = SimpleNamespace(id=terminal_id, session_name=session_name, name="private-window")
        on_created(terminal)
        if on_phase is not None:
            on_phase("native_send_input")
        if failure_phase == "native_send_input":
            raise RuntimeError("PRIVATE_PROVIDER_ERROR_SENTINEL")
        return terminal, 1

    monkeypatch.setattr(journey, "_source_auth_path", lambda **kwargs: source_auth)
    monkeypatch.setattr(
        postgres_fixtures, "isolated_live_main_target", lambda path: DatabaseContext()
    )
    monkeypatch.setattr(database, "init_db", lambda: None)
    monkeypatch.setattr(database, "engine", SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(repository_module, "InteractionRepository", lambda target: object())
    monkeypatch.setattr(interaction_service_module, "InteractionService", FakeService)
    monkeypatch.setattr(
        journey.common, "_instrument_ack_identity_service", lambda service: lambda: {}
    )
    monkeypatch.setattr(journey.common, "_install_profile", lambda *args: None)
    monkeypatch.setattr(journey, "_initialize_private_git_repository", lambda workdir: None)
    # Use the real short-root allocator and let run()'s cleanup own the directory.
    root_holder: list[Path] = []
    real_root_factory = journey._private_opencode_root

    def make_root() -> Path:
        root = real_root_factory()
        root_holder.append(root)
        return root

    monkeypatch.setattr(journey, "_private_opencode_root", make_root)
    monkeypatch.setattr(journey, "_create_and_send_native_terminal", fail_at_native_phase)
    monkeypatch.setattr(provider_module.OpenCodeCliProvider, "initialize", original_initialize)
    monkeypatch.setattr(
        provider_module,
        "resolve_opencode_runtime",
        lambda: ("/usr/bin/opencode", {"PATH": "/usr/bin:/bin"}),
    )
    monkeypatch.setattr(
        journey.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="opencode v2.0.18", stderr="", returncode=0),
    )
    monkeypatch.setattr(tmux_module, "TmuxClient", FakeTmuxClient)

    def pin_fake_client(client: Any, private_tmux: Path) -> Path:
        socket_path = (private_tmux / f"tmux-{os.getuid()}" / "default").absolute()
        client.server.socket_path = str(socket_path)
        return socket_path

    monkeypatch.setattr(journey, "_pin_tmux_client_to_private_server", pin_fake_client)
    monkeypatch.setattr(
        journey,
        "_start_and_pin_private_tmux_server",
        lambda *args, **kwargs: (
            journey._ProcessIdentity(1301, os.getuid(), 100, 1301, 1301),
            journey._TmuxSocketIdentity(11, 22, os.getuid(), 0o600),
        ),
    )
    bootstrap_cleanup_calls: list[dict[str, Any]] = []

    def snapshot_after_pins(*args: Any, **kwargs: Any) -> set[Any]:
        if failure_phase in {
            "bootstrap_snapshot_helper_failure",
            "bootstrap_recheck_helper_failure",
        }:
            reason = (
                "SNAPSHOT" if failure_phase == "bootstrap_snapshot_helper_failure" else "RECHECK"
            )
            raise RuntimeError(f"PRIVATE_BOOTSTRAP_{reason}_FAILURE")
        return set()

    def cleanup_failed_bootstrap(*args: Any, **kwargs: Any) -> bool:
        bootstrap_cleanup_calls.append(kwargs)
        return original_bootstrap_cleanup(*args, **kwargs)

    original_bootstrap_cleanup = journey._cleanup_failed_tmux_bootstrap
    replacement_server = journey._ProcessIdentity(1302, os.getuid(), 200, 1302, 1302)
    pinned_socket = journey._TmuxSocketIdentity(11, 22, os.getuid(), 0o600)
    monkeypatch.setattr(
        journey, "_capture_private_tmux_socket_identity", lambda path: pinned_socket
    )
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: replacement_server)

    monkeypatch.setattr(
        journey,
        "_snapshot_and_recheck_private_tmux_server",
        snapshot_after_pins,
        raising=False,
    )
    monkeypatch.setattr(
        journey,
        "_cleanup_failed_tmux_bootstrap",
        cleanup_failed_bootstrap,
        raising=False,
    )
    monkeypatch.setattr(journey, "_cleanup_private_tmux_server", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_cleanup_native_terminal", lambda *args, **kwargs: True)

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    environment_before = dict(os.environ)
    raw = journey.run(model=journey.common.OPENCODE_INTERACTION_MODEL, run_model=True)
    assert raw["result"] == "harness_error"
    bootstrap_failure = failure_phase in {
        "bootstrap_snapshot_helper_failure",
        "bootstrap_recheck_helper_failure",
    }
    expected_phase = "version_probe" if bootstrap_failure else failure_phase
    assert raw["phase"] == expected_phase
    assert raw["error_type"] == expected_error
    expected_cleanup = "incomplete" if bootstrap_failure else "complete"
    assert raw["cleanup_status"] == expected_cleanup
    assert raw["temporary_files_removed"] is not bootstrap_failure
    assert raw["operator_auth_unchanged"] is True
    if bootstrap_failure:
        assert "tmux_process_inventory_unavailable" in raw["cleanup_failure_categories"]
        assert "provider_cleanup_failed" in raw["cleanup_failure_categories"]
        assert len(bootstrap_cleanup_calls) == 1
        assert bootstrap_cleanup_calls[0]["server_identity"] == journey._ProcessIdentity(
            1301, os.getuid(), 100, 1301, 1301
        )
        assert bootstrap_cleanup_calls[0]["socket_identity"] == journey._TmuxSocketIdentity(
            11, 22, os.getuid(), 0o600
        )
        assert len(fake_tmux_clients) == 1
        assert fake_tmux_clients[0].commands == []
    else:
        assert bootstrap_cleanup_calls == []
    if failure_phase == "native_initialize":
        assert raw["native_mcp_ready"] is True
        assert raw["native_mcp_log_status"] == "connected"
    else:
        assert "native_mcp_ready" not in raw
        assert "native_mcp_log_status" not in raw
    assert dict(os.environ) == environment_before
    assert "PRIVATE_PROVIDER_ERROR_SENTINEL" not in repr(raw)
    assert "PRIVATE_MCP_LOG_SENTINEL" not in repr(raw)

    common_safe = journey.common._safe_provider_receipt(raw)
    # These are injected failures from the inventory-helper boundary. The actual
    # socket/server recheck failure behavior is exercised by the helper tests.
    assert common_safe["phase"] == expected_phase
    assert common_safe["error_type"] == expected_error
    if bootstrap_failure:
        assert "tmux_process_inventory_unavailable" in common_safe["cleanup_failure_categories"]
    if failure_phase == "native_initialize":
        assert common_safe["native_mcp_ready"] is True
        assert common_safe["native_mcp_log_status"] == "connected"
    else:
        assert "native_mcp_ready" not in common_safe
        assert "native_mcp_log_status" not in common_safe
    assert "PRIVATE_PROVIDER_ERROR_SENTINEL" not in repr(common_safe)
    assert "PRIVATE_MCP_LOG_SENTINEL" not in repr(common_safe)

    evidence_path, evidence = _write_and_read_safe_evidence(monkeypatch, tmp_path, raw, common_safe)
    assert evidence["phase"] == expected_phase
    assert evidence["error_type"] == expected_error
    if bootstrap_failure:
        assert "tmux_process_inventory_unavailable" in evidence["cleanup_failure_categories"]
    if failure_phase == "native_initialize":
        assert evidence["native_mcp_ready"] is True
        assert evidence["native_mcp_log_status"] == "connected"
    else:
        assert "native_mcp_ready" not in evidence
        assert "native_mcp_log_status" not in evidence
    evidence_text = evidence_path.read_text(encoding="utf-8")
    assert "PRIVATE_PROVIDER_ERROR_SENTINEL" not in evidence_text
    assert "PRIVATE_MCP_LOG_SENTINEL" not in evidence_text
    assert root_holder
    if bootstrap_failure:
        assert root_holder[0].exists()
        shutil.rmtree(root_holder[0])
    else:
        assert not root_holder[0].exists()


def test_setup_failure_uses_safe_default_phase_before_private_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        journey,
        "_private_opencode_root",
        lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE_SETUP_SENTINEL")),
    )
    raw = journey.run(model=journey.common.OPENCODE_INTERACTION_MODEL, run_model=True)
    assert raw == {
        "provider": "opencode_cli",
        "result": "harness_error",
        "phase": "setup",
        "error_type": "RuntimeError",
    }
    safe = journey.common._safe_provider_receipt(raw)
    assert safe["phase"] == "setup"
    assert safe["error_type"] == "RuntimeError"
    assert "PRIVATE_SETUP_SENTINEL" not in repr(safe)


def test_safe_receipts_collapse_unapproved_error_class_and_phase() -> None:
    unsafe = {
        "provider": "opencode_cli",
        "result": "harness_error",
        "phase": "private_phase_secret",
        "error_type": "PrivateAnswerError",
        "private_value": "RAW_PROVIDER_SENTINEL",
    }
    safe = journey.common._safe_provider_receipt(unsafe)

    assert safe == {
        "provider": "opencode_cli",
        "result": "harness_error",
        "phase": "unknown",
        "error_type": "unknown",
    }
    assert "RAW_PROVIDER_SENTINEL" not in repr(safe)


def test_safe_receipt_and_evidence_keep_native_readiness_sequence_and_cleanup_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cleanup_categories = [
        "private_files_preserved",
        "tmux_process_identity_unavailable",
        "tmux_process_inventory_unavailable",
        "tmux_socket_identity_mismatch",
        "tmux_socket_identity_unavailable",
        "private_answer_secret",
    ]
    raw = {
        "provider": "opencode_cli",
        "requested_model": journey.common.OPENCODE_INTERACTION_MODEL,
        "result": "timed_out_before_question",
        "phase": "receipt_collection",
        "native_mini_initialized": True,
        "native_mcp_ready": False,
        "native_tui_stage": "idle",
        "native_mcp_log_status": "no_connected_marker",
        "turn_sequence": 23,
        "cleanup_status": "incomplete",
        "cleanup_failure_categories": cleanup_categories,
        "private_answer": "PRIVATE_ANSWER_SENTINEL",
    }

    safe = journey.common._safe_provider_receipt(raw)
    expected_cleanup = sorted(set(cleanup_categories) - {"private_answer_secret"})
    assert safe["native_mini_initialized"] is True
    assert safe["native_mcp_ready"] is False
    assert safe["native_tui_stage"] == "idle"
    assert safe["native_mcp_log_status"] == "no_connected_marker"
    assert safe["turn_sequence"] == 23
    assert safe["cleanup_status"] == "incomplete"
    assert safe["cleanup_failure_categories"] == expected_cleanup
    assert "PRIVATE_ANSWER_SENTINEL" not in repr(safe)

    evidence_path, evidence = _write_and_read_safe_evidence(monkeypatch, tmp_path, raw, safe)
    for field in (
        "native_mini_initialized",
        "native_mcp_ready",
        "native_tui_stage",
        "native_mcp_log_status",
        "turn_sequence",
        "cleanup_status",
        "cleanup_failure_categories",
    ):
        assert evidence[field] == safe[field]
    assert "PRIVATE_ANSWER_SENTINEL" not in evidence_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("turn_sequence", [True, False, 0, -1, 1.0, 10**9 + 1])
def test_safe_receipt_rejects_invalid_turn_sequence(turn_sequence: object) -> None:
    raw = {"provider": "opencode_cli", "turn_sequence": turn_sequence}
    safe = journey.common._safe_provider_receipt(raw)

    assert "turn_sequence" not in safe


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("idle", "idle"),
        ("completed", "idle"),
        ("processing", "running"),
        ("waiting_user_answer", "unknown"),
        ("error", "unknown"),
        ("unknown", "unknown"),
    ],
)
def test_native_tui_stage_uses_only_fixed_status_classes(status: str, expected: str) -> None:
    provider = SimpleNamespace(get_status=lambda output: status)
    stage = journey._native_tui_stage(provider, "PRIVATE_TUI_SENTINEL")

    assert stage == expected
    assert "PRIVATE_TUI_SENTINEL" not in repr(stage)


def test_native_tui_stage_swallows_provider_diagnostic_errors() -> None:
    def fail_status(output: str) -> str:
        raise RuntimeError("PRIVATE_TUI_ERROR_SENTINEL")

    provider = SimpleNamespace(get_status=fail_status)
    stage = journey._native_tui_stage(provider, "PRIVATE_TUI_SENTINEL")

    assert stage == "unknown"
    assert "PRIVATE_TUI_ERROR_SENTINEL" not in repr(stage)
    assert "PRIVATE_TUI_SENTINEL" not in repr(stage)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("idle", "idle_no_turn"),
        ("completed", "completed_turn"),
        ("processing", "running"),
        ("waiting_user_answer", "waiting_user_answer"),
        ("error", "error"),
        ("unknown", "unknown"),
    ],
)
def test_native_turn_stage_distinguishes_idle_from_completed_turn(
    status: str, expected: str
) -> None:
    provider = SimpleNamespace(get_status=lambda output: status)

    assert journey._native_turn_stage(provider, "PRIVATE_TUI_SENTINEL") == expected


def test_native_turn_stage_keeps_visible_v2_prompt_pending() -> None:
    provider = SimpleNamespace(_cli_major=2, get_status=lambda output: "idle")
    output = "› bounded interaction prompt\nctrl+p menu"

    assert journey._native_turn_stage(provider, output) == "prompt_pending_submission"


def test_no_tool_probe_waits_past_ambiguous_idle_frame() -> None:
    source = _function_source(Path(journey.__file__), "run")

    assert 'native_turn_stage in {"completed_turn", "error"}' in source
    assert 'native_turn_stage in {"idle_no_turn", "completed_turn", "error"}' not in source


def test_no_tool_result_classifier_is_bounded_and_content_free() -> None:
    classify = journey._classify_no_tool_result

    assert classify(native_turn_stage="idle_no_turn", question_count=0, timed_out=False) == (
        "prompt_not_submitted"
    )
    assert classify(native_turn_stage="completed_turn", question_count=0, timed_out=False) == (
        "model_completed_without_tool"
    )
    assert classify(native_turn_stage="error", question_count=0, timed_out=False) == (
        "provider_error"
    )
    assert classify(native_turn_stage="unknown", question_count=0, timed_out=True) == (
        "timed_out_before_question"
    )
    assert classify(native_turn_stage="completed_turn", question_count=1, timed_out=True) == (
        "question_created_but_not_acknowledged"
    )


def test_safe_receipt_preserves_no_tool_diagnostic_without_provider_text() -> None:
    raw = {
        "provider": "opencode_cli",
        "result": "model_completed_without_tool",
        "native_turn_stage": "completed_turn",
        "native_turn_observed": True,
        "private_provider_output": "PRIVATE_PROVIDER_SENTINEL",
    }

    safe = journey.common._safe_provider_receipt(raw)

    assert safe["result"] == "model_completed_without_tool"
    assert safe["native_turn_stage"] == "completed_turn"
    assert safe["native_turn_observed"] is True
    assert "PRIVATE_PROVIDER_SENTINEL" not in repr(safe)


def test_safe_receipt_preserves_provider_error_without_provider_text() -> None:
    raw = {
        "provider": "opencode_cli",
        "result": "provider_error",
        "native_turn_stage": "error",
        "native_turn_observed": False,
        "failure_diagnostics": {"provider_auth_rejected": True},
        "private_provider_output": "This API key has expired. PRIVATE_PROVIDER_SENTINEL",
    }

    safe = journey.common._safe_provider_receipt(raw)

    assert safe["result"] == "provider_error"
    assert safe["native_turn_stage"] == "error"
    assert safe["failure_diagnostics"] == {"provider_auth_rejected": True}
    assert "PRIVATE_PROVIDER_SENTINEL" not in repr(safe)


def test_native_mcp_log_diagnostics_distinguishes_unreadable_path_without_text(
    tmp_path: Path,
) -> None:
    unreadable = tmp_path / "directory.log"
    unreadable.mkdir()
    diagnostic = journey._native_mcp_log_diagnostics(unreadable)

    assert diagnostic == (False, "unreadable")
    assert "PRIVATE_MCP_LOG_SENTINEL" not in repr(diagnostic)


def test_native_mcp_log_diagnostics_returns_only_fixed_classes(tmp_path: Path) -> None:
    assert journey._native_mcp_log_diagnostics(None) == (False, "not_configured")
    assert journey._native_mcp_log_diagnostics(tmp_path / "missing.log") == (
        False,
        "missing",
    )

    connected_log = tmp_path / "connected.log"
    connected_log.write_text(
        'PRIVATE_MCP_LOG_SENTINEL message="mcp connected" server=cao-mcp-server\n',
        encoding="utf-8",
    )
    assert journey._native_mcp_log_diagnostics(connected_log) == (True, "connected")

    unconnected_log = tmp_path / "unconnected.log"
    unconnected_log.write_text("PRIVATE_MCP_LOG_SENTINEL startup pending\n", encoding="utf-8")
    diagnostic = journey._native_mcp_log_diagnostics(unconnected_log)
    assert diagnostic == (False, "no_connected_marker")
    assert "PRIVATE_MCP_LOG_SENTINEL" not in repr(diagnostic)


def test_safe_receipts_collapse_unapproved_native_diagnostic_values() -> None:
    unsafe = {
        "provider": "opencode_cli",
        "native_tui_stage": "PRIVATE_TUI_ANSWER_SENTINEL",
        "native_mcp_log_status": "PRIVATE_MCP_LOG_SENTINEL",
    }
    safe = journey.common._safe_provider_receipt(unsafe)

    assert safe["native_tui_stage"] == "unknown"
    assert safe["native_mcp_log_status"] == "unknown"
    assert "PRIVATE_TUI_ANSWER_SENTINEL" not in repr(safe)
    assert "PRIVATE_MCP_LOG_SENTINEL" not in repr(safe)


def test_tmux_identity_scan_skips_unrelated_unreadable_fd_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc_root = tmp_path / "proc"
    (proc_root / "net").mkdir(mode=0o700, parents=True)
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    socket_path.parent.mkdir(mode=0o700, parents=True)
    socket_inode = 4242
    (proc_root / "net" / "unix").write_text(
        "Num RefCount Protocol Flags Type St Inode Path\n"
        f"00000000: 00000002 00000000 00010000 0001 01 {socket_inode} {socket_path}\n",
        encoding="utf-8",
    )

    def add_process(
        pid: int,
        *,
        comm: str,
        process_group: int,
        session_id: int,
        start_time: int,
        socket_owner: bool = False,
    ) -> Path:
        process = proc_root / str(pid)
        fd_dir = process / "fd"
        fd_dir.mkdir(mode=0o700, parents=True)
        fields = ["0"] * 20
        fields[0] = "S"
        fields[1] = "1"
        fields[2] = str(process_group)
        fields[3] = str(session_id)
        fields[19] = str(start_time)
        (process / "stat").write_text(f"{pid} ({comm}) {' '.join(fields)}", encoding="ascii")
        (process / "comm").write_text(comm, encoding="utf-8")
        (process / "environ").write_bytes(b"HOME=/private\0")
        if socket_owner:
            (fd_dir / "3").symlink_to(f"socket:[{socket_inode}]")
        return fd_dir

    tmux_fd_dir = add_process(
        101,
        comm="tmux: server",
        process_group=101,
        session_id=101,
        start_time=1001,
        socket_owner=True,
    )
    unrelated_fd_dir = add_process(
        102,
        comm="unrelated",
        process_group=102,
        session_id=102,
        start_time=1002,
    )
    original_iterdir = Path.iterdir
    scanned_fd_dirs: set[Path] = set()

    def deny_unrelated_fd_access(path: Path) -> Any:
        if path in {tmux_fd_dir, unrelated_fd_dir}:
            scanned_fd_dirs.add(path)
        if path == unrelated_fd_dir:
            raise PermissionError("fake proc fd directory is unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_unrelated_fd_access)

    assert journey._find_tmux_server_identity(
        socket_path,
        proc_root=proc_root,
        unix_socket_table=proc_root / "net" / "unix",
    ) == journey._ProcessIdentity(101, os.getuid(), 1001, 101, 101)
    assert scanned_fd_dirs == {tmux_fd_dir}


def test_tmux_identity_scan_fails_closed_for_unreadable_tmux_fd_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc_root = tmp_path / "proc"
    (proc_root / "net").mkdir(mode=0o700, parents=True)
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    socket_path.parent.mkdir(mode=0o700, parents=True)
    socket_inode = 4242
    (proc_root / "net" / "unix").write_text(
        "Num RefCount Protocol Flags Type St Inode Path\n"
        f"00000000: 00000002 00000000 00010000 0001 01 {socket_inode} {socket_path}\n",
        encoding="utf-8",
    )
    process = proc_root / "101"
    fd_dir = process / "fd"
    fd_dir.mkdir(mode=0o700, parents=True)
    fields = ["0"] * 20
    fields[0] = "S"
    fields[1] = "1"
    fields[2] = "101"
    fields[3] = "101"
    fields[19] = "1001"
    (process / "stat").write_text(f"101 (tmux: server) {' '.join(fields)}", encoding="ascii")
    (process / "comm").write_text("tmux: server", encoding="utf-8")
    (process / "environ").write_bytes(b"HOME=/private\0")
    (fd_dir / "3").symlink_to(f"socket:[{socket_inode}]")
    original_iterdir = Path.iterdir

    def deny_tmux_fd_access(path: Path) -> Any:
        if path == fd_dir:
            raise PermissionError("fake tmux proc fd directory is unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_tmux_fd_access)

    with pytest.raises(RuntimeError, match="private_process_table_unreadable"):
        journey._find_tmux_server_identity(
            socket_path,
            proc_root=proc_root,
            unix_socket_table=proc_root / "net" / "unix",
        )


def _bind_private_socket(
    tmp_path: Path,
) -> tuple[Path, socket.socket, journey._TmuxSocketIdentity]:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    identity = journey._capture_private_tmux_socket_identity(socket_path)
    return socket_path, listener, identity


def test_run_pins_marker_free_server_before_production_terminal_creation() -> None:
    source = _function_source(Path(journey.__file__), "run")
    assert "_start_and_pin_private_tmux_server(" in source
    pin_index = source.index("_pin_tmux_client_to_private_server(")
    start_index = source.index("_start_and_pin_private_tmux_server(", pin_index)
    snapshot_index = source.index("_snapshot_and_recheck_private_tmux_server(", start_index)
    create_index = source.index("_create_and_send_native_terminal(", start_index)
    assert pin_index < start_index < snapshot_index < create_index
    assert (
        source.index(
            "tmux_server_identity, tmux_socket_identity = _start_and_pin_private_tmux_server("
        )
        < snapshot_index
    )
    assert "_find_tmux_server_identity" not in source


def test_start_and_pin_returns_before_fallible_process_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    socket_path_value = str(socket_path)
    server_identity = journey._ProcessIdentity(1251, os.getuid(), 100, 1251, 1251)
    socket_identity = journey._TmuxSocketIdentity(11, 22, os.getuid(), 0o600)
    events: list[str] = []

    class Server:
        socket_path = socket_path_value

    client = SimpleNamespace(
        server=Server(),
        _set_server_exit_empty_off=lambda: events.append("start") or True,
    )
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("CAO_TERMINAL_ID", raising=False)
    monkeypatch.setattr(
        journey,
        "_capture_private_tmux_socket_identity",
        lambda path: events.append("socket") or socket_identity,
    )
    monkeypatch.setattr(
        journey,
        "_find_tmux_server_identity",
        lambda path: events.append("owner") or server_identity,
    )
    monkeypatch.setattr(
        journey,
        "_process_identity_alive",
        lambda identity: events.append("alive") or True,
    )

    def fail_if_inventory_starts(*args: Any, **kwargs: Any) -> set[Any]:
        events.append("snapshot")
        raise RuntimeError("PRIVATE_SNAPSHOT_FAILURE")

    monkeypatch.setattr(journey, "_owned_process_snapshot", fail_if_inventory_starts)
    pinned_server, pinned_socket = journey._start_and_pin_private_tmux_server(client, socket_path)

    assert pinned_server == server_identity
    assert pinned_socket == socket_identity
    assert events == ["start", "socket", "owner", "alive"]


@pytest.mark.parametrize("replacement", ["socket", "server"])
def test_snapshot_recheck_rejects_replacement_after_pins_are_available(
    replacement: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    server_identity = journey._ProcessIdentity(1261, os.getuid(), 100, 1261, 1261)
    socket_identity = journey._TmuxSocketIdentity(11, 22, os.getuid(), 0o600)
    replacement_socket = journey._TmuxSocketIdentity(11, 23, os.getuid(), 0o600)
    replacement_server = journey._ProcessIdentity(1262, os.getuid(), 200, 1262, 1262)
    events: list[str] = []
    monkeypatch.setattr(
        journey,
        "_owned_process_snapshot",
        lambda *args, **kwargs: events.append("snapshot") or set(),
    )
    monkeypatch.setattr(
        journey,
        "_capture_private_tmux_socket_identity",
        lambda path: events.append("socket")
        or (replacement_socket if replacement == "socket" else socket_identity),
    )
    monkeypatch.setattr(
        journey,
        "_find_tmux_server_identity",
        lambda path: events.append("server")
        or (replacement_server if replacement == "server" else server_identity),
    )
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: True)
    expected_error = (
        "private_tmux_socket_identity_changed"
        if replacement == "socket"
        else "private_tmux_server_identity_changed"
    )

    with pytest.raises(RuntimeError, match=expected_error):
        journey._snapshot_and_recheck_private_tmux_server(
            server_identity,
            socket_identity,
            socket_path,
            terminal_id="oc-private",
        )
    expected_events = ["snapshot", "socket"]
    if replacement == "server":
        expected_events.append("server")
    assert events == expected_events


def test_failed_bootstrap_cleanup_stops_only_exact_server_and_marks_inventory_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path, listener, socket_identity = _bind_private_socket(tmp_path)
    private_tmux = socket_path.parents[1]
    pinned = journey._ProcessIdentity(1271, os.getuid(), 100, 1271, 1271)
    commands: list[tuple[Any, ...]] = []
    alive = True

    def command(*args: Any) -> Any:
        nonlocal alive
        commands.append(args)
        alive = False
        return SimpleNamespace(returncode=0)

    client = SimpleNamespace(
        server=SimpleNamespace(socket_path=str(socket_path.absolute()), cmd=command)
    )
    monkeypatch.setattr(
        journey,
        "_capture_private_tmux_socket_identity",
        lambda path: socket_identity,
    )
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: pinned)
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: alive)
    monkeypatch.setattr(journey, "_tmux_socket_path_registered", lambda path: False)
    monkeypatch.setattr(
        journey,
        "_owned_process_snapshot",
        lambda *args, **kwargs: pytest.fail("bootstrap cleanup must not rescan processes"),
    )
    monkeypatch.setattr(
        journey,
        "_processes_in_groups",
        lambda *args, **kwargs: pytest.fail("bootstrap cleanup must not scan process groups"),
    )
    monkeypatch.setattr(
        journey,
        "_terminal_tagged_processes",
        lambda *args, **kwargs: pytest.fail("bootstrap cleanup must not scan markers"),
    )
    failures: set[str] = set()

    try:
        stopped = journey._cleanup_failed_tmux_bootstrap(
            client,
            private_tmux,
            failures,
            server_identity=pinned,
            socket_identity=socket_identity,
        )
        assert stopped
        assert commands == [("kill-server",)]
        assert "tmux_process_inventory_unavailable" in failures
        assert not socket_path.exists()
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


@pytest.mark.parametrize("replacement", ["server", "socket", "no_pins"])
def test_failed_bootstrap_cleanup_never_kills_replacement_or_unpinned_server(
    replacement: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    socket_path, listener, socket_identity = _bind_private_socket(tmp_path)
    private_tmux = socket_path.parents[1]
    pinned = journey._ProcessIdentity(1281, os.getuid(), 100, 1281, 1281)
    replacement_server = journey._ProcessIdentity(1282, os.getuid(), 200, 1282, 1282)
    replacement_socket = journey._TmuxSocketIdentity(11, 23, os.getuid(), 0o600)
    commands: list[tuple[Any, ...]] = []
    client = SimpleNamespace(
        server=SimpleNamespace(
            socket_path=str(socket_path.absolute()),
            cmd=lambda *args: commands.append(args) or SimpleNamespace(returncode=0),
        )
    )
    failures: set[str] = set()

    if replacement == "no_pins":
        monkeypatch.setattr(
            journey,
            "_capture_private_tmux_socket_identity",
            lambda path: pytest.fail("must not infer socket identity"),
        )
        server_identity = None
        expected_socket = None
    else:
        server_identity = pinned
        expected_socket = socket_identity
        monkeypatch.setattr(
            journey,
            "_capture_private_tmux_socket_identity",
            lambda path: replacement_socket if replacement == "socket" else socket_identity,
        )
        monkeypatch.setattr(
            journey,
            "_find_tmux_server_identity",
            lambda path: replacement_server if replacement == "server" else pinned,
        )

    try:
        stopped = journey._cleanup_failed_tmux_bootstrap(
            client,
            private_tmux,
            failures,
            server_identity=server_identity,
            socket_identity=expected_socket,
        )
        assert not stopped
        assert commands == []
        assert socket_path.exists()
        assert "tmux_process_inventory_unavailable" in failures
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


def test_cleanup_does_not_adopt_late_discovered_server_without_a_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path, listener, socket_identity = _bind_private_socket(tmp_path)
    private_tmux = socket_path.parents[1]
    replacement = journey._ProcessIdentity(501, os.getuid(), 1001, 501, 501)
    commands: list[tuple[Any, ...]] = []
    alive = True

    def command(*args: Any) -> Any:
        nonlocal alive
        commands.append(args)
        alive = False
        socket_path.unlink(missing_ok=True)
        return SimpleNamespace(returncode=0)

    client = SimpleNamespace(
        server=SimpleNamespace(socket_path=str(socket_path.absolute()), cmd=command)
    )
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: replacement)
    monkeypatch.setattr(journey, "_owned_process_snapshot", lambda *args: set())
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: alive)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(journey, "_terminal_tagged_processes", lambda *args, **kwargs: set())
    failures: set[str] = set()

    try:
        assert not journey._cleanup_private_tmux_server(
            client,
            private_tmux,
            failures,
            terminal_id="oc-private",
            server_identity=None,
            owned_processes=set(),
            socket_identity=socket_identity,
        )
        assert commands == []
        assert failures
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


def test_cleanup_without_captured_server_does_not_infer_a_cutoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    client = SimpleNamespace(server=SimpleNamespace(socket_path=str(socket_path.absolute())))
    tagged_scan_calls: list[Any] = []
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: None)
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: False)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(
        journey,
        "_terminal_tagged_processes",
        lambda *args, **kwargs: tagged_scan_calls.append(kwargs) or set(),
    )
    failures: set[str] = set()

    assert not journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=None,
        owned_processes=set(),
        socket_identity=None,
    )
    assert tagged_scan_calls == [{"server_identity": None}]
    assert failures


def test_cleanup_rejects_socket_replacement_before_killing_pinned_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path, original_listener, original_socket_identity = _bind_private_socket(tmp_path)
    original_identity = journey._ProcessIdentity(601, os.getuid(), 1001, 601, 601)
    socket_path.unlink()
    replacement_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement_listener.bind(str(socket_path))
    replacement_identity = journey._capture_private_tmux_socket_identity(socket_path)
    assert replacement_identity != original_socket_identity
    commands: list[tuple[Any, ...]] = []
    alive = True

    def command(*args: Any) -> Any:
        nonlocal alive
        commands.append(args)
        alive = False
        replacement_listener.close()
        socket_path.unlink(missing_ok=True)
        return SimpleNamespace(returncode=0)

    client = SimpleNamespace(
        server=SimpleNamespace(socket_path=str(socket_path.absolute()), cmd=command)
    )
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: original_identity)
    monkeypatch.setattr(journey, "_owned_process_snapshot", lambda *args: set())
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: alive)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(journey, "_terminal_tagged_processes", lambda *args, **kwargs: set())
    failures: set[str] = set()

    try:
        assert not journey._cleanup_private_tmux_server(
            client,
            socket_path.parents[1],
            failures,
            terminal_id="oc-private",
            server_identity=original_identity,
            owned_processes=set(),
            socket_identity=original_socket_identity,
        )
        assert commands == []
        assert failures
    finally:
        original_listener.close()
        replacement_listener.close()
        socket_path.unlink(missing_ok=True)


@pytest.mark.parametrize("scanner", ["snapshot", "terminal_tags"])
def test_process_scans_fail_closed_if_pid_identity_changes_before_birth_skip(
    scanner: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(mode=0o700)
    socket_path = tmp_path / "private-tmux" / "default"
    server_identity = journey._ProcessIdentity(701, os.getuid(), 100, 701, 701)
    if scanner == "snapshot":
        _add_fake_process(proc_root, 701, parent_pid=1, start_time=100, comm="tmux: server")
    stale_environment = _add_fake_process(
        proc_root, 702, parent_pid=1, start_time=99, comm="unrelated"
    )
    _deny_fake_environments(monkeypatch, {stale_environment})
    real_process_info = journey._process_info
    calls = 0
    stale_identity = journey._ProcessIdentity(702, os.getuid(), 99, 702, 702)
    reused_identity = journey._ProcessIdentity(702, os.getuid(), 101, 702, 702)

    def pid_reuse_between_samples(pid: int, *, proc_root: Path) -> Any:
        nonlocal calls
        if pid != 702:
            return real_process_info(pid, proc_root=proc_root)
        calls += 1
        if calls == 1:
            return stale_identity, 1, "S"
        return reused_identity, 1, "S"

    monkeypatch.setattr(journey, "_process_info", pid_reuse_between_samples)

    with pytest.raises(RuntimeError, match="private_process_identity_changed"):
        if scanner == "snapshot":
            journey._owned_process_snapshot(
                server_identity, "oc-private", socket_path, proc_root=proc_root
            )
        else:
            journey._terminal_tagged_processes(
                "oc-private",
                socket_path,
                proc_root=proc_root,
                server_identity=server_identity,
            )
    assert calls == 2


def test_cleanup_keeps_old_server_group_member_visible_after_server_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    server_identity = journey._ProcessIdentity(801, os.getuid(), 100, 801, 801)
    old_same_group = journey._ProcessIdentity(802, os.getuid(), 99, 801, 801)
    client = SimpleNamespace(server=SimpleNamespace(socket_path=str(socket_path.absolute())))
    tagged_scans: list[Any] = []
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: None)
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: False)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: {old_same_group})
    monkeypatch.setattr(
        journey,
        "_terminal_tagged_processes",
        lambda *args, **kwargs: tagged_scans.append(kwargs) or set(),
    )
    failures: set[str] = set()

    assert not journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=server_identity,
        owned_processes=set(),
        socket_identity=journey._TmuxSocketIdentity(1, 2, os.getuid(), 0o600),
    )
    assert failures == {"tmux_process_identity_unavailable"}
    assert tagged_scans == []


def test_cleanup_uses_pinned_birth_cutoff_when_server_is_already_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_tmux = tmp_path / "tmux"
    socket_path = private_tmux / f"tmux-{os.getuid()}" / "default"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    server_identity = journey._ProcessIdentity(901, os.getuid(), 100, 901, 901)
    client = SimpleNamespace(server=SimpleNamespace(socket_path=str(socket_path.absolute())))
    tagged_scans: list[Any] = []
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: None)
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: False)
    monkeypatch.setattr(journey, "_wait_process_identities_gone", lambda *args, **kwargs: True)
    monkeypatch.setattr(journey, "_processes_in_groups", lambda groups: set())
    monkeypatch.setattr(
        journey,
        "_terminal_tagged_processes",
        lambda *args, **kwargs: tagged_scans.append(kwargs) or set(),
    )
    failures: set[str] = set()

    assert journey._cleanup_private_tmux_server(
        client,
        private_tmux,
        failures,
        terminal_id="oc-private",
        server_identity=server_identity,
        owned_processes=set(),
        socket_identity=journey._TmuxSocketIdentity(1, 2, os.getuid(), 0o600),
    )
    assert tagged_scans == [{"server_identity": server_identity}]
    assert failures == set()


def test_start_and_pin_private_server_captures_stable_owner_before_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    server_identity = journey._ProcessIdentity(1001, os.getuid(), 100, 1001, 1001)
    socket_identity = journey._TmuxSocketIdentity(11, 22, os.getuid(), 0o600)
    child_identity = journey._ProcessIdentity(1002, os.getuid(), 101, 1001, 1001)
    events: list[str] = []
    socket_path_value = str(socket_path)

    class Server:
        socket_path = socket_path_value

    client = SimpleNamespace(
        server=Server(),
        _set_server_exit_empty_off=lambda: events.append("start") or True,
    )
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("CAO_TERMINAL_ID", raising=False)
    monkeypatch.setattr(
        journey,
        "_capture_private_tmux_socket_identity",
        lambda path: events.append("socket") or socket_identity,
    )
    monkeypatch.setattr(
        journey,
        "_find_tmux_server_identity",
        lambda path: events.append("owner") or server_identity,
    )
    monkeypatch.setattr(
        journey,
        "_process_identity_alive",
        lambda identity: events.append("alive") or True,
    )
    monkeypatch.setattr(
        journey,
        "_owned_process_snapshot",
        lambda *args: events.append("snapshot") or {child_identity},
    )

    pinned_server, pinned_socket = journey._start_and_pin_private_tmux_server(client, socket_path)

    assert events == ["start", "socket", "owner", "alive"]
    assert pinned_server == server_identity
    assert pinned_socket == socket_identity

    children = journey._snapshot_and_recheck_private_tmux_server(
        pinned_server,
        pinned_socket,
        socket_path,
        terminal_id="oc-private",
    )
    assert events == [
        "start",
        "socket",
        "owner",
        "alive",
        "snapshot",
        "socket",
        "owner",
        "alive",
    ]
    assert children == {child_identity}


def test_start_and_pin_refuses_to_capture_owner_if_marker_environment_leaked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    events: list[str] = []
    client = SimpleNamespace(
        server=SimpleNamespace(socket_path=str(socket_path)),
        _set_server_exit_empty_off=lambda: events.append("start") or True,
    )
    monkeypatch.setenv("CAO_TERMINAL_ID", "unexpected-marker")

    with pytest.raises(RuntimeError, match="private_tmux_bootstrap_environment_invalid"):
        journey._start_and_pin_private_tmux_server(client, socket_path)
    assert events == []


def test_start_and_pin_rejects_server_replacement_during_initial_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path = (tmp_path / "tmux" / f"tmux-{os.getuid()}" / "default").absolute()
    original = journey._ProcessIdentity(1051, os.getuid(), 100, 1051, 1051)
    replacement = journey._ProcessIdentity(1052, os.getuid(), 200, 1052, 1052)
    socket_identity = journey._TmuxSocketIdentity(11, 22, os.getuid(), 0o600)
    owners = iter((original, replacement))
    client = SimpleNamespace(
        server=SimpleNamespace(socket_path=str(socket_path)),
        _set_server_exit_empty_off=lambda: True,
    )
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("CAO_TERMINAL_ID", raising=False)
    monkeypatch.setattr(
        journey, "_capture_private_tmux_socket_identity", lambda path: socket_identity
    )
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: next(owners))
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: True)
    monkeypatch.setattr(journey, "_owned_process_snapshot", lambda *args: set())

    pinned_server, pinned_socket = journey._start_and_pin_private_tmux_server(client, socket_path)
    assert pinned_server == original
    with pytest.raises(RuntimeError, match="private_tmux_server_identity_changed"):
        journey._snapshot_and_recheck_private_tmux_server(
            pinned_server,
            pinned_socket,
            socket_path,
            terminal_id="oc-private",
        )


def test_cleanup_rejects_replacement_server_with_same_socket_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path, listener, socket_identity = _bind_private_socket(tmp_path)
    private_tmux = socket_path.parents[1]
    pinned = journey._ProcessIdentity(1101, os.getuid(), 100, 1101, 1101)
    replacement = journey._ProcessIdentity(1102, os.getuid(), 200, 1102, 1102)
    commands: list[tuple[Any, ...]] = []
    client = SimpleNamespace(
        server=SimpleNamespace(
            socket_path=str(socket_path.absolute()),
            cmd=lambda *args: commands.append(args) or SimpleNamespace(returncode=0),
        )
    )
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: replacement)
    monkeypatch.setattr(journey, "_owned_process_snapshot", lambda *args: set())
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: True)
    failures: set[str] = set()

    try:
        assert not journey._cleanup_private_tmux_server(
            client,
            private_tmux,
            failures,
            terminal_id="oc-private",
            server_identity=pinned,
            owned_processes=set(),
            socket_identity=socket_identity,
        )
        assert commands == []
        assert failures == {"tmux_process_identity_unavailable"}
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


def test_cleanup_rechecks_pinned_server_immediately_before_kill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    socket_path, listener, socket_identity = _bind_private_socket(tmp_path)
    private_tmux = socket_path.parents[1]
    pinned = journey._ProcessIdentity(1201, os.getuid(), 100, 1201, 1201)
    replacement = journey._ProcessIdentity(1202, os.getuid(), 200, 1202, 1202)
    identities = iter((pinned, replacement))
    commands: list[tuple[Any, ...]] = []
    client = SimpleNamespace(
        server=SimpleNamespace(
            socket_path=str(socket_path.absolute()),
            cmd=lambda *args: commands.append(args) or SimpleNamespace(returncode=0),
        )
    )
    monkeypatch.setattr(journey, "_find_tmux_server_identity", lambda path: next(identities))
    monkeypatch.setattr(
        journey,
        "_capture_private_tmux_socket_identity",
        lambda path: socket_identity,
    )
    monkeypatch.setattr(journey, "_owned_process_snapshot", lambda *args: set())
    monkeypatch.setattr(journey, "_process_identity_alive", lambda identity: True)
    failures: set[str] = set()

    try:
        assert not journey._cleanup_private_tmux_server(
            client,
            private_tmux,
            failures,
            terminal_id="oc-private",
            server_identity=pinned,
            owned_processes=set(),
            socket_identity=socket_identity,
        )
        assert commands == []
        assert failures == {"tmux_server_cleanup_failed"}
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


@pytest.mark.parametrize("scanner", ["snapshot", "terminal_tags"])
@pytest.mark.parametrize("second_sample", ["zombie", "gone", "reused", "live"])
def test_process_scans_revalidate_unreadable_zombies(
    scanner: str,
    second_sample: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(mode=0o700)
    socket_path = tmp_path / "private-tmux" / "default"
    server = journey._ProcessIdentity(801, os.getuid(), 100, 801, 801)
    if scanner == "snapshot":
        _add_fake_process(proc_root, 801, parent_pid=1, start_time=100)
    environ = _add_fake_process(proc_root, 802, parent_pid=1, start_time=101)
    original = journey._process_info
    zombie = journey._ProcessIdentity(802, os.getuid(), 101, 802, 802)
    calls = 0
    reads: list[Path] = []
    original_read = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == environ:
            reads.append(path)
            raise PermissionError("synthetic zombie environ")
        return original_read(path)

    def process_info(pid: int, *, proc_root: Path) -> Any:
        nonlocal calls
        if pid != 802:
            return original(pid, proc_root=proc_root)
        calls += 1
        if calls == 1 or second_sample == "zombie":
            return zombie, 1, "Z"
        if second_sample == "gone":
            return None
        if second_sample == "reused":
            return journey._ProcessIdentity(802, os.getuid(), 102, 802, 802), 1, "S"
        return zombie, 1, "S"

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(journey, "_process_info", process_info)

    def scan() -> set[Any]:
        if scanner == "snapshot":
            return journey._owned_process_snapshot(
                server, "oc-private", socket_path, proc_root=proc_root
            )
        return journey._terminal_tagged_processes(
            "oc-private", socket_path, proc_root=proc_root, server_identity=server
        )

    if second_sample in {"reused", "live"}:
        with pytest.raises(RuntimeError, match="private_process_identity_changed"):
            scan()
    else:
        assert scan() == set()
    assert calls == 2
    assert reads == []
