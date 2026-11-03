"""Opt-in runtime configuration and typed stdin protocol tests."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.runtime_channel import root_control
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject
from cli_agent_orchestrator.utils import process_sql_origin as sql_origin


class RootControlRuntimeTests(unittest.TestCase):
    def test_command_protocol_accepts_only_refresh_and_stop(self) -> None:
        self.assertEqual(root_control._parse_command_line(b'{"type":"refresh"}'), "refresh")
        self.assertEqual(root_control._parse_command_line(b'{"type":"stop"}'), "stop")
        for payload in (
            b'{"type":"submit","callback":"x"}',
            b'{"type":"stop","version":1}',
            b'{"type":"stop","type":"refresh"}',
            b'{"type":NaN}',
            b" " * (root_control._MAX_COMMAND_BYTES + 1),
        ):
            with self.subTest(payload=payload[:40]), self.assertRaises(ValueError):
                root_control._parse_command_line(payload)

    def test_trust_document_is_bounded_private_and_contains_public_keys_only(self) -> None:
        private = Ed25519PrivateKey.generate()
        raw = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        document = {
            "version": 1,
            "keys": {"deploy-key": base64.b64encode(raw).decode("ascii")},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trust.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            path.chmod(0o600)
            loaded = root_control._read_trusted_keys(path)
            self.assertEqual(set(loaded), {"deploy-key"})
            with self.assertRaises(ValueError):
                root_control._read_trusted_keys(Path("relative"))
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                root_control._read_trusted_keys(path)

    def test_trust_document_rejects_unknown_duplicate_or_noncanonical_keys(self) -> None:
        private = Ed25519PrivateKey.generate()
        raw = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        key = base64.b64encode(raw).decode("ascii")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trust.json"
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o600)
            for text in (
                json.dumps({"version": 1, "keys": {"a": key}, "extra": True}),
                '{"version":1,"keys":{"a":"' + key + '","a":"' + key + '"}',
                json.dumps({"version": True, "keys": {"a": key}}),
            ):
                path.write_text(text, encoding="utf-8")
                with self.subTest(document=text[:60]), self.assertRaises(ValueError):
                    root_control._read_trusted_keys(path)

    def test_activation_line_requires_one_bounded_newline_framed_packet(self) -> None:
        self.assertEqual(root_control._read_activation_line(io.BytesIO(b"{}\n")), b"{}")
        with self.assertRaises(ValueError):
            root_control._read_activation_line(io.BytesIO(b"{}"))
        with self.assertRaises(ValueError):
            root_control._read_activation_line(
                io.BytesIO(b"x" * (root_control.ROOT_CONTROL_ACTIVATION_MAX_BYTES + 2) + b"\n")
            )

    def test_main_publishes_subject_before_consuming_activation_line(self) -> None:
        events: list[str] = []

        @dataclass
        class FakeActor:
            started: bool = False
            closed: bool = False

            def start(self) -> None:
                self.started = True

            async def wait_ready(self) -> None:
                return None

            async def aclose(self, *, timeout: float | None = None) -> None:
                self.closed = True

        @dataclass
        class Binding:
            value: str

        subject = RootSqlSubject.__new__(RootSqlSubject)
        object.__setattr__(subject, "binding", Binding("opaque-test-binding"))
        object.__setattr__(subject, "sql_application_name", "caos-sql-test")
        object.__setattr__(subject, "boot_id", "boot")
        object.__setattr__(subject, "start_time_ticks", 1)
        object.__setattr__(subject, "pid_namespace", (1, 1))
        original_reader = root_control._read_activation_line

        def subject_reader(bootstrap):
            events.append("subject")
            return subject

        def packet_reader(stream):
            events.append("packet")
            return original_reader(stream)

        actor = FakeActor()

        async def prepare(**kwargs):
            events.append("prepare")
            return actor

        stdin = type("Input", (), {"buffer": io.BytesIO(b'packet\n{"type":"stop"}\n')})()
        stdout = io.StringIO()
        with (
            patch.object(root_control, "_read_trusted_keys", return_value={"k": object()}),
            patch.object(root_control, "scoped_process_bootstrap", return_value=object()),
            patch.object(root_control, "current_root_sql_subject", subject_reader),
            patch.object(root_control, "_read_activation_line", packet_reader),
            patch.object(root_control, "prepare_original_control_owner", prepare),
            patch.object(root_control.sys, "stdin", stdin),
            patch.object(root_control.sys, "stdout", stdout),
        ):
            status = root_control.main(
                [
                    "--trust-file",
                    "/private/trust",
                    "--policy-file",
                    "/private/policy",
                    "--scope",
                    "cli",
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(events, ["subject", "packet", "prepare"])
        self.assertTrue(actor.started)
        self.assertTrue(actor.closed)
        first_output = json.loads(stdout.getvalue().splitlines()[0])
        self.assertEqual(first_output["type"], "subject")

    def test_main_returns_failure_when_final_close_times_out_after_eof_or_stop(self) -> None:
        @dataclass
        class Binding:
            value: str

        subject = RootSqlSubject.__new__(RootSqlSubject)
        object.__setattr__(subject, "binding", Binding("opaque-test-binding"))
        object.__setattr__(subject, "sql_application_name", "caos-sql-test")
        object.__setattr__(subject, "boot_id", "boot")
        object.__setattr__(subject, "start_time_ticks", 1)
        object.__setattr__(subject, "pid_namespace", (1, 1))

        @dataclass
        class TimedOutActor:
            close_calls: int = 0

            def start(self) -> None:
                return None

            async def wait_ready(self) -> None:
                return None

            async def refresh(self):
                raise AssertionError("stop input must not refresh")

            async def aclose(self, *, timeout: float | None = None) -> None:
                self.close_calls += 1
                raise TimeoutError("close is still unproven")

        for commands in (b"packet\n", b'packet\n{"type":"stop"}\n'):
            with self.subTest(commands=commands):
                actor = TimedOutActor()
                stdin = type("Input", (), {"buffer": io.BytesIO(commands)})()
                stdout = io.StringIO()

                async def prepare(**kwargs):
                    return actor

                with (
                    patch.object(root_control, "_read_trusted_keys", return_value={"k": object()}),
                    patch.object(root_control, "scoped_process_bootstrap", return_value=object()),
                    patch.object(root_control, "current_root_sql_subject", return_value=subject),
                    patch.object(root_control, "prepare_original_control_owner", prepare),
                    patch.object(root_control.sys, "stdin", stdin),
                    patch.object(root_control.sys, "stdout", stdout),
                ):
                    status = root_control.main(
                        [
                            "--trust-file",
                            "/private/trust",
                            "--policy-file",
                            "/private/policy",
                            "--scope",
                            "cli",
                        ]
                    )
                self.assertEqual(status, 2)
                self.assertEqual(actor.close_calls, 1)
                self.assertNotIn('"type":"closed"', stdout.getvalue())

    def test_main_reports_missing_root_startup_flag_without_preparing_actor(self) -> None:
        stdin = type("Input", (), {"buffer": io.BytesIO()})()
        stdout = io.StringIO()
        prepare_calls: list[object] = []

        async def prepare(**kwargs):
            prepare_calls.append(kwargs)
            raise AssertionError("missing root flag must refuse before prepare")

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sql_origin, "_STATE", sql_origin._State(os.getpid())),
            patch.object(root_control, "_read_trusted_keys", return_value={}),
            patch.object(root_control, "scoped_process_bootstrap", return_value=object()),
            patch.object(root_control.sys, "stdin", stdin),
            patch.object(root_control.sys, "stdout", stdout),
            patch.object(root_control, "prepare_original_control_owner", prepare),
        ):
            status = root_control.main(
                [
                    "--trust-file",
                    "/private/trust",
                    "--policy-file",
                    "/private/policy",
                    "--scope",
                    "cli",
                ]
            )

        self.assertEqual(status, 2)
        self.assertEqual(prepare_calls, [])
        response = json.loads(stdout.getvalue().splitlines()[0])
        self.assertEqual(response, {"type": "error", "code": "root_control_unavailable"})


if __name__ == "__main__":
    unittest.main()
