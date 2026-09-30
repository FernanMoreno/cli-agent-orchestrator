"""Pre-GO Bubblewrap setup is an immutable, authority-checked transaction."""

import errno
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import replace
from test.clients.test_work_bubblewrap_process_identity import _identity
from test.services.test_work_contract_binding import bind, context

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.services.work_authority import AuthorityDenied
from cli_agent_orchestrator.services.work_bubblewrap_composition import (
    WorkBubblewrapAcknowledgement,
)
from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import (
    WorkBubblewrapSetupIntent,
)


def _setup(tmp_path, *, catalog_size=4, v2=True):
    data = context(tmp_path)
    models, _, repo, _, _, _, _, item, original = data
    token = "/bin/alpha"
    digest = "a" * 64
    executable = models.ExecutableIdentity(
        command_token=token,
        content_reference="sha256:" + digest,
        sha256_digest=digest,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
    )
    v2_contract = models.EffectiveWorkContractV2(
        **{
            **original.model_dump(),
            "schema_version": 2,
            "permissions": original.permissions.model_copy(update={"commands": (token,)}),
            "executable_identities": (executable,),
        }
    )
    binding = bind(data, contract=v2_contract if v2 else original)
    attempt_id = item["attempts"][0]["id"]
    with repo.transaction() as connection:
        connection.execute("UPDATE work_attempts SET state='sent' WHERE id=?", (attempt_id,))
        if catalog_size is not None:
            connection.execute(
                "INSERT INTO work_executable_contents VALUES (?,?,?,?)",
                (digest, digest, catalog_size, time.time()),
            )
    identity = _identity()
    ack = WorkBubblewrapAcknowledgement(
        destination="/exec/worker",
        sha256_digest=digest,
        mode=0o500,
        size=4,
        destination_device=1,
        destination_inode=2,
        open_fds=(0, 1, 2),
        landlock_abi=1,
        seccomp_mode=2,
        unlisted_path_denied=True,
        unmounted_host_path_absent=True,
        memfd_create_denied_errno=errno.EPERM,
        connect_denied_errno=errno.EPERM,
        monitor_pid=identity["monitor_pid"],
        proxy_socket_device=None,
        proxy_socket_inode=None,
    )
    kwargs = dict(
        attempt_id=attempt_id,
        generation=1,
        expected_attempt_revision=1,
        command_token=token,
        acknowledgement=ack,
        process_identity=identity,
        process_identity_sha256=identity["identity_sha256"],
    )
    return repo, binding, kwargs


def _counts(repo):
    with repo.connection() as connection:
        return (
            connection.execute(
                "SELECT count(*) FROM work_bubblewrap_process_identities"
            ).fetchone()[0],
            connection.execute("SELECT count(*) FROM work_bubblewrap_setup_intents").fetchone()[0],
        )


def test_setup_intent_commits_identity_ack_and_pending_once(tmp_path):
    repo, binding, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    first = intent.record_pre_go(**kwargs)
    assert first.contract_hash == binding.contract_hash
    assert first.release_intent == "pending"
    assert first.process_identity_sha256 == kwargs["process_identity_sha256"]
    assert intent.record_pre_go(**kwargs) == first
    assert _counts(repo) == (1, 1)
    assert (
        WorkBubblewrapSetupIntent(WorkRepository(repo.path)).read_historical(
            kwargs["attempt_id"], 1
        )
        == first
    )
    with repo.connection() as connection:
        row = connection.execute("SELECT * FROM work_bubblewrap_setup_intents").fetchone()
        assert (
            json.dumps(json.loads(row["ack_json"]), sort_keys=True, separators=(",", ":"))
            == row["ack_json"]
        )
        assert hashlib.sha256(row["ack_json"].encode()).hexdigest() == row["ack_sha256"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_intent_insert_rolls_back_process_identity(tmp_path):
    repo, _, kwargs = _setup(tmp_path)
    original = repo._persist_bubblewrap_process_identity

    def insert_then_reject(connection, *args):
        original(connection, *args)
        connection.execute(
            "CREATE TRIGGER reject_setup_intent BEFORE INSERT ON work_bubblewrap_setup_intents "
            "BEGIN SELECT RAISE(ABORT,'injected failure'); END"
        )

    repo._persist_bubblewrap_process_identity = insert_then_reject
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


@pytest.mark.parametrize(
    "change",
    [
        {"command_token": "/bin/beta"},
        {"expected_attempt_revision": 2},
        {"process_identity_sha256": "b" * 64},
    ],
)
def test_setup_intent_rejects_changed_input_without_new_rows(tmp_path, change):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    intent.record_pre_go(**kwargs)
    with pytest.raises((ValueError, WorkConflict)):
        intent.record_pre_go(**dict(kwargs, **change))
    assert _counts(repo) == (1, 1)


@pytest.mark.parametrize(
    "change",
    [
        {"size": 5},
        {"sha256_digest": "b" * 64},
        {"monitor_pid": 999},
        {"connect_denied_errno": errno.EACCES},
        {"destination_device": 2**80},
        {"destination": "/exec/worker" + "x" * 9000},
    ],
)
def test_setup_intent_rejects_bad_ack_without_rows(tmp_path, change):
    repo, _, kwargs = _setup(tmp_path)
    kwargs["acknowledgement"] = replace(kwargs["acknowledgement"], **change)
    with pytest.raises(ValueError):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


def test_setup_intent_rejects_missing_catalog_content(tmp_path):
    repo, _, kwargs = _setup(tmp_path, catalog_size=None)
    with pytest.raises(WorkConflict):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


def test_setup_intent_rejects_catalog_size_mismatch(tmp_path):
    repo, _, kwargs = _setup(tmp_path, catalog_size=5)
    with pytest.raises(ValueError):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


def test_setup_intent_rejects_terminal_attempt(tmp_path):
    repo, _, kwargs = _setup(tmp_path)
    with repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET state='finished' WHERE id=?", (kwargs["attempt_id"],)
        )
    with pytest.raises(ValueError):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


