"""A bound V2 command reaches Bubblewrap through its sealed content stage."""

import errno
import os
from test.clients.test_work_bubblewrap_process_identity import _identity
from test.security.test_work_bubblewrap_composition import (
    _TRUSTED_BWRAP_SHA256,
    _WORKER_MARKER,
    _minimal_static_worker,
    _real_bubblewrap,
)
from test.services.test_work_contract_binding import bind, context

import pytest

from cli_agent_orchestrator.services import work_bubblewrap_composition as composition
from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import WorkBubblewrapSetupIntent
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_executable_content import WorkExecutableContent
from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy


def _bound_worker(tmp_path):
    data = context(tmp_path)
    models, _, repository, _, _, _, _, _, original = data
    executable = _minimal_static_worker()
    identity = identify_static_executable("/bin/alpha", executable)
    contract = models.EffectiveWorkContractV2(
        **{
            **original.model_dump(),
            "schema_version": 2,
            "permissions": original.permissions.model_copy(update={"commands": ("/bin/alpha",)}),
            "executable_identities": (identity,),
        }
    )
    binding = bind(data, contract=contract)
    content = WorkExecutableContent(repository)
    content.publish(identity, executable)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET state='sent' WHERE id=?", (binding.attempt_id,)
        )
    return repository, binding, content, identity


def test_bound_v2_launch_uses_one_owned_stage_and_persists_identity_before_go(
    tmp_path, monkeypatch
):
    bwrap = _real_bubblewrap()
    repository, binding, content, identity = _bound_worker(tmp_path)
    stages = []
    original_resolver = content.resolve_and_stage

    def capture_stage(*args):
        staged = original_resolver(*args)
        stages.append(staged)
        return staged

    def reject_raw_restage(*_args):
        raise AssertionError("bound route must consume the already sealed resolver stage")

    monkeypatch.setattr(content, "resolve_and_stage", capture_stage)
    monkeypatch.setattr(composition, "stage_static_executable", reject_raw_restage)

    def persist_then_authorize(acknowledgement, process_identity):
        assert acknowledgement.sha256_digest == identity.sha256_digest
        assert len(stages) == 1
        assert stages[0].identity == identity
        assert os.fstat(stages[0].memfd_fd).st_size > 0
        repository.persist_bubblewrap_process_identity(binding.attempt_id, 1, process_identity)
        assert (
            repository.read_bubblewrap_process_identity(binding.attempt_id, 1) == process_identity
        )
        return True

    result = composition.launch_bound_work_static_elf(
        content,
        binding.attempt_id,
        1,
        identity.command_token,
        persist_and_authorize=persist_then_authorize,
        bwrap_path=bwrap,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
    )
    assert result.stdout == _WORKER_MARKER
    assert stages[0]._closed is True
    assert stages[0].memfd_fd == -1


def test_unbound_or_unknown_command_rejects_before_bubblewrap_spawn(tmp_path, monkeypatch):
    repository, binding, content, identity = _bound_worker(tmp_path)
    spawn_calls = []

    def unexpected_spawn(*args, **kwargs):
        spawn_calls.append((args, kwargs))
        raise AssertionError("Bubblewrap must not spawn without a bound V2 command")

    monkeypatch.setattr(composition.subprocess, "Popen", unexpected_spawn)
    for attempt_id, generation, command_token in (
        ("missing", 1, identity.command_token),
        (binding.attempt_id, 2, identity.command_token),
        (binding.attempt_id, 1, "/bin/beta"),
    ):
        with pytest.raises(ValueError):
            composition.launch_bound_work_static_elf(
                content,
                attempt_id,
                generation,
                command_token,
                persist_and_authorize=lambda _ack, _process_identity: True,
                bwrap_path="/missing/bwrap",
                bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
            )
    assert spawn_calls == []


def _ack_for_stage(staged, process_identity):
    return composition.WorkBubblewrapAcknowledgement(
        destination="/exec/worker",
        sha256_digest=staged.identity.sha256_digest,
        mode=0o500,
        size=os.fstat(staged.memfd_fd).st_size,
        destination_device=1,
        destination_inode=2,
        open_fds=(0, 1, 2),
        landlock_abi=1,
        seccomp_mode=2,
        unlisted_path_denied=True,
        unmounted_host_path_absent=True,
        memfd_create_denied_errno=errno.EPERM,
        connect_denied_errno=errno.EPERM,
        monitor_pid=process_identity["monitor_pid"],
        proxy_socket_device=None,
        proxy_socket_inode=None,
    )


def test_recorded_bound_launch_commits_pending_before_release_without_bwrap(tmp_path, monkeypatch):
    repository, binding, _, identity = _bound_worker(tmp_path)
    observed = []

    def launch_stage(staged, _selected_identity, **kwargs):
        process_identity = _identity()
        try:

            def release():
                evidence = WorkBubblewrapSetupIntent(repository).read_historical(
                    binding.attempt_id, 1
                )
                observed.append(evidence.release_intent)

            kwargs["release_after_ack"](
                _ack_for_stage(staged, process_identity),
                process_identity,
                release,
                lambda: None,
            )
            return "released"
        finally:
            staged.close()

    monkeypatch.setattr(composition, "_launch_owned_stage", launch_stage)
    result = composition.launch_recorded_bound_work_static_elf(
        repository,
        binding.attempt_id,
        1,
        expected_attempt_revision=1,
        command_token=identity.command_token,
        bwrap_path="/unused",
        bwrap_sha256_digest="0" * 64,
    )
    assert result == "released"
    assert observed == ["pending"]


