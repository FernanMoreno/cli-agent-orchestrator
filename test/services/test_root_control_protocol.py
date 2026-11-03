"""Strict bounded v1 frames for the private Root control transport."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from cli_agent_orchestrator.models import root_control_protocol as protocol


class RootControlProtocolTests(unittest.TestCase):
    def test_import_origin_and_activation_round_trip(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expected = root / "src/cli_agent_orchestrator/models/root_control_protocol.py"
        self.assertEqual(Path(protocol.__file__).resolve(), expected.resolve())

        packet = b'{"kind":"cao_root_control_activation","version":1}'
        request = protocol.RootControlRequest(0, "activation", packet)
        encoded = protocol.encode_root_control_request(request)
        self.assertLessEqual(len(encoded), protocol.ROOT_CONTROL_MAX_FRAME_BYTES)
        self.assertEqual(protocol.decode_root_control_request(encoded), request)
        decoded = json.loads(encoded)
        self.assertEqual(set(decoded), {"version", "sequence", "type", "packet"})
        self.assertEqual(decoded["type"], "activation")

    def test_commands_and_replies_round_trip_with_bounded_reason(self) -> None:
        for request in (
            protocol.RootControlRequest(1, "refresh"),
            protocol.RootControlRequest(2, "stop"),
        ):
            self.assertEqual(
                protocol.decode_root_control_request(protocol.encode_root_control_request(request)),
                request,
            )

        replies = (
            protocol.RootControlReply(0, "ready"),
            protocol.RootControlReply(1, "refresh", reason="capacity_available"),
            protocol.RootControlReply(2, "stopping"),
            protocol.RootControlReply(3, "error", code="root_control_unavailable"),
        )
        for reply in replies:
            encoded = protocol.encode_root_control_reply(reply)
            self.assertLessEqual(len(encoded), protocol.ROOT_CONTROL_MAX_FRAME_BYTES)
            self.assertEqual(protocol.decode_root_control_reply(encoded), reply)

    def test_request_codec_rejects_duplicate_unknown_and_noninteger_fields(self) -> None:
        invalid = (
            b'{"sequence":0,"sequence":0,"type":"activation","version":1,"packet":"e30="}',
            b'{"sequence":0,"type":"activation","version":1,"packet":"e30=","extra":0}',
            b'{"sequence":true,"type":"activation","version":1,"packet":"e30="}',
            b'{"sequence":0,"type":"activation","version":true,"packet":"e30="}',
            b'{"sequence":1,"type":"refresh","version":1,"packet":"e30="}',
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                protocol.decode_root_control_request(payload)

    def test_reply_codec_rejects_duplicate_unknown_and_wrong_error_code(self) -> None:
        invalid = (
            b'{"sequence":1,"sequence":1,"type":"refresh","version":1,"reason":"ok"}',
            b'{"sequence":1,"type":"refresh","version":1,"reason":"ok","extra":0}',
            b'{"sequence":1,"type":"error","version":1,"code":"arbitrary"}',
            b'{"sequence":true,"type":"ready","version":1}',
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                protocol.decode_root_control_reply(payload)

    def test_activation_and_command_bounds_are_distinct(self) -> None:
        with self.assertRaises(ValueError):
            protocol.RootControlRequest(
                0,
                "activation",
                b"x" * (protocol.ROOT_CONTROL_ACTIVATION_MAX_BYTES + 1),
            )
        with self.assertRaises(ValueError):
            protocol.RootControlRequest(
                1, "refresh", packet=b"x" * protocol.ROOT_CONTROL_MAX_COMMAND_BYTES
            )
        with self.assertRaises(ValueError):
            protocol.RootControlReply(1, "refresh", reason="x" * 65)


if __name__ == "__main__":
    unittest.main()
