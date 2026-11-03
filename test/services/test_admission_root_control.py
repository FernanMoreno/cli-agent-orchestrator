"""Root RPC stays owner-bound and separate from physical retirement evidence."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from test.services.test_admission_command_process import (
    TARGET_DIGEST,
    _binding,
    _command,
    _label,
    _owner,
    _supervisor,
)
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.resource_generation import _issue_process_exit_observation
from cli_agent_orchestrator.models.root_control_protocol import (
    ROOT_CONTROL_FD_ENV,
    decode_root_control_request,
)
from cli_agent_orchestrator.models.sql_root_subject import RootSqlSubject
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_ROOT_GENESIS_ENV,
    SQL_STARTUP_PERMISSION_ENV,
)
from cli_agent_orchestrator.services import delivery_admission
from cli_agent_orchestrator.services import resource_process_supervisor as process_supervisor
from cli_agent_orchestrator.services import root_control_transport, root_sql_control
from cli_agent_orchestrator.services.resource_process_supervisor import (
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)


class _RootChannel:
    def __init__(self) -> None:
        self.closed = False

    def fileno(self) -> int:
        return -1 if self.closed else 31

    def close(self) -> None:
        self.closed = True


class RootSqlSubjectTests(unittest.TestCase):
    def test_service_reexports_the_exact_pure_model_class(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(
            Path(root_sql_control.__file__).resolve(),
            (root / "src/cli_agent_orchestrator/services/root_sql_control.py").resolve(),
        )
        self.assertIs(root_sql_control.RootSqlSubject, RootSqlSubject)
        binding = _binding()
        subject = RootSqlSubject(
            binding,
            _label(),
            str(uuid4()),
            11,
            (1, 2),
        )
        self.assertIs(type(subject), RootSqlSubject)
        with self.assertRaises(FrozenInstanceError):
            subject.boot_id = str(uuid4())  # type: ignore[misc]


class ResourceProcessSupervisorRootChannelTests(unittest.TestCase):
    def _launch(self, *, root_control_channel: bool):
        pidfd = os.open(os.devnull, os.O_RDONLY)
        live = process_supervisor._identity(os.getpid())
        live = type(live)(
            live.boot_id,
            live.ticks,
            os.getpid(),
            live.uid,
            live.birth,
            live.pid_ns,
            live.mount_ns,
            live.cgroup_ns,
            live.view,
        )
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        class Process:
            pid = 74123
            returncode = None

            def __init__(self, argv):
                self.args = list(argv)

            def poll(self):
                return self.returncode

        def popen(argv, **kwargs):
            calls.append((tuple(argv), kwargs))
            return Process(argv)

        try:
            with (
                patch.object(process_supervisor.subprocess, "Popen", side_effect=popen),
                patch.object(process_supervisor.os, "pidfd_open", return_value=pidfd),
                patch.object(process_supervisor, "_identity", return_value=live),
                patch.object(process_supervisor, "_exit_ready", return_value=False),
            ):
                owner = ResourceProcessSupervisor.launch(
                    ["/unexecuted/resource-child"],
                    expected_store_id=str(uuid4()),
                    expected_target_identity_sha256=TARGET_DIGEST,
                    env={SQL_ROOT_GENESIS_ENV: "1"} if root_control_channel else {},
                    root_control_channel=root_control_channel,
                )
            return owner, calls, pidfd
        except BaseException:
            os.close(pidfd)
            raise

    def test_optin_creates_distinct_private_pair_and_passes_only_child_endpoints(self) -> None:
        owner, calls, pidfd = self._launch(root_control_channel=True)
        try:
            self.assertEqual(len(calls), 1)
            _argv, kwargs = calls[0]
            environment = kwargs["env"]
            passed = kwargs["pass_fds"]
            self.assertIsInstance(environment, dict)
            self.assertIsInstance(passed, tuple)
            root_fd = int(environment[ROOT_CONTROL_FD_ENV])
            binding_fd = int(environment[process_supervisor.BINDING_FD_ENV])
            self.assertNotEqual(root_fd, binding_fd)
            self.assertIn(root_fd, passed)
            self.assertIn(binding_fd, passed)
            self.assertEqual(environment[SQL_ROOT_GENESIS_ENV], "1")
            self.assertFalse(owner._first_sql_grant)
            self.assertIsNone(owner._broker_channel)
            self.assertTrue(owner._root_control_enabled)
            self.assertIsNotNone(owner._root_channel)
            assert owner._root_channel is not None
            self.assertEqual(
                owner._root_channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED), 1
            )
            self.assertNotEqual(owner._root_channel, owner._channel)
        finally:
            if owner._root_channel is not None:
                owner._root_channel.close()
            owner._channel.close()
            os.close(pidfd)

    def test_default_launch_has_no_root_pair_or_root_fd_environment(self) -> None:
        owner, calls, pidfd = self._launch(root_control_channel=False)
        try:
            _argv, kwargs = calls[0]
            self.assertNotIn(ROOT_CONTROL_FD_ENV, kwargs["env"])
            self.assertFalse(owner._root_control_enabled)
            self.assertIsNone(owner._root_channel)
        finally:
            owner._channel.close()
            os.close(pidfd)

    def test_invalid_root_modes_fail_before_socket_or_popen(self) -> None:
        cases = (
            ({}, False, False, True),
            ({SQL_ROOT_GENESIS_ENV: "0"}, False, False, True),
            ({SQL_ROOT_GENESIS_ENV: "1"}, True, False, True),
            ({SQL_ROOT_GENESIS_ENV: "1"}, True, True, True),
            ({SQL_ROOT_GENESIS_ENV: "1", ROOT_CONTROL_FD_ENV: "52"}, False, False, True),
            ({SQL_ROOT_GENESIS_ENV: "1", SQL_STARTUP_PERMISSION_ENV: "1"}, False, False, True),
            ({SQL_ROOT_GENESIS_ENV: "1", SQL_BROKER_FD_ENV: "52"}, False, False, True),
            ({ROOT_CONTROL_FD_ENV: "52"}, False, False, False),
        )
        for environment, startup_grant, broker, enabled in cases:
            with self.subTest(environment=environment, startup_grant=startup_grant, broker=broker):
                with (
                    patch.object(process_supervisor.socket, "socketpair") as make_pair,
                    patch.object(process_supervisor.subprocess, "Popen") as popen,
                    self.assertRaises(ResourceProcessUnproven),
                ):
                    ResourceProcessSupervisor.launch(
                        ["/unexecuted/resource-child"],
                        expected_store_id=str(uuid4()),
                        expected_target_identity_sha256=TARGET_DIGEST,
                        env=environment,
                        first_sql_grant=startup_grant,
                        sql_broker=broker,
                        root_control_channel=enabled,
                    )
                make_pair.assert_not_called()
                popen.assert_not_called()

    def test_popen_failure_closes_both_socket_pairs(self) -> None:
        pairs: list[socket.socket] = []
        socketpair = process_supervisor.socket.socketpair

        def capture_pair(*args, **kwargs):
            pair = socketpair(*args, **kwargs)
            pairs.extend(pair)
            return pair

        with (
            patch.object(process_supervisor.socket, "socketpair", side_effect=capture_pair),
            patch.object(
                process_supervisor.subprocess,
                "Popen",
                side_effect=RuntimeError("injected launch failure"),
            ),
            self.assertRaises(RuntimeError),
        ):
            ResourceProcessSupervisor.launch(
                ["/unexecuted/resource-child"],
                expected_store_id=str(uuid4()),
                expected_target_identity_sha256=TARGET_DIGEST,
                env={SQL_ROOT_GENESIS_ENV: "1"},
                root_control_channel=True,
            )
        self.assertEqual(len(pairs), 4)
        self.assertTrue(all(channel.fileno() == -1 for channel in pairs))


class RootControlCommandProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = _binding()
        self.label = _label()
        self.owner = _owner()
        self.reservation = _command(self.owner).reservation
        self.assertIsNotNone(self.reservation)
        self.supervisor = _supervisor()
        self.supervisor._root_control_enabled = True
        self.supervisor._root_channel = _RootChannel()
        self.supervisor._identity = type(
            "Identity",
            (),
            {
                "boot_id": str(uuid4()),
                "ticks": 11,
                "pid_ns": (1, 2),
            },
        )()
        self.supervisor._validate_root_control = lambda: None

    def _launch(self, *, root_control_channel: bool = False):
        self.supervisor._binding = None
        self.supervisor._binding_failed = False
        self.supervisor._sql_application_name = None
        self.supervisor._root_control_enabled = root_control_channel
        calls: list[dict[str, object]] = []

        def launch(
            cls,
            argv,
            *,
            expected_store_id,
            expected_target_identity_sha256,
            env=None,
            first_sql_grant=False,
            sql_broker=False,
            root_control_channel=False,
        ):
            calls.append(
                {
                    "expected_store_id": expected_store_id,
                    "target": expected_target_identity_sha256,
                    "env": env,
                    "first_sql_grant": first_sql_grant,
                    "sql_broker": sql_broker,
                    "root_control_channel": root_control_channel,
                }
            )
            return self.supervisor

        with patch.object(ResourceProcessSupervisor, "launch", classmethod(launch)):
            worker = self.reservation._launch_process(
                ["/unexecuted/root-child"],
                expected_store_id=self.binding.store_id,
                expected_target_identity_sha256=TARGET_DIGEST,
                env={"CAO_INTERNAL_ROOT_SQL_GENESIS": "1"},
                clock=lambda: 1.0,
                **({"root_control_channel": True} if root_control_channel else {}),
            )
        self.assertIsNotNone(worker)
        return worker, calls

    def test_default_factory_call_shape_omits_optin_keyword(self) -> None:
        strict_calls: list[dict[str, object]] = []

        def launch(
            cls,
            argv,
            *,
            expected_store_id,
            expected_target_identity_sha256,
            env=None,
            first_sql_grant=False,
            sql_broker=False,
            root_control_channel=False,
        ):
            strict_calls.append(locals())
            return self.supervisor

        with patch.object(ResourceProcessSupervisor, "launch", classmethod(launch)):
            worker = self.reservation._launch_process(
                ["/unexecuted/legacy-child"],
                expected_store_id=self.binding.store_id,
                expected_target_identity_sha256=TARGET_DIGEST,
                env={},
                clock=lambda: 1.0,
            )
        self.assertIsNotNone(worker)
        self.assertEqual(len(strict_calls), 1)
        self.assertFalse(strict_calls[0]["first_sql_grant"])
        self.assertFalse(strict_calls[0]["sql_broker"])

    def test_root_subject_is_built_from_authenticated_binding_and_kernel_identity(self) -> None:
        worker, _ = self._launch(root_control_channel=True)
        self.supervisor._binding = self.binding
        self.supervisor._sql_application_name = self.label
        self.supervisor._identity = type(
            "Identity",
            (),
            {"boot_id": "boot", "ticks": 37, "pid_ns": (41, 43)},
        )()
        self.supervisor._validate_root_control = lambda: None
        worker._binding = self.binding
        worker._sql_application_name = self.label
        subject = worker.root_subject()
        self.assertEqual(subject.binding, self.binding)
        self.assertEqual(subject.sql_application_name, self.label)
        self.assertEqual(
            (subject.boot_id, subject.start_time_ticks, subject.pid_namespace),
            ("boot", 37, (41, 43)),
        )

    def test_root_rpc_failure_closes_only_protocol_and_keeps_parent_charge_joinable(self) -> None:
        worker, _ = self._launch(root_control_channel=True)
        self.supervisor._binding = self.binding
        self.supervisor._sql_application_name = self.label
        worker._binding = self.binding
        worker._sql_application_name = self.label
        self.supervisor.root_refresh = lambda timeout_s: (_ for _ in ()).throw(
            delivery_admission.StorageAdmissionUnavailable()
        )

        def close_root_control() -> None:
            self.supervisor._root_channel.close()
            self.supervisor.root_closed = True

        self.supervisor.close_root_control = close_root_control
        with self.assertRaises(delivery_admission.StorageAdmissionUnavailable):
            worker.root_refresh()
        self.assertTrue(self.supervisor.root_closed)
        self.assertFalse(worker._terminal_uncertainty)
        self.assertEqual(worker._reservation.state, "started")
        self.assertFalse(worker._reservation.finish())
        self.assertTrue(worker.has_native_owner)

        observation = _issue_process_exit_observation(
            self.binding,
            boot_id=str(uuid4()),
            start_time_ticks=1,
            pid_namespace=(1, 1),
            exit_kind="exited",
            exit_status=0,
            sql_application_name=self.label,
        )

        def wait(*, timeout_s=None):
            self.supervisor._observation = observation
            self.supervisor._exited = True
            return observation

        def close():
            self.supervisor._channel.close()
            self.supervisor._root_channel.close()
            self.supervisor._pidfd = None
            self.supervisor._closed = True

        self.supervisor.wait = wait
        self.supervisor.close = close
        self.assertTrue(worker.join(timeout_s=0))
        self.assertEqual(worker._reservation.state, "released")

    def _bind_root_worker(self):
        worker, _ = self._launch(root_control_channel=True)
        self.supervisor._binding = self.binding
        self.supervisor._sql_application_name = self.label
        worker._binding = self.binding
        worker._sql_application_name = self.label
        return worker

    def _assert_uncertain_root_close_cannot_retire(self, worker) -> None:
        observation = _issue_process_exit_observation(
            self.binding,
            boot_id=str(uuid4()),
            start_time_ticks=1,
            pid_namespace=(1, 1),
            exit_kind="exited",
            exit_status=0,
            sql_application_name=self.label,
        )

        def wait(*, timeout_s=None):
            self.supervisor._observation = observation
            self.supervisor._exited = True
            return observation

        def close():
            self.supervisor._channel.close()
            self.supervisor._root_channel.close()
            self.supervisor._pidfd = None
            self.supervisor._closed = True

        self.supervisor.wait = wait
        self.supervisor.close = close
        self.assertFalse(worker.join(timeout_s=0))
        self.assertTrue(worker._terminal_uncertainty)
        self.assertEqual(self.reservation.state, "started")
        self.assertTrue(worker.cleanup(timeout_s=0))
        self.assertFalse(worker.join(timeout_s=0))
        self.assertEqual(self.reservation.state, "started")
        self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_swallowed_root_close_failure_stays_physical_after_successful_retry(self) -> None:
        worker = self._bind_root_worker()

        class UncertainClose(_RootChannel):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def close(self):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("controlled Root socket close uncertainty")
                super().close()

        self.supervisor._root_channel = UncertainClose()
        primary = delivery_admission.StorageAdmissionUnavailable()

        def refresh(*, timeout_s):
            ResourceProcessSupervisor._fail_root_control(self.supervisor, primary)
            raise primary

        self.supervisor.root_refresh = refresh
        with self.assertRaises(delivery_admission.StorageAdmissionUnavailable) as caught:
            worker.root_refresh()
        self.assertIs(caught.exception, primary)
        self.assertEqual(self.supervisor._root_channel.calls, 2)
        self._assert_uncertain_root_close_cannot_retire(worker)

    def test_rpc_abort_close_exception_stays_physical_after_positive_close(self) -> None:
        worker = self._bind_root_worker()
        close_root = self.supervisor.close_root_control
        primary = delivery_admission.StorageAdmissionUnavailable()
        self.supervisor.root_refresh = lambda **_: (_ for _ in ()).throw(primary)
        self.supervisor.close_root_control = lambda: (_ for _ in ()).throw(
            OSError("controlled Root abort close uncertainty")
        )
        with self.assertRaises(delivery_admission.StorageAdmissionUnavailable) as caught:
            worker.root_refresh()
        self.assertIs(caught.exception, primary)
        self.supervisor.close_root_control = close_root
        self._assert_uncertain_root_close_cannot_retire(worker)

    def test_explicit_root_abort_close_exception_stays_physical_after_positive_close(self) -> None:
        worker = self._bind_root_worker()
        close_root = self.supervisor.close_root_control
        self.supervisor.close_root_control = lambda: (_ for _ in ()).throw(
            OSError("controlled explicit Root close uncertainty")
        )
        with self.assertRaises(OSError):
            worker.root_abort()
        self.supervisor.close_root_control = close_root
        self._assert_uncertain_root_close_cannot_retire(worker)

    def test_cleanup_closes_root_endpoint_before_wait_and_retains_charge_on_timeout(self) -> None:
        worker, _ = self._launch(root_control_channel=True)
        observed_order: list[str] = []

        def close_root() -> None:
            self.supervisor._root_channel.close()
            observed_order.append("close_root")

        def wait(*, timeout_s=None):
            self.assertEqual(self.supervisor._root_channel.fileno(), -1)
            observed_order.append("wait")
            raise subprocess.TimeoutExpired("root-child", timeout_s)

        self.supervisor.close_root_control = close_root
        self.supervisor.wait = wait
        self.assertFalse(worker.cleanup())
        self.assertEqual(observed_order, ["close_root", "wait"])
        self.assertEqual(worker._reservation.state, "started")
        self.assertTrue(worker._terminal_uncertainty)

    def test_root_activation_is_single_attempt_and_abort_does_not_refund(self) -> None:
        worker, _ = self._launch(root_control_channel=True)
        self.supervisor._binding = self.binding
        self.supervisor._sql_application_name = self.label
        worker._binding = self.binding
        worker._sql_application_name = self.label
        calls: list[bytes] = []
        self.supervisor.root_activate = lambda packet, timeout_s: calls.append(packet)
        self.supervisor.close_root_control = lambda: setattr(self.supervisor, "root_closed", True)
        worker.root_activate(b"signed-packet")
        with self.assertRaises(delivery_admission.ResourceProcessUnproven):
            worker.root_activate(b"replay")
        worker.root_abort()
        self.assertEqual(calls, [b"signed-packet"])
        self.assertTrue(self.supervisor.root_closed)
        self.assertEqual(worker._reservation.state, "started")
        self.assertFalse(worker._terminal_uncertainty)


class RootControlSocketExchangeTests(unittest.TestCase):
    def test_parent_and_child_exchange_activation_refresh_and_stop_over_local_seqpacket(
        self,
    ) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        root_fd = inherited.detach()
        live = process_supervisor._identity(os.getpid())
        binding = ResourceBinding(
            instance_id=str(uuid4()),
            process_generation=str(uuid4()),
            process_pid=os.getpid(),
            process_started_at=live.birth,
            store_id=str(uuid4()),
            target_identity_sha256=TARGET_DIGEST,
            mount_ns_device=live.mount_ns[0],
            mount_ns_inode=live.mount_ns[1],
            cgroup_ns_device=live.cgroup_ns[0],
            cgroup_ns_inode=live.cgroup_ns[1],
            resource_view_sha256=live.view,
        )
        label = _label()
        supervisor = _supervisor()
        supervisor._root_control_enabled = True
        supervisor._root_channel = parent
        supervisor._root_state = "await_activation"
        supervisor._root_sequence = 1
        supervisor._root_activation_attempted = False
        supervisor._binding = binding
        supervisor._identity = live
        supervisor._sql_application_name = label
        supervisor._pidfd = 31
        supervisor._process = SimpleNamespace(pid=os.getpid(), args=["local-socket-child"])
        endpoint_ready = threading.Event()

        def serve_child():
            endpoint = root_control_transport.open_root_control_endpoint()
            if endpoint is None:
                raise AssertionError("private descriptor unexpectedly absent")
            endpoint_ready.set()
            activation = endpoint.receive_activation(timeout_s=1)
            if activation != b"signed-root-activation":
                raise AssertionError("activation packet changed in transport")
            endpoint.send_ready()
            if endpoint.receive_command(timeout_s=1) != "refresh":
                raise AssertionError("refresh command was not delivered")
            endpoint.send_refresh("capacity_available")
            if endpoint.receive_command(timeout_s=1) != "stop":
                raise AssertionError("stop command was not delivered")
            endpoint.send_stopping()
            endpoint.close()
            return activation

        environment = {
            SQL_ROOT_GENESIS_ENV: "1",
            ROOT_CONTROL_FD_ENV: str(root_fd),
            process_supervisor.BINDING_FD_ENV: str(root_fd + 1),
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(root_control_transport.os, "getppid", return_value=os.getpid()),
            patch.object(process_supervisor, "_exit_ready", return_value=False),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            child = pool.submit(serve_child)
            self.assertTrue(endpoint_ready.wait(timeout=2))
            supervisor.root_activate(b"signed-root-activation", timeout_s=2)
            self.assertEqual(supervisor._root_state, "active")
            self.assertEqual(supervisor.root_refresh(timeout_s=2), "capacity_available")
            supervisor.root_stop(timeout_s=2)
            self.assertEqual(supervisor._root_state, "stopping")
            self.assertEqual(child.result(timeout=2), b"signed-root-activation")
            supervisor.close_root_control()
            self.assertEqual(supervisor._root_channel.fileno(), -1)

    def test_stop_ack_remains_valid_when_exit_becomes_ready_after_send(self) -> None:
        parent, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        root_fd = inherited.detach()
        live = process_supervisor._identity(os.getpid())
        binding = ResourceBinding(
            instance_id=str(uuid4()),
            process_generation=str(uuid4()),
            process_pid=os.getpid(),
            process_started_at=live.birth,
            store_id=str(uuid4()),
            target_identity_sha256=TARGET_DIGEST,
            mount_ns_device=live.mount_ns[0],
            mount_ns_inode=live.mount_ns[1],
            cgroup_ns_device=live.cgroup_ns[0],
            cgroup_ns_inode=live.cgroup_ns[1],
            resource_view_sha256=live.view,
        )
        label = _label()
        supervisor = _supervisor()
        supervisor._root_control_enabled = True
        supervisor._root_state = "active"
        supervisor._root_sequence = 1
        supervisor._root_activation_attempted = True
        supervisor._binding = binding
        supervisor._identity = live
        supervisor._sql_application_name = label
        supervisor._pidfd = 31
        supervisor._process = SimpleNamespace(pid=os.getpid(), args=["local-stop-child"])
        reply_sent = threading.Event()
        process_exited = threading.Event()

        class ParentChannel:
            def send(self, payload):
                sent = parent.send(payload)
                if decode_root_control_request(payload).type == "stop":
                    if not reply_sent.wait(timeout=2):
                        raise AssertionError("child did not queue stopping reply")
                    process_exited.set()
                return sent

            def __getattr__(self, name):
                return getattr(parent, name)

        supervisor._root_channel = ParentChannel()
        supervisor._root_state = "active"

        def exit_ready(_pidfd):
            return process_exited.is_set()

        def serve_child():
            endpoint = root_control_transport.open_root_control_endpoint()
            if endpoint is None:
                raise AssertionError("private descriptor unexpectedly absent")
            endpoint.receive_activation(timeout_s=1)
            endpoint.send_ready()
            if endpoint.receive_command(timeout_s=1) != "stop":
                raise AssertionError("stop command was not delivered")
            endpoint.send_stopping()
            reply_sent.set()
            endpoint.close()

        environment = {
            SQL_ROOT_GENESIS_ENV: "1",
            ROOT_CONTROL_FD_ENV: str(root_fd),
            process_supervisor.BINDING_FD_ENV: str(root_fd + 1),
        }
        try:
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(root_control_transport.os, "getppid", return_value=os.getpid()),
                patch.object(process_supervisor, "_exit_ready", side_effect=exit_ready) as ready,
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                child = pool.submit(serve_child)
                supervisor._root_state = "await_activation"
                supervisor._root_sequence = 1
                supervisor._root_activation_attempted = False
                supervisor.root_activate(b"signed-root-activation", timeout_s=2)
                supervisor.root_stop(timeout_s=2)
                child.result(timeout=2)
                self.assertEqual(supervisor._root_state, "stopping")
                self.assertTrue(process_exited.is_set())
                self.assertEqual(ready.call_count, 6)
                supervisor.close_root_control()
        finally:
            parent.close()


class RootSupervisorTaskOwnershipTests(unittest.TestCase):
    def test_foreign_async_task_cannot_send_or_close_root_channel(self) -> None:
        supervisor = object.__new__(ResourceProcessSupervisor)
        supervisor._owner_pid = os.getpid()
        supervisor._owner_thread = threading.current_thread()
        supervisor._root_owner_task = None
        supervisor._root_control_enabled = True
        supervisor._root_state = "await_activation"
        supervisor._root_sequence = 1
        supervisor._root_activation_attempted = False
        supervisor._root_channel = _RootChannel()
        supervisor._closed = False
        supervisor._binding_failed = False
        live = process_supervisor._identity(os.getpid())
        supervisor._identity = live
        supervisor._binding = ResourceBinding(
            instance_id=str(uuid4()),
            process_generation=str(uuid4()),
            process_pid=os.getpid(),
            process_started_at=live.birth,
            store_id=str(uuid4()),
            target_identity_sha256=TARGET_DIGEST,
            mount_ns_device=live.mount_ns[0],
            mount_ns_inode=live.mount_ns[1],
            cgroup_ns_device=live.cgroup_ns[0],
            cgroup_ns_inode=live.cgroup_ns[1],
            resource_view_sha256=live.view,
        )
        supervisor._sql_application_name = _label()
        supervisor._pidfd = 31
        supervisor._process = SimpleNamespace(pid=os.getpid(), args=["local-task-child"])
        original = (supervisor._root_state, supervisor._root_activation_attempted)

        operations = (
            lambda: supervisor.root_activate(b"signed", timeout_s=0),
            lambda: supervisor.root_refresh(timeout_s=0),
            lambda: supervisor.root_stop(timeout_s=0),
            supervisor.close_root_control,
        )
        with (
            patch.object(process_supervisor, "_exit_ready", return_value=False) as ready,
            patch.object(process_supervisor, "_identity", return_value=live) as identity,
            patch.object(process_supervisor, "_matches", return_value=True) as matches,
        ):
            for operation in operations:

                async def foreign() -> None:
                    operation()

                with self.assertRaises(PermissionError):
                    asyncio.run(foreign())
                self.assertEqual(
                    (supervisor._root_state, supervisor._root_activation_attempted), original
                )
                self.assertEqual(supervisor._root_channel.fileno(), 31)
            ready.assert_not_called()
            identity.assert_not_called()
            matches.assert_not_called()

        supervisor.close_root_control()
        self.assertEqual(supervisor._root_channel.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
