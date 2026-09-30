"""Private executable bytes are bound to immutable V2 attempt evidence."""

import importlib
import importlib.util
import os
import time
from array import array
from contextlib import contextmanager
from test.services.test_work_contract_binding import bind, context
from test.services.test_work_executable_staging import _minimal_static_elf

import pytest

from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
from cli_agent_orchestrator.services.work_authority import WorkAuthority
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_executable_staging import (
    WorkExecutableStagingUnavailable,
)


def executable_content_module():
    name = "cli_agent_orchestrator.services.work_executable_content"
    assert importlib.util.find_spec(name) is not None, "executable content service is missing"
    return importlib.import_module(name)


def bound_v2(tmp_path):
    data = context(tmp_path)
    models, _, repo, _, _, _, _, _, original = data
    content = _minimal_static_elf()
    identity = identify_static_executable("/bin/alpha", content)
    contract = models.EffectiveWorkContractV2(
        **{
            **original.model_dump(),
            "schema_version": 2,
            "permissions": original.permissions.model_copy(update={"commands": ("/bin/alpha",)}),
            "executable_identities": (identity,),
        }
    )
    binding = bind(data, contract=contract)
    return repo, binding, identity, content


def test_publish_requires_exact_static_identity_and_deduplicates(tmp_path):
    module = executable_content_module()
    repo, _, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)

    with pytest.raises(ValueError):
        service.publish(identity, content[:-1] + b"\x91")
    with pytest.raises(ValueError):
        service.publish(identity, b"not ELF")
    with repo.read_snapshot() as connection:
        count = connection.execute("SELECT count(*) FROM work_executable_contents").fetchone()[0]
        assert count == 0

    first = service.publish(identity, content)
    assert service.publish(identity, content) == first
    assert (first.content_hash, first.immutable_location, first.byte_length) == (
        identity.sha256_digest,
        identity.sha256_digest,
        len(content),
    )
    with repo.read_snapshot() as connection:
        row = connection.execute(
            "SELECT content_hash,immutable_location,byte_length FROM work_executable_contents"
        ).fetchone()
        assert tuple(row) == (identity.sha256_digest, identity.sha256_digest, len(content))
        count = connection.execute("SELECT count(*) FROM work_executable_contents").fetchone()[0]
        assert count == 1


def test_publish_bounds_memoryview_by_byte_count_before_snapshot(tmp_path):
    module = executable_content_module()
    repo, _, identity, _ = bound_v2(tmp_path)
    oversized = memoryview(array("Q", [0]) * (1024 * 1024 + 1))
    assert len(oversized) < 8 * 1024 * 1024 < oversized.nbytes
    with pytest.raises(ValueError, match="size bound"):
        module.WorkExecutableContent(repo).publish(identity, oversized)


def test_publish_rejects_conflicting_catalog_metadata(tmp_path):
    module = executable_content_module()
    repo, _, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)
    service.publish(identity, content)
    with repo.connection() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_executable_contents_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_executable_contents_immutable_update")
        connection.execute(
            "UPDATE work_executable_contents SET byte_length=byte_length+1 WHERE content_hash=?",
            (identity.sha256_digest,),
        )
        connection.execute(trigger)
    with pytest.raises(ValueError, match="catalog conflicts"):
        service.publish(identity, content)
    assert (repo.executable_content_root / identity.sha256_digest).read_bytes() == content


def test_resolve_stages_exact_registered_bytes_after_snapshot_closes(tmp_path, monkeypatch):
    module = executable_content_module()
    repo, binding, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)
    service.publish(identity, content)
    active = False
    original_snapshot = repo.read_snapshot
    original_read = ImmutableResultStore.read

    @contextmanager
    def tracked_snapshot():
        nonlocal active
        with original_snapshot() as connection:
            active = True
            try:
                yield connection
            finally:
                active = False

    def replacing_read(store, ref):
        assert not active
        verified = original_read(store, ref)
        replacement = store.root / "replacement"
        replacement.write_bytes(content[:-1] + b"\x91")
        os.chmod(replacement, 0o600)
        os.replace(replacement, store.root / ref.immutable_location)
        return verified

    def checked_stage(selected_identity, selected_bytes):
        assert not active
        assert selected_identity == identity
        assert selected_bytes == content
        return "exact staged snapshot"

    monkeypatch.setattr(repo, "read_snapshot", tracked_snapshot)
    monkeypatch.setattr(ImmutableResultStore, "read", replacing_read)
    monkeypatch.setattr(module, "stage_static_executable", checked_stage)
    assert service.resolve_and_stage(binding.attempt_id, 1, identity.command_token) == (
        "exact staged snapshot"
    )


def test_resolve_returns_owned_sealed_memfd_for_bound_v2(tmp_path):
    module = executable_content_module()
    repo, binding, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)
    service.publish(identity, content)
    try:
        staged = service.resolve_and_stage(binding.attempt_id, 1, identity.command_token)
    except WorkExecutableStagingUnavailable as exc:
        pytest.skip(f"kernel executable memfd unavailable: {exc}")
    with staged:
        assert os.pread(staged.memfd_fd, len(content), 0) == content


