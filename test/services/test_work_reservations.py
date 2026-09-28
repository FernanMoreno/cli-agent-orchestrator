"""Real SQLite/path/process checks for cooperative exclusive write reservations."""

import hashlib
import importlib
import multiprocessing
import os
import sqlite3
import time

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence


def context(tmp_path, count=3):
    module = importlib.import_module("cli_agent_orchestrator.services.work_reservations")
    repository = WorkRepository(tmp_path / "reservations.sqlite3")
    repository.initialize()
    root = tmp_path / "checkout"
    root.mkdir()
    job = repository.create_job(
        project_id="project",
        principal_id="operator",
        allowed_providers=["mock_cli"],
        grant_id="grant",
    )
    work = [
        repository.admit_work(
            job_id=job["id"],
            operation_kind="test",
            idempotency_key=str(i),
            request_hash=hashlib.sha256(str(i).encode()).hexdigest(),
            contract_id="contract",
            snapshot_id=None,
            provider="mock_cli",
            actor_id="operator",
            lease_seconds=3600,
        )
        for i in range(count)
    ]
    return module, repository, root, job, work


def reserve(module, repository, root, job, work, paths, **overrides):
    attempt = work["attempts"][-1]
    arguments = dict(
        job_id=job["id"],
        work_item_id=work["id"],
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_attempt_revision=attempt["revision"],
        checkout_root=root,
        paths=paths,
        expires_at=time.time() + 120,
        actor_id="operator",
    )
    arguments.update(overrides)
    return module.WorkReservations(repository).reserve(**arguments)


def cancel(repository, work):
    attempt = repository.get_work(work["id"])["attempts"][-1]
    return repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state=attempt["state"],
        target="cancelled",
        actor_id="operator",
        event_id="cancel-" + attempt["id"],
        evidence=TransitionEvidence(
            generation=attempt["generation"], expected_generation=attempt["generation"]
        ),
    )


def test_reserve_helper_revalidates_paths_and_rolls_back_with_caller(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    manager = module.WorkReservations(repo)
    arguments = dict(
        job_id=job["id"],
        work_item_id=work[0]["id"],
        attempt_id=work[0]["attempts"][0]["id"],
        generation=1,
        expected_attempt_revision=1,
        checkout_root=root,
        paths=["target"],
        expires_at=time.time() + 100,
        actor_id="operator",
    )
    before = repo.read_events(job["id"])["high_water"]
    with repo.connection() as connection:
        with pytest.raises(ValueError):
            manager._reserve(connection, **arguments)
    with pytest.raises(RuntimeError, match="rollback caller"):
        with repo.transaction() as connection:
            repo._verify(connection)
            with pytest.raises(module.ReservationConflict):
                manager._reserve(connection, **dict(arguments, paths=["../escape"]))
            assert manager._reserve(connection, **arguments).state == "active"
            assert connection.in_transaction
            raise RuntimeError("rollback caller")
    assert repo.read_events(job["id"])["high_water"] == before
    with repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_reservation_sets").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_path_reservations").fetchone()[0] == 0


@pytest.mark.parametrize("first,second", [("src", "src/file.py"), ("src/file.py", "src")])
def test_parent_child_paths_conflict_in_both_directions(tmp_path, first, second):
    module, repo, root, job, work = context(tmp_path)
    reserve(module, repo, root, job, work[0], [first])
    with pytest.raises(module.ReservationConflict):
        reserve(module, repo, root, job, work[1], [second])
    assert reserve(module, repo, root, job, work[2], ["src2"]).state == "active"


