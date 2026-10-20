from __future__ import annotations

import os
import shutil
from pathlib import Path
from test.live_provider import claude_auth_archive as archive
from test.live_provider import interaction_cli_journey as journey
from types import ModuleType

import pytest

pytestmark = pytest.mark.offline_database

_ARCHIVE_CATEGORY = "claude_auth_archive_incomplete"
_CREDENTIAL_BYTES = b"FAKE-CLAUDE-CREDENTIAL"


class FakeJourney(ModuleType):
    def __init__(self) -> None:
        super().__init__("fake_journey")
        self.cleanup_calls: list[tuple[Path, bool, set[str]]] = []

        def remove_private_run_root(
            root: Path, *, process_stopped: bool, failures: set[str]
        ) -> bool:
            self.cleanup_calls.append((root, process_stopped, set(failures)))
            if process_stopped and not failures:
                shutil.rmtree(root)
                return True
            failures.add("private_files_preserved")
            return False

        self._remove_private_run_root = remove_private_run_root


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    path.chmod(0o700)
    return path


def _run_root(parent: Path, content: bytes = _CREDENTIAL_BYTES) -> Path:
    root = _private_dir(parent / "interaction-fake-1")
    claude_home = _private_dir(root / "claude-home")
    credential = claude_home / ".credentials.json"
    credential.write_bytes(content)
    credential.chmod(0o600)
    return root


