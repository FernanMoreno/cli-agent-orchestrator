"""Funded command ownership of one exact child process."""

import asyncio
import copy
import os
import pickle
import subprocess
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from test.services.test_admission_command_reservation import _command, _owner
from unittest.mock import patch
from uuid import uuid4

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.resource_generation import _issue_process_exit_observation
from cli_agent_orchestrator.services import delivery_admission
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    ResourceAmounts,
)
from cli_agent_orchestrator.services.resource_process_supervisor import (
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)

TARGET_DIGEST = "a" * 64
_UNSET = object()


class _FakeChannel:
    def __init__(self) -> None:
        self.closed = False

    def fileno(self) -> int:
        return -1 if self.closed else 31

    def close(self) -> None:
        self.closed = True


def _binding(*, store_id: str | None = None, digest: str = TARGET_DIGEST) -> ResourceBinding:
    return ResourceBinding(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=os.getpid(),
        process_started_at=1.0,
        store_id=store_id or str(uuid4()),
        target_identity_sha256=digest,
        mount_ns_device=0,
        mount_ns_inode=1,
        cgroup_ns_device=0,
        cgroup_ns_inode=1,
        resource_view_sha256="b" * 64,
    )


def _label() -> str:
    return "caos-sql-v1-" + uuid4().hex


def _observation(
    binding: ResourceBinding,
    label: str,
):
    return _issue_process_exit_observation(
        binding,
        boot_id=str(uuid4()),
        start_time_ticks=1,
        pid_namespace=(1, 1),
        exit_kind="exited",
        exit_status=0,
        sql_application_name=label,
    )


def _supervisor() -> ResourceProcessSupervisor:
    value = object.__new__(ResourceProcessSupervisor)
    value._owner_pid = os.getpid()
    value._owner_thread = threading.current_thread()
    value._binding = None
    value._binding_failed = False
    value._sql_application_name = None
    value._observation = None
    value._closed = False
    value._exited = False
    value._pidfd = 31
    value._channel = _FakeChannel()
    value._broker_channel = None
    return value


class CommandProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = _binding()
        self.label = _label()
        self.owner = _owner()
        self.reservation = _command(self.owner).reservation
        self.assertIsNotNone(self.reservation)

    def launch(self, reservation=None, *, env=None, clock=lambda: 1.0):
        selected = reservation or self.reservation
        return selected._launch_process(
            ["/unexecuted/test-child"],
            expected_store_id=self.binding.store_id,
            expected_target_identity_sha256=TARGET_DIGEST,
            env=env,
            clock=clock,
        )

    def patched_supervisor(
        self,
        *,
        binding: ResourceBinding | None = None,
        label: str | None = None,
        observation: object = _UNSET,
        published_observation: object = _UNSET,
        launch_error: BaseException | None = None,
        wait_error: BaseException | None = None,
        close_error: BaseException | None = None,
        binding_failed: bool = False,
    ):
        supervisor = _supervisor()
        supervisor._binding_failed = binding_failed
        chosen_binding = self.binding if binding is None else binding
        chosen_label = self.label if label is None else label
        chosen_observation = (
            _observation(chosen_binding, chosen_label) if observation is _UNSET else observation
        )
        calls: list[tuple[object, ...]] = []

        def launch(cls, argv, **kwargs):
            calls.append((tuple(argv), kwargs))
            if launch_error is not None:
                raise launch_error
            return supervisor

        def receive(owner, *, timeout_s=30):
            owner._binding = chosen_binding
            owner._sql_application_name = chosen_label
            return chosen_binding

        def wait(owner, *, timeout_s=None):
            owner._last_wait_timeout = timeout_s
            if wait_error is not None:
                raise wait_error
            owner._observation = (
                chosen_observation if published_observation is _UNSET else published_observation
            )
            owner._exited = True
            return chosen_observation

        def close(owner):
            if close_error is not None:
                raise close_error
            owner._channel.close()
            owner._pidfd = None
            owner._closed = True

        def send_signal(owner, signum):
            calls.append(("signal", signum))

        stack = (
            patch.object(ResourceProcessSupervisor, "launch", classmethod(launch)),
            patch.object(ResourceProcessSupervisor, "receive_binding", receive),
            patch.object(ResourceProcessSupervisor, "wait", wait),
            patch.object(ResourceProcessSupervisor, "close", close),
            patch.object(ResourceProcessSupervisor, "send_signal", send_signal),
        )
        return supervisor, calls, stack

    def test_import_origin_and_api(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expected = root / "src/cli_agent_orchestrator/services/delivery_admission.py"
        self.assertEqual(Path(delivery_admission.__file__).resolve(), expected.resolve())
        self.assertTrue(
            callable(getattr(delivery_admission.CommandReservation, "_launch_process", None))
        )
        self.assertIsInstance(getattr(delivery_admission, "CommandProcess", None), type)
        self.assertIsInstance(
            getattr(delivery_admission, "CommandProcessStartUncertain", None), type
        )
        self.assertFalse(hasattr(delivery_admission.CommandProcess, "supervisor"))
        self.assertTrue(callable(getattr(delivery_admission.CommandProcess, "cleanup", None)))
        self.assertTrue(hasattr(delivery_admission.CommandProcess, "has_native_owner"))

    def test_charge_precedes_supervisor_launch_and_only_exact_positive_exit_releases(self) -> None:
        supervisor, calls, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:

            def launch(cls, argv, **kwargs):
                self.assertEqual(self.reservation.state, "started")
                self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
                self.assertFalse(self.owner._guard._is_owned())
                self.assertFalse(kwargs["first_sql_grant"])
                self.assertFalse(kwargs["sql_broker"])
                self.assertEqual(tuple(argv), ("/unexecuted/test-child",))
                calls.append((tuple(argv), kwargs))
                return supervisor

            with patch.object(ResourceProcessSupervisor, "launch", classmethod(launch)):
                worker = self.launch()
            self.assertIs(worker._supervisor, supervisor)
            self.assertFalse(self.reservation.cancel_before_start())
            self.assertFalse(self.reservation.finish())
            self.assertFalse(worker.join(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).committed, ResourceAmounts(1, 1, 0))
            self.assertIs(worker.receive_binding(), self.binding)
            self.assertTrue(worker.join(timeout_s=1))
            self.assertTrue(supervisor._closed)
            self.assertEqual(self.owner.snapshot(now=1.0).command_inflight, 0)
            self.assertEqual(self.owner.snapshot(now=1.0).committed, ResourceAmounts())
            self.assertFalse(worker.join(timeout_s=0))
            self.assertEqual(len(calls), 1)

    def test_uncertain_start_without_owner_retains_charge_and_never_retries(self) -> None:
        original = OSError("popen acknowledgement lost")
        supervisor, calls, stack = self.patched_supervisor(launch_error=original)
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            with self.assertRaises(delivery_admission.CommandProcessStartUncertain) as caught:
                self.launch()
            worker = caught.exception.worker
            self.assertIs(caught.exception.cause, original)
            self.assertIs(caught.exception.__cause__, original)
            self.assertIsNone(worker._supervisor)
            self.assertFalse(worker.has_native_owner)
            self.assertFalse(worker.cleanup(timeout_s=0))
            self.assertFalse(self.reservation.finish())
            self.assertFalse(self.reservation.cancel_before_start())
            self.assertIsNone(self.launch())
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            self.assertEqual(self.owner.snapshot(now=1.0).committed, ResourceAmounts(1, 1, 0))

    def test_uncertain_start_keeps_exact_owner_attached_to_original_failure(self) -> None:
        original = RuntimeError("failure after Popen")
        supervisor, calls, stack = self.patched_supervisor(launch_error=original)
        original.resource_process_owner = supervisor
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            with self.assertRaises(delivery_admission.CommandProcessStartUncertain) as caught:
                self.launch()
            self.assertIs(caught.exception.cause, original)
            self.assertIs(caught.exception.worker._supervisor, supervisor)
            self.assertTrue(caught.exception.worker.has_native_owner)
            self.assertEqual(len(calls), 1)
            self.assertFalse(caught.exception.worker.join(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_interrupted_attach_retains_returned_supervisor_and_primary_cause(self) -> None:
        supervisor, calls, stack = self.patched_supervisor()
        original_attach = delivery_admission.CommandProcess._attach_supervisor
        failure = KeyboardInterrupt("supervisor attach interrupted")
        attempts = 0

        def interrupted_attach(worker, exact_supervisor):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise failure
            return original_attach(worker, exact_supervisor)

        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            with patch.object(
                delivery_admission.CommandProcess, "_attach_supervisor", interrupted_attach
            ):
                with self.assertRaises(delivery_admission.CommandProcessStartUncertain) as caught:
                    self.launch()
            worker = caught.exception.worker
            self.assertIs(caught.exception.cause, failure)
            self.assertIs(caught.exception.__cause__, failure)
            self.assertIs(worker._supervisor, supervisor)
            self.assertTrue(worker.has_native_owner)
            self.assertFalse(self.reservation.finish())
            self.assertFalse(self.reservation.cancel_before_start())
            self.assertIsNone(self.launch())
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_join_default_is_nonblocking_and_timeout_keeps_same_owner_retryable(self) -> None:
        supervisor, _, stack = self.patched_supervisor(
            wait_error=subprocess.TimeoutExpired(["child"], 0)
        )
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            self.assertFalse(worker.join())
            self.assertEqual(supervisor._last_wait_timeout, 0)
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            recovered = _observation(self.binding, self.label)

            def later_wait(owner, *, timeout_s=None):
                owner._last_wait_timeout = timeout_s
                owner._observation = recovered
                owner._exited = True
                return recovered

            def later_close(owner):
                owner._channel.close()
                owner._pidfd = None
                owner._closed = True

            with (
                patch.object(ResourceProcessSupervisor, "wait", later_wait),
                patch.object(ResourceProcessSupervisor, "close", later_close),
            ):
                self.assertTrue(worker.join(timeout_s=20))
            self.assertEqual(supervisor._last_wait_timeout, 20)
            self.assertEqual(self.owner.snapshot(now=1.0).command_inflight, 0)

    def test_failed_wait_is_terminal_even_if_a_later_wait_returns_an_exit(self) -> None:
        original = OSError("wait status was unavailable")
        supervisor, _, stack = self.patched_supervisor(wait_error=original)
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            with self.assertRaises(OSError) as caught:
                worker.join(timeout_s=0)
            self.assertIs(caught.exception, original)
            recovered = _observation(self.binding, self.label)

            def later_wait(owner, *, timeout_s=None):
                owner._observation = recovered
                owner._exited = True
                return recovered

            with patch.object(ResourceProcessSupervisor, "wait", later_wait):
                self.assertFalse(worker.join(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            self.assertFalse(supervisor._closed)

    def test_close_failure_is_terminal_even_if_a_later_close_succeeds(self) -> None:
        original = OSError("native close uncertain")
        supervisor, _, stack = self.patched_supervisor(close_error=original)
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            with self.assertRaises(OSError) as caught:
                worker.join(timeout_s=0)
            self.assertIs(caught.exception, original)

            def later_close(owner):
                owner._channel.close()
                owner._pidfd = None
                owner._closed = True

            with patch.object(ResourceProcessSupervisor, "close", later_close):
                self.assertFalse(worker.join(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            self.assertFalse(supervisor._closed)

    def test_timeout_can_retry_same_supervisor_and_retire_after_later_positive_exit(self) -> None:
        supervisor, calls, stack = self.patched_supervisor(
            wait_error=subprocess.TimeoutExpired(["child"], 0)
        )
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            self.assertFalse(worker.join(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            recovered = _observation(self.binding, self.label)

            def later_wait(owner, *, timeout_s=None):
                owner._observation = recovered
                owner._exited = True
                return recovered

            def later_close(owner):
                owner._channel.close()
                owner._pidfd = None
                owner._closed = True

            with (
                patch.object(ResourceProcessSupervisor, "wait", later_wait),
                patch.object(ResourceProcessSupervisor, "close", later_close),
            ):
                self.assertTrue(worker.join(timeout_s=0))
            self.assertEqual(len(calls), 1)
            self.assertTrue(supervisor._closed)
            self.assertEqual(self.owner.snapshot(now=1.0).command_inflight, 0)

    def test_missing_mismatched_and_postclose_proof_failures_are_sticky(self) -> None:
        cases = (
            ("missing", {"observation": None}),
            ("invalid", {"observation": object()}),
            ("binding_mismatch", {"observation": _observation(replace(self.binding), self.label)}),
            ("postclose", {}),
        )
        for name, options in cases:
            with self.subTest(name=name):
                self.owner = _owner()
                self.reservation = _command(self.owner).reservation
                supervisor, _, stack = self.patched_supervisor(**options)
                with stack[0], stack[1], stack[2], stack[3], stack[4]:
                    worker = self.launch()
                    worker.receive_binding()
                    wait_count = 0
                    close_count = 0
                    initial_observation = options.get("observation", _UNSET)
                    if initial_observation is _UNSET or name == "postclose":
                        initial_observation = _observation(self.binding, self.label)

                    def first_wait(owner, *, timeout_s=None):
                        nonlocal wait_count
                        wait_count += 1
                        owner._observation = initial_observation
                        owner._exited = True
                        return initial_observation

                    def first_close(owner):
                        nonlocal close_count
                        close_count += 1
                        if name == "postclose":
                            owner._pidfd = None
                            owner._closed = True
                            return
                        owner._channel.close()
                        owner._pidfd = None
                        owner._closed = True

                    with (
                        patch.object(ResourceProcessSupervisor, "wait", first_wait),
                        patch.object(ResourceProcessSupervisor, "close", first_close),
                    ):
                        self.assertFalse(worker.join(timeout_s=0))

                    recovered = _observation(self.binding, self.label)

                    def later_wait(owner, *, timeout_s=None):
                        nonlocal wait_count
                        wait_count += 1
                        owner._observation = recovered
                        owner._exited = True
                        return recovered

                    def later_close(owner):
                        nonlocal close_count
                        close_count += 1
                        owner._channel.close()
                        owner._pidfd = None
                        owner._closed = True

                    with (
                        patch.object(ResourceProcessSupervisor, "wait", later_wait),
                        patch.object(ResourceProcessSupervisor, "close", later_close),
                    ):
                        self.assertFalse(worker.join(timeout_s=0))
                    self.assertEqual(wait_count, 1)
                    self.assertEqual(close_count, 1 if name == "postclose" else 0)
                    self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
                    self.assertEqual(
                        self.owner.snapshot(now=1.0).committed, ResourceAmounts(1, 1, 0)
                    )
                    self.assertEqual(supervisor._closed, name == "postclose")
                    self.assertFalse(supervisor._channel.closed)

    def test_receive_preflights_affinity_and_binding_failure_before_one_shot_read(self) -> None:
        for field in ("_binding_failed", "_binding", "_owner_pid", "_owner_thread"):
            with self.subTest(field=field):
                self.owner = _owner()
                self.reservation = _command(self.owner).reservation
                supervisor, _, stack = self.patched_supervisor()
                with stack[0], stack[1], stack[2], stack[3], stack[4]:
                    worker = self.launch()
                    if field == "_binding_failed":
                        supervisor._binding_failed = True
                    elif field == "_binding":
                        supervisor._binding = self.binding
                        supervisor._sql_application_name = self.label
                    elif field == "_owner_pid":
                        supervisor._owner_pid += 1
                    else:
                        supervisor._owner_thread = threading.Thread()
                    reads: list[int] = []

                    def receive(owner, *, timeout_s=30):
                        reads.append(1)
                        owner._binding = self.binding
                        owner._sql_application_name = self.label
                        return self.binding

                    with patch.object(ResourceProcessSupervisor, "receive_binding", receive):
                        with self.assertRaises(ResourceProcessUnproven):
                            worker.receive_binding()
                    self.assertEqual(reads, [])
                    self.assertFalse(worker._binding_attempted)
                    self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_poisoned_binding_can_still_signal_the_exact_child_for_cleanup(self) -> None:
        invalid_binding = _binding(digest="c" * 64)
        supervisor, calls, stack = self.patched_supervisor(binding=invalid_binding)
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            with self.assertRaises(ResourceProcessUnproven):
                worker.receive_binding()
            worker.send_signal(15)
            self.assertIn(("signal", 15), calls)
            self.assertFalse(worker.join(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_cleanup_closes_exact_owner_but_never_releases_command_charge(self) -> None:
        supervisor, _, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            self.assertTrue(worker.has_native_owner)
            self.assertTrue(worker.cleanup())
            self.assertEqual(supervisor._last_wait_timeout, 0)
            self.assertTrue(supervisor._closed)
            self.assertTrue(worker.has_native_owner)
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            self.assertEqual(self.owner.snapshot(now=1.0).committed, ResourceAmounts(1, 1, 0))
            self.assertFalse(worker.join(timeout_s=0))

    def test_cleanup_timeout_allows_same_owner_signal_then_cleanup_retry(self) -> None:
        supervisor, calls, stack = self.patched_supervisor(
            wait_error=subprocess.TimeoutExpired(["child"], 0)
        )
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            self.assertFalse(worker.cleanup(timeout_s=0))
            self.assertFalse(supervisor._closed)
            worker.send_signal(15)
            self.assertIn(("signal", 15), calls)
            recovered = _observation(self.binding, self.label)

            def later_wait(owner, *, timeout_s=None):
                owner._last_wait_timeout = timeout_s
                owner._observation = recovered
                owner._exited = True
                return recovered

            def later_close(owner):
                owner._channel.close()
                owner._pidfd = None
                owner._closed = True

            with (
                patch.object(ResourceProcessSupervisor, "wait", later_wait),
                patch.object(ResourceProcessSupervisor, "close", later_close),
            ):
                self.assertTrue(worker.cleanup(timeout_s=20))
            self.assertEqual(supervisor._last_wait_timeout, 20)
            self.assertTrue(supervisor._closed)
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            self.assertEqual(self.owner.snapshot(now=1.0).committed, ResourceAmounts(1, 1, 0))

    def test_cleanup_rejects_unbounded_or_invalid_timeout_before_native_effects(self) -> None:
        supervisor, _, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            for timeout in (True, -1, float("nan"), float("inf"), 10**1000):
                with self.subTest(timeout=timeout):
                    with self.assertRaises(ValueError):
                        worker.cleanup(timeout_s=timeout)
                    self.assertFalse(supervisor._exited)
                    self.assertFalse(supervisor._closed)
                    self.assertFalse(hasattr(supervisor, "_last_wait_timeout"))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            self.assertTrue(worker.cleanup(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_cleanup_rejects_a_different_async_task_before_native_effects(self) -> None:
        async def scenario() -> None:
            supervisor, _, stack = self.patched_supervisor()
            with stack[0], stack[1], stack[2], stack[3], stack[4]:
                worker = self.launch()

                async def cleanup_from_another_task() -> BaseException | None:
                    try:
                        worker.cleanup(timeout_s=0)
                    except BaseException as error:
                        return error
                    return None

                error = await asyncio.create_task(cleanup_from_another_task())
                self.assertIsInstance(error, delivery_admission.StorageAdmissionUnavailable)
                self.assertFalse(supervisor._exited)
                self.assertFalse(supervisor._closed)
                self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
                self.assertTrue(worker.cleanup(timeout_s=0))
                self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

        asyncio.run(scenario())

    def test_reentrant_join_does_not_make_a_second_wait_or_close(self) -> None:
        supervisor, _, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            wait_count = 0
            nested_results: list[bool] = []
            observation = _observation(self.binding, self.label)

            def wait(owner, *, timeout_s=None):
                nonlocal wait_count
                wait_count += 1
                if wait_count == 1:
                    nested_results.append(worker.join(timeout_s=0))
                owner._observation = observation
                owner._exited = True
                return observation

            with patch.object(ResourceProcessSupervisor, "wait", wait):
                self.assertTrue(worker.join(timeout_s=0))
            self.assertEqual(wait_count, 1)
            self.assertEqual(nested_results, [False])
            self.assertTrue(supervisor._closed)
            self.assertEqual(self.owner.snapshot(now=1.0).command_inflight, 0)

    def test_misbound_supervisor_is_retained_but_cannot_retire(self) -> None:
        for field in ("_owner_pid", "_owner_thread"):
            with self.subTest(field=field):
                self.owner = _owner()
                self.reservation = _command(self.owner).reservation
                supervisor, _, stack = self.patched_supervisor()
                if field == "_owner_pid":
                    supervisor._owner_pid += 1
                else:
                    supervisor._owner_thread = threading.Thread()
                with stack[0], stack[1], stack[2], stack[3], stack[4]:
                    with self.assertRaises(
                        delivery_admission.CommandProcessStartUncertain
                    ) as caught:
                        self.launch()
                    worker = caught.exception.worker
                    self.assertIs(worker._supervisor, supervisor)
                    self.assertFalse(worker._supervisor_context_valid)
                    self.assertFalse(worker.join(timeout_s=0))
                    self.assertFalse(self.reservation.finish())
                    self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_missing_binding_timeout_missing_or_invalid_exit_and_close_failure_retain(self) -> None:
        cases = (
            ("no_binding", {}, False),
            (
                "timeout",
                {"wait_error": subprocess.TimeoutExpired(["child"], 0)},
                True,
            ),
            ("missing_exit", {"observation": None}, True),
            ("invalid_exit", {"observation": object()}, True),
            ("close_failure", {"close_error": OSError("close unclear")}, True),
        )
        for name, options, receive in cases:
            with self.subTest(name=name):
                self.owner = _owner()
                self.reservation = _command(self.owner).reservation
                self.assertIsNotNone(self.reservation)
                supervisor, _, stack = self.patched_supervisor(**options)
                with stack[0], stack[1], stack[2], stack[3], stack[4]:
                    worker = self.launch()
                    if receive:
                        worker.receive_binding()
                    if name == "close_failure":
                        with self.assertRaisesRegex(OSError, "close unclear"):
                            worker.join(timeout_s=0)
                    else:
                        self.assertFalse(worker.join(timeout_s=0))
                    self.assertFalse(self.reservation.finish())
                    self.assertFalse(self.reservation.cancel_before_start())
                    self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
                    self.assertEqual(
                        self.owner.snapshot(now=1.0).committed, ResourceAmounts(1, 1, 0)
                    )
                    self.assertFalse(supervisor._closed)

    def test_invalid_join_timeouts_fail_before_wait_and_keep_the_started_charge(self) -> None:
        supervisor, _, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            worker.receive_binding()
            for timeout in (True, -1, float("nan"), float("inf"), 10**1000):
                with self.subTest(timeout=timeout):
                    with self.assertRaises(ValueError):
                        worker.join(timeout_s=timeout)
                    self.assertFalse(supervisor._exited)
                    self.assertFalse(supervisor._closed)
                    self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_stop_invalidate_reconfigure_and_pressure_keep_started_process_charge(self) -> None:
        actions = ("stop", "invalidate", "reconfigure", "pressure")
        for action in actions:
            with self.subTest(action=action):
                self.owner = _owner()
                self.reservation = _command(self.owner).reservation
                supervisor, _, stack = self.patched_supervisor()
                with stack[0], stack[1], stack[2], stack[3], stack[4]:
                    worker = self.launch()
                    worker.receive_binding()
                    generation = self.owner.generation
                    if action == "stop":
                        self.owner.stop_accepting()
                    elif action == "invalidate":
                        self.assertTrue(self.owner.invalidate(expected_generation=generation))
                    elif action == "reconfigure":
                        policy = AdmissionPolicy(
                            memory_per_delivery_bytes=5,
                            cpu_per_delivery_millicores=4,
                            memory_reserve_percent=0,
                            cpu_reserve_percent=0,
                            max_sample_age_s=2.0,
                        )
                        self.assertEqual(
                            self.owner.reconfigure(policy, expected_generation=generation),
                            generation + 1,
                        )
                    else:
                        self.assertTrue(
                            self.owner.observe(
                                CapacityObservation(
                                    observed_at=2.0,
                                    memory_available_bytes=0,
                                    memory_limit_bytes=40,
                                    cpu_available_millicores=0,
                                    cpu_limit_millicores=40,
                                    storage_available_slots=0,
                                ),
                                now=2.0,
                                generation=generation,
                            )
                        )
                        self.assertEqual(
                            self.owner.snapshot(now=2.0).resource_debt,
                            ResourceAmounts(1, 1, 0),
                        )
                    self.assertEqual(self.owner.snapshot(now=2.0).command_started, 1)
                    self.assertEqual(
                        self.owner.snapshot(now=2.0).committed, ResourceAmounts(1, 1, 0)
                    )
                    self.assertTrue(worker.join(timeout_s=1))
                    self.assertEqual(self.owner.snapshot(now=2.0).command_inflight, 0)
                    self.assertTrue(supervisor._closed)

    def test_exit_must_match_exact_binding_and_authenticated_label(self) -> None:
        equivalent_binding = replace(self.binding)
        wrong_binding_observation = _observation(equivalent_binding, self.label)
        mismatched_label_observation = _observation(self.binding, _label())
        cases = (
            ("binding", {"observation": wrong_binding_observation}),
            ("label", {"observation": mismatched_label_observation}),
            (
                "supervisor_observation_identity",
                {
                    "observation": _observation(self.binding, self.label),
                    "published_observation": _observation(self.binding, self.label),
                },
            ),
            ("binding_failed", {"binding_failed": True}),
        )
        for name, options in cases:
            with self.subTest(name=name):
                self.owner = _owner()
                self.reservation = _command(self.owner).reservation
                supervisor, _, stack = self.patched_supervisor(**options)
                with stack[0], stack[1], stack[2], stack[3], stack[4]:
                    worker = self.launch()
                    if name == "binding_failed":
                        with self.assertRaises(ResourceProcessUnproven):
                            worker.receive_binding()
                    else:
                        worker.receive_binding()
                    self.assertFalse(worker.join(timeout_s=0))
                    self.assertFalse(supervisor._closed)
                    self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_untrusted_binding_is_terminal_for_the_process_handle(self) -> None:
        invalid_binding = _binding(digest="c" * 64)
        supervisor, _, stack = self.patched_supervisor(binding=invalid_binding)
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            with self.assertRaises(ResourceProcessUnproven):
                worker.receive_binding()
            with self.assertRaises(ResourceProcessUnproven):
                worker.receive_binding()
            self.assertFalse(worker.join(timeout_s=0))
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)
            self.assertFalse(supervisor._closed)

    def test_start_rejects_root_basis_other_owners_and_invalid_input_before_launch(self) -> None:
        supervisor, calls, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            object.__setattr__(self.reservation, "_startup_basis", object())
            self.assertIsNone(self.launch())
            self.assertEqual(self.reservation.state, "reserved")
            object.__setattr__(self.reservation, "_startup_basis", None)
            object.__setattr__(self.reservation, "_actor_thread", object())
            self.assertIsNone(self.launch())
            object.__setattr__(self.reservation, "_actor_thread", None)
            with self.assertRaises(ValueError):
                self.reservation._launch_process(
                    [""],
                    expected_store_id=self.binding.store_id,
                    expected_target_identity_sha256=TARGET_DIGEST,
                    clock=lambda: 1.0,
                )
            with self.assertRaises(ValueError):
                self.reservation._launch_process(
                    ["child"],
                    expected_store_id=self.binding.store_id,
                    expected_target_identity_sha256=TARGET_DIGEST,
                    clock=lambda: float("nan"),
                )
            self.assertEqual(calls, [])
            self.assertEqual(self.reservation.state, "reserved")
            self.assertTrue(self.reservation.cancel_before_start())

    def test_existing_thread_and_process_handoffs_cannot_be_reused(self) -> None:
        thread_owner = _owner()
        thread_reservation = _command(thread_owner).reservation
        self.assertIsNotNone(thread_reservation)
        thread_worker = thread_reservation._launch_thread(lambda: None, clock=lambda: 1.0)
        self.assertIsNotNone(thread_worker)
        self.assertIsNone(self.launch(thread_reservation))
        self.assertTrue(thread_worker.join(timeout=5))

        supervisor, calls, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            self.assertIsNone(self.launch())
            self.assertEqual(len(calls), 1)
            self.assertTrue(worker.has_native_owner)
            self.assertEqual(self.owner.snapshot(now=1.0).command_started, 1)

    def test_send_signal_and_process_handle_are_bound_to_pid_thread_and_task(self) -> None:
        supervisor, calls, stack = self.patched_supervisor()
        with stack[0], stack[1], stack[2], stack[3], stack[4]:
            worker = self.launch()
            self.assertTrue(worker.has_native_owner)
            errors: list[BaseException] = []

            def wrong_thread() -> None:
                try:
                    worker.send_signal(15)
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=wrong_thread)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], delivery_admission.StorageAdmissionUnavailable)
            worker.send_signal(15)
            self.assertIn(("signal", 15), calls)

            with self.assertRaises(TypeError):
                copy.copy(worker)
            with self.assertRaises(TypeError):
                copy.deepcopy(worker)
            with self.assertRaises(TypeError):
                pickle.dumps(worker)

    def test_async_task_identity_is_part_of_process_owner(self) -> None:
        async def scenario() -> None:
            supervisor, _, stack = self.patched_supervisor()
            with stack[0], stack[1], stack[2], stack[3], stack[4]:
                worker = self.launch()

                async def read_from_another_task() -> BaseException | None:
                    try:
                        _ = worker.has_native_owner
                    except BaseException as error:
                        return error
                    return None

                task_error = await asyncio.create_task(read_from_another_task())
                self.assertIsInstance(task_error, delivery_admission.StorageAdmissionUnavailable)
                self.assertTrue(worker.has_native_owner)
                worker.receive_binding()
                self.assertTrue(worker.join(timeout_s=1))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