def test_setup_intent_rejects_unshare_identity(tmp_path):
    repo, _, kwargs = _setup(tmp_path)
    with repo.transaction() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,1,3,'{}',?,?)",
            (kwargs["attempt_id"], "c" * 64, time.time()),
        )
    with pytest.raises(sqlite3.IntegrityError):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


def test_setup_intent_rejects_different_valid_ack_on_replay(tmp_path):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    intent.record_pre_go(**kwargs)
    kwargs["acknowledgement"] = replace(kwargs["acknowledgement"], destination_inode=3)
    with pytest.raises(WorkConflict):
        intent.record_pre_go(**kwargs)
    assert _counts(repo) == (1, 1)


@pytest.mark.parametrize("invalidity", ["revoked", "lease_expired", "replaced", "stale_revision"])
def test_setup_intent_rejects_lost_authority_before_writing(tmp_path, invalidity):
    repo, _, kwargs = _setup(tmp_path)
    with repo.transaction() as connection:
        if invalidity == "revoked":
            grant = connection.execute(
                "SELECT id,revision,principal_id FROM work_grants LIMIT 1"
            ).fetchone()
            connection.execute(
                "INSERT INTO work_grant_revocations VALUES (?,?,?,?,?)",
                (grant["id"], grant["revision"], grant["principal_id"], time.time(), "test"),
            )
        elif invalidity == "lease_expired":
            connection.execute(
                "UPDATE work_attempts SET lease_expires_at=? WHERE id=?",
                (time.time() - 1, kwargs["attempt_id"]),
            )
        elif invalidity == "replaced":
            work_item_id = connection.execute(
                "SELECT work_item_id FROM work_attempts WHERE id=?", (kwargs["attempt_id"],)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO work_attempts "
                "(id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) "
                "VALUES (?,?,2,2,'mock_cli','planned',?,?)",
                ("replacement", work_item_id, time.time() + 600, time.time()),
            )
        else:
            connection.execute(
                "UPDATE work_attempts SET revision=revision+1 WHERE id=?", (kwargs["attempt_id"],)
            )
    with pytest.raises((ValueError, AuthorityDenied)):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


def test_setup_intent_rejects_v1_contract(tmp_path):
    repo, _, kwargs = _setup(tmp_path, v2=False)
    with pytest.raises(WorkConflict):
        WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    assert _counts(repo) == (0, 0)


def test_historical_read_survives_revocation_and_terminal_attempt(tmp_path):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    with repo.transaction() as connection:
        grant = connection.execute(
            "SELECT id,revision,principal_id FROM work_grants LIMIT 1"
        ).fetchone()
        connection.execute(
            "INSERT INTO work_grant_revocations VALUES (?,?,?,?,?)",
            (grant["id"], grant["revision"], grant["principal_id"], time.time(), "test"),
        )
        connection.execute(
            "UPDATE work_attempts SET state='finished' WHERE id=?", (kwargs["attempt_id"],)
        )
    assert intent.read_historical(kwargs["attempt_id"], 1) == evidence
    with pytest.raises(ValueError):
        intent.record_pre_go(**kwargs)
    assert _counts(repo) == (1, 1)


