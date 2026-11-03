"""Funded handoff before native thread creation; no SQL or provider processes."""

import asyncio
import copy
import os
import pickle
import threading
import unittest
from test.services.test_admission_command_reservation import _command, _observation, _owner
from unittest.mock import patch

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services import delivery_admission as module
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts


class CommandThreadTests(unittest.TestCase):
    def reservation(self, owner=None):
        owner = owner or _owner()
        reservation = _command(owner).reservation
        self.assertIsNotNone(reservation)
        return owner, reservation

    def test_funded_thread_api_exists(self):
        self.assertTrue(callable(getattr(module.CommandReservation, "_launch_thread", None)))
        self.assertTrue(isinstance(getattr(module, "CommandThread", None), type))
        self.assertTrue(isinstance(getattr(module, "CommandThreadStartUncertain", None), type))

    def test_charge_is_started_before_thread_start_and_retained_until_join(self):
        owner, reservation = self.reservation()
        original = threading.Thread.start
        observations = []

        def start(thread):
            observations.append((reservation.state, owner.snapshot(now=1.0).command_started))
            self.assertFalse(reservation.cancel_before_start())
            self.assertFalse(reservation.finish())
            original(thread)

        with patch.object(threading.Thread, "start", start):
            worker = reservation._launch_thread(lambda: 42, clock=lambda: 1.0)
        self.assertEqual(observations, [("started", 1)])
        self.assertEqual(worker.result(timeout=5), 42)
        self.assertEqual(owner.snapshot(now=1.0).committed, ResourceAmounts(1, 1, 0))
        self.assertTrue(worker.join(timeout=5))
        self.assertFalse(worker.join(timeout=0))
        self.assertEqual(owner.snapshot(now=1.0).command_inflight, 0)

    def test_callback_return_is_not_thread_exit(self):
        owner, reservation = self.reservation()
        tail = threading.Event()
        original = module.CommandThread._run

        def run(worker):
            original(worker)
            self.assertTrue(tail.wait(5))

        with patch.object(module.CommandThread, "_run", run):
            worker = reservation._launch_thread(lambda: "done", clock=lambda: 1.0)
        try:
            self.assertEqual(worker.result(timeout=5), "done")
            self.assertFalse(worker.join(timeout=0))
            self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
        finally:
            tail.set()
            self.assertTrue(worker.join(timeout=5))

    def test_join_waits_for_start_attempt_completion_even_after_target_return(self):
        owner, reservation = self.reservation()
        original = threading.Thread.start

        def start(thread):
            original(thread)
            worker = reservation._thread_worker
            self.assertEqual(worker.result(timeout=5), 42)
            self.assertFalse(worker.join(timeout=0))
            self.assertEqual(owner.snapshot(now=1.0).command_started, 1)

        with patch.object(threading.Thread, "start", start):
            worker = reservation._launch_thread(lambda: 42, clock=lambda: 1.0)
        self.assertTrue(worker.join(timeout=5))

    def test_bad_inputs_and_clock_fail_before_physical_start(self):
        owner, reservation = self.reservation()
        with patch.object(threading.Thread, "start") as start:
            for clock in (lambda: float("nan"), lambda: -1.0):
                with self.assertRaises(ValueError):
                    reservation._launch_thread(lambda: 1, clock=clock)
            with self.assertRaises(ValueError):
                reservation._launch_thread(None, clock=lambda: 1.0)
            with self.assertRaises(ValueError):
                reservation._launch_thread(lambda: 1, clock=None)
        start.assert_not_called()
        self.assertEqual(reservation.state, "reserved")
        self.assertTrue(reservation.cancel_before_start())
        self.assertEqual(owner.snapshot(now=1.0).command_inflight, 0)

    def test_join_rejects_invalid_timeouts_without_refund(self):
        owner, reservation = self.reservation()
        worker = reservation._launch_thread(lambda: 1, clock=lambda: 1.0)
        for timeout in (True, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                worker.join(timeout=timeout)
        self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
        self.assertTrue(worker.join(timeout=5))

    def test_stop_invalidate_reconfigure_retain_live_worker(self):
        for action in ("stop", "invalidate", "reconfigure"):
            with self.subTest(action=action):
                owner, reservation = self.reservation()
                entered, release = threading.Event(), threading.Event()

                def target():
                    entered.set()
                    return release.wait(5)

                worker = reservation._launch_thread(target, clock=lambda: 1.0)
                try:
                    self.assertTrue(entered.wait(5))
                    if action == "stop":
                        owner.stop_accepting()
                    elif action == "invalidate":
                        self.assertTrue(owner.invalidate(expected_generation=owner.generation))
                    else:
                        owner.reconfigure(
                            owner.diagnostic(now=1.0).policy, expected_generation=owner.generation
                        )
                    self.assertFalse(worker.join(timeout=0))
                    self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
                    self.assertFalse(reservation.cancel_before_start())
                finally:
                    release.set()
                    self.assertTrue(worker.join(timeout=5))

    def test_start_error_before_entry_retains_reconcilable_charge(self):
        owner, reservation = self.reservation()
        calls = []
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("start unknown")):
            with self.assertRaises(module.CommandThreadStartUncertain) as caught:
                reservation._launch_thread(lambda: calls.append(1), clock=lambda: 1.0)
        worker = caught.exception.worker
        self.assertFalse(worker.join(timeout=0))
        self.assertFalse(reservation.finish())
        self.assertFalse(reservation.cancel_before_start())
        self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
        self.assertIsNone(reservation._launch_thread(lambda: calls.append(2), clock=lambda: 1.0))
        self.assertEqual(calls, [])
        # No entry/exit witness means a sticky charge, never a supported retry/refund.
        self.assertEqual(reservation.state, "started")

    def test_start_error_after_native_start_retains_until_exit(self):
        owner, reservation = self.reservation()
        entered, release = threading.Event(), threading.Event()
        original = threading.Thread.start

        def target():
            entered.set()
            return release.wait(5)

        def start(thread):
            original(thread)
            raise RuntimeError("lost start acknowledgement")

        with patch.object(threading.Thread, "start", start):
            with self.assertRaises(module.CommandThreadStartUncertain) as caught:
                reservation._launch_thread(target, clock=lambda: 1.0)
        worker = caught.exception.worker
        try:
            self.assertTrue(entered.wait(5))
            self.assertFalse(worker.join(timeout=0))
            self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
        finally:
            release.set()
            self.assertTrue(worker.join(timeout=5))

    def test_callback_exception_is_separate_from_physical_retirement(self):
        owner, reservation = self.reservation()
        error = ValueError("owned failure")

        def target():
            raise error

        worker = reservation._launch_thread(target, clock=lambda: 1.0)
        with self.assertRaises(ValueError) as caught:
            worker.result(timeout=5)
        self.assertIs(caught.exception, error)
        self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
        self.assertTrue(worker.join(timeout=5))

    def test_async_wait_cancellation_does_not_cancel_worker_or_result(self):
        owner, reservation = self.reservation()
        entered, release = threading.Event(), threading.Event()

        def target():
            entered.set()
            return release.wait(5)

        worker = reservation._launch_thread(target, clock=lambda: 1.0)
        try:
            self.assertTrue(entered.wait(5))

            async def wait_and_cancel():
                waiting = asyncio.create_task(worker.await_result())
                await asyncio.sleep(0)
                waiting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiting
                self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
                release.set()
                self.assertTrue(await worker.await_result())

            asyncio.run(wait_and_cancel())
        finally:
            release.set()
            self.assertTrue(worker.join(timeout=5))

    def test_entry_rechecks_generation_stop_freshness_and_capacity(self):
        for action in ("generation", "stop", "stale", "capacity"):
            with self.subTest(action=action):
                owner, reservation = self.reservation()
                now, calls = [1.0], []
                original = threading.Thread.start

                def start(thread):
                    if action == "generation":
                        self.assertTrue(owner.invalidate(expected_generation=owner.generation))
                    elif action == "stop":
                        owner.stop_accepting()
                    elif action == "stale":
                        now[0] = 4.0
                    else:
                        self.assertTrue(
                            owner.observe(
                                _observation(at=2.0, memory=0, cpu=0, storage=10),
                                now=2.0,
                                generation=owner.generation,
                            )
                        )
                        now[0] = 2.0
                    original(thread)

                with patch.object(threading.Thread, "start", start):
                    worker = reservation._launch_thread(
                        lambda: calls.append(1), clock=lambda: now[0]
                    )
                with self.assertRaises(StorageAdmissionUnavailable):
                    worker.result(timeout=5)
                self.assertTrue(worker.join(timeout=5))
                self.assertEqual(calls, [])

    def test_pre_dispatch_refusal_never_starts_thread(self):
        for action in ("stale", "stop", "cancel", "generation"):
            with self.subTest(action=action):
                owner, reservation = self.reservation()
                if action == "stop":
                    owner.stop_accepting()
                elif action == "cancel":
                    self.assertTrue(reservation.cancel_before_start())
                elif action == "generation":
                    owner.invalidate(expected_generation=owner.generation)
                with patch.object(threading.Thread, "start") as start:
                    self.assertIsNone(
                        reservation._launch_thread(
                            lambda: self.fail("unfunded callback"),
                            clock=lambda: 4.0 if action == "stale" else 1.0,
                        )
                    )
                start.assert_not_called()
                reservation.cancel_before_start()

    def test_handle_and_reservation_copies_cannot_retire_or_launch(self):
        owner, reservation = self.reservation()
        worker = reservation._launch_thread(lambda: 1, clock=lambda: 1.0)
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.assertRaises(TypeError):
                operation(worker)
        self.assertIsNone(reservation._launch_thread(lambda: 2, clock=lambda: 1.0))
        self.assertTrue(worker.join(timeout=5))
        self.assertEqual(owner.snapshot(now=1.0).command_inflight, 0)

    def test_foreign_process_cannot_join_or_read_result(self):
        owner, reservation = self.reservation()
        worker = reservation._launch_thread(lambda: 1, clock=lambda: 1.0)
        pid = os.getpid()
        with patch.object(module.os, "getpid", return_value=pid + 1):
            self.assertFalse(worker.join(timeout=0))
            with self.assertRaises(StorageAdmissionUnavailable):
                worker.result(timeout=0)
        self.assertTrue(worker.join(timeout=5))
        self.assertEqual(owner.snapshot(now=1.0).command_inflight, 0)

    def test_worker_cannot_manually_finish_or_join_itself(self):
        owner, reservation = self.reservation()
        release, handles = threading.Event(), []

        def target():
            self.assertTrue(release.wait(5))
            return reservation.finish(), handles[0].join(timeout=0)

        worker = reservation._launch_thread(target, clock=lambda: 1.0)
        handles.append(worker)
        release.set()
        self.assertEqual(worker.result(timeout=5), (False, False))
        self.assertTrue(worker.join(timeout=5))

    def test_more_than_twenty_workers_follow_resources_not_a_count_limit(self):
        owner = _owner(memory=25, cpu=25, storage=0)
        release, workers = threading.Event(), []
        try:
            for _ in range(25):
                _, reservation = self.reservation(owner)
                worker = reservation._launch_thread(lambda: release.wait(5), clock=lambda: 1.0)
                self.assertIsNotNone(worker)
                workers.append(worker)
            self.assertIsNone(_command(owner).reservation)
            self.assertEqual(owner.snapshot(now=1.0).command_started, 25)
            self.assertEqual(owner.snapshot(now=1.0).control_storage_slots, 0)
        finally:
            release.set()
            for worker in workers:
                self.assertTrue(worker.join(timeout=5))
        self.assertEqual(owner.snapshot(now=1.0).committed, ResourceAmounts())


if __name__ == "__main__":
    unittest.main()