def test_symlink_and_direct_hardlink_aliases_conflict(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    (root / "source").write_text("data")
    (root / "symlink").symlink_to(root / "source")
    os.link(root / "source", root / "hardlink")
    first = reserve(module, repo, root, job, work[0], ["symlink"])
    assert first.paths == (str(root / "source"),)
    for alias in ("source", "hardlink"):
        with pytest.raises(module.ReservationConflict):
            reserve(module, repo, root, job, work[1], [alias])


def test_directory_reservations_do_not_claim_subtree_inode_isolation(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    for name in ("first", "second"):
        (root / name).mkdir()
    (root / "first/file").write_text("shared inode")
    os.link(root / "first/file", root / "second/alias")
    reserve(module, repo, root, job, work[0], ["first"])
    # These locks cover namespace ancestry, not a recursive inode snapshot.
    # Backend isolation is required to protect aliases hidden below directories.
    assert reserve(module, repo, root, job, work[1], ["second"]).state == "active"


def test_checkout_escape_is_rejected_and_reads_do_not_retarget_stored_paths(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    (root / "source").mkdir()
    first = reserve(module, repo, root, job, work[0], ["source"])
    (root / "source").rename(root / "moved")
    (root / "source").symlink_to(tmp_path, target_is_directory=True)
    assert module.WorkReservations(repo).get(first.id).paths == (str(root / "source"),)
    for path in ("../outside", "source/elsewhere"):
        with pytest.raises(module.ReservationConflict):
            reserve(module, repo, root, job, work[1], [path])


def test_conflicting_multi_path_request_does_not_leak_partial_reservations(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    reserve(module, repo, root, job, work[0], ["blocked"])
    with pytest.raises(module.ReservationConflict):
        reserve(module, repo, root, job, work[1], ["free", "blocked"])
    assert reserve(module, repo, root, job, work[2], ["free"]).state == "active"
    events = repo.read_events(job["id"])["events"]
    assert not any(
        event["event_type"] == "paths.reserved" and event["work_item_id"] == work[1]["id"]
        for event in events
    )


def test_event_failure_rolls_back_entire_reservation_set(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_reservation_event BEFORE INSERT ON work_events WHEN NEW.event_type='paths.reserved' BEGIN SELECT RAISE(ABORT,'reservation event unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="event unavailable"):
        reserve(module, repo, root, job, work[0], ["one", "two"])
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute("SELECT count(*) FROM work_reservation_sets").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_path_reservations").fetchone()[0] == 0


def test_expired_reservation_blocks_reassignment_and_cannot_be_renewed(tmp_path, monkeypatch):
    module, repo, root, job, work = context(tmp_path)
    first = reserve(module, repo, root, job, work[0], ["target"])
    monkeypatch.setattr(module.time, "time", lambda: first.expires_at + 1)
    manager = module.WorkReservations(repo)
    with pytest.raises(module.ReservationConflict):
        reserve(module, repo, root, job, work[1], ["target"])
    with pytest.raises(module.ReservationConflict):
        manager.renew(
            first.id,
            generation=1,
            expected_revision=1,
            expected_attempt_revision=1,
            expires_at=first.expires_at + 100,
            actor_id="operator",
        )
    with pytest.raises(module.ReservationConflict):
        manager.assert_held(
            first.id, generation=1, expected_revision=1, expected_attempt_revision=1
        )
    assert manager.get(first.id).state == "active"


def test_generation_and_revision_fences_prevent_stale_owner_updates(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    first = reserve(module, repo, root, job, work[0], ["target"])
    manager = module.WorkReservations(repo)
    for override in ({"generation": 2}, {"expected_revision": 2}, {"expected_attempt_revision": 2}):
        arguments = dict(generation=1, expected_revision=1, expected_attempt_revision=1)
        arguments.update(override)
        with pytest.raises(module.ReservationConflict):
            manager.assert_held(first.id, **arguments)
    renewed = manager.renew(
        first.id,
        generation=1,
        expected_revision=1,
        expected_attempt_revision=1,
        expires_at=first.expires_at + 10,
        actor_id="operator",
    )
    assert renewed.revision == 2
    with pytest.raises(module.ReservationConflict):
        manager.assert_held(
            first.id, generation=1, expected_revision=1, expected_attempt_revision=1
        )


def test_release_rejects_client_boolean_and_restartable_attempt(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    first = reserve(module, repo, root, job, work[0], ["target"])
    forged = module.WorkReservations(repo, stop_verifier=lambda owner: True)
    with pytest.raises(module.ReservationConflict):
        forged.release(
            first.id,
            generation=1,
            expected_revision=1,
            expected_attempt_revision=1,
            actor_id="operator",
        )
    cancel(repo, work[0])
    with pytest.raises(module.ReservationConflict):
        forged.release(
            first.id,
            generation=1,
            expected_revision=1,
            expected_attempt_revision=2,
            actor_id="operator",
        )


def _external_writer(path, ready, stop):
    with path.open("a") as output:
        ready.set()
        stop.wait(20)
        output.write("last external write")


def test_terminal_attempt_does_not_release_until_real_external_writer_stops(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    ctx = multiprocessing.get_context("fork")
    ready, stop = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_external_writer, args=(root / "target", ready, stop))
    process.start()
    try:
        assert ready.wait(10)
        first = reserve(module, repo, root, job, work[0], ["target"])
        cancel(repo, work[0])

        def verify(owner):
            if process.is_alive():
                return None
            return module.StoppedWriter(
                owner.attempt_id, owner.generation, owner.revision, "backend-process-exited"
            )

        manager = module.WorkReservations(repo, stop_verifier=verify)
        args = dict(
            generation=1, expected_revision=1, expected_attempt_revision=2, actor_id="operator"
        )
        with pytest.raises(module.ReservationConflict):
            manager.release(first.id, **args)
        with pytest.raises(module.ReservationConflict):
            reserve(module, repo, root, job, work[1], ["target"])
        stop.set()
        process.join(10)
        assert process.exitcode == 0
        assert manager.release(first.id, **args).state == "released"
        assert reserve(module, repo, root, job, work[1], ["target"]).state == "active"
    finally:
        stop.set()
        if process.is_alive():
            process.terminate()
            process.join(5)


def _race_reserve(database, root, job, work, barrier, outcomes):
    module = importlib.import_module("cli_agent_orchestrator.services.work_reservations")
    try:
        barrier.wait(15)
        value = reserve(module, WorkRepository(database), root, job, work, ["contended"])
        outcomes.put(("reserved", value.id))
    except module.ReservationConflict:
        outcomes.put(("conflict", None))
    except BaseException as exc:
        outcomes.put(("error", repr(exc)))


def test_independent_processes_cannot_reserve_the_same_path(tmp_path):
    module, repo, root, job, work = context(tmp_path, count=4)
    ctx = multiprocessing.get_context("fork")
    barrier, outcomes = ctx.Barrier(4), ctx.Queue()
    processes = [
        ctx.Process(target=_race_reserve, args=(repo.path, root, job, item, barrier, outcomes))
        for item in work
    ]
    for process in processes:
        process.start()
    try:
        results = [outcomes.get(timeout=20) for _ in processes]
        assert [kind for kind, _ in results].count("reserved") == 1
        assert [kind for kind, _ in results].count("conflict") == 3
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


def test_new_attempt_generation_fences_the_old_reservation_holder(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    first = reserve(module, repo, root, job, work[0], ["target"])
    with repo.transaction() as connection:
        connection.execute(
            "INSERT INTO work_attempts(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) VALUES ('new-attempt',?,2,2,'mock_cli',?,?)",
            (work[0]["id"], time.time() + 300, time.time()),
        )
    with pytest.raises(module.ReservationConflict):
        module.WorkReservations(repo).assert_held(
            first.id, generation=1, expected_revision=1, expected_attempt_revision=1
        )


def test_release_rechecks_cas_after_server_stop_verification(tmp_path):
    module, repo, root, job, work = context(tmp_path)
    first = reserve(module, repo, root, job, work[0], ["target"])
    cancel(repo, work[0])

    def verify(owner):
        with repo.transaction() as connection:
            connection.execute(
                "UPDATE work_reservation_sets SET revision=revision+1 WHERE id=?", (first.id,)
            )
        return module.StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "server-stop"
        )

    manager = module.WorkReservations(repo, stop_verifier=verify)
    with pytest.raises(module.ReservationConflict):
        manager.release(
            first.id,
            generation=1,
            expected_revision=1,
            expected_attempt_revision=2,
            actor_id="operator",
        )
    assert manager.get(first.id).state == "active"