def test_recorded_bound_launch_withholds_release_after_post_persist_revision_change(
    tmp_path, monkeypatch
):
    repository, binding, _, identity = _bound_worker(tmp_path)
    original_record = WorkBubblewrapSetupIntent.record_pre_go
    effects = []

    def record_then_change(self, *args, **kwargs):
        evidence = original_record(self, *args, **kwargs)
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE work_attempts SET revision=revision+1 WHERE id=?",
                (binding.attempt_id,),
            )
        return evidence

    def launch_stage(staged, _selected_identity, **kwargs):
        process_identity = _identity()
        try:
            kwargs["release_after_ack"](
                _ack_for_stage(staged, process_identity),
                process_identity,
                lambda: effects.append("GO"),
                lambda: None,
            )
        finally:
            staged.close()

    monkeypatch.setattr(WorkBubblewrapSetupIntent, "record_pre_go", record_then_change)
    monkeypatch.setattr(composition, "_launch_owned_stage", launch_stage)
    with pytest.raises(ValueError):
        composition.launch_recorded_bound_work_static_elf(
            repository,
            binding.attempt_id,
            1,
            expected_attempt_revision=1,
            command_token=identity.command_token,
            bwrap_path="/unused",
            bwrap_sha256_digest="0" * 64,
        )
    assert effects == []
    assert WorkBubblewrapSetupIntent(repository).read_historical(binding.attempt_id, 1)


def test_recorded_bound_launch_runs_real_worker_after_pending_setup(tmp_path):
    bwrap = _real_bubblewrap()
    repository, binding, _, identity = _bound_worker(tmp_path)
    result = composition.launch_recorded_bound_work_static_elf(
        repository,
        binding.attempt_id,
        1,
        expected_attempt_revision=1,
        command_token=identity.command_token,
        bwrap_path=bwrap,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
    )
    evidence = WorkBubblewrapSetupIntent(repository).read_historical(binding.attempt_id, 1)
    assert result.stdout == _WORKER_MARKER
    assert evidence.release_intent == "pending"
    assert evidence.executable_sha256 == identity.sha256_digest


def test_recorded_bound_launch_activates_private_proxy_only_after_ack_and_durable_setup(tmp_path):
    bwrap = _real_bubblewrap()
    repository, binding, _, identity = _bound_worker(tmp_path)
    observations = []

    def secret_factory():
        evidence = WorkBubblewrapSetupIntent(repository).read_historical(binding.attempt_id, 1)
        assert evidence is not None
        observations.append(("secret", evidence.ack_sha256))
        return b"server-only-secret"

    proxy = WorkMcpProxy(
        repository,
        server_secret_factory=secret_factory,
        upstream=lambda request, secret: {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"ok": secret.startswith(b"server")},
        },
    )
    result = composition.launch_recorded_bound_work_static_elf(
        repository,
        binding.attempt_id,
        1,
        expected_attempt_revision=1,
        command_token=identity.command_token,
        bwrap_path=bwrap,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        mcp_proxy=proxy,
    )
    assert result.stdout == _WORKER_MARKER
    assert result.acknowledgement.open_fds[0:3] == (0, 1, 2)
    assert len(result.acknowledgement.open_fds) == 4
    assert result.acknowledgement.open_fds[3] >= 3
    assert len(observations) == 1
    with repository.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_mcp_proxy_issues WHERE attempt_id=?",
                (binding.attempt_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=?",
                (binding.attempt_id,),
            ).fetchone()[0]
            == 0
        )


def test_recorded_bound_launch_reports_uncertainty_after_go_write(tmp_path, monkeypatch):
    bwrap = _real_bubblewrap()
    repository, binding, _, identity = _bound_worker(tmp_path)
    original_release = WorkBubblewrapSetupIntent.release_if_current

    def fail_after_go(self, evidence, *, expected_attempt_revision, release, authorize):
        def release_then_fail():
            release()
            raise OSError("injected failure after GO write")

        return original_release(
            self,
            evidence,
            expected_attempt_revision=expected_attempt_revision,
            release=release_then_fail,
            authorize=authorize,
        )

    monkeypatch.setattr(WorkBubblewrapSetupIntent, "release_if_current", fail_after_go)
    with pytest.raises(composition.WorkBubblewrapExecutionUncertain) as raised:
        composition.launch_recorded_bound_work_static_elf(
            repository,
            binding.attempt_id,
            1,
            expected_attempt_revision=1,
            command_token=identity.command_token,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )
    assert "reconcile" in str(raised.value)
    evidence = WorkBubblewrapSetupIntent(repository).read_historical(binding.attempt_id, 1)
    assert evidence.release_intent == "pending"