@pytest.mark.parametrize("stale", ["revoked", "expired", "replaced", "terminal"])
def test_resolve_rejects_stale_authority_before_content_read_or_stage(tmp_path, monkeypatch, stale):
    module = executable_content_module()
    repo, binding, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)
    service.publish(identity, content)

    if stale == "revoked":
        actor = auth._verified_principal("https://issuer.test", "owner", [auth.SCOPE_ADMIN], "jwt")
        WorkAuthority(repo).revoke(
            actor, grant_id=binding.grant_id, expected_grant_revision=1, reason="revoked"
        )
    elif stale == "expired":
        expired_time = time.time() + 601
        monkeypatch.setattr(time, "time", lambda: expired_time)
    elif stale == "replaced":
        with repo.transaction() as connection:
            connection.execute(
                "INSERT INTO work_attempts(id,work_item_id,attempt_number,generation,"
                "provider,lease_expires_at,created_at) "
                "SELECT 'replacement',work_item_id,2,2,provider,?,? "
                "FROM work_attempts WHERE id=?",
                (time.time() + 300, time.time(), binding.attempt_id),
            )
    else:
        with repo.transaction() as connection:
            connection.execute(
                "UPDATE work_attempts SET state='finished' WHERE id=?",
                (binding.attempt_id,),
            )

    def forbidden_content_read(*_args, **_kwargs):
        pytest.fail("stale authority reached executable content read")

    def forbidden_stage(*_args, **_kwargs):
        pytest.fail("stale authority reached executable staging")

    monkeypatch.setattr(service.content, "read", forbidden_content_read)
    monkeypatch.setattr(module, "stage_static_executable", forbidden_stage)
    with pytest.raises((ValueError, PermissionError)):
        service.resolve_and_stage(binding.attempt_id, 1, identity.command_token)


def test_resolve_rejects_v1_and_unknown_binding_or_token(tmp_path):
    module = executable_content_module()
    data = context(tmp_path)
    _, _, repo, _, _, _, _, _, _ = data
    binding = bind(data)
    service = module.WorkExecutableContent(repo)
    for attempt, generation, token in (
        (binding.attempt_id, 1, "/bin/alpha"),
        ("missing", 1, "/bin/alpha"),
        (binding.attempt_id, 2, "/bin/alpha"),
        (binding.attempt_id, 1, "/bin/beta"),
    ):
        with pytest.raises(ValueError):
            service.resolve_and_stage(attempt, generation, token)


@pytest.mark.parametrize("damage", ["size", "location"])
def test_resolve_rejects_missing_and_corrupt_catalog_or_content(tmp_path, damage):
    module = executable_content_module()
    repo, binding, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)
    with pytest.raises(ValueError):
        service.resolve_and_stage(binding.attempt_id, 1, identity.command_token)
    service.publish(identity, content)
    with repo.connection() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_executable_contents_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_executable_contents_immutable_update")
        if damage == "size":
            connection.execute(
                "UPDATE work_executable_contents SET byte_length=byte_length+1 "
                "WHERE content_hash=?",
                (identity.sha256_digest,),
            )
        else:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE work_executable_contents SET immutable_location=? WHERE content_hash=?",
                ("f" * 64, identity.sha256_digest),
            )
        connection.execute(trigger)
    with pytest.raises(ValueError):
        service.resolve_and_stage(binding.attempt_id, 1, identity.command_token)


def test_resolve_rejects_noncanonical_v2_evidence(tmp_path):
    module = executable_content_module()
    repo, binding, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)
    service.publish(identity, content)
    with repo.connection() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_dispatch_v2_evidence_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_dispatch_v2_evidence_immutable_update")
        connection.execute(
            "UPDATE work_dispatch_v2_evidence SET contract_json=contract_json||' ' "
            "WHERE attempt_id=?",
            (binding.attempt_id,),
        )
        connection.execute(trigger)
    with pytest.raises(ValueError, match="canonical"):
        service.resolve_and_stage(binding.attempt_id, 1, identity.command_token)


def test_resolve_rejects_missing_or_changed_file(tmp_path):
    module = executable_content_module()
    repo, binding, identity, content = bound_v2(tmp_path)
    service = module.WorkExecutableContent(repo)
    service.publish(identity, content)
    path = repo.executable_content_root / identity.sha256_digest
    path.unlink()
    with pytest.raises((FileNotFoundError, ValueError)):
        service.resolve_and_stage(binding.attempt_id, 1, identity.command_token)
    path.write_bytes(content[:-1] + b"\x91")
    os.chmod(path, 0o600)
    with pytest.raises(ValueError):
        service.resolve_and_stage(binding.attempt_id, 1, identity.command_token)
