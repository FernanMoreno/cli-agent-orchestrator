from __future__ import annotations

import errno
import hashlib
import importlib.util
import inspect
import io
import json
import os
import signal
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from test.live_provider import bounded_process_controller as controller
from unittest.mock import patch

_SOURCE_CONTROLLER = controller
_CONTROLLER_SOURCE_PATH = Path(controller.__file__).resolve()


def _fake_proc_files(
    pid: int,
    *,
    ppid: int,
    pgrp: int,
    session: int,
    starttime: int,
    comm: bytes = b"worker",
    name: bytes = b"worker",
    uid_token: bytes | None = None,
) -> dict[Path, bytes]:
    fields = [b"S", str(ppid).encode("ascii"), str(pgrp).encode("ascii")]
    fields.extend([str(session).encode("ascii"), b"0"])
    fields.extend([b"0"] * 15)
    fields[19] = str(starttime).encode("ascii")
    uid = uid_token or str(os.geteuid()).encode("ascii")
    process = Path("/proc") / str(pid)
    return {
        process / "stat": str(pid).encode("ascii")
        + b" ("
        + comm
        + b") "
        + b" ".join(fields)
        + b"\n",
        process / "status": b"Name:\t" + name + b"\nUid:\t" + b"\t".join([uid] * 4) + b"\n",
    }


def _process_identity(pid: int, *, ppid: int = 1, starttime: int = 1) -> controller.ProcessIdentity:
    return controller.ProcessIdentity(
        pid=pid,
        ppid=ppid,
        pgrp=pid,
        session=pid,
        starttime=starttime,
        uid=os.geteuid(),
        state="S",
    )


class BoundedControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._controller_temp = tempfile.TemporaryDirectory()
        frozen_module = (
            Path(cls._controller_temp.name) / "frozen-source" / "bounded_process_controller.py"
        )
        frozen_module.parent.mkdir(mode=0o700)
        frozen_module.write_bytes(_CONTROLLER_SOURCE_PATH.read_bytes())
        frozen_module.chmod(0o600)
        module_name = f"_cao_bounded_controller_offline_{id(cls)}"
        spec = importlib.util.spec_from_file_location(module_name, frozen_module)
        if spec is None or spec.loader is None:
            raise RuntimeError("offline_controller_import_unavailable")
        loaded_controller = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = loaded_controller
        try:
            spec.loader.exec_module(loaded_controller)
        except Exception:
            sys.modules.pop(module_name, None)
            cls._controller_temp.cleanup()
            raise
        cls._controller_module_name = module_name
        global controller
        controller = loaded_controller

    @classmethod
    def tearDownClass(cls) -> None:
        global controller
        controller = _SOURCE_CONTROLLER
        sys.modules.pop(cls._controller_module_name, None)
        cls._controller_temp.cleanup()

    def setUp(self) -> None:
        # Portable core receives all host-specific values explicitly. The
        # fixture is private to this test and contains no credentials.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).absolute()
        launcher_root = root / "launcher"
        launcher_root.mkdir(mode=0o700)
        profile_home = root / "private-home"
        profile_home.mkdir(mode=0o700)
        runner = launcher_root / "runner.py"
        manifest = launcher_root / "manifest.json"
        shim = launcher_root / "sitecustomize.py"
        for path, payload in (
            (runner, b"synthetic runner source"),
            (manifest, b"synthetic manifest source"),
            (shim, b"synthetic shim source"),
        ):
            path.write_bytes(payload)
            path.chmod(0o600)
        frozen_module = Path(controller.__file__).resolve(strict=True)
        self.config = controller.ControllerConfig(
            launcher_root=launcher_root,
            runner=runner,
            runner_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            python_executable=Path(sys.executable).resolve(),
            python_version=tuple(sys.version_info[:3]),
            site_shim=shim,
            site_shim_sha256=hashlib.sha256(shim.read_bytes()).hexdigest(),
            private_profile_home=profile_home,
            receipt_root=root / "receipts",
            controller_module=frozen_module,
            controller_module_sha256=hashlib.sha256(frozen_module.read_bytes()).hexdigest(),
            total_wall_seconds=controller.TOTAL_WALL_SECONDS,
            run_stop_offset_seconds=controller.RUN_STOP_OFFSET_SECONDS,
        )
        # Existing temporary-root tests patch these names. Keep their override
        # local to this test module; the portable runtime defines no host paths.
        controller.RECEIPT_ROOT = None
        controller.LAUNCHER_ROOT = None
        # These tests are fake-only. An omitted or accidentally changed spawn
        # injection must fail here instead of starting a real provider runner.
        self._popen_guard = patch.object(
            controller.subprocess,
            "Popen",
            side_effect=AssertionError("offline tests must inject fake Popen"),
        )
        self._popen_guard.start()
        self.addCleanup(self._popen_guard.stop)

    def _execute(self, **kwargs):
        config = kwargs.pop("config", self.config)
        receipt_root = getattr(controller, "RECEIPT_ROOT", None)
        launcher_root = getattr(controller, "LAUNCHER_ROOT", None)
        updates = {}
        if receipt_root is not None:
            updates["receipt_root"] = Path(receipt_root)
        if launcher_root is not None:
            updates["launcher_root"] = Path(launcher_root)
        if updates:
            config = replace(config, **updates)
        return controller._execute(config=config, **kwargs)

    def test_inventory_race_diagnostic_distinguishes_identity_absent_and_changed_at_start(
        self,
    ) -> None:
        pid = 511
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=2100,
            uid=os.geteuid(),
            state="S",
        )
        changed = controller.ProcessIdentity(
            **{**pinned.__dict__, "starttime": pinned.starttime + 1}
        )
        task_lists = 0
        original_iterdir = Path.iterdir
        task_directory = Path("/synthetic-proc") / str(pid) / "task"

        def count_task_lists(path: Path):
            nonlocal task_lists
            if path == task_directory:
                task_lists += 1
            return original_iterdir(path)

        cases = (
            ("identity_missing_at_inventory_start", lambda _requested: None),
            (
                "identity_changed_at_inventory_start",
                lambda requested: changed if requested == pid else None,
            ),
        )
        for expected_branch, identity_reader in cases:
            with self.subTest(branch=expected_branch):
                task_lists = 0
                with patch.object(Path, "iterdir", new=count_task_lists):
                    with self.assertRaises(controller.ControllerError) as caught:
                        controller._read_children(
                            pid,
                            proc_root=Path("/synthetic-proc"),
                            read_identity=identity_reader,
                            expected_identity=pinned,
                        )
                diagnostic = controller._inventory_exception_diagnostic(caught.exception)
                self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
                self.assertEqual(diagnostic["inventory_branch"], expected_branch)
                self.assertEqual(task_lists, 0)

    def test_inventory_race_diagnostic_distinguishes_identity_loss_and_reuse_after_snapshot(
        self,
    ) -> None:
        pid = 521
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=2200,
            uid=os.geteuid(),
            state="S",
        )
        changed = controller.ProcessIdentity(**{**pinned.__dict__, "pgrp": pinned.pgrp + 1})
        cases = (
            ("identity_missing_after_snapshot", None, "none"),
            ("identity_changed_after_snapshot", changed, "none"),
        )
        for expected_branch, terminal_identity, expected_cause in cases:
            with self.subTest(branch=expected_branch):
                identity_reads = 0

                def read_identity(requested: int):
                    nonlocal identity_reads
                    identity_reads += 1
                    if requested != pid:
                        return None
                    return terminal_identity if identity_reads >= 3 else pinned

                with self.assertRaises(controller.ControllerError) as caught:
                    self._read_fake_task_inventory(
                        pid=pid,
                        task_snapshots=[("521",), ("521",)],
                        child_reads={"521": ["701"]},
                        identity_read=read_identity,
                        expected_identity=pinned,
                    )
                diagnostic = controller._inventory_exception_diagnostic(caught.exception)
                self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
                self.assertEqual(diagnostic["inventory_branch"], expected_branch)
                self.assertEqual(diagnostic["cause_class"], expected_cause)
                self.assertEqual(identity_reads, 3)

    def test_inventory_race_diagnostic_distinguishes_tid_retry_exhaustion(self) -> None:
        pid = 531
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=2300,
            uid=os.geteuid(),
            state="S",
        )
        cases = (
            (
                "tid_set_changed_retry_exhausted",
                [("531",), ("531", "532")] * 3,
                {"531": ["701"], "532": ["702"]},
                "none",
            ),
            (
                "tid_children_enoent_retry_exhausted",
                [("531",)] * 6,
                {"531": ["__ENOENT__"]},
                "file_not_found",
            ),
        )
        for expected_branch, snapshots, reads, expected_cause in cases:
            with self.subTest(branch=expected_branch):
                with self.assertRaises(controller.ControllerError) as caught:
                    self._read_fake_task_inventory(
                        pid=pid,
                        task_snapshots=snapshots,
                        child_reads=reads,
                        identity_read=lambda requested: identity if requested == pid else None,
                        expected_identity=identity,
                    )
                diagnostic = controller._inventory_exception_diagnostic(caught.exception)
                self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
                self.assertEqual(diagnostic["inventory_branch"], expected_branch)
                self.assertEqual(diagnostic["cause_class"], expected_cause)
                self.assertEqual(diagnostic["frames"], [])

    def test_inventory_branch_diagnostic_suppresses_unrecognized_values(self) -> None:
        error = controller.ControllerError(
            "process_task_inventory_raced", inventory_branch="private prompt /proc/777"
        )
        diagnostic = controller._inventory_exception_diagnostic(error)
        self.assertNotIn("inventory_branch", diagnostic)
        self.assertNotIn("private prompt", json.dumps(diagnostic, sort_keys=True))
        self.assertNotIn("777", json.dumps(diagnostic, sort_keys=True))

    def _read_fake_task_inventory(
        self,
        *,
        pid: int,
        task_snapshots: list[tuple[str, ...]],
        child_reads: dict[str, list[str]],
        identity_read,
        expected_identity: controller.ProcessIdentity | None = None,
        clock=None,
        deadline=None,
    ) -> tuple[set[int] | None, int, dict[str, int]]:
        proc_root = Path("/synthetic-proc")
        task_directory = proc_root / str(pid) / "task"
        original_iterdir = Path.iterdir
        original_read_text = Path.read_text
        snapshot_count = 0
        child_read_counts: dict[str, int] = {}

        def fake_iterdir(path: Path):
            nonlocal snapshot_count
            if path == task_directory:
                index = min(snapshot_count, len(task_snapshots) - 1)
                snapshot_count += 1
                return iter(task_directory / tid for tid in task_snapshots[index])
            return original_iterdir(path)

        def fake_read_text(path: Path, encoding=None):
            if path.parent.parent == task_directory and path.name == "children":
                tid = path.parent.name
                index = child_read_counts.get(tid, 0)
                child_read_counts[tid] = index + 1
                actions = child_reads.get(tid, [""])
                action = actions[min(index, len(actions) - 1)]
                if action == "__ENOENT__":
                    raise FileNotFoundError("synthetic task children race")
                if action == "__EACCES__":
                    raise PermissionError("synthetic task children permission")
                return action
            return original_read_text(path, encoding=encoding)

        with patch.object(Path, "iterdir", new=fake_iterdir):
            with patch.object(Path, "read_text", new=fake_read_text):
                options = {}
                if clock is not None:
                    options["clock"] = clock
                if deadline is not None:
                    options["deadline"] = deadline
                result = controller._read_children(
                    pid,
                    proc_root=proc_root,
                    read_identity=identity_read,
                    expected_identity=expected_identity,
                    **options,
                )
        return result, snapshot_count, child_read_counts

    def test_empty_first_snapshot_rechecks_identity_and_blocks_reused_pid_signal(self) -> None:
        pid = 541
        pinned = _process_identity(pid, starttime=2400)
        changed = replace(pinned, starttime=pinned.starttime + 1)
        cases = (
            ("identity_missing_after_snapshot", None),
            ("identity_changed_after_snapshot", changed),
            (None, pinned),
        )
        for expected_branch, terminal_identity in cases:
            with self.subTest(branch=expected_branch or "same_pin"):
                reads = 0

                def read_identity(requested: int):
                    nonlocal reads
                    reads += 1
                    return terminal_identity if reads >= 3 else pinned

                with self.assertRaises(controller.ControllerError) as caught:
                    self._read_fake_task_inventory(
                        pid=pid,
                        task_snapshots=[(), ()],
                        child_reads={},
                        identity_read=read_identity,
                        expected_identity=pinned,
                    )
                if expected_branch is None:
                    self.assertEqual(caught.exception.args, ("process_task_inventory_empty",))
                else:
                    diagnostic = controller._inventory_exception_diagnostic(caught.exception)
                    self.assertEqual(diagnostic.get("inventory_branch"), expected_branch)
                self.assertEqual(reads, 3)

                if expected_branch == "identity_changed_after_snapshot":
                    known = {pid: pinned}
                    group = controller.GroupIdentity(
                        pgid=pid,
                        anchor_pid=pid,
                        anchor_starttime=pinned.starttime,
                        uid=pinned.uid,
                        session=pinned.session,
                    )
                    signals = []
                    signaled = controller._signal_group_if_verified(
                        group,
                        known,
                        signal.SIGINT,
                        killpg=lambda pgid, signum: signals.append((pgid, signum)),
                        read_identity=lambda requested: changed if requested == pid else None,
                    )
                    self.assertFalse(signaled)
                    self.assertEqual(signals, [])
                    self.assertIs(known[pid], pinned)

    def test_empty_second_snapshot_rechecks_missing_same_and_changed_identity(self) -> None:
        pid = 551
        pinned = _process_identity(pid, starttime=2500)
        changed = replace(pinned, starttime=pinned.starttime + 1)
        cases = (
            ("identity_missing_after_snapshot", None),
            ("identity_changed_after_snapshot", changed),
            (None, pinned),
        )
        for expected_branch, terminal_identity in cases:
            with self.subTest(branch=expected_branch or "same_pin"):
                reads = 0

                def read_identity(requested: int):
                    nonlocal reads
                    reads += 1
                    return terminal_identity if reads >= 3 else pinned

                with self.assertRaises(controller.ControllerError) as caught:
                    self._read_fake_task_inventory(
                        pid=pid,
                        task_snapshots=[(str(pid),), ()],
                        child_reads={str(pid): ["701"]},
                        identity_read=read_identity,
                        expected_identity=pinned,
                    )
                if expected_branch is None:
                    self.assertEqual(caught.exception.args, ("process_task_inventory_empty",))
                else:
                    diagnostic = controller._inventory_exception_diagnostic(caught.exception)
                    self.assertEqual(diagnostic.get("inventory_branch"), expected_branch)
                self.assertEqual(reads, 3)

    def test_empty_task_identity_read_error_is_not_misclassified_as_exit(self) -> None:
        pid = 561
        pinned = _process_identity(pid, starttime=2600)
        reads = 0

        def read_identity(requested: int):
            nonlocal reads
            reads += 1
            if reads >= 3:
                raise controller.ControllerError("process_identity_unreadable")
            return pinned if requested == pid else None

        with self.assertRaises(controller.ControllerError) as caught:
            self._read_fake_task_inventory(
                pid=pid,
                task_snapshots=[(), ()],
                child_reads={},
                identity_read=read_identity,
                expected_identity=pinned,
            )
        self.assertEqual(caught.exception.args, ("process_identity_unreadable",))
        self.assertEqual(reads, 3)

    def test_empty_task_recheck_deadline_expiry_remains_fail_closed(self) -> None:
        pid = 571
        pinned = _process_identity(pid, starttime=2700)
        ticks = 0

        def clock() -> float:
            nonlocal ticks
            ticks += 1
            return float(ticks)

        with self.assertRaises(controller.ControllerError) as caught:
            self._read_fake_task_inventory(
                pid=pid,
                task_snapshots=[(), ()],
                child_reads={},
                identity_read=lambda requested: pinned if requested == pid else None,
                expected_identity=pinned,
                clock=clock,
                deadline=5.5,
            )
        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(diagnostic.get("inventory_branch"), "inventory_deadline_exceeded")

    def test_owned_scan_restarts_only_after_empty_snapshot_confirms_missing_pin(self) -> None:
        controller_pid, launcher_pid = 581, 582
        controller_pin = _process_identity(controller_pid, starttime=2800)
        launcher_pin = _process_identity(launcher_pid, ppid=controller_pid, starttime=2801)
        proc_root = Path("/synthetic-proc")
        controller_task = proc_root / str(controller_pid) / "task"
        launcher_task = proc_root / str(launcher_pid) / "task"
        thread = controller_task / "100"
        original_iterdir, original_read_text = Path.iterdir, Path.read_text
        controller_snapshots = launcher_snapshots = launcher_reads = 0

        def fake_iterdir(path: Path):
            nonlocal controller_snapshots, launcher_snapshots
            if path == controller_task:
                controller_snapshots += 1
                return iter((thread,))
            if path == launcher_task:
                launcher_snapshots += 1
                return iter(())
            return original_iterdir(path)

        def fake_read_text(path: Path, encoding=None):
            if path == thread / "children":
                return f"{launcher_pid}\n"
            return original_read_text(path, encoding=encoding)

        def read_identity(pid: int):
            nonlocal launcher_reads
            if pid == controller_pid:
                return controller_pin
            if pid == launcher_pid:
                launcher_reads += 1
                return launcher_pin if launcher_reads <= 3 else None
            return None

        source_read_children = controller._read_children

        def read_synthetic_children(pid: int, *, read_identity, expected_identity, clock, deadline):
            return source_read_children(
                pid,
                proc_root=proc_root,
                read_identity=read_identity,
                expected_identity=expected_identity,
                clock=clock,
                deadline=deadline,
            )

        with patch.object(controller, "_read_identity", new=read_identity):
            with patch.object(controller, "_read_children", new=read_synthetic_children):
                with patch.object(Path, "iterdir", new=fake_iterdir):
                    with patch.object(Path, "read_text", new=fake_read_text):
                        observed, groups, unreadable = controller._scan_owned_tree(
                            controller_pid=controller_pid,
                            launcher=launcher_pin,
                            baseline_controller_children=set(),
                            known={launcher_pid: launcher_pin},
                            read_identity=read_identity,
                        )

        self.assertEqual(controller_snapshots, 2 * controller.MAX_OWNED_TREE_SCAN_ATTEMPTS)
        self.assertEqual(launcher_snapshots, 1)
        self.assertEqual(launcher_reads, 6)
        self.assertEqual((observed, groups, unreadable), ({}, {}, False))

    def test_scan_does_not_adopt_reused_proc_identity(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 7700
        child_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=100,
            uid=os.geteuid(),
            state="S",
        )
        old_child = controller.ProcessIdentity(
            pid=child_pid,
            ppid=launcher_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=101,
            uid=os.geteuid(),
            state="S",
        )
        files = _fake_proc_files(
            launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=100,
        )
        files.update(
            _fake_proc_files(
                child_pid,
                ppid=launcher_pid,
                pgrp=launcher_pid + 99,
                session=launcher_pid,
                starttime=202,
            )
        )
        child_map = {
            controller_pid: {launcher_pid},
            launcher_pid: {child_pid},
            child_pid: set(),
        }
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path in files:
                return files[path]
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", new=read_bytes):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher, child_pid: old_child},
                read_children=lambda pid: child_map.get(pid, set()),
            )

        self.assertTrue(unreadable)
        self.assertNotIn(child_pid, observed)
        self.assertNotIn(launcher_pid + 99, groups)

    def test_scan_records_only_fixed_unreadable_guard_reason(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 7800
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=200,
            uid=os.geteuid(),
            state="S",
        )
        old_launcher = controller.ProcessIdentity(**{**launcher.__dict__, "starttime": 199})
        uid_mismatch = controller.ProcessIdentity(**{**launcher.__dict__, "uid": os.geteuid() + 1})
        started_before = controller.ProcessIdentity(
            pid=launcher_pid + 1,
            ppid=launcher_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=199,
            uid=os.geteuid(),
            state="S",
        )
        launcher_mismatch = controller.ProcessIdentity(
            **{**launcher.__dict__, "pgrp": launcher_pid + 2}
        )
        cases = (
            (
                "controller_children_unavailable",
                launcher,
                {launcher_pid: launcher},
                None,
                {},
            ),
            (
                "known_pid_starttime_changed",
                launcher,
                {launcher_pid: launcher},
                {launcher_pid},
                {launcher_pid: old_launcher},
            ),
            (
                "process_uid_mismatch",
                launcher,
                {launcher_pid: uid_mismatch},
                {launcher_pid},
                {},
            ),
            (
                "process_started_before_launcher",
                launcher,
                {launcher_pid: launcher, started_before.pid: started_before},
                {launcher_pid},
                {},
            ),
            (
                "launcher_identity_mismatch",
                launcher,
                {launcher_pid: launcher_mismatch},
                {launcher_pid},
                {},
            ),
        )
        for reason, pinned_launcher, identities, controller_children, known in cases:
            with self.subTest(reason=reason):
                diagnostic: dict[str, object] = {}

                def read_children(pid: int) -> set[int] | None:
                    if pid == controller_pid:
                        return controller_children
                    if pid == launcher_pid:
                        return (
                            {started_before.pid}
                            if reason == "process_started_before_launcher"
                            else set()
                        )
                    return set()

                _observed, _groups, unreadable = controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=pinned_launcher,
                    baseline_controller_children=set(),
                    known=dict(known),
                    read_identity=identities.get,
                    read_children=read_children,
                    diagnostic_sink=diagnostic,
                )
                self.assertTrue(unreadable)
                self.assertEqual(diagnostic["failure_scope"], "unreadable_flag")
                self.assertEqual(diagnostic["failure_code"], "owned_tree_unreadable")
                self.assertEqual(diagnostic["cause_class"], "none")
                self.assertEqual(diagnostic["reason"], reason)
                self.assertEqual(diagnostic["frames"], ["owned_tree_scan"])
                encoded = json.dumps(diagnostic, sort_keys=True)
                self.assertNotIn(str(launcher_pid), encoded)
                self.assertNotIn("private-name", encoded)

    def test_task_inventory_race_exception_has_safe_first_failure(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 7900
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=300,
            uid=os.geteuid(),
            state="S",
        )
        with tempfile.TemporaryDirectory() as temp:
            proc_root = Path(temp)
            (proc_root / str(launcher_pid) / "task" / str(launcher_pid)).mkdir(parents=True)
            try:
                controller._read_children(
                    launcher_pid,
                    proc_root=proc_root,
                    read_identity=lambda _pid: launcher,
                )
            except controller.ControllerError as error:
                diagnostic = controller._inventory_exception_diagnostic(error)
            else:
                self.fail("a live task without its children file must fail closed")

        self.assertEqual(diagnostic["failure_scope"], "exception")
        self.assertEqual(diagnostic["failure_code"], "process_task_inventory_raced")
        self.assertEqual(diagnostic["cause_class"], "file_not_found")
        self.assertEqual(diagnostic["frames"], ["children_read"])
        self.assertNotIn(str(launcher_pid), json.dumps(diagnostic, sort_keys=True))

    def test_transient_tid_enoent_retries_and_discards_partial_children(self) -> None:
        pid = 421
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1000,
            uid=os.geteuid(),
            state="S",
        )
        result, snapshots, reads = self._read_fake_task_inventory(
            pid=pid,
            task_snapshots=[("421", "422"), ("421",), ("421",), ("421",)],
            child_reads={"421": ["999", "701"], "422": ["__ENOENT__"]},
            identity_read=lambda requested: identity if requested == pid else None,
        )
        self.assertEqual(result, {701})
        self.assertNotIn(999, result)
        self.assertEqual(snapshots, 4)
        self.assertEqual(reads, {"421": 2, "422": 1})

    def test_new_tid_during_snapshot_forces_complete_rescan(self) -> None:
        pid = 431
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1100,
            uid=os.geteuid(),
            state="S",
        )
        result, snapshots, reads = self._read_fake_task_inventory(
            pid=pid,
            task_snapshots=[
                ("431",),
                ("431", "432"),
                ("431", "432"),
                ("431", "432"),
            ],
            child_reads={"431": ["999", "701"], "432": ["702"]},
            identity_read=lambda requested: identity if requested == pid else None,
        )
        self.assertEqual(result, {701, 702})
        self.assertNotIn(999, result)
        self.assertEqual(snapshots, 4)
        self.assertEqual(reads, {"431": 2, "432": 1})

    def test_persistent_task_set_churn_fails_after_three_full_attempts(self) -> None:
        pid = 441
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1200,
            uid=os.geteuid(),
            state="S",
        )
        proc_root = Path("/synthetic-proc")
        task_directory = proc_root / str(pid) / "task"
        original_iterdir = Path.iterdir
        original_read_text = Path.read_text
        list_calls = 0
        child_calls = 0

        def churn_task_set(path: Path):
            nonlocal list_calls
            if path == task_directory:
                index = list_calls
                list_calls += 1
                ids = ("441",) if index % 2 == 0 else ("441", "442")
                return iter(task_directory / tid for tid in ids)
            return original_iterdir(path)

        def read_first_thread(path: Path, encoding=None):
            nonlocal child_calls
            if path.parent.parent == task_directory and path.name == "children":
                child_calls += 1
                return "999"
            return original_read_text(path, encoding=encoding)

        with patch.object(Path, "iterdir", new=churn_task_set):
            with patch.object(Path, "read_text", new=read_first_thread):
                with self.assertRaises(controller.ControllerError) as caught:
                    controller._read_children(
                        pid,
                        proc_root=proc_root,
                        read_identity=lambda requested: identity if requested == pid else None,
                    )
        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(diagnostic["cause_class"], "none")
        self.assertEqual(diagnostic["failure_code"], "process_task_inventory_raced")
        self.assertEqual(list_calls, 6)
        self.assertEqual(child_calls, 3)

    def test_persistent_tid_enoent_fails_after_three_full_attempts(self) -> None:
        pid = 451
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1300,
            uid=os.geteuid(),
            state="S",
        )
        proc_root = Path("/synthetic-proc")
        task_directory = proc_root / str(pid) / "task"
        task_path = task_directory / str(pid) / "children"
        original_iterdir = Path.iterdir
        original_read_text = Path.read_text
        list_calls = 0
        child_calls = 0

        def one_task(path: Path):
            nonlocal list_calls
            if path == task_directory:
                list_calls += 1
                return iter((task_directory / str(pid),))
            return original_iterdir(path)

        def persistent_enoent(path: Path, encoding=None):
            nonlocal child_calls
            if path == task_path:
                child_calls += 1
                raise FileNotFoundError("synthetic persistent task race")
            return original_read_text(path, encoding=encoding)

        with patch.object(Path, "iterdir", new=one_task):
            with patch.object(Path, "read_text", new=persistent_enoent):
                with self.assertRaises(controller.ControllerError) as caught:
                    controller._read_children(
                        pid,
                        proc_root=proc_root,
                        read_identity=lambda requested: identity if requested == pid else None,
                    )
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertEqual(list_calls, 6)
        self.assertEqual(child_calls, 3)

    def test_identity_changes_during_snapshot_fail_closed_without_retry(self) -> None:
        pid = 461
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1400,
            uid=os.geteuid(),
            state="S",
        )
        mutations = (
            {"starttime": 1401},
            {"uid": identity.uid + 1},
            {"pgrp": identity.pgrp + 1},
            {"session": identity.session + 1},
        )
        for mutation in mutations:
            with self.subTest(mutation=tuple(mutation)):
                changed = controller.ProcessIdentity(**{**identity.__dict__, **mutation})
                identity_reads = 0

                def read_identity(requested: int):
                    nonlocal identity_reads
                    identity_reads += 1
                    if requested != pid:
                        return None
                    return changed if identity_reads >= 3 else identity

                with self.assertRaises(controller.ControllerError) as caught:
                    self._read_fake_task_inventory(
                        pid=pid,
                        task_snapshots=[("461",), ("461",)],
                        child_reads={"461": ["700"]},
                        identity_read=read_identity,
                    )
                self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
                self.assertEqual(identity_reads, 3)

    def test_process_exit_after_identity_pin_is_not_an_empty_snapshot(self) -> None:
        pid = 471
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1500,
            uid=os.geteuid(),
            state="S",
        )
        identity_reads = 0

        def read_identity(requested: int):
            nonlocal identity_reads
            identity_reads += 1
            if requested != pid:
                return None
            return identity if identity_reads < 3 else None

        with self.assertRaises(controller.ControllerError) as caught:
            self._read_fake_task_inventory(
                pid=pid,
                task_snapshots=[("471",), ("471",)],
                child_reads={"471": ["700"]},
                identity_read=read_identity,
            )
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(identity_reads, 3)

    def test_expected_identity_prevents_late_pid_adoption(self) -> None:
        pid = 476
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1550,
            uid=os.geteuid(),
            state="S",
        )
        task_lists = 0
        original_iterdir = Path.iterdir
        task_directory = Path("/synthetic-proc") / str(pid) / "task"

        def count_task_lists(path: Path):
            nonlocal task_lists
            if path == task_directory:
                task_lists += 1
            return original_iterdir(path)

        with patch.object(Path, "iterdir", new=count_task_lists):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._read_children(
                    pid,
                    proc_root=Path("/synthetic-proc"),
                    read_identity=lambda _requested: None,
                    expected_identity=pinned,
                )
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(task_lists, 0)

    def test_expected_identity_rejects_reused_pid_before_task_enumeration(self) -> None:
        pid = 477
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1551,
            uid=os.geteuid(),
            state="S",
        )
        reused = controller.ProcessIdentity(
            **{**pinned.__dict__, "starttime": pinned.starttime + 1}
        )
        task_lists = 0
        original_iterdir = Path.iterdir
        task_directory = Path("/synthetic-proc") / str(pid) / "task"

        def count_task_lists(path: Path):
            nonlocal task_lists
            if path == task_directory:
                task_lists += 1
            return original_iterdir(path)

        with patch.object(Path, "iterdir", new=count_task_lists):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._read_children(
                    pid,
                    proc_root=Path("/synthetic-proc"),
                    read_identity=lambda requested: reused if requested == pid else None,
                    expected_identity=pinned,
                )
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(task_lists, 0)

    def test_identity_is_rechecked_before_each_retry(self) -> None:
        pid = 478
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1552,
            uid=os.geteuid(),
            state="S",
        )
        changed = controller.ProcessIdentity(**{**identity.__dict__, "pgrp": identity.pgrp + 1})
        identity_reads = 0

        def read_identity(requested: int):
            nonlocal identity_reads
            identity_reads += 1
            if requested != pid:
                return None
            return changed if identity_reads >= 4 else identity

        with self.assertRaises(controller.ControllerError) as caught:
            self._read_fake_task_inventory(
                pid=pid,
                task_snapshots=[("478", "479"), ("478",)],
                child_reads={"478": ["999"], "479": ["__ENOENT__"]},
                identity_read=read_identity,
            )
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(identity_reads, 4)

    def test_task_root_disappearance_fails_closed_without_retry(self) -> None:
        pid = 481
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1600,
            uid=os.geteuid(),
            state="S",
        )
        original_iterdir = Path.iterdir
        calls = 0
        task_directory = Path("/synthetic-proc") / str(pid) / "task"

        def missing_task_root(path: Path):
            nonlocal calls
            if path == task_directory:
                calls += 1
                raise FileNotFoundError("synthetic task root missing")
            return original_iterdir(path)

        with patch.object(Path, "iterdir", new=missing_task_root):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._read_children(
                    pid,
                    proc_root=Path("/synthetic-proc"),
                    read_identity=lambda requested: identity if requested == pid else None,
                )
        self.assertEqual(caught.exception.args, ("process_children_unreadable",))
        self.assertEqual(calls, 1)

    def test_task_root_enoent_with_missing_pinned_process_has_fixed_race_branch(self) -> None:
        pid = 482
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1601,
            uid=os.geteuid(),
            state="S",
        )
        identity_reads = 0
        task_directory = Path("/synthetic-proc") / str(pid) / "task"
        original_iterdir = Path.iterdir

        def read_identity(requested: int):
            nonlocal identity_reads
            if requested != pid:
                return None
            identity_reads += 1
            return identity if identity_reads < 3 else None

        def missing_task_root(path: Path):
            if path == task_directory:
                raise FileNotFoundError("synthetic task root disappeared")
            return original_iterdir(path)

        with patch.object(Path, "iterdir", new=missing_task_root):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._read_children(
                    pid,
                    proc_root=Path("/synthetic-proc"),
                    read_identity=read_identity,
                    expected_identity=identity,
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(
            diagnostic["inventory_branch"],
            "identity_missing_after_task_root_enoent",
        )
        self.assertEqual(diagnostic["cause_class"], "file_not_found")
        self.assertEqual(identity_reads, 3)

    def test_task_root_enoent_with_reused_or_changed_identity_fails_closed(self) -> None:
        pid = 483
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1602,
            uid=os.geteuid(),
            state="S",
        )
        mutations = (
            {"starttime": pinned.starttime + 1},
            {"uid": pinned.uid + 1},
            {"pgrp": pinned.pgrp + 1},
            {"session": pinned.session + 1},
        )
        task_directory = Path("/synthetic-proc") / str(pid) / "task"
        original_iterdir = Path.iterdir

        def missing_task_root(path: Path):
            if path == task_directory:
                raise FileNotFoundError("synthetic task root disappeared")
            return original_iterdir(path)

        for mutation in mutations:
            with self.subTest(mutation=tuple(mutation)):
                changed = controller.ProcessIdentity(**{**pinned.__dict__, **mutation})
                identity_reads = 0

                def read_identity(requested: int):
                    nonlocal identity_reads
                    if requested != pid:
                        return None
                    identity_reads += 1
                    return pinned if identity_reads < 3 else changed

                with patch.object(Path, "iterdir", new=missing_task_root):
                    with self.assertRaises(controller.ControllerError) as caught:
                        controller._read_children(
                            pid,
                            proc_root=Path("/synthetic-proc"),
                            read_identity=read_identity,
                            expected_identity=pinned,
                        )

                diagnostic = controller._inventory_exception_diagnostic(caught.exception)
                self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
                self.assertEqual(
                    diagnostic["inventory_branch"],
                    "identity_changed_after_task_root_enoent",
                )
                self.assertEqual(identity_reads, 3)

    def test_task_root_pid_reuse_never_signals_the_replacement_group(self) -> None:
        pid = 484
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1603,
            uid=os.geteuid(),
            state="S",
        )
        replacement = controller.ProcessIdentity(
            **{**pinned.__dict__, "starttime": pinned.starttime + 1}
        )
        group = controller.GroupIdentity(
            pgid=pinned.pgrp,
            anchor_pid=pid,
            anchor_starttime=pinned.starttime,
            uid=pinned.uid,
            session=pinned.session,
        )
        identity_reads = 0
        task_directory = Path("/synthetic-proc") / str(pid) / "task"
        original_iterdir = Path.iterdir
        signaled: list[tuple[int, int]] = []

        def read_identity(requested: int):
            nonlocal identity_reads
            if requested != pid:
                return None
            identity_reads += 1
            return pinned if identity_reads < 3 else replacement

        def missing_task_root(path: Path):
            if path == task_directory:
                raise FileNotFoundError("synthetic task root disappeared")
            return original_iterdir(path)

        with patch.object(Path, "iterdir", new=missing_task_root):
            with self.assertRaises(controller.ControllerError):
                controller._read_children(
                    pid,
                    proc_root=Path("/synthetic-proc"),
                    read_identity=read_identity,
                    expected_identity=pinned,
                )

        signaled_ok = controller._signal_group_if_verified(
            group,
            {pid: pinned},
            signal.SIGKILL,
            killpg=lambda pgid, signum: signaled.append((pgid, signum)),
            read_identity=lambda requested: replacement if requested == pid else None,
        )
        self.assertFalse(signaled_ok)
        self.assertEqual(signaled, [])

    def test_scan_restarts_when_process_exits_before_snapshot_and_finds_adopted_child(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 8000
        transient_pid = launcher_pid + 1
        adopted_pid = launcher_pid + 2
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=450,
            uid=os.geteuid(),
            state="S",
        )
        transient = controller.ProcessIdentity(
            pid=transient_pid,
            ppid=launcher_pid,
            pgrp=transient_pid,
            session=launcher_pid,
            starttime=451,
            uid=os.geteuid(),
            state="S",
        )
        adopted_before_exit = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=transient_pid,
            pgrp=adopted_pid,
            session=adopted_pid,
            starttime=452,
            uid=os.geteuid(),
            state="S",
        )
        adopted_after_exit = controller.ProcessIdentity(
            **{**adopted_before_exit.__dict__, "ppid": controller_pid}
        )
        identities = {
            launcher_pid: launcher,
            transient_pid: transient,
            adopted_pid: adopted_before_exit,
        }
        identity_reads: dict[int, int] = {}
        controller_snapshots = 0
        launcher_snapshots = 0
        transient_children_reads = 0
        proc_root = Path("/synthetic-proc")

        def read_identity(requested: int):
            if requested == transient_pid:
                identity_reads[requested] = identity_reads.get(requested, 0) + 1
                if identity_reads[requested] == 4:
                    identities.pop(transient_pid)
                    identities[adopted_pid] = adopted_after_exit
                    return None
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots, launcher_snapshots, transient_children_reads
            if requested == controller_pid:
                controller_snapshots += 1
                if controller_snapshots == 1:
                    return {launcher_pid}
                return {launcher_pid, adopted_pid}
            if requested == launcher_pid:
                launcher_snapshots += 1
                return {transient_pid} if launcher_snapshots == 1 else set()
            if requested == transient_pid:
                transient_children_reads += 1
                return controller._read_children(
                    requested,
                    proc_root=proc_root,
                    read_identity=read_identity,
                    expected_identity=transient,
                )
            if requested == adopted_pid:
                return set()
            return set()

        observed, groups, unreadable = controller._scan_owned_tree(
            controller_pid=controller_pid,
            launcher=launcher,
            baseline_controller_children=set(),
            known={launcher_pid: launcher},
            read_identity=read_identity,
            read_children=read_children,
        )

        self.assertFalse(unreadable)
        self.assertEqual(controller_snapshots, 2)
        self.assertEqual(launcher_snapshots, 2)
        self.assertEqual(transient_children_reads, 1)
        self.assertEqual(identity_reads[transient_pid], 4)
        self.assertEqual(set(observed), {launcher_pid, adopted_pid})
        self.assertNotIn(transient_pid, observed)
        self.assertNotIn(transient_pid, groups)
        self.assertIn(adopted_pid, groups)

    def test_scan_restarts_for_each_confirmed_missing_identity_branch(self) -> None:
        missing_branches = (
            "identity_missing_at_inventory_start",
            "identity_missing_before_snapshot",
            "identity_missing_after_snapshot",
            "identity_missing_after_tid_enoent",
            "identity_missing_after_task_root_enoent",
        )
        controller_pid = os.getpid()
        for index, branch in enumerate(missing_branches):
            with self.subTest(branch=branch):
                launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 9000 + index * 10
                transient_pid = launcher_pid + 1
                adopted_pid = launcher_pid + 2
                launcher = controller.ProcessIdentity(
                    pid=launcher_pid,
                    ppid=controller_pid,
                    pgrp=launcher_pid,
                    session=launcher_pid,
                    starttime=550 + index * 3,
                    uid=os.geteuid(),
                    state="S",
                )
                transient = controller.ProcessIdentity(
                    pid=transient_pid,
                    ppid=launcher_pid,
                    pgrp=transient_pid,
                    session=launcher_pid,
                    starttime=551 + index * 3,
                    uid=os.geteuid(),
                    state="S",
                )
                adopted = controller.ProcessIdentity(
                    pid=adopted_pid,
                    ppid=controller_pid,
                    pgrp=adopted_pid,
                    session=adopted_pid,
                    starttime=552 + index * 3,
                    uid=os.geteuid(),
                    state="S",
                )
                identities = {launcher_pid: launcher, transient_pid: transient}
                controller_snapshots = 0
                launcher_snapshots = 0
                transient_failures = 0

                def read_identity(requested: int):
                    return identities.get(requested)

                def read_children(requested: int):
                    nonlocal controller_snapshots, launcher_snapshots, transient_failures
                    if requested == controller_pid:
                        controller_snapshots += 1
                        if controller_snapshots == 1:
                            return {launcher_pid}
                        return {launcher_pid, adopted_pid}
                    if requested == launcher_pid:
                        launcher_snapshots += 1
                        return {transient_pid} if launcher_snapshots == 1 else set()
                    if requested == transient_pid:
                        transient_failures += 1
                        identities.pop(transient_pid)
                        identities[adopted_pid] = adopted
                        cause = FileNotFoundError("synthetic owned process exit")
                        raise controller._task_inventory_race(branch) from cause
                    if requested == adopted_pid:
                        return set()
                    return set()

                observed, groups, unreadable = controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={launcher_pid: launcher},
                    read_identity=read_identity,
                    read_children=read_children,
                )

                self.assertFalse(unreadable)
                self.assertEqual(controller_snapshots, 2)
                self.assertEqual(launcher_snapshots, 2)
                self.assertEqual(transient_failures, 1)
                self.assertEqual(set(observed), {launcher_pid, adopted_pid})
                self.assertNotIn(transient_pid, observed)
                self.assertNotIn(transient_pid, groups)
                self.assertIn(adopted_pid, groups)

    def test_scan_missing_identity_retry_budget_exhaustion_fails_closed(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 9100
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=650,
            uid=os.geteuid(),
            state="S",
        )
        identities: dict[int, controller.ProcessIdentity] = {launcher_pid: launcher}
        controller_snapshots = 0
        launcher_snapshots = 0
        error_pids: list[int] = []
        active_pid = launcher_pid + 1

        def add_active_process(pid: int, starttime: int, ppid: int) -> None:
            identities[pid] = controller.ProcessIdentity(
                pid=pid,
                ppid=ppid,
                pgrp=pid,
                session=pid,
                starttime=starttime,
                uid=os.geteuid(),
                state="S",
            )

        add_active_process(active_pid, 651, launcher_pid)

        def read_identity(requested: int):
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots, launcher_snapshots, active_pid
            if requested == controller_pid:
                controller_snapshots += 1
                if controller_snapshots == 1:
                    return {launcher_pid}
                return {launcher_pid, active_pid}
            if requested == launcher_pid:
                launcher_snapshots += 1
                return {launcher_pid + 1} if launcher_snapshots == 1 else set()
            if requested == active_pid:
                current_pid = active_pid
                current_identity = identities.pop(current_pid)
                error_pids.append(current_pid)
                active_pid += 1
                add_active_process(active_pid, current_identity.starttime + 1, controller_pid)
                cause = FileNotFoundError("synthetic persistent owned process exits")
                raise controller._task_inventory_race("identity_missing_before_snapshot") from cause
            return set()

        with self.assertRaises(controller.ControllerError) as caught:
            controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=read_identity,
                read_children=read_children,
            )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(diagnostic["inventory_branch"], "owned_tree_retry_budget_exhausted")
        self.assertEqual(controller_snapshots, controller.MAX_OWNED_TREE_SCAN_ATTEMPTS)
        self.assertEqual(len(error_pids), controller.MAX_OWNED_TREE_SCAN_ATTEMPTS)
        self.assertEqual(len(set(error_pids)), len(error_pids))

    def test_scan_missing_identity_retry_rejects_pid_reuse_without_signaling(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 9200
        transient_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=700,
            uid=os.geteuid(),
            state="S",
        )
        transient = controller.ProcessIdentity(
            pid=transient_pid,
            ppid=launcher_pid,
            pgrp=transient_pid,
            session=launcher_pid,
            starttime=701,
            uid=os.geteuid(),
            state="S",
        )
        replacement = controller.ProcessIdentity(
            **{
                **transient.__dict__,
                "ppid": controller_pid,
                "pgrp": transient_pid + 100,
                "session": transient_pid + 100,
                "starttime": transient.starttime + 1,
            }
        )
        identities = {launcher_pid: launcher, transient_pid: transient}
        controller_snapshots = 0
        launcher_snapshots = 0
        transient_reads = 0

        def read_identity(requested: int):
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots, launcher_snapshots, transient_reads
            if requested == controller_pid:
                controller_snapshots += 1
                if controller_snapshots == 1:
                    return {launcher_pid}
                return {launcher_pid, transient_pid}
            if requested == launcher_pid:
                launcher_snapshots += 1
                return {transient_pid} if launcher_snapshots == 1 else set()
            if requested == transient_pid:
                transient_reads += 1
                if transient_reads == 1:
                    identities[transient_pid] = replacement
                    cause = FileNotFoundError("synthetic identity loss followed by reuse")
                    raise controller._task_inventory_race(
                        "identity_missing_before_snapshot"
                    ) from cause
                self.fail("replacement identity must not be traversed")
            return set()

        known = {launcher_pid: launcher}
        observed, groups, unreadable = controller._scan_owned_tree(
            controller_pid=controller_pid,
            launcher=launcher,
            baseline_controller_children=set(),
            known=known,
            read_identity=read_identity,
            read_children=read_children,
        )

        self.assertTrue(unreadable)
        self.assertEqual(controller_snapshots, 2)
        self.assertEqual(launcher_snapshots, 2)
        self.assertEqual(transient_reads, 1)
        self.assertNotIn(transient_pid, observed)
        self.assertNotIn(transient_pid, groups)
        self.assertNotIn(replacement.pgrp, groups)
        self.assertEqual(known[transient_pid].starttime, transient.starttime)
        original_group = controller.GroupIdentity(
            pgid=transient.pgrp,
            anchor_pid=transient.pid,
            anchor_starttime=transient.starttime,
            uid=transient.uid,
            session=transient.session,
        )
        signaled: list[tuple[int, int]] = []
        signaled_ok = controller._signal_group_if_verified(
            original_group,
            known,
            signal.SIGKILL,
            killpg=lambda pgid, signum: signaled.append((pgid, signum)),
            read_identity=read_identity,
        )
        self.assertFalse(signaled_ok)
        self.assertEqual(signaled, [])

    def test_scan_restarts_full_walk_and_captures_orphan_adopted_after_parent_exit(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 8100
        transient_pid = launcher_pid + 1
        adopted_pid = launcher_pid + 2
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=500,
            uid=os.geteuid(),
            state="S",
        )
        transient = controller.ProcessIdentity(
            pid=transient_pid,
            ppid=launcher_pid,
            pgrp=transient_pid,
            session=launcher_pid,
            starttime=501,
            uid=os.geteuid(),
            state="S",
        )
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=adopted_pid,
            session=adopted_pid,
            starttime=502,
            uid=os.geteuid(),
            state="S",
        )
        identities = {launcher_pid: launcher, transient_pid: transient}
        controller_snapshots = 0
        child_calls: list[int] = []

        def read_identity(requested: int):
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots
            child_calls.append(requested)
            if requested == controller_pid:
                controller_snapshots += 1
                if controller_snapshots == 1:
                    return {launcher_pid}
                return {launcher_pid, adopted_pid}
            if requested == launcher_pid:
                return {transient_pid} if transient_pid in identities else set()
            if requested == transient_pid:
                # Model task-root ENOENT after this pinned process exits and
                # its child is adopted by the configured subreaper.
                identities.pop(transient_pid)
                identities[adopted_pid] = adopted
                error = FileNotFoundError("synthetic task root disappeared")
                raise controller._task_inventory_race(
                    "identity_missing_after_task_root_enoent"
                ) from error
            if requested == adopted_pid:
                return set()
            return set()

        observed, groups, unreadable = controller._scan_owned_tree(
            controller_pid=controller_pid,
            launcher=launcher,
            baseline_controller_children=set(),
            known={launcher_pid: launcher},
            read_identity=read_identity,
            read_children=read_children,
        )

        self.assertFalse(unreadable)
        self.assertEqual(controller_snapshots, 2)
        self.assertEqual(set(observed), {launcher_pid, adopted_pid})
        self.assertNotIn(transient_pid, observed)
        self.assertNotIn(transient_pid, groups)
        self.assertIn(adopted_pid, groups)
        self.assertEqual(child_calls.count(transient_pid), 1)
        self.assertEqual(child_calls.count(adopted_pid), 1)

    def test_scan_full_restart_budget_exhaustion_is_not_empty_success(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 8200
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=600,
            uid=os.geteuid(),
            state="S",
        )
        identities: dict[int, controller.ProcessIdentity] = {launcher_pid: launcher}
        controller_snapshots = 0
        error_pids: list[int] = []
        next_pid = launcher_pid + 1
        identities[next_pid] = controller.ProcessIdentity(
            pid=next_pid,
            ppid=controller_pid,
            pgrp=next_pid,
            session=next_pid,
            starttime=601,
            uid=os.geteuid(),
            state="S",
        )

        def read_identity(requested: int):
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots, next_pid
            if requested == controller_pid:
                controller_snapshots += 1
                return {launcher_pid, next_pid}
            if requested == launcher_pid:
                return set()
            current = identities.get(requested)
            if current is not None:
                error_pids.append(requested)
                identities.pop(requested)
                next_pid += 1
                identities[next_pid] = controller.ProcessIdentity(
                    pid=next_pid,
                    ppid=controller_pid,
                    pgrp=next_pid,
                    session=next_pid,
                    starttime=current.starttime + 1,
                    uid=os.geteuid(),
                    state="S",
                )
                error = FileNotFoundError("synthetic task root disappeared")
                raise controller._task_inventory_race(
                    "identity_missing_after_task_root_enoent"
                ) from error
            return set()

        with self.assertRaises(controller.ControllerError) as caught:
            controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=read_identity,
                read_children=read_children,
            )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(diagnostic["inventory_branch"], "owned_tree_retry_budget_exhausted")
        self.assertEqual(controller_snapshots, 3)
        self.assertEqual(len(error_pids), 3)
        self.assertEqual(len(set(error_pids)), 3)

    def test_scan_does_not_restart_on_permission_or_changed_identity_errors(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 8300
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=700,
            uid=os.geteuid(),
            state="S",
        )
        controller_snapshots = 0
        failures = (
            controller.ControllerError("process_children_unreadable"),
            controller.ControllerError(
                "process_task_inventory_raced",
                inventory_branch="identity_changed_after_task_root_enoent",
            ),
            controller.ControllerError(
                "process_task_inventory_raced",
                inventory_branch="identity_changed_after_task_root_esrch",
            ),
            controller.ControllerError(
                "process_task_inventory_raced",
                inventory_branch="identity_changed_after_tid_esrch",
            ),
            controller.ControllerError(
                "process_task_inventory_raced",
                inventory_branch="identity_changed_before_snapshot",
            ),
            controller.ControllerError(
                "process_task_inventory_raced",
                inventory_branch="identity_changed_after_snapshot",
            ),
        )

        for failure in failures:
            with self.subTest(failure=failure.args):
                controller_snapshots = 0

                def read_children(requested: int):
                    nonlocal controller_snapshots
                    if requested == controller_pid:
                        controller_snapshots += 1
                        return {launcher_pid}
                    raise failure

                with self.assertRaises(controller.ControllerError) as caught:
                    controller._scan_owned_tree(
                        controller_pid=controller_pid,
                        launcher=launcher,
                        baseline_controller_children=set(),
                        known={launcher_pid: launcher},
                        read_identity=lambda requested: (
                            launcher if requested == launcher_pid else None
                        ),
                        read_children=read_children,
                    )

                self.assertIs(caught.exception, failure)
                self.assertEqual(controller_snapshots, 1)

    def test_scan_retry_requires_unchanged_controller_identity(self) -> None:
        controller_pid = os.getpid()
        actual_controller = controller._read_identity(controller_pid)
        assert actual_controller is not None
        changed_controller = controller.ProcessIdentity(
            **{**actual_controller.__dict__, "pgrp": actual_controller.pgrp + 1}
        )
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 8400
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=800,
            uid=os.geteuid(),
            state="S",
        )
        controller_reads = 0
        controller_snapshots = 0
        failure = controller._task_inventory_race("identity_missing_before_snapshot")

        def proc_identity(requested: int):
            nonlocal controller_reads
            if requested == controller_pid:
                controller_reads += 1
                return actual_controller if controller_reads < 3 else changed_controller
            return launcher if requested == launcher_pid else None

        def read_children(requested: int):
            nonlocal controller_snapshots
            if requested == controller_pid:
                controller_snapshots += 1
                return {launcher_pid}
            raise failure

        with patch.object(controller, "_read_identity", new=proc_identity):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={launcher_pid: launcher},
                    read_identity=proc_identity,
                    read_children=read_children,
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(
            diagnostic["inventory_branch"],
            "controller_identity_changed_before_retry",
        )
        self.assertEqual(controller_snapshots, 1)

    def test_task_permission_error_fails_without_retry(self) -> None:
        pid = 491
        identity = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1700,
            uid=os.geteuid(),
            state="S",
        )
        proc_root = Path("/synthetic-proc")
        task_directory = proc_root / str(pid) / "task"
        task_path = task_directory / str(pid) / "children"
        original_iterdir = Path.iterdir
        original_read_text = Path.read_text
        list_calls = 0
        read_calls = 0

        def one_task(path: Path):
            nonlocal list_calls
            if path == task_directory:
                list_calls += 1
                return iter((task_directory / str(pid),))
            return original_iterdir(path)

        def no_permission(path: Path, encoding=None):
            nonlocal read_calls
            if path == task_path:
                read_calls += 1
                raise PermissionError("synthetic permission error")
            return original_read_text(path, encoding=encoding)

        with patch.object(Path, "iterdir", new=one_task):
            with patch.object(Path, "read_text", new=no_permission):
                with self.assertRaises(controller.ControllerError) as caught:
                    controller._read_children(
                        pid,
                        proc_root=proc_root,
                        read_identity=lambda requested: identity if requested == pid else None,
                    )
        self.assertEqual(caught.exception.args, ("process_children_unreadable",))
        self.assertEqual(list_calls, 1)
        self.assertEqual(read_calls, 1)

    def test_scan_passes_the_validated_process_identity_to_inventory_reader(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 8800
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1800,
            uid=os.geteuid(),
            state="S",
        )
        seen: list[controller.ProcessIdentity | None] = []

        def fake_read_children(
            pid: int,
            *,
            read_identity=None,
            expected_identity: controller.ProcessIdentity | None = None,
            clock=None,
            deadline=None,
        ):
            if pid == launcher_pid:
                seen.append(expected_identity)
            return set()

        with patch.object(controller, "_read_children", new=fake_read_children):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=lambda requested: launcher if requested == launcher_pid else None,
            )
        self.assertFalse(unreadable)
        self.assertEqual(
            groups,
            {
                launcher_pid: controller.GroupIdentity(
                    pgid=launcher_pid,
                    anchor_pid=launcher_pid,
                    anchor_starttime=launcher.starttime,
                    uid=launcher.uid,
                    session=launcher.session,
                )
            },
        )
        self.assertEqual(observed, {launcher_pid: launcher})
        self.assertEqual(seen, [launcher])

    def test_scan_census_fallback_exception_is_sanitized(
        self,
    ) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 8000
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=400,
            uid=os.geteuid(),
            state="S",
        )
        with tempfile.TemporaryDirectory() as temp:
            proc_root = Path(temp)
            (proc_root / str(launcher_pid) / "task" / str(launcher_pid)).mkdir(parents=True)

            def read_children(pid: int) -> set[int] | None:
                if pid == controller_pid:
                    return {launcher_pid}
                return controller._read_children(
                    pid,
                    proc_root=proc_root,
                    read_identity={launcher_pid: launcher}.get,
                )

            def read_process_census():
                try:
                    raise FileNotFoundError("synthetic proc census gap")
                except FileNotFoundError as error:
                    raise controller.ControllerError("process_identity_unreadable") from error

            try:
                controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={},
                    read_identity={launcher_pid: launcher}.get,
                    read_children=read_children,
                    read_process_census=read_process_census,
                )
            except controller.ControllerError as error:
                diagnostic = controller._inventory_exception_diagnostic(error)
            else:
                self.fail("scan must propagate incomplete task inventory")

        self.assertEqual(diagnostic["failure_code"], "process_identity_unreadable")
        self.assertEqual(diagnostic["cause_class"], "file_not_found")
        self.assertEqual(diagnostic["frames"], ["owned_tree_scan", "process_census"])
        encoded = json.dumps(diagnostic, sort_keys=True)
        self.assertNotIn(str(launcher_pid), encoded)
        self.assertNotIn(temp, encoded)

    def test_unknown_inventory_exception_diagnostic_suppresses_message(self) -> None:
        sentinel = "private prompt /proc/12345/comm provider-token"
        try:
            raise RuntimeError(sentinel)
        except RuntimeError as error:
            diagnostic = controller._inventory_exception_diagnostic(error)

        self.assertEqual(diagnostic["failure_scope"], "exception")
        self.assertEqual(diagnostic["failure_code"], "process_inventory_unclassified")
        self.assertEqual(diagnostic["cause_class"], "other_error")
        encoded = json.dumps(diagnostic, sort_keys=True)
        self.assertNotIn(sentinel, encoded)
        self.assertNotIn("12345", encoded)
        self.assertNotIn("provider-token", encoded)

    def test_execute_requires_explicit_spawn_callable(self) -> None:
        parameter = inspect.signature(controller._execute).parameters["popen"]
        self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_proc_identity_ignores_unicode_comm_and_status_name_bytes(self) -> None:
        pid = max(os.getpid(), os.getsid(os.getpid())) + 7400
        unusual = b"worker ) private-name \xff \xe2\x98\x83"
        files = _fake_proc_files(
            pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1001,
            comm=unusual,
            name=unusual,
        )
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path in files:
                return files[path]
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", new=read_bytes):
            identity = controller._read_identity(pid)

        self.assertEqual(
            identity,
            controller.ProcessIdentity(
                pid=pid,
                ppid=1,
                pgrp=pid,
                session=pid,
                starttime=1001,
                uid=os.geteuid(),
                state="S",
            ),
        )

    def test_proc_identity_rejects_malformed_numeric_fields(self) -> None:
        pid = max(os.getpid(), os.getsid(os.getpid())) + 7500
        cases = (
            ("non_ascii_ppid", 1, b"\xff", None),
            ("nondigit_pgrp", 2, b"12x", None),
            ("negative_starttime", 19, b"-1", None),
            ("nondigit_uid", None, None, b"10x0"),
        )
        for label, stat_field, bad_value, uid_token in cases:
            with self.subTest(field=label):
                files = _fake_proc_files(
                    pid,
                    ppid=1,
                    pgrp=pid,
                    session=pid,
                    starttime=1001,
                    uid_token=uid_token,
                )
                stat_path = Path("/proc") / str(pid) / "stat"
                if stat_field is not None:
                    prefix, suffix = files[stat_path].rsplit(b")", 1)
                    fields = suffix.split()
                    fields[stat_field] = bad_value
                    files[stat_path] = prefix + b") " + b" ".join(fields) + b"\n"
                original_read_bytes = Path.read_bytes

                def read_bytes(path: Path) -> bytes:
                    if path in files:
                        return files[path]
                    return original_read_bytes(path)

                with patch.object(Path, "read_bytes", new=read_bytes):
                    with self.assertRaises(controller.ControllerError) as caught:
                        controller._read_identity(pid)
                self.assertEqual(str(caught.exception), "process_identity_invalid")
                self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_proc_identity_exit_between_stat_and_status_is_gone(self) -> None:
        pid = max(os.getpid(), os.getsid(os.getpid())) + 7600
        files = _fake_proc_files(
            pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1001,
        )
        stat_path = Path("/proc") / str(pid) / "stat"
        status_path = Path("/proc") / str(pid) / "status"
        reads: list[str] = []
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path == stat_path:
                reads.append("stat")
                return files[path]
            if path == status_path:
                reads.append("status")
                raise FileNotFoundError("synthetic process exit")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", new=read_bytes):
            identity = controller._read_identity(pid)

        self.assertIsNone(identity)
        self.assertEqual(reads, ["stat", "status"])

    def test_scan_records_only_fixed_unreadable_reason(self) -> None:
        controller_pid = os.getpid()
        launcher = controller.ProcessIdentity(
            pid=940001,
            ppid=controller_pid,
            pgrp=940001,
            session=940001,
            starttime=400,
            uid=os.geteuid(),
            state="S",
        )
        diagnostic: dict[str, object] = {}

        _observed, _groups, unreadable = controller._scan_owned_tree(
            controller_pid=controller_pid,
            launcher=launcher,
            baseline_controller_children=set(),
            known={launcher.pid: launcher},
            read_identity=lambda _pid: launcher,
            read_children=lambda pid: None if pid == controller_pid else set(),
            diagnostic_sink=diagnostic,
        )

        self.assertTrue(unreadable)
        self.assertEqual(
            diagnostic,
            {
                "failure_scope": "unreadable_flag",
                "failure_code": "owned_tree_unreadable",
                "cause_class": "none",
                "reason": "controller_children_unavailable",
                "frames": ["owned_tree_scan"],
            },
        )

    def test_first_identity_exception_is_receipted_without_exception_text(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 7700
        files = _fake_proc_files(
            launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1001,
            comm=b"private diagnostic text",
        )
        stat_path = Path("/proc") / str(launcher_pid) / "stat"
        prefix, suffix = files[stat_path].rsplit(b")", 1)
        fields = suffix.split()
        fields[1] = b"malformed-parent"
        files[stat_path] = prefix + b") " + b" ".join(fields) + b"\n"
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path in files:
                return files[path]
            return original_read_bytes(path)

        class GoneBeforePin:
            pid = launcher_pid
            stdout = None

            def poll(self) -> int:
                return 0

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(Path, "read_bytes", new=read_bytes):
                with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                    code, receipt = self._execute(
                        popen=lambda *_args, **_kwargs: GoneBeforePin(),
                        verify_pins=lambda: {},
                        enable_subreaper=lambda: None,
                        read_identity=controller._read_identity,
                        read_children=lambda _pid: set(),
                    )

        self.assertEqual(code, 1)
        self.assertEqual(
            receipt["process_inventory_diagnostic"],
            {
                "failure_scope": "exception",
                "failure_code": "process_identity_invalid",
                "cause_class": "invalid_proc_field",
                "frames": ["identity_read"],
            },
        )
        self.assertNotIn("private diagnostic text", json.dumps(receipt))

    def test_exception_diagnostic_maps_os_error_number_to_fixed_allowlist(self) -> None:
        cases = (
            (errno.ESRCH, "ESRCH"),
            (errno.ENOENT, "ENOENT"),
            (errno.EACCES, "EACCES"),
            (errno.EPERM, "EPERM"),
            (errno.EIO, "EIO"),
            (errno.EINVAL, "other"),
        )
        for error_number, expected_code in cases:
            with self.subTest(errno_code=expected_code):
                error = controller.ControllerError("process_identity_invalid")
                error.__cause__ = OSError(error_number, "private path and text")

                diagnostic = controller._inventory_exception_diagnostic(error)

                self.assertEqual(diagnostic.get("errno_code"), expected_code)
                self.assertNotIn("private path and text", json.dumps(diagnostic))

    def test_identity_os_error_diagnostic_keeps_fixed_errno_and_frames(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 7800
        files = _fake_proc_files(
            launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1001,
        )
        stat_path = Path("/proc") / str(launcher_pid) / "stat"
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path == stat_path:
                raise OSError(errno.EIO, "private path and text")
            if path in files:
                return files[path]
            return original_read_bytes(path)

        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1001,
            uid=os.geteuid(),
            state="S",
        )
        diagnostic: dict[str, object] = {}
        with patch.object(Path, "read_bytes", new=read_bytes):
            try:
                controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={launcher_pid: launcher},
                    read_identity=controller._read_identity,
                    read_children=lambda _pid: set(),
                    diagnostic_sink=diagnostic,
                )
            except controller.ControllerError as error:
                controller._record_inventory_exception(diagnostic, error)
            else:
                self.fail("expected the inventory identity read to fail")

        self.assertEqual(
            diagnostic,
            {
                "failure_scope": "exception",
                "failure_code": "process_identity_invalid",
                "cause_class": "os_error",
                "frames": ["owned_tree_scan", "identity_read"],
                "errno_code": "EIO",
            },
        )
        self.assertNotIn("private path and text", json.dumps(diagnostic))

    def test_static_pins_are_supplied_by_explicit_controller_config(self) -> None:
        pins = controller._verify_static_pins(self.config)
        self.assertEqual(pins["runner_sha256"], self.config.runner_sha256)
        self.assertEqual(pins["source_manifest_sha256"], self.config.manifest_sha256)
        self.assertEqual(pins["site_shim_sha256"], self.config.site_shim_sha256)
        self.assertEqual(self.config.controller_module.stat().st_mode & 0o777, 0o600)
        with tempfile.TemporaryDirectory() as temp:
            loose_copy = Path(temp) / "controller.py"
            loose_copy.write_bytes(self.config.controller_module.read_bytes())
            loose_copy.chmod(0o644)
            loose_config = replace(
                self.config,
                controller_module=loose_copy,
                controller_module_sha256=hashlib.sha256(loose_copy.read_bytes()).hexdigest(),
            )
            with self.assertRaisesRegex(controller.ControllerError, "owned_path_invalid"):
                controller._verify_static_pins(loose_config)
        portable_source = Path(controller.__file__).read_text()
        for host_default in (
            "LAUNCHER_ROOT =",
            "RUNNER_SHA256 =",
            "PRIVATE_PROFILE_HOME =",
            "RECEIPT_ROOT =",
        ):
            self.assertNotIn(host_default, portable_source)

    def test_static_pins_reject_matching_foreign_controller_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            foreign_module = Path(temp) / "bounded_process_controller.py"
            foreign_module.write_bytes(self.config.controller_module.read_bytes())
            foreign_module.chmod(0o600)
            foreign_config = replace(
                self.config,
                controller_module=foreign_module,
                controller_module_sha256=hashlib.sha256(foreign_module.read_bytes()).hexdigest(),
            )

            with self.assertRaisesRegex(
                controller.ControllerError, "controller_module_origin_mismatch"
            ):
                controller._verify_static_pins(foreign_config)

    def _config_mapping(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        for name, value in self.config.__dict__.items():
            if isinstance(value, Path):
                payload[name] = str(value)
            elif isinstance(value, tuple):
                payload[name] = list(value)
            else:
                payload[name] = value
        return payload

    def test_main_is_prepared_only_without_explicit_execution_and_config_loader_fails_closed(
        self,
    ) -> None:
        missing = self.config.receipt_root.parent / "missing-config.json"
        with patch.object(controller, "_execute", side_effect=AssertionError("must not execute")):
            with patch.object(
                controller, "_load_config", side_effect=AssertionError("must not load")
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(controller.main(config=self.config), 0)
                    self.assertEqual(controller.main(argv=[]), 0)
                    self.assertEqual(controller.main(argv=["--config", str(missing)]), 0)
        self.assertEqual(
            output.getvalue().count('"execution_started": false'),
            3,
        )

        valid = json.dumps(self._config_mapping()).encode("utf-8")
        invalid_payloads = (
            b"{",
            json.dumps({**self._config_mapping(), "unexpected": True}).encode("utf-8"),
            b" " * (64 * 1024 + 1),
        )
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for index, payload in enumerate(invalid_payloads):
                with self.subTest(config_case=index):
                    path = directory / f"invalid-{index}.json"
                    path.write_bytes(payload)
                    path.chmod(0o600)
                    with self.assertRaises(controller.ControllerError):
                        controller._load_config(path)
            loose = directory / "loose.json"
            loose.write_bytes(valid)
            loose.chmod(0o644)
            with self.assertRaises(controller.ControllerError):
                controller._load_config(loose)
            target = directory / "target.json"
            target.write_bytes(valid)
            target.chmod(0o600)
            link = directory / "linked.json"
            link.symlink_to(target)
            with self.assertRaises(controller.ControllerError):
                controller._load_config(link)

    def test_main_executes_only_after_config_and_explicit_flag_without_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            config_path.write_text(json.dumps(self._config_mapping()), encoding="utf-8")
            config_path.chmod(0o600)
            captured: dict[str, object] = {}

            def fake_execute(*, config, popen):
                captured["config"] = config
                captured["popen"] = popen
                return 0, {"controller_status": "complete", "receipt_path": "/safe/receipt.json"}

            output = io.StringIO()
            with patch.object(controller, "_execute", side_effect=fake_execute):
                with redirect_stdout(output):
                    code = controller.main(argv=["--config", str(config_path), "--execute"])
            self.assertEqual(code, 0)
            self.assertEqual(captured["config"], self.config)
            self.assertIs(captured["popen"], controller.subprocess.Popen)
            self.assertEqual(
                json.loads(output.getvalue()),
                {"status": "complete", "exit_code": 0, "receipt_path": "/safe/receipt.json"},
            )

    def test_success_exit_requires_successful_launcher_and_cleanup(self) -> None:
        launcher_result = {"status": "finished", "exit_code": 0}
        accepted = {
            "launcher_returncode": 0,
            "cleanup_status": "complete",
            "outer_timed_out": False,
            "identity_failure": False,
        }
        self.assertTrue(controller._successful_exit(launcher_result, **accepted))
        for changed in (
            {**accepted, "launcher_returncode": 1},
            {**accepted, "cleanup_status": "incomplete"},
            {**accepted, "outer_timed_out": True},
            {**accepted, "identity_failure": True},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(controller._successful_exit(launcher_result, **changed))
        self.assertFalse(
            controller._successful_exit({"status": "finished", "exit_code": 1}, **accepted)
        )
        self.assertFalse(
            controller._successful_exit({"status": "unavailable", "exit_code": None}, **accepted)
        )

    def test_deadline_reserves_bounded_signal_and_receipt_window(self) -> None:
        stop, hard = controller._deadlines(100.0, self.config)
        self.assertEqual(stop, 980.0)
        self.assertEqual(hard, 1000.0)
        self.assertEqual(hard - stop, 20.0)
        self.assertEqual(controller.TOTAL_WALL_SECONDS, 900.0)

    def test_child_inventory_includes_children_created_by_each_thread(self) -> None:
        identity = controller.ProcessIdentity(
            pid=99,
            ppid=1,
            pgrp=99,
            session=99,
            starttime=200,
            uid=os.geteuid(),
            state="S",
        )
        with tempfile.TemporaryDirectory() as temp:
            proc_root = Path(temp)
            for tid, value in ((100, "201 202"), (101, "203")):
                task_dir = proc_root / "99" / "task" / str(tid)
                task_dir.mkdir(parents=True)
                (task_dir / "children").write_text(value, encoding="ascii")

            children = controller._read_children(
                99,
                proc_root=proc_root,
                read_identity=lambda requested: identity if requested == 99 else None,
            )
        self.assertEqual(children, {201, 202, 203})

    def test_scan_tracks_groups_using_an_observed_descendant_anchor(self) -> None:
        controller_pid = os.getpid()
        launcher = controller.ProcessIdentity(
            pid=910001,
            ppid=controller_pid,
            pgrp=910001,
            session=910001,
            starttime=100,
            uid=os.geteuid(),
            state="S",
        )
        moved_descendant = controller.ProcessIdentity(
            pid=910002,
            ppid=launcher.pid,
            pgrp=910099,
            session=launcher.session,
            starttime=101,
            uid=os.geteuid(),
            state="S",
        )
        identities = {launcher.pid: launcher, moved_descendant.pid: moved_descendant}
        child_map = {
            controller_pid: {launcher.pid},
            launcher.pid: {moved_descendant.pid},
            moved_descendant.pid: set(),
        }
        observed, groups, unreadable = controller._scan_owned_tree(
            controller_pid=controller_pid,
            launcher=launcher,
            baseline_controller_children=set(),
            known={launcher.pid: launcher},
            read_identity=identities.get,
            read_children=child_map.get,
        )
        self.assertFalse(unreadable)
        self.assertEqual(set(observed), {launcher.pid, moved_descendant.pid})
        self.assertEqual(groups[910099].anchor_pid, moved_descendant.pid)
        self.assertTrue(
            controller._group_has_verified_member(
                groups[910099],
                {launcher.pid: launcher, **observed},
                read_identity=identities.get,
            )
        )

    def test_reaps_only_exact_adopted_zombie_pids(self) -> None:
        controller_pid = os.getpid()
        launcher = controller.ProcessIdentity(
            pid=920001,
            ppid=controller_pid,
            pgrp=920001,
            session=920001,
            starttime=200,
            uid=os.geteuid(),
            state="S",
        )
        adopted_zombie = controller.ProcessIdentity(
            pid=920002,
            ppid=controller_pid,
            pgrp=920001,
            session=920001,
            starttime=201,
            uid=os.geteuid(),
            state="Z",
        )
        other_parent_zombie = controller.ProcessIdentity(
            pid=920003,
            ppid=920004,
            pgrp=920001,
            session=920001,
            starttime=202,
            uid=os.geteuid(),
            state="Z",
        )
        identities = {
            launcher.pid: launcher,
            adopted_zombie.pid: adopted_zombie,
            other_parent_zombie.pid: other_parent_zombie,
        }
        known = dict(identities)
        waited: list[tuple[int, int]] = []

        def waitpid(pid: int, options: int) -> tuple[int, int]:
            waited.append((pid, options))
            return pid, 0

        incomplete = controller._reap_owned_adopted_children(
            known,
            controller_pid=controller_pid,
            launcher_pid=launcher.pid,
            read_identity=identities.get,
            waitpid=waitpid,
        )
        self.assertFalse(incomplete)
        self.assertEqual(waited, [(adopted_zombie.pid, os.WNOHANG)])
        self.assertNotIn(adopted_zombie.pid, known)
        self.assertIn(other_parent_zombie.pid, known)
        zombie_group = controller.GroupIdentity(
            pgid=920001,
            anchor_pid=other_parent_zombie.pid,
            anchor_starttime=other_parent_zombie.starttime,
            uid=os.geteuid(),
            session=920001,
        )
        self.assertFalse(
            controller._group_has_verified_member(
                zombie_group,
                {other_parent_zombie.pid: other_parent_zombie},
                read_identity=identities.get,
            )
        )

    def test_launcher_environment_is_allowlisted_and_preserves_home_bindings(
        self,
    ) -> None:
        parent = {
            "HOME": "/operator/home",
            "CODEX_HOME": "/operator/codex",
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "TERM": "xterm-256color",
            "OPENAI_API_KEY": "sentinel-openai-secret",
            "ANTHROPIC_API_KEY": "sentinel-claude-secret",
            "AWS_SECRET_ACCESS_KEY": "sentinel-cloud-secret",
            "CAO_AUTH_LOCAL_TOKEN": "sentinel-cao-secret",
            "CAO_TEST_POSTGRES_METADATA_FILE": "/operator/metadata.json",
        }
        result = controller._launcher_environment(parent, self.config)
        self.assertEqual(result["HOME"], parent["HOME"])
        self.assertEqual(result["CODEX_HOME"], parent["CODEX_HOME"])
        self.assertEqual(result["PATH"], parent["PATH"])
        self.assertEqual(result["CAO_PRIVATE_PROFILE_HOME"], str(self.config.private_profile_home))
        self.assertEqual(result["PYTHONPATH"], str(self.config.site_shim.parent))
        self.assertEqual(result["PYTHONNOUSERSITE"], "1")
        self.assertEqual(result["PYTHONDONTWRITEBYTECODE"], "1")
        for forbidden in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "CAO_AUTH_LOCAL_TOKEN",
            "CAO_TEST_POSTGRES_METADATA_FILE",
        ):
            self.assertNotIn(forbidden, result)

    def test_source_pin_refusal_precedes_any_process_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runner.py"
            path.write_bytes(b"pinned bytes")
            path.chmod(0o600)
            with self.assertRaisesRegex(controller.ControllerError, "runner_pin_mismatch"):
                controller._verify_static_pins(
                    replace(self.config, runner=path, runner_sha256="0" * 64)
                )

        with tempfile.TemporaryDirectory() as temp:
            receipt_root = Path(temp) / "receipts"
            attempted_spawn: list[tuple[tuple[object, ...], dict[str, object]]] = []

            def forbidden_spawn(*args: object, **kwargs: object) -> None:
                attempted_spawn.append((args, kwargs))
                raise AssertionError("prelaunch refusal must not spawn a process")

            with patch.object(controller, "RECEIPT_ROOT", receipt_root):
                with patch.object(
                    controller,
                    "_verify_static_pins",
                    side_effect=controller.ControllerError("runner_pin_mismatch"),
                ):
                    code, receipt = self._execute(popen=forbidden_spawn)
            self.assertEqual(code, 1)
            self.assertEqual(receipt["controller_status"], "prelaunch_refused")
            self.assertEqual(receipt["controller_failure"], "runner_pin_mismatch")
            self.assertEqual(attempted_spawn, [])
            receipt_path = Path(receipt["receipt_path"])
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)

    def test_stdout_setup_failure_after_identity_pin_signals_owned_group(self) -> None:
        controller_pid = os.getpid()
        launcher = controller.ProcessIdentity(
            pid=930001,
            ppid=controller_pid,
            pgrp=930001,
            session=930001,
            starttime=300,
            uid=os.geteuid(),
            state="S",
        )
        alive = True
        children_calls = 0
        events: list[str] = []
        identity_reads_at_stdout: list[int] = []
        identity_reads = 0

        class BrokenPipeProcess:
            pid = launcher.pid

            @property
            def stdout(self) -> None:
                events.append("stdout")
                identity_reads_at_stdout.append(identity_reads)
                return None

            def poll(self) -> int | None:
                return None if alive else 0

        def popen(*_args: object, **_kwargs: object) -> BrokenPipeProcess:
            events.append("popen")
            return BrokenPipeProcess()

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            nonlocal identity_reads
            events.append("identity")
            identity_reads += 1
            return launcher if alive and pid == launcher.pid else None

        def read_children(pid: int) -> set[int] | None:
            nonlocal children_calls
            if pid == launcher.pid:
                return set()
            if pid == controller_pid:
                children_calls += 1
                return set() if children_calls == 1 else ({launcher.pid} if alive else set())
            return set()

        signaled: list[tuple[int, int]] = []

        def killpg(pgid: int, signum: int) -> None:
            nonlocal alive
            signaled.append((pgid, signum))
            alive = False

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                code, receipt = self._execute(
                    popen=popen,
                    verify_pins=lambda: {},
                    enable_subreaper=lambda: None,
                    read_identity=read_identity,
                    read_children=read_children,
                    killpg=killpg,
                )

        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_failure"], "launcher_stdout_unavailable")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertFalse(receipt["temporary_files_removed"])
        self.assertIsNone(receipt["process_inventory_diagnostic"])
        self.assertEqual(signaled, [(launcher.pgrp, signal.SIGINT)])
        self.assertEqual(events[:3], ["popen", "identity", "stdout"])
        self.assertEqual(identity_reads_at_stdout, [1])

    def test_first_launcher_pin_rejects_identity_with_wrong_parent(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 929
        wrong_parent = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid + 1,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=300,
            uid=os.geteuid(),
            state="S",
        )
        spawned = False
        alive = True
        identity_reads = 0
        stdout_accesses = 0
        actions: list[str] = []

        class WrongParentProcess:
            pid = launcher_pid

            @property
            def stdout(self) -> None:
                nonlocal stdout_accesses
                stdout_accesses += 1
                return None

            def poll(self) -> int | None:
                return None if alive else 0

            def terminate(self) -> None:
                nonlocal alive
                actions.append("terminate")
                alive = False

            def kill(self) -> None:
                nonlocal alive
                actions.append("kill")
                alive = False

        def popen(*_args: object, **_kwargs: object) -> WrongParentProcess:
            nonlocal spawned
            spawned = True
            return WrongParentProcess()

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            nonlocal identity_reads
            if pid != launcher_pid:
                return None
            identity_reads += 1
            return wrong_parent if identity_reads == 1 else None

        def read_children(pid: int) -> set[int]:
            return {launcher_pid} if pid == controller_pid and spawned else set()

        signaled: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                code, receipt = self._execute(
                    popen=popen,
                    verify_pins=lambda: {},
                    enable_subreaper=lambda: None,
                    read_identity=read_identity,
                    read_children=read_children,
                    killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                    waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                )

        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_failure"], "launcher_identity_unverified")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertFalse(receipt["temporary_files_removed"])
        self.assertEqual(identity_reads, 1)
        self.assertEqual(stdout_accesses, 0)
        self.assertFalse(alive)
        self.assertEqual(actions, ["terminate"])
        self.assertEqual(signaled, [])

    def test_unpinned_live_launcher_is_stopped_through_its_popen_handle(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 930
        spawned = False
        alive = True
        stdout_accesses = 0
        actions: list[str] = []

        class UnpinnedLiveProcess:
            pid = launcher_pid

            @property
            def stdout(self) -> None:
                nonlocal stdout_accesses
                stdout_accesses += 1
                return None

            def poll(self) -> int | None:
                return None if alive else 0

            def terminate(self) -> None:
                nonlocal alive
                actions.append("terminate")
                alive = False

            def kill(self) -> None:
                nonlocal alive
                actions.append("kill")
                alive = False

        def popen(*_args: object, **_kwargs: object) -> UnpinnedLiveProcess:
            nonlocal spawned
            spawned = True
            return UnpinnedLiveProcess()

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            return None if pid == launcher_pid else None

        def read_children(pid: int) -> set[int]:
            return {launcher_pid} if pid == controller_pid and spawned else set()

        signaled: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                code, receipt = self._execute(
                    popen=popen,
                    verify_pins=lambda: {},
                    enable_subreaper=lambda: None,
                    read_identity=read_identity,
                    read_children=read_children,
                    killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                    waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                )

        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_failure"], "launcher_identity_unverified")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertFalse(receipt["temporary_files_removed"])
        self.assertFalse(alive)
        self.assertEqual(actions, ["terminate"])
        self.assertEqual(stdout_accesses, 0)
        self.assertEqual(signaled, [])

    def test_unpinned_live_launcher_escalates_to_popen_kill_within_deadline(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 931
        spawned = False
        alive = True
        now = 0.0
        actions: list[str] = []

        class UnresponsiveProcess:
            pid = launcher_pid
            stdout = None

            def poll(self) -> int | None:
                return None if alive else 0

            def terminate(self) -> None:
                actions.append("terminate")

            def kill(self) -> None:
                nonlocal alive
                actions.append("kill")
                alive = False

        def popen(*_args: object, **_kwargs: object) -> UnresponsiveProcess:
            nonlocal spawned
            spawned = True
            return UnresponsiveProcess()

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            return None if pid == launcher_pid else None

        def read_children(pid: int) -> set[int]:
            return {launcher_pid} if pid == controller_pid and spawned else set()

        def clock() -> float:
            return now

        def sleep(duration: float) -> None:
            nonlocal now
            now += duration

        signaled: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                code, receipt = self._execute(
                    popen=popen,
                    clock=clock,
                    sleep=sleep,
                    verify_pins=lambda: {},
                    enable_subreaper=lambda: None,
                    read_identity=read_identity,
                    read_children=read_children,
                    killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                    waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                )

        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_failure"], "launcher_identity_unverified")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertFalse(receipt["temporary_files_removed"])
        self.assertFalse(alive)
        self.assertEqual(actions, ["terminate", "kill"])
        self.assertEqual(signaled, [])
        self.assertLessEqual(now, controller.TOTAL_WALL_SECONDS)

    def test_unverified_controller_identity_stops_only_exact_popen_child(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 932
        alive = True
        actions: list[str] = []
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=302,
            uid=os.geteuid(),
            state="S",
        )

        class PinnedLiveProcess:
            pid = launcher_pid
            stdout = None

            def poll(self) -> int | None:
                return None if alive else 0

            def terminate(self) -> None:
                nonlocal alive
                actions.append("terminate")
                alive = False

            def kill(self) -> None:
                nonlocal alive
                actions.append("kill")
                alive = False

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            return launcher if pid == launcher_pid else None

        signaled: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                with patch.object(controller, "_read_identity", return_value=None):
                    code, receipt = self._execute(
                        popen=lambda *_args, **_kwargs: PinnedLiveProcess(),
                        verify_pins=lambda: {},
                        enable_subreaper=lambda: None,
                        read_identity=read_identity,
                        read_children=lambda _pid: set(),
                        killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                        waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                    )

        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_failure"], "launcher_stdout_unavailable")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertFalse(receipt["temporary_files_removed"])
        self.assertFalse(alive)
        self.assertEqual(actions, ["terminate"])
        self.assertEqual(signaled, [])

    def test_first_pin_failure_never_adopts_a_reused_pid(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 930
        replacement = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=301,
            uid=os.geteuid(),
            state="S",
        )
        spawned = False
        replacement_active = True
        identity_reads = 0
        stdout_accesses = 0
        events: list[str] = []

        class ReusedPidProcess:
            pid = launcher_pid

            @property
            def stdout(self) -> None:
                nonlocal stdout_accesses
                stdout_accesses += 1
                events.append("stdout")
                return None

            def poll(self) -> int:
                # Popen still owns and can reap only its original child. The
                # replacement at this numeric PID is a different process.
                return 0

        def popen(*_args: object, **_kwargs: object) -> ReusedPidProcess:
            nonlocal spawned
            spawned = True
            events.append("popen")
            return ReusedPidProcess()

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            nonlocal identity_reads
            if pid != launcher_pid:
                return None
            identity_reads += 1
            events.append("identity")
            # The original child is gone before its first readable /proc
            # identity. A later read of the same number sees a reused PID.
            return None if identity_reads == 1 else replacement if replacement_active else None

        def read_children(pid: int) -> set[int]:
            if pid == controller_pid:
                return {launcher_pid} if spawned else set()
            return set()

        signaled: list[tuple[int, int]] = []

        def killpg(pgid: int, signum: int) -> None:
            nonlocal replacement_active
            signaled.append((pgid, signum))
            replacement_active = False

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                code, receipt = self._execute(
                    popen=popen,
                    verify_pins=lambda: {},
                    enable_subreaper=lambda: None,
                    read_identity=read_identity,
                    read_children=read_children,
                    killpg=killpg,
                    waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                )

        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_failure"], "launcher_identity_unverified")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertTrue(receipt["unreadable_process_inventory"])
        self.assertEqual(events, ["popen", "identity"])
        self.assertEqual(identity_reads, 1)
        self.assertEqual(stdout_accesses, 0)
        self.assertEqual(signaled, [])

    def test_initial_identity_failure_does_not_adopt_reused_launcher_pid(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 1000
        replacement = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=935,
            uid=os.geteuid(),
            state="S",
        )
        now = 0.0
        child_state = "before_spawn"
        read_count = 0

        class ReusedPidProcess:
            pid = launcher_pid

            @property
            def stdout(self) -> object:
                raise AssertionError("stdout must not be touched before identity pin")

            def poll(self) -> int:
                nonlocal child_state
                child_state = "reaped"
                return 0

        def popen(*_args: object, **_kwargs: object) -> ReusedPidProcess:
            nonlocal child_state
            child_state = "spawned"
            return ReusedPidProcess()

        def clock() -> float:
            return now

        def sleep(duration: float) -> None:
            nonlocal now
            now += duration

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            nonlocal read_count
            if pid != launcher_pid:
                return None
            read_count += 1
            return replacement if child_state == "reaped" else None

        def read_children(pid: int) -> set[int]:
            if pid == controller_pid:
                return {launcher_pid} if child_state != "before_spawn" else set()
            return set()

        signaled: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                code, receipt = self._execute(
                    popen=popen,
                    clock=clock,
                    sleep=sleep,
                    verify_pins=lambda: {},
                    enable_subreaper=lambda: None,
                    read_identity=read_identity,
                    read_children=read_children,
                    killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                    waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                )
        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_status"], "controller_error")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertTrue(receipt["unreadable_process_inventory"])
        self.assertEqual(signaled, [])
        self.assertEqual(read_count, 1)

    def test_postspawn_failure_after_identity_pin_signals_exact_owned_group(
        self,
    ) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 1001
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=936,
            uid=os.geteuid(),
            state="S",
        )
        alive = True
        spawned = False
        child_reads = 0
        stream_read, stream_write = os.pipe()

        class PinnedProcess:
            pid = launcher_pid
            stdout = os.fdopen(stream_read, "rb")

            def poll(self) -> int | None:
                return None if alive else 0

        def popen(*_args: object, **_kwargs: object) -> PinnedProcess:
            nonlocal spawned
            spawned = True
            return PinnedProcess()

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            return launcher if alive and pid == launcher.pid else None

        def read_children(pid: int) -> set[int]:
            nonlocal child_reads
            if pid == controller_pid:
                child_reads += 1
                if child_reads == 2:
                    raise OSError("injected post-pin inventory failure")
                return {launcher.pid} if spawned else set()
            return set()

        signaled: list[tuple[int, int]] = []

        def killpg(pgid: int, signum: int) -> None:
            nonlocal alive
            signaled.append((pgid, signum))
            alive = False

        os.close(stream_write)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "receipts"):
                code, receipt = self._execute(
                    popen=popen,
                    verify_pins=lambda: {},
                    enable_subreaper=lambda: None,
                    read_identity=read_identity,
                    read_children=read_children,
                    killpg=killpg,
                    waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                )
            PinnedProcess.stdout.close()

        self.assertEqual(code, 1)
        self.assertEqual(receipt["controller_failure"], "controller_internal_error")
        self.assertEqual(receipt["cleanup_status"], "incomplete")
        self.assertEqual(signaled, [(launcher.pgrp, signal.SIGINT)], receipt)

    def test_outer_receipt_write_failure_forces_nonzero_exit(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = 940001
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=400,
            uid=os.geteuid(),
            state="S",
        )
        identity_reads = 0
        stream_read, stream_write = os.pipe()

        class CompletedProcess:
            pid = launcher_pid
            stdout = os.fdopen(stream_read, "rb")

            def poll(self) -> int:
                return 0

        def popen(*_args: object, **_kwargs: object) -> CompletedProcess:
            return CompletedProcess()

        def read_identity(pid: int) -> controller.ProcessIdentity | None:
            nonlocal identity_reads
            if pid != launcher_pid:
                return None
            identity_reads += 1
            return launcher if identity_reads == 1 else None

        def read_children(_pid: int) -> set[int]:
            return set()

        outer_path: Path | None = None

        def failed_writer(path: str, descriptor: int, _receipt: dict[str, object]) -> None:
            nonlocal outer_path
            outer_path = Path(path)
            os.close(descriptor)
            raise OSError("injected receipt write failure")

        with tempfile.TemporaryDirectory() as temp:
            launch_root = Path(temp) / "launcher"
            inner_path = launch_root / "runs" / ("a" * 32) / "receipt.json"
            inner_path.parent.mkdir(parents=True)
            inner_receipt = {
                "cleanup_status": "complete",
                "temporary_files_removed": True,
                "operator_auth_unchanged": True,
                "source_unchanged": True,
            }
            inner_path.write_text(json.dumps(inner_receipt), encoding="utf-8")
            inner_path.chmod(0o600)
            stdout_payload = json.dumps(
                {
                    "status": "finished",
                    "receipt_path": str(inner_path),
                    "exit_code": 0,
                }
            ).encode("utf-8")
            os.write(stream_write, stdout_payload)
            os.close(stream_write)

            with patch.object(controller, "LAUNCHER_ROOT", launch_root):
                with patch.object(controller, "RECEIPT_ROOT", Path(temp) / "outer-receipts"):
                    code, receipt = self._execute(
                        popen=popen,
                        verify_pins=lambda: {},
                        enable_subreaper=lambda: None,
                        read_identity=read_identity,
                        read_children=read_children,
                        killpg=lambda _pgid, _signum: self.fail("unexpected process signal"),
                        waitpid=lambda _pid, _options: self.fail("unexpected waitpid"),
                        write_receipt=failed_writer,
                    )
            CompletedProcess.stdout.close()

            self.assertEqual(code, 1)
            self.assertEqual(receipt["controller_failure"], "outer_receipt_write_failed")
            self.assertEqual(receipt["cleanup_status"], "incomplete")
            self.assertFalse(receipt["temporary_files_removed"])
            self.assertIsNotNone(outer_path)
            assert outer_path is not None
            self.assertEqual(outer_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(outer_path.stat().st_size, 0)

    def test_signals_only_a_process_group_with_matching_identity(self) -> None:
        identity = controller.ProcessIdentity(
            pid=101,
            ppid=10,
            pgrp=101,
            session=101,
            starttime=9001,
            uid=os.geteuid(),
            state="S",
        )
        group = controller.GroupIdentity(
            pgid=101,
            anchor_pid=101,
            anchor_starttime=9001,
            uid=os.geteuid(),
            session=101,
        )
        sent: list[tuple[int, int]] = []
        with patch.object(controller, "_read_identity", return_value=identity):
            signaled = controller._signal_group_if_verified(
                group,
                {101: identity},
                signal.SIGTERM,
                killpg=lambda pgid, signum: sent.append((pgid, signum)),
                read_identity=lambda _pid: identity,
            )
        self.assertTrue(signaled)
        self.assertEqual(sent, [(101, signal.SIGTERM)])

        reused = controller.ProcessIdentity(**{**identity.__dict__, "starttime": 9002})
        sent.clear()
        with patch.object(controller, "_read_identity", return_value=reused):
            signaled = controller._signal_group_if_verified(
                group,
                {101: identity},
                signal.SIGKILL,
                killpg=lambda pgid, signum: sent.append((pgid, signum)),
                read_identity=lambda _pid: reused,
            )
        self.assertFalse(signaled)
        self.assertEqual(sent, [])

    def test_termination_ladder_stops_after_verified_group_exits(self) -> None:
        identity = controller.ProcessIdentity(
            pid=202,
            ppid=10,
            pgrp=202,
            session=202,
            starttime=9010,
            uid=os.geteuid(),
            state="S",
        )
        group = controller.GroupIdentity(
            pgid=202,
            anchor_pid=202,
            anchor_starttime=9010,
            uid=os.geteuid(),
            session=202,
        )
        known = {202: identity}
        active = True
        now = 0.0
        sent: list[tuple[int, int]] = []

        def read_identity(pid: int):
            return identity if active and pid == 202 else None

        def killpg(pgid: int, signum: int) -> None:
            nonlocal active
            sent.append((pgid, signum))
            if signum == signal.SIGINT:
                active = False

        def clock() -> float:
            return now

        def sleep(duration: float) -> None:
            nonlocal now
            now += duration

        remaining = controller._terminate_verified_groups(
            {202: group},
            known,
            launcher_pgid=101,
            clock=clock,
            sleep=sleep,
            killpg=killpg,
            read_identity=read_identity,
        )
        self.assertEqual(remaining, set())
        self.assertEqual(sent, [(202, signal.SIGINT)])

    def test_proc_identity_esrch_at_stat_and_status_is_gone(self) -> None:
        for index, leaf in enumerate(("stat", "status")):
            with self.subTest(proc_file=leaf):
                pid = max(os.getpid(), os.getsid(os.getpid())) + 10000 + index
                files = _fake_proc_files(
                    pid,
                    ppid=1,
                    pgrp=pid,
                    session=pid,
                    starttime=1200,
                )
                missing_path = Path("/proc") / str(pid) / leaf
                reads: list[str] = []
                original_read_bytes = Path.read_bytes

                def read_bytes(path: Path) -> bytes:
                    if path == missing_path:
                        reads.append(leaf)
                        raise ProcessLookupError(errno.ESRCH, "private proc race")
                    if path in files:
                        return files[path]
                    return original_read_bytes(path)

                with patch.object(Path, "read_bytes", new=read_bytes):
                    identity = controller._read_identity(pid)

                self.assertIsNone(identity)
                self.assertEqual(reads, [leaf])

    def test_proc_identity_non_esrch_process_lookup_stays_fail_closed(self) -> None:
        pid = max(os.getpid(), os.getsid(os.getpid())) + 10005
        stat_path = Path("/proc") / str(pid) / "stat"
        original_read_bytes = Path.read_bytes

        for code in (errno.EPERM, errno.EIO):
            with self.subTest(errno=code):

                def read_bytes(path: Path) -> bytes:
                    if path == stat_path:
                        raise ProcessLookupError(code, "private proc error")
                    return original_read_bytes(path)

                with patch.object(Path, "read_bytes", new=read_bytes):
                    with self.assertRaises(controller.ControllerError) as caught:
                        controller._read_identity(pid)

                self.assertEqual(caught.exception.args, ("process_identity_invalid",))
                self.assertIsInstance(caught.exception.__cause__, ProcessLookupError)
                self.assertEqual(caught.exception.__cause__.errno, code)

    def test_task_root_esrch_requires_absent_pinned_identity(self) -> None:
        pid = 700001
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1300,
            uid=os.geteuid(),
            state="S",
        )
        changed = controller.ProcessIdentity(
            **{**pinned.__dict__, "starttime": pinned.starttime + 1}
        )
        task_directory = Path("/synthetic-proc") / str(pid) / "task"
        original_iterdir = Path.iterdir

        for case, identities, expected in (
            ("gone", [pinned, pinned, None], "identity_missing_after_task_root_esrch"),
            ("reused", [pinned, pinned, changed], "identity_changed_after_task_root_esrch"),
            ("still_live", [pinned, pinned, pinned], "process_children_unreadable"),
        ):
            with self.subTest(case=case):
                reads = 0

                def read_identity(requested: int):
                    nonlocal reads
                    if requested != pid:
                        return None
                    result = identities[min(reads, len(identities) - 1)]
                    reads += 1
                    return result

                def iterdir(path: Path):
                    if path == task_directory:
                        raise ProcessLookupError(errno.ESRCH, "private task-root race")
                    return original_iterdir(path)

                with patch.object(Path, "iterdir", new=iterdir):
                    with self.assertRaises(controller.ControllerError) as caught:
                        controller._read_children(
                            pid,
                            proc_root=Path("/synthetic-proc"),
                            read_identity=read_identity,
                            expected_identity=pinned,
                        )

                if expected.startswith("identity_"):
                    diagnostic = controller._inventory_exception_diagnostic(caught.exception)
                    self.assertEqual(diagnostic["inventory_branch"], expected)
                else:
                    self.assertEqual(caught.exception.args, (expected,))
                self.assertIsInstance(caught.exception.__cause__, ProcessLookupError)
                self.assertEqual(caught.exception.__cause__.errno, errno.ESRCH)
                self.assertEqual(reads, 3)

    def test_tid_children_esrch_revalidates_pin_and_never_infers_empty(self) -> None:
        pid = 700011
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1310,
            uid=os.geteuid(),
            state="S",
        )
        task_directory = Path("/synthetic-proc") / str(pid) / "task"
        task_directory_path = task_directory / str(pid)
        children_path = task_directory_path / "children"
        original_iterdir = Path.iterdir
        original_read_text = Path.read_text

        def iterdir(path: Path):
            if path == task_directory:
                return iter([task_directory_path])
            return original_iterdir(path)

        def read_text(path: Path, encoding=None):
            if path == children_path:
                raise ProcessLookupError(errno.ESRCH, "private TID race")
            return original_read_text(path, encoding=encoding)

        reads = 0

        def read_identity(requested: int):
            nonlocal reads
            if requested != pid:
                return None
            reads += 1
            return pinned if reads < 3 else None

        with (
            patch.object(Path, "iterdir", new=iterdir),
            patch.object(Path, "read_text", new=read_text),
        ):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._read_children(
                    pid,
                    proc_root=Path("/synthetic-proc"),
                    read_identity=read_identity,
                    expected_identity=pinned,
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(diagnostic["inventory_branch"], "identity_missing_after_tid_esrch")
        self.assertIsInstance(caught.exception.__cause__, ProcessLookupError)
        self.assertEqual(caught.exception.__cause__.errno, errno.ESRCH)
        self.assertEqual(reads, 3)

    def test_persistent_tid_children_esrch_fails_closed_after_bounded_retries(self) -> None:
        pid = 700021
        pinned = controller.ProcessIdentity(
            pid=pid,
            ppid=1,
            pgrp=pid,
            session=pid,
            starttime=1320,
            uid=os.geteuid(),
            state="S",
        )
        task_directory = Path("/synthetic-proc") / str(pid) / "task"
        task_directory_path = task_directory / str(pid)
        children_path = task_directory_path / "children"
        original_iterdir = Path.iterdir
        original_read_text = Path.read_text
        child_reads = 0

        def iterdir(path: Path):
            if path == task_directory:
                return iter([task_directory_path])
            return original_iterdir(path)

        def read_text(path: Path, encoding=None):
            nonlocal child_reads
            if path == children_path:
                child_reads += 1
                raise ProcessLookupError(errno.ESRCH, "private TID race")
            return original_read_text(path, encoding=encoding)

        with (
            patch.object(Path, "iterdir", new=iterdir),
            patch.object(Path, "read_text", new=read_text),
        ):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._read_children(
                    pid,
                    proc_root=Path("/synthetic-proc"),
                    read_identity=lambda requested: pinned if requested == pid else None,
                    expected_identity=pinned,
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(diagnostic["inventory_branch"], "tid_children_esrch_retry_exhausted")
        self.assertEqual(child_reads, controller.MAX_TASK_INVENTORY_ATTEMPTS)
        self.assertIsInstance(caught.exception.__cause__, ProcessLookupError)
        self.assertEqual(caught.exception.__cause__.errno, errno.ESRCH)

    def test_scan_restarts_after_known_launcher_disappears_and_finds_adopted_child(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10100
        adopted_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1400,
            uid=os.geteuid(),
            state="S",
        )
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=adopted_pid,
            session=adopted_pid,
            starttime=1401,
            uid=os.geteuid(),
            state="S",
        )
        identities = {launcher_pid: launcher}
        controller_snapshots = 0
        launcher_reads = 0

        def read_identity(requested: int):
            nonlocal launcher_reads
            if requested == controller_pid:
                return controller_identity
            if requested == launcher_pid:
                launcher_reads += 1
                if launcher_reads == 1:
                    identities.pop(launcher_pid, None)
                    identities[adopted_pid] = adopted
                    return None
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots
            if requested == controller_pid:
                controller_snapshots += 1
                return {launcher_pid} if controller_snapshots == 1 else {adopted_pid}
            return set()

        with patch.object(controller, "_read_identity", new=read_identity):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=read_identity,
                read_children=read_children,
            )

        self.assertFalse(unreadable)
        self.assertEqual(controller_snapshots, 2)
        self.assertEqual(set(observed), {adopted_pid})
        self.assertNotIn(launcher_pid, groups)
        self.assertIn(adopted_pid, groups)

    def test_scan_restarts_when_unpinned_child_disappears_before_identity_pin(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10110
        transient_pid = launcher_pid + 1
        adopted_pid = launcher_pid + 2
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1410,
            uid=os.geteuid(),
            state="S",
        )
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=adopted_pid,
            session=adopted_pid,
            starttime=1412,
            uid=os.geteuid(),
            state="S",
        )
        identities = {launcher_pid: launcher, transient_pid: launcher}
        controller_snapshots = 0
        launcher_child_reads = 0
        transient_identity_reads = 0

        def read_identity(requested: int):
            nonlocal transient_identity_reads
            if requested == controller_pid:
                return controller_identity
            if requested == transient_pid:
                transient_identity_reads += 1
                if transient_identity_reads == 1:
                    identities.pop(transient_pid, None)
                    identities[adopted_pid] = adopted
                    return None
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots, launcher_child_reads
            if requested == controller_pid:
                controller_snapshots += 1
                return {launcher_pid} if controller_snapshots == 1 else {launcher_pid, adopted_pid}
            if requested == launcher_pid:
                launcher_child_reads += 1
                return {transient_pid} if launcher_child_reads == 1 else set()
            return set()

        with patch.object(controller, "_read_identity", new=read_identity):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=read_identity,
                read_children=read_children,
            )

        self.assertFalse(unreadable)
        self.assertEqual(controller_snapshots, 2)
        self.assertEqual(set(observed), {launcher_pid, adopted_pid})
        self.assertNotIn(transient_pid, observed)
        self.assertNotIn(transient_pid, groups)
        self.assertIn(adopted_pid, groups)

    def test_scan_bounds_restart_for_confirmed_dead_launcher(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10120
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1420,
            uid=os.geteuid(),
            state="S",
        )
        controller_snapshots = 0
        launcher_reads = 0

        def read_identity(requested: int):
            nonlocal launcher_reads
            if requested == controller_pid:
                return controller_identity
            if requested == launcher_pid:
                launcher_reads += 1
                return None
            return None

        def read_children(requested: int):
            nonlocal controller_snapshots
            if requested == controller_pid:
                controller_snapshots += 1
            return set()

        with patch.object(controller, "_read_identity", new=read_identity):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=read_identity,
                read_children=read_children,
            )

        self.assertFalse(unreadable)
        self.assertEqual(controller_snapshots, 2)
        self.assertEqual(launcher_reads, 2)
        self.assertEqual(observed, {})
        self.assertEqual(groups, {})

    def test_scan_fails_closed_if_unpinned_pid_reappears_after_confirmed_absence(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10130
        transient_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1430,
            uid=os.geteuid(),
            state="S",
        )
        transient_replacement = controller.ProcessIdentity(
            pid=transient_pid,
            ppid=controller_pid,
            pgrp=transient_pid,
            session=transient_pid,
            starttime=1431,
            uid=os.geteuid(),
            state="S",
        )
        controller_snapshots = 0
        launcher_child_reads = 0
        transient_reads = 0
        replacement_children_reads = 0

        def read_identity(requested: int):
            nonlocal transient_reads
            if requested == controller_pid:
                return controller_identity
            if requested == transient_pid:
                transient_reads += 1
                return None if transient_reads == 1 else transient_replacement
            return launcher if requested == launcher_pid else None

        def read_children(requested: int):
            nonlocal controller_snapshots, launcher_child_reads, replacement_children_reads
            if requested == controller_pid:
                controller_snapshots += 1
                return (
                    {launcher_pid} if controller_snapshots == 1 else {launcher_pid, transient_pid}
                )
            if requested == launcher_pid:
                launcher_child_reads += 1
                return {transient_pid} if launcher_child_reads == 1 else set()
            if requested == transient_pid:
                replacement_children_reads += 1
            return set()

        with patch.object(controller, "_read_identity", new=read_identity):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=read_identity,
                read_children=read_children,
            )

        self.assertTrue(unreadable)
        self.assertEqual(controller_snapshots, 2)
        self.assertNotIn(transient_pid, observed)
        self.assertNotIn(transient_pid, groups)
        self.assertEqual(replacement_children_reads, 0)

    def test_scan_missing_identity_retry_budget_exhaustion_stays_closed(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10140
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1440,
            uid=os.geteuid(),
            state="S",
        )
        controller_snapshots = 0
        transient_pid = launcher_pid + 1
        identities: dict[int, controller.ProcessIdentity] = {launcher_pid: launcher}

        def read_identity(requested: int):
            if requested == controller_pid:
                return controller_identity
            return identities.get(requested)

        def read_children(requested: int):
            nonlocal controller_snapshots, transient_pid
            if requested == controller_pid:
                controller_snapshots += 1
                current = transient_pid
                transient_pid += 1
                return {launcher_pid, current}
            return set()

        with patch.object(controller, "_read_identity", new=read_identity):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={launcher_pid: launcher},
                    read_identity=read_identity,
                    read_children=read_children,
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(caught.exception.args, ("process_task_inventory_raced",))
        self.assertEqual(diagnostic["inventory_branch"], "owned_tree_retry_budget_exhausted")
        self.assertEqual(controller_snapshots, controller.MAX_OWNED_TREE_SCAN_ATTEMPTS)

    def test_scan_recovers_task_churn_with_pinned_process_census_and_keeps_escaped_groups(
        self,
    ) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10300
        child_a_pid = launcher_pid + 1
        child_b_pid = launcher_pid + 2
        unrelated_pid = launcher_pid + 3
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        # Both workers are direct children of the pinned launcher, but each
        # has escaped its original session/process group. A TID census cannot
        # be used as their ownership proof; the PPID edges can.
        child_a = controller.ProcessIdentity(
            pid=child_a_pid,
            ppid=launcher_pid,
            pgrp=child_a_pid,
            session=child_a_pid,
            starttime=21,
            uid=os.geteuid(),
            state="S",
        )
        child_b = controller.ProcessIdentity(
            pid=child_b_pid,
            ppid=launcher_pid,
            pgrp=child_b_pid,
            session=child_b_pid,
            starttime=22,
            uid=os.geteuid(),
            state="S",
        )
        unrelated = controller.ProcessIdentity(
            pid=unrelated_pid,
            ppid=unrelated_pid + 50,
            pgrp=unrelated_pid,
            session=unrelated_pid,
            starttime=23,
            uid=os.geteuid(),
            state="S",
        )
        identities = {
            controller_pid: root,
            launcher_pid: launcher,
            child_a_pid: child_a,
            child_b_pid: child_b,
            unrelated_pid: unrelated,
        }
        census_calls = 0

        def read_children(pid: int):
            if pid == controller_pid:
                return {launcher_pid}
            if pid == launcher_pid:
                raise controller.ControllerError(
                    "process_task_inventory_raced",
                    inventory_branch="tid_set_changed_retry_exhausted",
                )
            return set()

        def read_process_census():
            nonlocal census_calls
            census_calls += 1
            return {pid: identity.ppid for pid, identity in identities.items()}

        with patch.object(controller, "_read_identity", new=identities.get):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=identities.get,
                read_children=read_children,
                read_process_census=read_process_census,
            )

        self.assertFalse(unreadable)
        self.assertEqual(set(observed), {launcher_pid, child_a_pid, child_b_pid})
        self.assertEqual(set(groups), {launcher_pid, child_a_pid, child_b_pid})
        self.assertNotIn(unrelated_pid, observed)
        self.assertEqual(census_calls, 1)
        self.assertLessEqual(census_calls, controller.MAX_TASK_INVENTORY_ATTEMPTS)

    def test_scan_census_fallback_handles_exhausted_tid_children_entries(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10320
        child_pid = launcher_pid + 1
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        child = controller.ProcessIdentity(
            pid=child_pid,
            ppid=launcher_pid,
            pgrp=child_pid,
            session=child_pid,
            starttime=21,
            uid=os.geteuid(),
            state="S",
        )
        identities = {controller_pid: root, launcher_pid: launcher, child_pid: child}

        for branch in (
            "tid_children_enoent_retry_exhausted",
            "tid_children_esrch_retry_exhausted",
        ):
            with self.subTest(branch=branch):
                census_calls = 0

                def read_children(pid: int):
                    if pid == controller_pid:
                        return {launcher_pid}
                    if pid == launcher_pid:
                        raise controller.ControllerError(
                            "process_task_inventory_raced",
                            inventory_branch=branch,
                        )
                    return set()

                def read_process_census():
                    nonlocal census_calls
                    census_calls += 1
                    return {pid: identity.ppid for pid, identity in identities.items()}

                with patch.object(controller, "_read_identity", new=identities.get):
                    observed, groups, unreadable = controller._scan_owned_tree(
                        controller_pid=controller_pid,
                        launcher=launcher,
                        baseline_controller_children=set(),
                        known={launcher_pid: launcher},
                        read_identity=identities.get,
                        read_children=read_children,
                        read_process_census=read_process_census,
                    )

                self.assertFalse(unreadable)
                self.assertEqual(set(observed), {launcher_pid, child_pid})
                self.assertEqual(set(groups), {launcher_pid, child_pid})
                self.assertEqual(census_calls, 1)

    def test_scan_recovers_synthetic_proc_task_reader_churn(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10350
        child_pid = launcher_pid + 1
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        child = controller.ProcessIdentity(
            pid=child_pid,
            ppid=launcher_pid,
            pgrp=child_pid,
            session=child_pid,
            starttime=21,
            uid=os.geteuid(),
            state="S",
        )
        identities = {
            controller_pid: root,
            launcher_pid: launcher,
            child_pid: child,
        }
        proc_root = Path("/synthetic-proc")
        task_directory = proc_root / str(launcher_pid) / "task"
        snapshots = 0
        original_iterdir = Path.iterdir
        original_read_text = Path.read_text

        def churn_task_set(path: Path):
            nonlocal snapshots
            if path == task_directory:
                ids = (
                    (str(launcher_pid),)
                    if snapshots % 2 == 0
                    else (str(launcher_pid), str(launcher_pid + 1000))
                )
                snapshots += 1
                return iter(task_directory / tid for tid in ids)
            return original_iterdir(path)

        def read_task_children(path: Path, encoding=None):
            if path.parent.parent == task_directory and path.name == "children":
                return ""
            return original_read_text(path, encoding=encoding)

        def read_children(pid: int):
            if pid == controller_pid:
                return {launcher_pid}
            if pid == launcher_pid:
                return controller._read_children(
                    pid,
                    proc_root=proc_root,
                    read_identity=identities.get,
                    expected_identity=launcher,
                )
            return set()

        with (
            patch.object(controller, "_read_identity", new=identities.get),
            patch.object(Path, "iterdir", new=churn_task_set),
            patch.object(Path, "read_text", new=read_task_children),
        ):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                read_identity=identities.get,
                read_children=read_children,
                read_process_census=lambda: {
                    pid: identity.ppid for pid, identity in identities.items()
                },
            )

        self.assertFalse(unreadable)
        self.assertEqual(snapshots, 2 * controller.MAX_TASK_INVENTORY_ATTEMPTS)
        self.assertEqual(set(observed), {launcher_pid, child_pid})
        self.assertEqual(set(groups), {launcher_pid, child_pid})

    def test_scan_process_census_discovers_subreaper_reparented_descendant(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10400
        adopted_pid = launcher_pid + 1
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        # The child deliberately has a different session and is already
        # reparented to the subreaper controller.
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=adopted_pid,
            session=adopted_pid,
            starttime=21,
            uid=os.geteuid(),
            state="S",
        )
        identities = {
            controller_pid: root,
            launcher_pid: launcher,
            adopted_pid: adopted,
        }
        adopted_before_reparent = controller.ProcessIdentity(
            **{**adopted.__dict__, "ppid": launcher_pid}
        )

        def read_children(pid: int):
            if pid == controller_pid:
                raise controller.ControllerError(
                    "process_task_inventory_raced",
                    inventory_branch="tid_set_changed_retry_exhausted",
                )
            return set()

        with patch.object(controller, "_read_identity", new=identities.get):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher, adopted_pid: adopted_before_reparent},
                read_identity=identities.get,
                read_children=read_children,
                read_process_census=lambda: {
                    pid: identity.ppid for pid, identity in identities.items()
                },
            )

        self.assertFalse(unreadable)
        self.assertEqual(set(observed), {launcher_pid, adopted_pid})
        self.assertIn(adopted_pid, groups)

    def test_scan_process_census_rejects_pid_reuse_of_known_descendant(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10500
        child_pid = launcher_pid + 1
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        old_child = controller.ProcessIdentity(
            pid=child_pid,
            ppid=launcher_pid,
            pgrp=child_pid,
            session=child_pid,
            starttime=21,
            uid=os.geteuid(),
            state="S",
        )
        replacement = controller.ProcessIdentity(
            **{**old_child.__dict__, "starttime": old_child.starttime + 100}
        )
        identities = {
            controller_pid: root,
            launcher_pid: launcher,
            child_pid: replacement,
        }

        def read_children(pid: int):
            if pid == controller_pid:
                return {launcher_pid}
            if pid == launcher_pid:
                raise controller.ControllerError(
                    "process_task_inventory_raced",
                    inventory_branch="tid_set_changed_retry_exhausted",
                )
            return set()

        with patch.object(controller, "_read_identity", new=identities.get):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher, child_pid: old_child},
                read_identity=identities.get,
                read_children=read_children,
                read_process_census=lambda: {
                    pid: identity.ppid for pid, identity in identities.items()
                },
            )

        self.assertTrue(unreadable)
        self.assertNotIn(child_pid, observed)
        self.assertNotIn(child_pid, groups)

    def test_scan_process_census_eacces_is_fail_closed(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10600
        child_pid = launcher_pid + 1
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        child = controller.ProcessIdentity(
            pid=child_pid,
            ppid=launcher_pid,
            pgrp=child_pid,
            session=child_pid,
            starttime=21,
            uid=os.geteuid(),
            state="S",
        )
        identities = {controller_pid: root, launcher_pid: launcher, child_pid: child}
        census_calls = 0

        def read_identity(pid: int):
            if pid == child_pid and census_calls:
                raise controller.ControllerError("process_identity_unreadable")
            return identities.get(pid)

        def read_process_census():
            nonlocal census_calls
            census_calls += 1
            return {pid: identity.ppid for pid, identity in identities.items()}

        def read_children(pid: int):
            if pid == launcher_pid:
                raise controller.ControllerError(
                    "process_task_inventory_raced",
                    inventory_branch="tid_set_changed_retry_exhausted",
                )
            return {launcher_pid} if pid == controller_pid else set()

        with patch.object(controller, "_read_identity", new=identities.get):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={launcher_pid: launcher},
                    read_identity=read_identity,
                    read_children=read_children,
                    read_process_census=read_process_census,
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(diagnostic["failure_code"], "process_identity_unreadable")
        self.assertEqual(diagnostic["cause_class"], "none")

    def test_scan_process_census_closes_transitive_tree_without_depth_retry_limit(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10650
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        identities = {
            controller_pid: root,
            launcher_pid: controller.ProcessIdentity(
                pid=launcher_pid,
                ppid=controller_pid,
                pgrp=launcher_pid,
                session=launcher_pid,
                starttime=20,
                uid=os.geteuid(),
                state="S",
            ),
        }
        previous_pid = launcher_pid
        expected = {launcher_pid}
        for depth in range(1, 7):
            pid = launcher_pid + depth
            identities[pid] = controller.ProcessIdentity(
                pid=pid,
                ppid=previous_pid,
                pgrp=pid,
                session=pid if depth % 2 else launcher_pid,
                starttime=20 + depth,
                uid=os.geteuid(),
                state="S",
            )
            expected.add(pid)
            previous_pid = pid
        census_calls = 0

        def read_children(pid: int):
            if pid == controller_pid:
                return {launcher_pid}
            raise controller.ControllerError(
                "process_task_inventory_raced",
                inventory_branch="tid_set_changed_retry_exhausted",
            )

        def read_process_census():
            nonlocal census_calls
            census_calls += 1
            return {pid: identity.ppid for pid, identity in identities.items()}

        with patch.object(controller, "_read_identity", new=identities.get):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=identities[launcher_pid],
                baseline_controller_children=set(),
                known={launcher_pid: identities[launcher_pid]},
                read_identity=identities.get,
                read_children=read_children,
                read_process_census=read_process_census,
            )

        self.assertFalse(unreadable)
        self.assertEqual(set(observed), expected)
        self.assertEqual(set(groups), expected)
        self.assertEqual(census_calls, len(expected))

    def test_scan_process_census_does_not_adopt_baseline_controller_child(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10670
        baseline_pid = launcher_pid + 1
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        baseline_child = controller.ProcessIdentity(
            pid=baseline_pid,
            ppid=controller_pid,
            pgrp=baseline_pid,
            session=baseline_pid,
            starttime=19,
            uid=os.geteuid(),
            state="S",
        )
        identities = {
            controller_pid: root,
            launcher_pid: launcher,
            baseline_pid: baseline_child,
        }

        def read_children(pid: int):
            if pid == controller_pid:
                return {launcher_pid, baseline_pid}
            return set()

        with patch.object(controller, "_read_identity", new=identities.get):
            observed, groups, unreadable = controller._scan_owned_tree(
                controller_pid=controller_pid,
                launcher=launcher,
                baseline_controller_children={baseline_pid},
                known={launcher_pid: launcher},
                read_identity=identities.get,
                read_children=read_children,
            )

        self.assertFalse(unreadable)
        self.assertEqual(set(observed), {launcher_pid})
        self.assertEqual(set(groups), {launcher_pid})

    def test_scan_process_census_rejects_parent_reuse_during_candidate_check(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10680
        child_pid = launcher_pid + 1
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        replacement = controller.ProcessIdentity(
            **{**launcher.__dict__, "starttime": launcher.starttime + 100}
        )
        child = controller.ProcessIdentity(
            pid=child_pid,
            ppid=launcher_pid,
            pgrp=child_pid,
            session=child_pid,
            starttime=21,
            uid=os.geteuid(),
            state="S",
        )
        launcher_reads = 0

        def read_identity(pid: int):
            nonlocal launcher_reads
            if pid == launcher_pid:
                launcher_reads += 1
                return replacement if launcher_reads >= 3 else launcher
            return {controller_pid: root, child_pid: child}.get(pid)

        def read_children(pid: int):
            if pid == launcher_pid:
                raise controller.ControllerError(
                    "process_task_inventory_raced",
                    inventory_branch="tid_set_changed_retry_exhausted",
                )
            return {launcher_pid} if pid == controller_pid else set()

        with patch.object(
            controller,
            "_read_identity",
            new=lambda pid: (
                root
                if pid == controller_pid
                else launcher if pid == launcher_pid else child if pid == child_pid else None
            ),
        ):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={launcher_pid: launcher},
                    read_identity=read_identity,
                    read_children=read_children,
                    read_process_census=lambda: {
                        controller_pid: 1,
                        launcher_pid: controller_pid,
                        child_pid: launcher_pid,
                    },
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(diagnostic["failure_code"], "process_task_inventory_raced")
        self.assertEqual(diagnostic["inventory_branch"], "identity_changed_after_snapshot")
        self.assertGreaterEqual(launcher_reads, 3)

    def test_scan_process_census_respects_existing_deadline(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10700
        root = controller.ProcessIdentity(
            pid=controller_pid,
            ppid=1,
            pgrp=controller_pid,
            session=controller_pid,
            starttime=10,
            uid=os.geteuid(),
            state="S",
        )
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=20,
            uid=os.geteuid(),
            state="S",
        )
        identities = {controller_pid: root, launcher_pid: launcher}
        census_calls = 0

        def read_children(pid: int):
            if pid == launcher_pid:
                raise controller.ControllerError(
                    "process_task_inventory_raced",
                    inventory_branch="tid_set_changed_retry_exhausted",
                )
            return {launcher_pid} if pid == controller_pid else set()

        def read_process_census():
            nonlocal census_calls
            census_calls += 1
            return dict(identities)

        with patch.object(controller, "_read_identity", new=identities.get):
            with self.assertRaises(controller.ControllerError) as caught:
                controller._scan_owned_tree(
                    controller_pid=controller_pid,
                    launcher=launcher,
                    baseline_controller_children=set(),
                    known={launcher_pid: launcher},
                    read_identity=identities.get,
                    read_children=read_children,
                    read_process_census=read_process_census,
                    deadline=50.0,
                    clock=lambda: 50.0,
                )

        diagnostic = controller._inventory_exception_diagnostic(caught.exception)
        self.assertEqual(diagnostic["inventory_branch"], "inventory_deadline_exceeded")
        self.assertEqual(census_calls, 0)

    def test_reap_retains_identity_tombstone_when_proc_entry_is_absent(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10200
        adopted_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1500,
            uid=os.geteuid(),
            state="S",
        )
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1501,
            uid=os.geteuid(),
            state="Z",
        )
        known = {launcher_pid: launcher, adopted_pid: adopted}
        waited: list[int] = []

        incomplete = controller._reap_owned_adopted_children(
            known,
            controller_pid=controller_pid,
            launcher_pid=launcher_pid,
            read_identity=lambda _pid: None,
            waitpid=lambda pid, _options: (waited.append(pid) or pid, 0),
        )

        self.assertTrue(incomplete)
        self.assertEqual(known, {launcher_pid: launcher, adopted_pid: adopted})
        self.assertEqual(waited, [])

    def test_stop_tree_rescans_after_reaper_absence_and_captures_late_adoption(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10210
        adopted_pid = launcher_pid + 1
        late_child_pid = launcher_pid + 2
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1510,
            uid=os.geteuid(),
            state="S",
        )
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1511,
            uid=os.geteuid(),
            state="Z",
        )
        late_child = controller.ProcessIdentity(
            pid=late_child_pid,
            ppid=controller_pid,
            pgrp=late_child_pid,
            session=late_child_pid,
            starttime=1512,
            uid=os.geteuid(),
            state="S",
        )
        adopted_reads = 0
        late_adoption = False
        late_child_identity_reads = 0
        controller_snapshots = 0
        signaled: list[tuple[int, int]] = []
        late_group_signal_proofs: list[tuple[int, int]] = []

        def read_identity(pid: int):
            nonlocal adopted_reads, late_adoption, late_child_identity_reads
            if pid == controller_pid:
                return controller_identity
            if pid == launcher_pid:
                return launcher
            if pid == adopted_pid:
                adopted_reads += 1
                if adopted_reads <= 2:
                    return adopted
                late_adoption = True
                return None
            if pid == late_child_pid:
                if late_adoption and controller_snapshots >= 2:
                    late_child_identity_reads += 1
                    return late_child
                return None
            return None

        def read_children(pid: int):
            nonlocal controller_snapshots
            if pid == controller_pid:
                controller_snapshots += 1
                if late_adoption and controller_snapshots >= 2:
                    return {launcher_pid, late_child_pid}
                return {launcher_pid, adopted_pid}
            return set()

        def killpg(pgid: int, signum: int) -> None:
            signaled.append((pgid, signum))
            if pgid == late_child.pgrp:
                late_group_signal_proofs.append((controller_snapshots, late_child_identity_reads))

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        known = {launcher_pid: launcher, adopted_pid: adopted}
        clock_values = iter((2.0, 0.0, 0.0))
        groups = {
            launcher.pgrp: controller.GroupIdentity(
                pgid=launcher.pgrp,
                anchor_pid=launcher.pid,
                anchor_starttime=launcher.starttime,
                uid=launcher.uid,
                session=launcher.session,
            )
        }
        with (
            patch.object(
                controller,
                "_read_identity",
                new=lambda pid: controller_identity if pid == controller_pid else None,
            ),
            patch.object(controller, "INT_GRACE_SECONDS", 0.0),
            patch.object(controller, "TERM_GRACE_SECONDS", 0.0),
            patch.object(controller, "KILL_GRACE_SECONDS", 0.0),
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups=groups,
                deadline=1.0,
                clock=lambda: next(clock_values, 0.0),
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=read_children,
                killpg=killpg,
                waitpid=lambda _pid, _options: (0, 0),
            )

        self.assertFalse(inventory_incomplete)
        self.assertIn(late_child.pgrp, remaining)
        self.assertIn(late_child_pid, known)
        self.assertGreaterEqual(controller_snapshots, 2)
        self.assertEqual(signaled, [])

    def test_stop_tree_deadline_exhaustion_after_new_absence_is_incomplete(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10220
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1520,
            uid=os.geteuid(),
            state="S",
        )
        launcher_reads = 0
        signaled: list[tuple[int, int]] = []

        def read_identity(pid: int):
            nonlocal launcher_reads
            if pid == controller_pid:
                return controller_identity
            if pid == launcher_pid:
                launcher_reads += 1
                return launcher if launcher_reads == 1 else None
            return None

        def read_children(pid: int):
            return {launcher_pid} if pid == controller_pid else set()

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        known = {launcher_pid: launcher}
        group = controller.GroupIdentity(
            pgid=launcher.pgrp,
            anchor_pid=launcher.pid,
            anchor_starttime=launcher.starttime,
            uid=launcher.uid,
            session=launcher.session,
        )
        with patch.object(
            controller,
            "_read_identity",
            new=lambda pid: controller_identity if pid == controller_pid else None,
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups={launcher.pgrp: group},
                deadline=0.0,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=read_children,
                killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                waitpid=lambda _pid, _options: (0, 0),
            )

        self.assertTrue(inventory_incomplete)
        self.assertEqual(remaining, set())
        self.assertEqual(signaled, [])
        self.assertIn(launcher_pid, known)

    def test_stop_tree_final_kernel_inventory_catches_child_missing_from_census(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10215
        adopted_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1525,
            uid=os.geteuid(),
            state="S",
        )
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1526,
            uid=os.geteuid(),
            state="S",
        )
        signaled: list[tuple[int, int]] = []

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        def read_identity(pid: int):
            if pid == controller_pid:
                return controller_identity
            if pid == adopted_pid:
                return adopted
            return None

        def read_children(pid: int):
            return {adopted_pid} if pid == controller_pid else set()

        known = {launcher_pid: launcher}
        group = controller.GroupIdentity(
            pgid=launcher.pgrp,
            anchor_pid=launcher.pid,
            anchor_starttime=launcher.starttime,
            uid=launcher.uid,
            session=launcher.session,
        )
        diagnostic: dict[str, object] = {}
        with (
            patch.object(
                controller,
                "_scan_owned_tree",
                return_value=({}, {}, False),
            ),
            patch.object(
                controller,
                "_read_proc_parent_census",
                return_value={controller_pid: controller_identity.ppid},
            ),
        ):
            _remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups={launcher.pgrp: group},
                deadline=1.0,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=read_children,
                killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                waitpid=lambda _pid, _options: (0, 0),
                diagnostic_sink=diagnostic,
            )

        self.assertTrue(inventory_incomplete)
        self.assertEqual(diagnostic.get("reason"), "postcleanup_subreaper_child")
        self.assertEqual(signaled, [])

    def test_final_subreaper_check_uses_real_pinned_children_reader_for_self(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        baseline_controller_children = controller._read_children(
            controller_pid,
            read_identity=controller._read_identity,
            expected_identity=controller_identity,
        )
        assert baseline_controller_children is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10216
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1,
            uid=os.geteuid(),
            state="S",
        )
        deadline = controller.time.monotonic() + 3.0

        remaining = controller._postcleanup_subreaper_children(
            controller_pid=controller_pid,
            controller_identity=controller_identity,
            baseline_controller_children=baseline_controller_children,
            launcher_identity=launcher,
            known={},
            read_identity=controller._read_identity,
            read_children=controller._read_children,
            clock=controller.time.monotonic,
            deadline=deadline,
        )

        self.assertEqual(remaining, set())

    def test_stop_tree_active_group_absence_rescans_for_adopted_child(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10230
        late_child_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1530,
            uid=os.geteuid(),
            state="S",
        )
        late_child = controller.ProcessIdentity(
            pid=late_child_pid,
            ppid=controller_pid,
            pgrp=late_child_pid,
            session=late_child_pid,
            starttime=1531,
            uid=os.geteuid(),
            state="S",
        )
        launcher_reads = 0
        late_adoption = False
        late_child_reads = 0
        controller_snapshots = 0
        late_group_signal_proofs: list[tuple[int, int]] = []

        def read_identity(pid: int):
            nonlocal launcher_reads, late_adoption, late_child_reads
            if pid == controller_pid:
                return controller_identity
            if pid == launcher_pid:
                launcher_reads += 1
                if launcher_reads <= 1:
                    return launcher
                late_adoption = True
                return None
            if pid == late_child_pid:
                if late_adoption and controller_snapshots >= 2:
                    late_child_reads += 1
                    return late_child
            return None

        def read_children(pid: int):
            nonlocal controller_snapshots
            if pid == controller_pid:
                controller_snapshots += 1
                if late_adoption and controller_snapshots >= 2:
                    return {late_child_pid}
                return {launcher_pid}
            return set()

        def killpg(pgid: int, signum: int) -> None:
            if pgid == late_child.pgrp:
                late_group_signal_proofs.append((controller_snapshots, late_child_reads))

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        group = controller.GroupIdentity(
            pgid=launcher.pgrp,
            anchor_pid=launcher.pid,
            anchor_starttime=launcher.starttime,
            uid=launcher.uid,
            session=launcher.session,
        )
        known = {launcher_pid: launcher}
        clock_values = iter((2.0, 0.0, 0.0))
        with (
            patch.object(
                controller,
                "_read_identity",
                new=lambda pid: controller_identity if pid == controller_pid else None,
            ),
            patch.object(controller, "INT_GRACE_SECONDS", 0.0),
            patch.object(controller, "TERM_GRACE_SECONDS", 0.0),
            patch.object(controller, "KILL_GRACE_SECONDS", 0.0),
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups={launcher.pgrp: group},
                deadline=1.0,
                clock=lambda: next(clock_values, 0.0),
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=read_children,
                killpg=killpg,
                waitpid=lambda _pid, _options: (0, 0),
            )

        self.assertFalse(inventory_incomplete)
        self.assertIn(
            late_child.pgrp,
            remaining,
            (controller_snapshots, late_child_reads, launcher_reads, known),
        )
        self.assertIn(late_child_pid, known)
        self.assertGreaterEqual(controller_snapshots, 2)
        self.assertGreater(late_child_reads, 0)
        self.assertEqual(late_group_signal_proofs, [])

    def test_stop_tree_pid_reuse_in_group_check_is_incomplete_and_unsignaled(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10240
        pinned = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1540,
            uid=os.geteuid(),
            state="S",
        )
        reused = controller.ProcessIdentity(
            **{**pinned.__dict__, "starttime": pinned.starttime + 1}
        )
        group = controller.GroupIdentity(
            pgid=pinned.pgrp,
            anchor_pid=pinned.pid,
            anchor_starttime=pinned.starttime,
            uid=pinned.uid,
            session=pinned.session,
        )

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        signaled: list[tuple[int, int]] = []
        diagnostic: dict[str, object] = {}
        known = {launcher_pid: pinned}
        with patch.object(
            controller,
            "_scan_owned_tree",
            return_value=({launcher_pid: pinned}, {pinned.pgrp: group}, False),
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=pinned,
                baseline_controller_children=set(),
                known=known,
                groups={pinned.pgrp: group},
                deadline=1.0,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
                read_identity=lambda pid: reused if pid == launcher_pid else None,
                read_children=lambda _pid: set(),
                killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                waitpid=lambda _pid, _options: (0, 0),
                diagnostic_sink=diagnostic,
            )

        self.assertTrue(inventory_incomplete)
        self.assertEqual(remaining, set())
        self.assertEqual(signaled, [])
        self.assertEqual(known[launcher_pid], pinned)
        self.assertEqual(diagnostic.get("reason"), "known_pid_identity_changed")

    def test_stop_tree_confirmed_launcher_absence_does_not_loop(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10250
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1550,
            uid=os.geteuid(),
            state="S",
        )
        launcher_reads = 0
        controller_snapshots = 0

        def read_identity(pid: int):
            nonlocal launcher_reads
            if pid == controller_pid:
                return controller_identity
            if pid == launcher_pid:
                launcher_reads += 1
                return launcher if launcher_reads <= 2 else None
            return None

        def read_children(pid: int):
            nonlocal controller_snapshots
            if pid == controller_pid:
                controller_snapshots += 1
                return {launcher_pid} if launcher_reads <= 2 else set()
            return set()

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        group = controller.GroupIdentity(
            pgid=launcher.pgrp,
            anchor_pid=launcher.pid,
            anchor_starttime=launcher.starttime,
            uid=launcher.uid,
            session=launcher.session,
        )
        known = {launcher_pid: launcher}
        with patch.object(
            controller,
            "_read_identity",
            new=lambda pid: controller_identity if pid == controller_pid else None,
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups={launcher.pgrp: group},
                deadline=10.0,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=read_children,
                killpg=lambda _pgid, _signum: None,
                waitpid=lambda _pid, _options: (0, 0),
            )

        self.assertFalse(inventory_incomplete)
        self.assertEqual(remaining, set())
        self.assertEqual(known[launcher_pid], launcher)
        self.assertLessEqual(
            controller_snapshots,
            3 * controller.MAX_OWNED_TREE_SCAN_ATTEMPTS + 3,
        )

    def test_stop_tree_same_pin_reappearance_invalidates_absence_confirmation(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10260
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1560,
            uid=os.geteuid(),
            state="S",
        )
        group = controller.GroupIdentity(
            pgid=launcher.pgrp,
            anchor_pid=launcher.pid,
            anchor_starttime=launcher.starttime,
            uid=launcher.uid,
            session=launcher.session,
        )
        identity_reads = 0
        scan_calls = 0

        def read_identity(pid: int):
            nonlocal identity_reads
            if pid != launcher_pid:
                return None
            identity_reads += 1
            # First absence starts reconciliation, second confirms it, then
            # the exact same pin reappears before disappearing again.
            if identity_reads == 3:
                return launcher
            return None

        def scan(**_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            observed = {launcher_pid: launcher} if scan_calls == 1 else {}
            seen_groups = {launcher.pgrp: group} if scan_calls == 1 else {}
            return observed, seen_groups, False

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        known = {launcher_pid: launcher}
        with (
            patch.object(controller, "_scan_owned_tree", side_effect=scan),
            patch.object(controller, "INT_GRACE_SECONDS", 0.0),
            patch.object(controller, "TERM_GRACE_SECONDS", 0.0),
            patch.object(controller, "KILL_GRACE_SECONDS", 0.0),
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups={launcher.pgrp: group},
                deadline=10.0,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=lambda _pid: set(),
                killpg=lambda _pgid, _signum: None,
                waitpid=lambda _pid, _options: (0, 0),
            )

        self.assertFalse(inventory_incomplete)
        self.assertEqual(remaining, set())
        self.assertGreaterEqual(identity_reads, 5)
        self.assertGreaterEqual(scan_calls, 4)

    def test_stop_tree_reaped_pid_tombstone_blocks_reuse_after_rescan(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10270
        adopted_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1570,
            uid=os.geteuid(),
            state="S",
        )
        adopted = controller.ProcessIdentity(
            pid=adopted_pid,
            ppid=controller_pid,
            pgrp=adopted_pid,
            session=adopted_pid,
            starttime=1571,
            uid=os.geteuid(),
            state="Z",
        )
        reused = controller.ProcessIdentity(
            **{**adopted.__dict__, "starttime": adopted.starttime + 1, "state": "S"}
        )
        reaped = False
        controller_snapshots = 0
        signaled: list[tuple[int, int]] = []

        def read_identity(pid: int):
            if pid == controller_pid:
                return controller_identity
            if pid == launcher_pid:
                return launcher
            if pid == adopted_pid:
                return reused if reaped else adopted
            return None

        def read_children(pid: int):
            nonlocal controller_snapshots
            if pid == controller_pid:
                controller_snapshots += 1
                return {launcher_pid, adopted_pid}
            return set()

        def waitpid(pid: int, _options: int):
            nonlocal reaped
            if pid == adopted_pid:
                reaped = True
                return pid, 0
            return 0, 0

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        known = {launcher_pid: launcher, adopted_pid: adopted}
        clock_values = iter((2.0, 0.0, 0.0))
        with patch.object(
            controller,
            "_read_identity",
            new=lambda pid: controller_identity if pid == controller_pid else None,
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups={
                    launcher.pgrp: controller.GroupIdentity(
                        pgid=launcher.pgrp,
                        anchor_pid=launcher.pid,
                        anchor_starttime=launcher.starttime,
                        uid=launcher.uid,
                        session=launcher.session,
                    ),
                    adopted.pgrp: controller.GroupIdentity(
                        pgid=adopted.pgrp,
                        anchor_pid=adopted.pid,
                        anchor_starttime=adopted.starttime,
                        uid=adopted.uid,
                        session=adopted.session,
                    ),
                },
                deadline=1.0,
                clock=lambda: next(clock_values, 0.0),
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=read_children,
                killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                waitpid=waitpid,
            )

        self.assertTrue(reaped)
        self.assertTrue(inventory_incomplete)
        self.assertNotIn(adopted.pgrp, remaining)
        self.assertEqual(known[adopted_pid], adopted)
        self.assertGreaterEqual(controller_snapshots, 2)
        self.assertEqual(signaled, [])

    def test_stop_tree_observed_reappearance_requires_new_snapshot_after_second_exit(self) -> None:
        controller_pid = os.getpid()
        controller_identity = controller._read_identity(controller_pid)
        assert controller_identity is not None
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10280
        late_child_pid = launcher_pid + 1
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1580,
            uid=os.geteuid(),
            state="S",
        )
        late_child = controller.ProcessIdentity(
            pid=late_child_pid,
            ppid=controller_pid,
            pgrp=late_child_pid,
            session=late_child_pid,
            starttime=1581,
            uid=os.geteuid(),
            state="S",
        )
        launcher_group = controller.GroupIdentity(
            pgid=launcher.pgrp,
            anchor_pid=launcher.pid,
            anchor_starttime=launcher.starttime,
            uid=launcher.uid,
            session=launcher.session,
        )
        late_group = controller.GroupIdentity(
            pgid=late_child.pgrp,
            anchor_pid=late_child.pid,
            anchor_starttime=late_child.starttime,
            uid=late_child.uid,
            session=late_child.session,
        )
        scan_calls = 0
        launcher_reads = 0

        def scan(**_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            if scan_calls == 3:
                # This pin is visible during the full scan but exits before
                # refresh() performs its post-scan identity read.
                return {launcher_pid: launcher}, {launcher.pgrp: launcher_group}, False
            if scan_calls >= 4:
                return {late_child_pid: late_child}, {late_child.pgrp: late_group}, False
            return {}, {}, False

        def read_identity(pid: int):
            nonlocal launcher_reads
            if pid == controller_pid:
                return controller_identity
            if pid == launcher_pid:
                launcher_reads += 1
                return None
            if pid == late_child_pid and scan_calls >= 4:
                return late_child
            return None

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        known = {launcher_pid: launcher}
        with (
            patch.object(controller, "_scan_owned_tree", side_effect=scan),
            patch.object(controller, "INT_GRACE_SECONDS", 0.0),
            patch.object(controller, "TERM_GRACE_SECONDS", 0.0),
            patch.object(controller, "KILL_GRACE_SECONDS", 0.0),
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known=known,
                groups={launcher.pgrp: launcher_group},
                deadline=10.0,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
                read_identity=read_identity,
                read_children=lambda _pid: set(),
                killpg=lambda _pgid, _signum: None,
                waitpid=lambda _pid, _options: (0, 0),
            )

        self.assertFalse(inventory_incomplete)
        self.assertIn(late_child.pgrp, remaining)
        self.assertIn(late_child_pid, known)
        self.assertGreaterEqual(scan_calls, 4)
        self.assertGreaterEqual(launcher_reads, 4)

    def test_stop_tree_scan_finishing_at_deadline_cannot_confirm_absence(self) -> None:
        controller_pid = os.getpid()
        launcher_pid = max(controller_pid, os.getsid(controller_pid)) + 10290
        launcher = controller.ProcessIdentity(
            pid=launcher_pid,
            ppid=controller_pid,
            pgrp=launcher_pid,
            session=launcher_pid,
            starttime=1590,
            uid=os.geteuid(),
            state="S",
        )
        group = controller.GroupIdentity(
            pgid=launcher.pgrp,
            anchor_pid=launcher.pid,
            anchor_starttime=launcher.starttime,
            uid=launcher.uid,
            session=launcher.session,
        )
        now = 0.0
        scan_calls = 0
        signaled: list[tuple[int, int]] = []

        def scan(**_kwargs):
            nonlocal now, scan_calls
            scan_calls += 1
            if scan_calls == 2:
                now = 1.0
            return {}, {}, False

        class ExitedLauncher:
            pid = launcher_pid

            def poll(self) -> int:
                return 0

        with (
            patch.object(controller, "_scan_owned_tree", side_effect=scan),
            patch.object(controller, "INT_GRACE_SECONDS", 0.0),
            patch.object(controller, "TERM_GRACE_SECONDS", 0.0),
            patch.object(controller, "KILL_GRACE_SECONDS", 0.0),
        ):
            remaining, inventory_incomplete, _saw_live = controller._stop_owned_tree(
                controller_pid=controller_pid,
                process=ExitedLauncher(),
                launcher_identity=launcher,
                baseline_controller_children=set(),
                known={launcher_pid: launcher},
                groups={launcher.pgrp: group},
                deadline=1.0,
                clock=lambda: now,
                sleep=lambda _seconds: None,
                read_identity=lambda pid: None,
                read_children=lambda _pid: set(),
                killpg=lambda pgid, signum: signaled.append((pgid, signum)),
                waitpid=lambda _pid, _options: (0, 0),
            )

        self.assertTrue(inventory_incomplete)
        self.assertEqual(remaining, set())
        self.assertEqual(signaled, [])
        self.assertGreaterEqual(scan_calls, 2)


if __name__ == "__main__":
    unittest.main()
