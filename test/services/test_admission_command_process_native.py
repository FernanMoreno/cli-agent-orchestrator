"""Real owned Linux children; declared compute costs are synthetic, no model/SQL load."""

import os
import signal
import sys
import unittest
from pathlib import Path
from test.services.test_admission_command_reservation import _command, _owner
from test.services.test_resource_process_supervisor import CHILD, STORE, TARGET
from unittest.mock import patch

import psutil

from cli_agent_orchestrator.models.sql_process_origin import RESOURCE_BINDING_FD_ENV
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_ROOT_GENESIS_ENV,
    SQL_STARTUP_PERMISSION_ENV,
)
from cli_agent_orchestrator.services import delivery_admission as admission
from cli_agent_orchestrator.services.resource_process_supervisor import (
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)

NATIVE_CHILD = (
    "import os\n"
    "from pathlib import Path\n"
    "import cli_agent_orchestrator\n"
    "expected = Path(os.environ['CAOS_FUNDED_PROCESS_REPO'])\n"
    "assert Path.cwd() == expected\n"
    "assert Path(cli_agent_orchestrator.__file__).parents[2] == expected\n" + CHILD
)


class CommandProcessNativeTests(unittest.TestCase):
    def test_process_api_exists(self):
        self.assertTrue(callable(getattr(admission.CommandReservation, "_launch_process", None)))
        self.assertTrue(isinstance(getattr(admission, "CommandProcess", None), type))

    def launch(self, mode="ok"):
        import cli_agent_orchestrator

        repository = Path(__file__).parents[2]
        self.assertEqual(Path.cwd(), repository)
        self.assertEqual(Path(cli_agent_orchestrator.__file__).parents[2], repository)
        self.assertGreaterEqual(psutil.virtual_memory().available, 6144 * 1024 * 1024)
        owner = _owner()
        reservation = _command(owner).reservation
        self.assertIsNotNone(reservation)
        original = ResourceProcessSupervisor.launch
        observed = []

        def launch(*args, **kwargs):
            observed.append(reservation.state)
            self.assertFalse(reservation.finish())
            self.assertFalse(reservation.cancel_before_start())
            return original(*args, **kwargs)

        environment = dict(os.environ)
        for name in (
            SQL_STARTUP_PERMISSION_ENV,
            SQL_BROKER_FD_ENV,
            RESOURCE_BINDING_FD_ENV,
            SQL_ROOT_GENESIS_ENV,
        ):
            environment.pop(name, None)
        environment["CAOS_EXIT_TEST_MODE"] = mode
        environment["CAOS_FUNDED_PROCESS_REPO"] = str(repository)
        environment["PYTHONPATH"] = str(repository / "src") + os.pathsep + str(repository)
        with patch.object(ResourceProcessSupervisor, "launch", side_effect=launch):
            worker = reservation._launch_process(
                [sys.executable, "-c", NATIVE_CHILD],
                expected_store_id=STORE,
                expected_target_identity_sha256=TARGET,
                env=environment,
                clock=lambda: 1.0,
            )
        self.assertEqual(observed, ["started"])
        self.assertIsNotNone(worker)
        return owner, reservation, worker

    def cleanup(self, worker):
        if not worker.has_native_owner:
            return
        # No global kill or alternate reaper: only the exact owned pidfd/Popen.
        if worker.cleanup(timeout_s=0):
            return
        worker.send_signal(signal.SIGTERM)
        if not worker.cleanup(timeout_s=20):
            worker.send_signal(signal.SIGKILL)
            self.assertTrue(worker.cleanup(timeout_s=20))

    def test_real_child_charge_survives_live_wait_and_stop_until_join(self):
        owner, reservation, worker = self.launch()
        try:
            binding = worker.receive_binding(timeout_s=30)
            self.assertEqual(binding.store_id, STORE)
            self.assertFalse(worker.join())
            self.assertFalse(worker.join(timeout_s=0))
            self.assertFalse(reservation.finish())
            owner.stop_accepting()
            self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
            self.assertEqual(owner.snapshot(now=1.0).committed, reservation.footprint)
            worker.send_signal(signal.SIGTERM)
            self.assertTrue(worker.join(timeout_s=20))
            self.assertEqual(owner.snapshot(now=1.0).command_inflight, 0)
            self.assertFalse(worker.join(timeout_s=0))
        finally:
            self.cleanup(worker)

    def test_real_bad_binding_exit_does_not_refund_or_mint_retirement(self):
        owner, reservation, worker = self.launch("store")
        try:
            with self.assertRaises(ResourceProcessUnproven):
                worker.receive_binding(timeout_s=30)
            worker.send_signal(signal.SIGTERM)
            self.assertFalse(worker.join(timeout_s=20))
            self.cleanup(worker)
            self.assertFalse(worker.join(timeout_s=0))
            self.assertFalse(reservation.finish())
            self.assertFalse(reservation.cancel_before_start())
            self.assertEqual(owner.snapshot(now=1.0).command_started, 1)
            self.assertEqual(owner.snapshot(now=1.0).committed, reservation.footprint)
        finally:
            self.cleanup(worker)


if __name__ == "__main__":
    unittest.main()