def test_release_sees_committed_pending_intent_before_effect(tmp_path):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    observed = []

    def release():
        with repo.connection() as connection:
            row = connection.execute(
                "SELECT release_intent,ack_sha256 FROM work_bubblewrap_setup_intents "
                "WHERE attempt_id=?",
                (kwargs["attempt_id"],),
            ).fetchone()
        with sqlite3.connect(repo.path, timeout=0, isolation_level=None) as peer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                peer.execute("BEGIN IMMEDIATE")
        observed.append((row["release_intent"], row["ack_sha256"]))

    intent.release_if_current(evidence, expected_attempt_revision=1, release=release)
    assert observed == [("pending", evidence.ack_sha256)]


@pytest.mark.parametrize("invalidity", ["revoked", "stale_revision", "replaced"])
def test_release_withholds_effect_when_authority_changes_after_persistence(tmp_path, invalidity):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    with repo.transaction() as connection:
        if invalidity == "revoked":
            grant = connection.execute(
                "SELECT id,revision,principal_id FROM work_grants LIMIT 1"
            ).fetchone()
            connection.execute(
                "INSERT INTO work_grant_revocations VALUES (?,?,?,?,?)",
                (grant["id"], grant["revision"], grant["principal_id"], time.time(), "test"),
            )
        elif invalidity == "stale_revision":
            connection.execute(
                "UPDATE work_attempts SET revision=revision+1 WHERE id=?", (kwargs["attempt_id"],)
            )
        else:
            work_item_id = connection.execute(
                "SELECT work_item_id FROM work_attempts WHERE id=?", (kwargs["attempt_id"],)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO work_attempts "
                "(id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) "
                "VALUES (?,?,2,2,'mock_cli','planned',?,?)",
                ("replacement", work_item_id, time.time() + 600, time.time()),
            )
    effects = []

    with pytest.raises((ValueError, AuthorityDenied, WorkConflict)):
        intent.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("GO")
        )
    assert effects == []
    assert intent.read_historical(kwargs["attempt_id"], 1) == evidence


def test_release_write_failure_is_uncertain_and_keeps_pending_intent(tmp_path):
    from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import (
        WorkBubblewrapReleaseUncertain,
    )

    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    effects = []

    def release_then_fail():
        effects.append("GO attempted")
        raise OSError("release pipe failed after effect attempt")

    with pytest.raises(WorkBubblewrapReleaseUncertain):
        intent.release_if_current(evidence, expected_attempt_revision=1, release=release_then_fail)
    assert effects == ["GO attempted"]
    assert intent.read_historical(kwargs["attempt_id"], 1) == evidence


def test_release_commit_failure_after_go_attempt_is_uncertain(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import (
        WorkBubblewrapReleaseUncertain,
    )

    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    original_transaction = repo.transaction
    effects = []

    @contextmanager
    def fail_after_release():
        with original_transaction() as connection:
            yield connection
            if effects:
                raise sqlite3.OperationalError("commit failed after GO attempt")

    monkeypatch.setattr(repo, "transaction", fail_after_release)
    with pytest.raises(WorkBubblewrapReleaseUncertain):
        intent.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("GO attempted")
        )
    restarted = WorkBubblewrapSetupIntent(WorkRepository(repo.path))
    with pytest.raises(WorkConflict):
        restarted.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("retry GO")
        )
    assert effects == ["GO attempted"]
    assert intent.read_historical(kwargs["attempt_id"], 1) == evidence


def test_release_rechecks_wall_clock_expiry_after_final_order_validation(tmp_path, monkeypatch):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    start = time.time()
    expiry = start + 10
    with repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=? WHERE id=?",
            (expiry, kwargs["attempt_id"]),
        )
    clock = [start]
    revalidations = [0]
    original_revalidate = intent.contracts._revalidate_order

    def advance_after_final_revalidation(*args, **kwargs):
        result = original_revalidate(*args, **kwargs)
        revalidations[0] += 1
        if revalidations[0] == 2:
            clock[0] = expiry
        return result

    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(intent.contracts, "_revalidate_order", advance_after_final_revalidation)
    effects = []
    with pytest.raises(ValueError):
        intent.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("GO")
        )
    assert revalidations == [2]
    assert effects == []


def test_release_checks_expiry_immediately_before_go(tmp_path, monkeypatch):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    start = time.time()
    expiry = start + 10
    with repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=? WHERE id=?",
            (expiry, kwargs["attempt_id"]),
        )
    clock = [start]
    validations = [0]
    original_check = intent._require_current_release

    def expire_after_final_validation(*args, **kwargs):
        result = original_check(*args, **kwargs)
        validations[0] += 1
        if validations[0] == 2:
            clock[0] = expiry
        return result

    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(intent, "_require_current_release", expire_after_final_validation)
    effects = []
    with pytest.raises(WorkConflict):
        intent.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("GO")
        )
    assert validations == [2]
    assert effects == []


