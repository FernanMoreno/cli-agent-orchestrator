"""Private Root CLI composition over the authenticated control endpoint."""

from __future__ import annotations

import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.root_control_protocol import ROOT_CONTROL_FD_ENV
from cli_agent_orchestrator.runtime_channel import root_control
from cli_agent_orchestrator.services.root_control_transport import (
    RootControlTransportUnavailable,
)
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject


def _subject() -> RootSqlSubject:
    binding = ResourceBinding(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=os.getpid(),
        process_started_at=1.0,
        store_id=str(uuid4()),
        target_identity_sha256="a" * 64,
        mount_ns_device=0,
        mount_ns_inode=1,
        cgroup_ns_device=0,
        cgroup_ns_inode=1,
        resource_view_sha256="b" * 64,
    )
    return RootSqlSubject(binding, f"caos-sql-v1-{uuid4().hex}", str(uuid4()), 1, (1, 1))


class _Endpoint:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.activations: list[bytes | None] = [None, b"signed-activation"]
        self.commands: list[str | None] = [None, "refresh", "stop"]

    def receive_activation(self, *, timeout_s: float = 1.0) -> bytes | None:
        self.events.append(("receive_activation", timeout_s))
        return self.activations.pop(0)

    def send_ready(self) -> None:
        self.events.append("ready")

    def receive_command(self, *, timeout_s: float = 1.0) -> str | None:
        self.events.append(("receive_command", timeout_s))
        return self.commands.pop(0)

    def send_refresh(self, reason: str) -> None:
        self.events.append(("refresh", reason))

    def send_stopping(self) -> None:
        self.events.append("stopping")

    def send_error(self, code: str = "root_control_unavailable") -> None:
        self.events.append(("error", code))

    def close(self) -> None:
        self.events.append("endpoint_closed")


class RootControlPrivateCliTests(unittest.TestCase):
    def _argv(self) -> list[str]:
        return [
            "--trust-file",
            "/private/trust.json",
            "--policy-file",
            "/private/policy.json",
            "--scope",
            "root-control-test",
        ]

    def test_bad_private_descriptor_never_falls_back_to_stdio_protocol(self) -> None:
        stdout = io.StringIO()
        bootstrap_calls: list[object] = []

        def pop_bad_descriptor():
            os.environ.pop(ROOT_CONTROL_FD_ENV, None)
            raise RootControlTransportUnavailable()

        with (
            patch.dict(os.environ, {ROOT_CONTROL_FD_ENV: "invalid"}),
            patch.object(
                root_control,
                "open_root_control_endpoint",
                side_effect=pop_bad_descriptor,
            ),
            patch.object(
                root_control,
                "scoped_process_bootstrap",
                side_effect=lambda *args, **kwargs: bootstrap_calls.append(args),
            ),
            patch.object(root_control.sys, "stdout", stdout),
        ):
            status = root_control.main(self._argv())

        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(bootstrap_calls, [])

    def test_private_bootstrap_binds_before_activation_and_never_writes_stdio(self) -> None:
        events: list[object] = []
        endpoint = _Endpoint(events)
        subject = _subject()
        actor = SimpleNamespace(
            start=lambda: events.append("actor_started"),
            wait_ready=lambda: None,
            refresh=lambda: SimpleNamespace(reason="capacity_available"),
            aclose=lambda **kwargs: None,
        )

        async def wait_ready() -> None:
            events.append("actor_ready")

        async def refresh():
            events.append("actor_refresh")
            return SimpleNamespace(reason="capacity_available")

        async def aclose(**kwargs) -> None:
            events.append(("actor_closed", kwargs.get("timeout")))

        actor.wait_ready = wait_ready
        actor.refresh = refresh
        actor.aclose = aclose
        stdout = io.StringIO()

        def open_endpoint():
            self.assertIn(ROOT_CONTROL_FD_ENV, os.environ)
            events.append("endpoint_open")
            return endpoint

        async def prepare(**kwargs):
            events.append(("prepare", kwargs["packet"]))
            return actor

        with (
            patch.dict(os.environ, {ROOT_CONTROL_FD_ENV: "41"}),
            patch.object(root_control, "open_root_control_endpoint", side_effect=open_endpoint),
            patch.object(root_control, "_read_trusted_keys", return_value={"deploy": object()}),
            patch.object(
                root_control,
                "scoped_process_bootstrap",
                side_effect=lambda scope: events.append(("bootstrap", scope)) or object(),
            ),
            patch.object(root_control, "current_root_sql_subject", return_value=subject),
            patch.object(
                root_control,
                "publish_resource_binding",
                side_effect=lambda binding, **kwargs: events.append(
                    ("publish", binding, kwargs["first_sql_grant"])
                )
                or True,
            ),
            patch.object(root_control, "prepare_original_control_owner", side_effect=prepare),
            patch.object(root_control.sys, "stdout", stdout),
        ):
            status = root_control.main(self._argv())

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(events[0], "endpoint_open")
        publication_index = next(
            i
            for i, event in enumerate(events)
            if isinstance(event, tuple) and event[0] == "publish"
        )
        publication = events[publication_index]
        self.assertIsInstance(publication, tuple)
        self.assertIs(publication[1], subject.binding)
        self.assertIs(publication[2], False)
        self.assertLess(
            publication_index,
            next(
                i
                for i, event in enumerate(events)
                if isinstance(event, tuple) and event[0] == "prepare"
            ),
        )
        self.assertEqual(events[-2:], [("actor_closed", 5.0), "endpoint_closed"])
        self.assertIn("ready", events)
        self.assertIn(("refresh", "capacity_available"), events)
        self.assertIn("stopping", events)

    def test_private_binding_rejection_closes_without_reading_activation(self) -> None:
        events: list[object] = []
        endpoint = _Endpoint(events)
        stdout = io.StringIO()

        with (
            patch.dict(os.environ, {ROOT_CONTROL_FD_ENV: "41"}),
            patch.object(root_control, "open_root_control_endpoint", return_value=endpoint),
            patch.object(root_control, "_read_trusted_keys", return_value={"deploy": object()}),
            patch.object(root_control, "scoped_process_bootstrap", return_value=object()),
            patch.object(root_control, "current_root_sql_subject", return_value=_subject()),
            patch.object(root_control, "publish_resource_binding", return_value=False),
            patch.object(root_control.sys, "stdout", stdout),
        ):
            status = root_control.main(self._argv())

        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "receive_activation" for event in events)
        )
        self.assertEqual(events[-1], "endpoint_closed")

    def test_malformed_received_activation_gets_only_private_error_reply(self) -> None:
        events: list[object] = []
        endpoint = _Endpoint(events)
        endpoint.activations = [object()]
        stdout = io.StringIO()

        with (
            patch.dict(os.environ, {ROOT_CONTROL_FD_ENV: "41"}),
            patch.object(root_control, "open_root_control_endpoint", return_value=endpoint),
            patch.object(root_control, "_read_trusted_keys", return_value={"deploy": object()}),
            patch.object(root_control, "scoped_process_bootstrap", return_value=object()),
            patch.object(root_control, "current_root_sql_subject", return_value=_subject()),
            patch.object(root_control, "publish_resource_binding", return_value=True),
            patch.object(
                root_control,
                "prepare_original_control_owner",
                side_effect=AssertionError("malformed activation reached actor preparation"),
            ),
            patch.object(root_control.sys, "stdout", stdout),
        ):
            status = root_control.main(self._argv())

        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(("error", "root_control_unavailable"), events)
        self.assertEqual(events[-1], "endpoint_closed")


if __name__ == "__main__":
    unittest.main()