def _file_snapshot(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _hook(archive_root: Path) -> tuple[FakeJourney, object]:
    fake_journey = FakeJourney()
    wrapped = archive.install_cleanup_archive_hook(fake_journey, archive_root)
    return fake_journey, wrapped


def test_interaction_journey_does_not_eagerly_load_claude_archive_helper():
    assert journey._claude_auth_archive is None


def test_archives_copy_before_removing_run_root_and_leaves_other_copies_unchanged(
    tmp_path, monkeypatch
):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    source_home = _private_dir(tmp_path / "operator-home")
    source_profile = _private_dir(source_home / ".claude")
    source_credential = source_profile / ".credentials.json"
    source_credential.write_bytes(b"FAKE-ORIGINAL-CREDENTIAL")
    source_credential.chmod(0o600)
    stage_root = _private_dir(tmp_path / "auth-stage")
    stage_credential = stage_root / "claude-credential.json"
    stage_credential.write_bytes(b"FAKE-STAGED-CREDENTIAL")
    stage_credential.chmod(0o600)
    source_snapshot = _file_snapshot(source_credential)
    stage_snapshot = _file_snapshot(stage_credential)
    monkeypatch.setenv("HOME", str(source_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(source_profile))
    journey_module, hook = _hook(archive_root)

    failures: set[str] = set()
    removed = hook(root, process_stopped=True, failures=failures)

    archived = archive_root / root.name / "credentials.json"
    assert removed is True
    assert not root.exists()
    assert archived.read_bytes() == _CREDENTIAL_BYTES
    assert archived.stat().st_mode & 0o777 == 0o600
    assert archived.stat().st_uid == os.geteuid()
    assert archived.parent.stat().st_mode & 0o777 == 0o700
    assert archived.parent.stat().st_uid == os.geteuid()
    assert source_credential.read_bytes() == b"FAKE-ORIGINAL-CREDENTIAL"
    assert _file_snapshot(source_credential) == source_snapshot
    assert stage_credential.read_bytes() == b"FAKE-STAGED-CREDENTIAL"
    assert _file_snapshot(stage_credential) == stage_snapshot
    assert not failures
    assert journey_module.cleanup_calls == [(root, True, set())]


def test_archives_current_bytes_when_private_copy_changed(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path, b"FAKE-OLD-CREDENTIAL")
    source = root / "claude-home" / ".credentials.json"
    source.write_bytes(b"FAKE-ROTATED-CREDENTIAL")
    source.chmod(0o600)
    _, hook = _hook(archive_root)

    assert hook(root, process_stopped=True, failures=set()) is True

    assert (archive_root / root.name / "credentials.json").read_bytes() == (
        b"FAKE-ROTATED-CREDENTIAL"
    )
    assert not root.exists()


def test_live_provider_process_prevents_archive_and_root_removal(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=False, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert not any(archive_root.iterdir())
    assert fake_journey.cleanup_calls[0][1] is False
    assert "private_files_preserved" in failures


def test_truthy_non_boolean_stop_does_not_authorize_removal(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=1, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert not any(archive_root.iterdir())
    assert fake_journey.cleanup_calls[0][1] is False
    assert "private_files_preserved" in failures


def test_preexisting_cleanup_failure_skips_archive_and_preserves_root(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    fake_journey, hook = _hook(archive_root)
    failures = {"provider_process_not_stopped"}

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert not any(archive_root.iterdir())
    assert fake_journey.cleanup_calls[0][1] is True
    assert fake_journey.cleanup_calls[0][2] == {"provider_process_not_stopped"}
    assert failures == {"provider_process_not_stopped", "private_files_preserved"}


def test_symlinked_credential_fails_closed_without_diagnostic_leak(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    credential = root / "claude-home" / ".credentials.json"
    target = tmp_path / "fake-target"
    target.write_bytes(b"FAKE-SYNTHETIC-SECRET-SENTINEL")
    target.chmod(0o600)
    credential.unlink()
    credential.symlink_to(target)
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert failures == {_ARCHIVE_CATEGORY, "private_files_preserved"}
    assert "FAKE-SYNTHETIC-SECRET-SENTINEL" not in repr(failures)
    assert fake_journey.cleanup_calls[0][1] is False
    assert not any(archive_root.iterdir())


def test_source_replacement_during_read_fails_closed(tmp_path, monkeypatch):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    source = root / "claude-home" / ".credentials.json"
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()
    real_read = os.read
    mutated = False

    def replace_source_after_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        data = real_read(descriptor, size)
        if data and not mutated:
            nonlocal_source_write()
            mutated = True
        return data

    def nonlocal_source_write() -> None:
        source.write_bytes(b"FAKE-NEW-CREDENTIAL-BYTES")
        source.chmod(0o600)

    monkeypatch.setattr(archive.os, "read", replace_source_after_read)
    removed = hook(root, process_stopped=True, failures=failures)

    assert mutated
    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert fake_journey.cleanup_calls[0][1] is False
    assert not any(archive_root.iterdir())


def test_source_replacement_during_archive_write_fails_closed(tmp_path, monkeypatch):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    source = root / "claude-home" / ".credentials.json"
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()
    real_write = archive._write_new_archive_file
    mutated = False

    def replace_source_after_archive(parent_fd, destination, content):
        nonlocal mutated
        real_write(parent_fd, destination, content)
        source.write_bytes(b"FAKE-LATE-ROTATED-CREDENTIAL")
        source.chmod(0o600)
        mutated = True

    monkeypatch.setattr(archive, "_write_new_archive_file", replace_source_after_archive)
    removed = hook(root, process_stopped=True, failures=failures)

    assert mutated
    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert fake_journey.cleanup_calls[0][1] is False
    assert (archive_root / root.name / "credentials.json").read_bytes() == (_CREDENTIAL_BYTES)


def test_existing_archive_is_never_overwritten_and_root_is_preserved(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    existing = _private_dir(archive_root / root.name)
    destination = existing / "credentials.json"
    destination.write_bytes(b"FAKE-OLDER-ARCHIVE")
    destination.chmod(0o600)
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert destination.read_bytes() == b"FAKE-OLDER-ARCHIVE"
    assert _ARCHIVE_CATEGORY in failures
    assert fake_journey.cleanup_calls[0][1] is False


def test_archive_directory_replacement_race_fails_closed(tmp_path, monkeypatch):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    moved_archive = archive_root / "moved-private-directory"
    attacker_dir = _private_dir(tmp_path / "attacker-target")
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()
    original_write = archive._write_new_archive_file
    swapped = False

    def replace_archive_entry(parent_fd, destination, content):
        nonlocal swapped
        if not swapped:
            os.rename(destination.parent, moved_archive)
            destination.parent.symlink_to(attacker_dir, target_is_directory=True)
            swapped = True
        return original_write(parent_fd, destination, content)

    monkeypatch.setattr(archive, "_write_new_archive_file", replace_archive_entry)
    removed = hook(root, process_stopped=True, failures=failures)

    assert swapped
    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert not (attacker_dir / "credentials.json").exists()
    assert (moved_archive / "credentials.json").read_bytes() == _CREDENTIAL_BYTES
    assert fake_journey.cleanup_calls[0][1] is False


def test_symlinked_claude_home_fails_closed_and_preserves_root(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    real_home = root / "claude-home"
    moved_home = root / "claude-home-private"
    os.rename(real_home, moved_home)
    real_home.symlink_to(moved_home, target_is_directory=True)
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert fake_journey.cleanup_calls[0][1] is False
    assert not any(archive_root.iterdir())


def test_archive_root_rejects_symlink_and_bad_mode(tmp_path):
    parent = _private_dir(tmp_path / "parent")
    unsafe = parent / "unsafe"
    unsafe.mkdir(mode=0o755)
    unsafe.chmod(0o755)
    assert unsafe.stat().st_mode & 0o777 == 0o755
    with pytest.raises(archive.CredentialArchiveError, match="directory ownership or mode invalid"):
        archive.prepare_archive_root(unsafe)

    real = _private_dir(parent / "real")
    link = parent / "linked"
    link.symlink_to(real)
    with pytest.raises(archive.CredentialArchiveError, match="symlink rejected"):
        archive.prepare_archive_root(link)


def test_missing_credential_fails_closed_and_preserves_root(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    (root / "claude-home" / ".credentials.json").unlink()
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert failures == {_ARCHIVE_CATEGORY, "private_files_preserved"}
    assert not any(archive_root.iterdir())
    assert fake_journey.cleanup_calls[0][1] is False


@pytest.mark.parametrize("unsafe_kind", ["mode", "hardlink"])
def test_unsafe_credential_file_fails_closed(tmp_path, unsafe_kind):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    source = root / "claude-home" / ".credentials.json"
    if unsafe_kind == "mode":
        source.chmod(0o640)
    else:
        os.link(source, root / "credential-hardlink")
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert fake_journey.cleanup_calls[0][1] is False
    assert not any(archive_root.iterdir())


def test_wrong_effective_owner_fails_closed(tmp_path, monkeypatch):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()
    actual_uid = os.geteuid()
    monkeypatch.setattr(archive.os, "geteuid", lambda: actual_uid + 1)

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert fake_journey.cleanup_calls[0][1] is False
    assert not any(archive_root.iterdir())


def test_oversize_credential_fails_closed_and_preserves_root(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path, b"x" * (archive.MAX_CREDENTIAL_BYTES + 1))
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()

    removed = hook(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert fake_journey.cleanup_calls[0][1] is False
    assert not any(archive_root.iterdir())


def test_archive_helper_rejects_unconfirmed_process_stop(tmp_path):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)

    with pytest.raises(archive.CredentialArchiveError, match="not confirmed stopped"):
        archive.archive_private_credential_copy(root, archive_root, process_stopped=1)

    assert root.is_dir()
    assert not any(archive_root.iterdir())


def test_archive_root_sync_failure_preserves_run_root_and_archived_copy(tmp_path, monkeypatch):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    archive_stat = archive_root.stat()
    root = _run_root(tmp_path)
    fake_journey, hook = _hook(archive_root)
    failures: set[str] = set()
    real_fsync = os.fsync
    archive_sync_count = 0

    def fail_final_archive_sync(descriptor: int) -> None:
        nonlocal archive_sync_count
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (archive_stat.st_dev, archive_stat.st_ino):
            archive_sync_count += 1
            if archive_sync_count == 2:
                raise OSError("fake archive directory sync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(archive.os, "fsync", fail_final_archive_sync)
    removed = hook(root, process_stopped=True, failures=failures)

    assert archive_sync_count == 2
    assert removed is False
    assert root.is_dir()
    assert _ARCHIVE_CATEGORY in failures
    assert (archive_root / root.name / "credentials.json").read_bytes() == (_CREDENTIAL_BYTES)
    assert fake_journey.cleanup_calls[0][1] is False


def test_prepare_archive_root_sync_failure_is_reported(tmp_path, monkeypatch):
    parent = _private_dir(tmp_path / "parent")
    parent_stat = parent.stat()
    real_fsync = os.fsync

    def fail_parent_sync(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (parent_stat.st_dev, parent_stat.st_ino):
            raise OSError("fake parent sync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(archive.os, "fsync", fail_parent_sync)
    with pytest.raises(archive.CredentialArchiveError, match="archive parent sync failed"):
        archive.prepare_archive_root(parent / "archive")


def test_main_journey_cleanup_archives_then_removes_fake_run_root(tmp_path, monkeypatch):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    original = journey._remove_private_run_root
    monkeypatch.setattr(journey, "_remove_private_run_root", original)
    wrapped = archive.install_cleanup_archive_hook(journey, archive_root)
    failures: set[str] = set()

    removed = journey._remove_private_run_root(root, process_stopped=True, failures=failures)

    assert journey._remove_private_run_root is wrapped
    assert removed is True
    assert not root.exists()
    assert (archive_root / root.name / "credentials.json").read_bytes() == (_CREDENTIAL_BYTES)
    assert not failures
    monkeypatch.undo()
    assert journey._remove_private_run_root is original


def test_r14_provider_cleanup_archives_before_removing_run_root(tmp_path):
    cache = _private_dir(tmp_path / "cache")
    root = _run_root(cache)
    operator_claude_home = _private_dir(_private_dir(tmp_path / "operator") / ".claude")
    operator_credential = operator_claude_home / ".credentials.json"
    operator_credential.write_bytes(b"FAKE-OPERATOR-LOGIN-SENTINEL")
    operator_credential.chmod(0o600)
    staged_credential = _private_dir(tmp_path / "staged") / "credentials.json"
    staged_credential.write_bytes(b"FAKE-STAGED-LOGIN-SENTINEL")
    staged_credential.chmod(0o600)
    failures: set[str] = set()

    removed = journey._remove_provider_run_root(
        root,
        provider_name="claude_code",
        process_stopped=True,
        failures=failures,
    )

    archived = cache / "claude-auth-archive" / root.name / "credentials.json"
    assert removed is True
    assert not root.exists()
    assert archived.read_bytes() == _CREDENTIAL_BYTES
    assert operator_credential.read_bytes() == b"FAKE-OPERATOR-LOGIN-SENTINEL"
    assert staged_credential.read_bytes() == b"FAKE-STAGED-LOGIN-SENTINEL"
    assert not failures


def test_r14_archive_helper_origin_matches_the_selected_journey_source():
    assert journey._claude_archive_helper_origin_matches_journey() is True


def test_r14_archive_helper_origin_rejects_unexpected_helper_contents(monkeypatch):
    monkeypatch.setattr(journey, "_CLAUDE_ARCHIVE_HELPER_SHA256", "0" * 64)

    assert journey._claude_archive_helper_origin_matches_journey() is False


def test_r14_archive_helper_origin_fails_closed_without_nofollow(monkeypatch):
    monkeypatch.delattr(journey.os, "O_NOFOLLOW", raising=False)

    assert journey._claude_archive_helper_origin_matches_journey() is False


def test_pinned_helper_loader_rejects_modified_code_before_execution(tmp_path):
    helper_path = tmp_path / "claude_auth_archive.py"
    marker_path = tmp_path / "module-executed"
    helper_path.write_text(
        f"from pathlib import Path\nPath({str(marker_path)!r}).touch()\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="source binding invalid"):
        journey._load_pinned_claude_archive_helper(helper_path, "0" * 64)

    assert not marker_path.exists()


def test_r14_provider_cleanup_preserves_root_when_archive_helper_origin_is_unpinned(
    tmp_path, monkeypatch
):
    cache = _private_dir(tmp_path / "cache")
    root = _run_root(cache)
    monkeypatch.setattr(
        journey, "_claude_archive_helper_origin_matches_journey", lambda *_args: False
    )
    failures: set[str] = set()

    removed = journey._remove_provider_run_root(
        root,
        provider_name="claude_code",
        process_stopped=True,
        failures=failures,
    )

    assert removed is False
    assert root.is_dir()
    assert failures == {_ARCHIVE_CATEGORY, "private_files_preserved"}


def test_r14_provider_cleanup_preserves_root_when_process_is_unverified(tmp_path):
    cache = _private_dir(tmp_path / "cache")
    root = _run_root(cache)
    failures = {"provider_process_not_stopped"}

    removed = journey._remove_provider_run_root(
        root,
        provider_name="claude_code",
        process_stopped=False,
        failures=failures,
    )

    assert removed is False
    assert root.is_dir()
    assert not (cache / "claude-auth-archive").exists()
    assert "private_files_preserved" in failures


def test_r14_provider_cleanup_preserves_root_when_archive_fails(tmp_path, monkeypatch):
    cache = _private_dir(tmp_path / "cache")
    root = _run_root(cache)

    def fail_archive(*args, **kwargs):
        raise archive.CredentialArchiveError("synthetic archive failure")

    helper_module = journey._get_claude_archive_helper()
    monkeypatch.setattr(helper_module, "archive_private_credential_copy", fail_archive)
    failures: set[str] = set()

    removed = journey._remove_provider_run_root(
        root,
        provider_name="claude_code",
        process_stopped=True,
        failures=failures,
    )

    assert removed is False
    assert root.is_dir()
    assert failures == {_ARCHIVE_CATEGORY, "private_files_preserved"}


def test_r14_provider_cleanup_does_not_load_claude_helper_for_other_providers(
    tmp_path, monkeypatch
):
    cache = _private_dir(tmp_path / "cache")
    root = _private_dir(cache / "codex-fake-run")
    failures: set[str] = set()
    monkeypatch.setattr(journey, "_claude_auth_archive", None)

    def fail_helper_load(*args, **kwargs):
        raise AssertionError("non-Claude cleanup loaded the Claude helper")

    monkeypatch.setattr(journey, "_load_pinned_claude_archive_helper", fail_helper_load)

    removed = journey._remove_provider_run_root(
        root,
        provider_name="codex",
        process_stopped=True,
        failures=failures,
    )

    assert removed is True
    assert not root.exists()
    assert not (cache / "claude-auth-archive").exists()
    assert not failures
    assert journey._claude_auth_archive is None


def test_main_journey_cleanup_preserves_root_when_archive_fails(tmp_path, monkeypatch):
    archive_root = archive.prepare_archive_root(_private_dir(tmp_path / "archive"))
    root = _run_root(tmp_path)
    existing = _private_dir(archive_root / root.name)
    existing_file = existing / "credentials.json"
    existing_file.write_bytes(b"FAKE-EXISTING-ARCHIVE")
    existing_file.chmod(0o600)
    original = journey._remove_private_run_root
    monkeypatch.setattr(journey, "_remove_private_run_root", original)
    archive.install_cleanup_archive_hook(journey, archive_root)
    failures: set[str] = set()

    removed = journey._remove_private_run_root(root, process_stopped=True, failures=failures)

    assert removed is False
    assert root.is_dir()
    assert existing_file.read_bytes() == b"FAKE-EXISTING-ARCHIVE"
    assert failures == {_ARCHIVE_CATEGORY, "private_files_preserved"}
    monkeypatch.undo()
    assert journey._remove_private_run_root is original
