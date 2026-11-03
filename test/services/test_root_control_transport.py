"""Owner-bound private Root transport over local Unix SEQPACKET sockets."""

from __future__ import annotations

import asyncio
import os
import socket
import unittest
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from cli_agent_orchestrator.models.root_control_protocol import (
    ROOT_CONTROL_FD_ENV,
    RootControlRequest,
    decode_root_control_reply,
    encode_root_control_request,
)
from cli_agent_orchestrator.models.sql_startup_permission import SQL_ROOT_GENESIS_ENV
from cli_agent_orchestrator.services import root_control_transport as transport
from cli_agent_orchestrator.services.resource_process_supervisor import (
    BINDING_FD_ENV,
)


class RootControlTransportTests(unittest.TestCase):
    def test_import_origin_and_absent_optin(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expected = root / "src/cli_agent_orchestrator/services/root_control_transport.py"
        self.assertEqual(Path(transport.__file__).resolve(), expected.resolve())
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(transport.open_root_control_endpoint())

    def test_invalid_private_descriptor_never_falls_back_to_stdio(self) -> None:
        with patch.dict(
            os.environ,
            {
                SQL_ROOT_GENESIS_ENV: "1",
                ROOT_CONTROL_FD_ENV: "not-a-fd",
                BINDING_FD_ENV: "44",
            },
            clear=True,
        ):
            with self.assertRaises(transport.RootControlTransportUnavailable):
                transport.open_root_control_endpoint()

    def test_local_socket_activation_idle_poll_commands_and_close(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        raw_fd = inherited.detach()
        environment = {
            SQL_ROOT_GENESIS_ENV: "1",
            ROOT_CONTROL_FD_ENV: str(raw_fd),
            BINDING_FD_ENV: str(raw_fd + 1),
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            self.assertIsNotNone(endpoint)
            assert endpoint is not None
            self.assertIsNone(endpoint.receive_activation(timeout_s=0.01))
            parent.send(
                encode_root_control_request(
                    RootControlRequest(0, "activation", b"signed-activation")
                )
            )
            self.assertEqual(endpoint.receive_activation(timeout_s=1), b"signed-activation")
            endpoint.send_ready()
            reply = decode_root_control_reply(parent.recv(4096))
            self.assertEqual((reply.sequence, reply.type), (0, "ready"))

            parent.send(encode_root_control_request(RootControlRequest(1, "refresh")))
            self.assertEqual(endpoint.receive_command(timeout_s=1), "refresh")
            endpoint.send_refresh("capacity_available")
            reply = decode_root_control_reply(parent.recv(4096))
            self.assertEqual(
                (reply.sequence, reply.type, reply.reason), (1, "refresh", "capacity_available")
            )

            parent.send(encode_root_control_request(RootControlRequest(2, "stop")))
            self.assertEqual(endpoint.receive_command(timeout_s=1), "stop")
            endpoint.send_stopping()
            reply = decode_root_control_reply(parent.recv(4096))
            self.assertEqual((reply.sequence, reply.type), (2, "stopping"))
            endpoint.close()
            endpoint.close()
            parent.close()

    def test_binding_descriptor_collision_is_rejected_before_adoption(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        environment = {
            SQL_ROOT_GENESIS_ENV: "1",
            ROOT_CONTROL_FD_ENV: str(fd),
            BINDING_FD_ENV: str(fd),
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(transport.RootControlTransportUnavailable):
                transport.open_root_control_endpoint()
        os.close(fd)
        parent.close()

    def test_eof_is_distinct_from_idle_timeout(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            self.assertIsNone(endpoint.receive_activation(timeout_s=0.01))
            parent.close()
            with self.assertRaises(transport.RootControlChannelEOF):
                endpoint.receive_activation(timeout_s=1)
            endpoint.close()

    def test_child_endpoint_denies_foreign_thread_without_mutating_owner_state(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            with self.assertRaises(TypeError):
                import copy

                copy.copy(endpoint)
            original_state = endpoint._state
            original_fd = endpoint._socket.fileno()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(endpoint.close)
                with self.assertRaises(PermissionError):
                    future.result(timeout=2)
            self.assertEqual(endpoint._state, original_state)
            self.assertEqual(endpoint._socket.fileno(), original_fd)
            parent.send(
                encode_root_control_request(
                    RootControlRequest(0, "activation", b"owner-still-works")
                )
            )
            self.assertEqual(endpoint.receive_activation(timeout_s=1), b"owner-still-works")
            endpoint.close()
        parent.close()

    def test_child_endpoint_denies_foreign_task_without_mutating_owner_state(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            original_state = endpoint._state
            original_fd = endpoint._socket.fileno()

            async def foreign_close() -> None:
                with self.assertRaises(PermissionError):
                    endpoint.close()

            asyncio.run(foreign_close())
            self.assertEqual(endpoint._state, original_state)
            self.assertEqual(endpoint._socket.fileno(), original_fd)
            endpoint.close()
        parent.close()

    def test_unknown_frame_rights_are_closed_and_endpoint_is_terminal(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            read_fd, write_fd = os.pipe()
            closed: list[int] = []
            original_close = os.close

            def track_close(value: int) -> None:
                closed.append(value)
                original_close(value)

            payload = encode_root_control_request(
                RootControlRequest(0, "activation", b"signed-activation")
            )
            with patch.object(transport.os, "close", side_effect=track_close):
                parent.sendmsg(
                    [payload],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", [read_fd]))],
                )
                with self.assertRaises(transport.RootControlTransportUnavailable):
                    endpoint.receive_activation(timeout_s=1)
            self.assertTrue(closed)
            self.assertNotIn(read_fd, closed)
            with self.assertRaises(transport.RootControlTransportUnavailable):
                endpoint.receive_activation(timeout_s=0.01)
            endpoint.close()
            parent.close()
            original_close(read_fd)
            original_close(write_fd)

    def test_parent_credentials_are_checked_on_every_frame(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            payload = encode_root_control_request(
                RootControlRequest(0, "activation", b"signed-activation")
            )
            with patch.object(
                transport,
                "_receive_frame",
                return_value=(payload, (os.getpid() + 1, os.getuid(), os.getgid())),
            ):
                with self.assertRaises(transport.RootControlTransportUnavailable):
                    endpoint.receive_activation(timeout_s=1)
            self.assertEqual(endpoint._socket.fileno(), -1)
            endpoint.close()
        parent.close()

    def test_single_fork_hook_closes_inherited_endpoint_socket(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            transport._after_fork_close_root_sockets()
            self.assertEqual(endpoint._socket.fileno(), -1)
            with self.assertRaises(transport.RootControlTransportUnavailable):
                endpoint.receive_activation(timeout_s=0.01)
            endpoint.close()
        parent.close()

    def test_zero_timeout_is_a_nonconsuming_idle_poll(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            self.assertIsNone(endpoint.receive_activation(timeout_s=0))
            self.assertEqual(endpoint._state, "await_activation")
            parent.send(
                encode_root_control_request(RootControlRequest(0, "activation", b"after-poll"))
            )
            self.assertEqual(endpoint.receive_activation(timeout_s=1), b"after-poll")
            endpoint.close()
        parent.close()

    def test_empty_seqpacket_datagram_is_invalid_not_peer_eof(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fd = inherited.detach()
        with (
            patch.dict(
                os.environ,
                {
                    SQL_ROOT_GENESIS_ENV: "1",
                    ROOT_CONTROL_FD_ENV: str(fd),
                    BINDING_FD_ENV: str(fd + 1),
                },
                clear=True,
            ),
            patch.object(transport.os, "getppid", return_value=os.getpid()),
        ):
            endpoint = transport.open_root_control_endpoint()
            assert endpoint is not None
            self.assertEqual(parent.sendmsg([b""], []), 0)
            with self.assertRaises(transport.RootControlTransportUnavailable):
                endpoint.receive_activation(timeout_s=1)
            endpoint.close()
        parent.close()


if __name__ == "__main__":
    unittest.main()