def test_release_claim_blocks_same_evidence_after_uncertain_go(tmp_path):
    from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import (
        WorkBubblewrapReleaseUncertain,
    )

    repo, _, kwargs = _setup(tmp_path)
    evidence = WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    effects = []

    def attempted_go():
        effects.append("first GO")
        raise OSError("GO may have reached worker")

    with pytest.raises(WorkBubblewrapReleaseUncertain):
        WorkBubblewrapSetupIntent(repo).release_if_current(
            evidence, expected_attempt_revision=1, release=attempted_go
        )
    restarted = WorkBubblewrapSetupIntent(WorkRepository(repo.path))
    with pytest.raises(WorkConflict):
        restarted.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("second GO")
        )
    assert effects == ["first GO"]
    assert restarted.read_historical(kwargs["attempt_id"], 1) == evidence


def test_successful_release_claim_is_one_shot(tmp_path):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    effects = []
    intent.release_if_current(
        evidence, expected_attempt_revision=1, release=lambda: effects.append("GO")
    )
    with pytest.raises(WorkConflict):
        intent.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("second GO")
        )
    assert effects == ["GO"]
    with repo.connection() as connection:
        claim = connection.execute(
            "SELECT generation,contract_hash,ack_sha256,process_identity_sha256 "
            "FROM work_bubblewrap_release_claims WHERE attempt_id=?",
            (kwargs["attempt_id"],),
        ).fetchone()
    assert tuple(claim) == (
        1,
        evidence.contract_hash,
        evidence.ack_sha256,
        evidence.process_identity_sha256,
    )


@pytest.mark.parametrize("invalidity", ["revoked", "stale_revision", "replaced"])
def test_claim_does_not_authorize_go_after_intervening_authority_change(
    tmp_path, monkeypatch, invalidity
):
    repo, _, kwargs = _setup(tmp_path)
    intent = WorkBubblewrapSetupIntent(repo)
    evidence = intent.record_pre_go(**kwargs)
    original_transaction = repo.transaction
    transactions = [0]
    effects = []

    @contextmanager
    def change_after_claim():
        transactions[0] += 1
        with original_transaction() as connection:
            yield connection
        if transactions[0] != 1:
            return
        with original_transaction() as connection:
            if invalidity == "revoked":
                grant = connection.execute(
                    "SELECT id,revision,principal_id FROM work_grants LIMIT 1"
                ).fetchone()
                connection.execute(
                    "INSERT INTO work_grant_revocations VALUES (?,?,?,?,?)",
                    (grant["id"], grant["revision"], grant["principal_id"], time.time(), "test"),
                )
            elif invalidity == "stale_revision":
                connection.execute(
                    "UPDATE work_attempts SET revision=revision+1 WHERE id=?",
                    (kwargs["attempt_id"],),
                )
            else:
                work_item_id = connection.execute(
                    "SELECT work_item_id FROM work_attempts WHERE id=?",
                    (kwargs["attempt_id"],),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO work_attempts "
                    "(id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) "
                    "VALUES (?,?,2,2,'mock_cli','planned',?,?)",
                    ("replacement", work_item_id, time.time() + 600, time.time()),
                )

    monkeypatch.setattr(repo, "transaction", change_after_claim)
    with pytest.raises((ValueError, AuthorityDenied, WorkConflict)):
        intent.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("GO")
        )
    assert effects == []
    assert transactions == [2]
    with repo.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_bubblewrap_release_claims WHERE attempt_id=?",
                (kwargs["attempt_id"],),
            ).fetchone()[0]
            == 1
        )
    assert intent.read_historical(kwargs["attempt_id"], 1) == evidence


def test_restart_after_claim_before_go_cannot_reissue(tmp_path, monkeypatch):
    repo, _, kwargs = _setup(tmp_path)
    evidence = WorkBubblewrapSetupIntent(repo).record_pre_go(**kwargs)
    original_transaction = repo.transaction
    effects = []
    transactions = [0]

    @contextmanager
    def crash_after_claim():
        with original_transaction() as connection:
            yield connection
        transactions[0] += 1
        if transactions[0] == 1:
            raise OSError("process stopped after durable claim")

    monkeypatch.setattr(repo, "transaction", crash_after_claim)
    with pytest.raises(OSError, match="after durable claim"):
        WorkBubblewrapSetupIntent(repo).release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("GO")
        )
    restarted = WorkBubblewrapSetupIntent(WorkRepository(repo.path))
    with pytest.raises(WorkConflict):
        restarted.release_if_current(
            evidence, expected_attempt_revision=1, release=lambda: effects.append("retry GO")
        )
    assert effects == []
    assert restarted.read_historical(kwargs["attempt_id"], 1) == evidence
