from __future__ import annotations

import os
import stat
from pathlib import Path
from test.live_provider import claude_auth_archive as archive
from types import SimpleNamespace

import message_api_restart_live as live
import pytest

pytestmark = pytest.mark.offline_database

_TEST_CREDENTIAL = b'{"test_fixture":"synthetic-only"}\n'


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _message_credential(workspace: Path) -> Path:
    claude_home = _private_dir(_private_dir(workspace / "home") / ".claude")
    credential = claude_home / ".credentials.json"
    descriptor = os.open(
        credential,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_TEST_CREDENTIAL)
        stream.flush()
        os.fsync(stream.fileno())
    return credential


def _state(tmp_path: Path, provider: str, *, server: object | None = None) -> live.RunState:
    run_root = _private_dir(tmp_path / "spec023-run-0123456789abcdef")
    workspace = _private_dir(run_root / "workspace")
    state = live.RunState(
        provider=provider,
        run_root=run_root,
        workspace=workspace,
        home=workspace / "home",
        source_root=tmp_path / "source",
        audit_path=run_root / "audit.jsonl",
    )
    state.claude_auth_staging_attempted = provider == "claude_code"
    if server is not None:
        state.servers.append(server)
    return state


def test_message_layout_archives_only_private_copy_with_run_root_key(tmp_path: Path) -> None:
    run_root = _private_dir(tmp_path / "spec023-run-0123456789abcdef")
    workspace = _private_dir(run_root / "workspace")
    source = _message_credential(workspace)
    source_before = source.stat()
    source_identity_before = (
        source_before.st_dev,
        source_before.st_ino,
        source_before.st_mode,
        source_before.st_uid,
        source_before.st_nlink,
        source_before.st_size,
        source_before.st_mtime_ns,
        source_before.st_ctime_ns,
    )
    archive_root = archive.prepare_archive_root(run_root / "claude-auth-archive")

    destination = archive.archive_private_credential_copy(
        workspace,
        archive_root,
        process_stopped=True,
        layout="message",
        archive_key=run_root.name,
    )

    assert destination == archive_root / run_root.name / "credentials.json"
    assert source.read_bytes() == _TEST_CREDENTIAL
    source_after = source.stat()
    assert (
        source_after.st_dev,
        source_after.st_ino,
        source_after.st_mode,
        source_after.st_uid,
        source_after.st_nlink,
        source_after.st_size,
        source_after.st_mtime_ns,
        source_after.st_ctime_ns,
    ) == source_identity_before
    assert destination.read_bytes() == _TEST_CREDENTIAL
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert workspace.exists()


def test_message_layout_rejects_path_like_archive_key_without_writing(tmp_path: Path) -> None:
    run_root = _private_dir(tmp_path / "spec023-run-0123456789abcdef")
    workspace = _private_dir(run_root / "workspace")
    _message_credential(workspace)
    archive_root = archive.prepare_archive_root(run_root / "claude-auth-archive")

    with pytest.raises(archive.CredentialArchiveError):
        archive.archive_private_credential_copy(
            workspace,
            archive_root,
            process_stopped=True,
            layout="message",
            archive_key="../escape",
        )

    with pytest.raises(archive.CredentialArchiveError):
        archive.archive_private_credential_copy(
            workspace,
            archive_root,
            process_stopped=True,
            layout="native-home",
            archive_key=run_root.name,
        )

    assert list(archive_root.iterdir()) == []
    assert workspace.exists()


def test_claude_archive_module_is_in_candidate_binding() -> None:
    candidate_dir = Path(live.__file__).parent
    candidate_bindings = live._hash_candidate_bindings(candidate_dir)

    assert live._claude_auth_archive is archive
    assert candidate_bindings["claude_auth_archive.py"]
    assert live._claude_archive_helper_origin_matches_candidate() is True
    assert Path(archive.__file__).resolve(strict=True) == (
        candidate_dir / "claude_auth_archive.py"
    ).resolve(strict=True)


def test_claude_cleanup_archives_after_owned_server_exit_before_workspace_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, "claude_code")
    credential = _message_credential(state.workspace)
    archive_root = state.run_root / "claude-auth-archive"
    events: list[str] = []
    archive_precedes_removal: list[bool] = []

    class ExitedProcess:
        pid = 0

        def poll(self) -> int:
            events.append("process_exited")
            return 0

    class Server:
        def stop(self) -> None:
            events.append("server_stop")

    server = Server()
    state.servers.append(server)
    monkeypatch.setattr(live, "_api_process", lambda _server: ExitedProcess())
    real_rmtree = live.shutil.rmtree

    def checked_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        if Path(path) == state.workspace:
            destination = archive_root / state.run_root.name / "credentials.json"
            archive_precedes_removal.append(
                destination.is_file() and destination.read_bytes() == _TEST_CREDENTIAL
            )
            events.append("server_stop_seen" if "server_stop" in events else "server_stop_missing")
            events.append(
                "process_exit_seen" if "process_exited" in events else "process_exit_missing"
            )
            events.append("workspace_remove")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(live.shutil, "rmtree", checked_rmtree)
    assert live._cleanup(state, server_module=SimpleNamespace()) is True
    assert state.cleanup == "pass"
    assert archive_precedes_removal == [True]
    assert events[-3:] == ["server_stop_seen", "process_exit_seen", "workspace_remove"]
    assert not state.workspace.exists()
    assert (
        archive_root / state.run_root.name / "credentials.json"
    ).read_bytes() == _TEST_CREDENTIAL


def test_claude_cleanup_retains_workspace_when_server_exit_is_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, "claude_code")
    _message_credential(state.workspace)
    server = SimpleNamespace(stop=lambda: None)
    state.servers.append(server)

    class RunningProcess:
        pid = 123

        def poll(self) -> None:
            return None

    monkeypatch.setattr(live, "_api_process", lambda _server: RunningProcess())
    monkeypatch.setattr(live.os, "getpgid", lambda _pid: 124)

    assert live._cleanup(state, server_module=SimpleNamespace()) is False
    assert state.cleanup == "failed"
    assert state.workspace.exists()
    assert not (state.run_root / "claude-auth-archive").exists()


def test_claude_cleanup_retains_workspace_when_archive_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, "claude_code")
    _message_credential(state.workspace)

    def failed_archive(*_args: object, **_kwargs: object) -> Path:
        raise archive.CredentialArchiveError("archive failure fixture")

    monkeypatch.setattr(
        live._claude_auth_archive,
        "archive_private_credential_copy",
        failed_archive,
    )

    assert live._cleanup(state, server_module=SimpleNamespace()) is False
    assert state.cleanup == "failed"
    assert state.workspace.exists()
    assert (state.run_root / "claude-auth-archive").is_dir()
    assert list((state.run_root / "claude-auth-archive").iterdir()) == []


@pytest.mark.parametrize("provider", ["codex", "opencode_cli"])
def test_non_claude_cleanup_does_not_archive_claude_looking_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    state = _state(tmp_path, provider)
    credential = _message_credential(state.workspace)

    def unexpected_archive(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("non-Claude cleanup attempted auth archival")

    monkeypatch.setattr(
        live._claude_auth_archive,
        "archive_private_credential_copy",
        unexpected_archive,
    )

    assert live._cleanup(state, server_module=SimpleNamespace()) is True
    assert state.cleanup == "pass"
    assert not state.workspace.exists()
    assert credential.parent.exists() is False
    assert not (state.run_root / "claude-auth-archive").exists()
