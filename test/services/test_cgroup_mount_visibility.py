"""Synthetic mount decisions; these tests neither create mounts nor prove host coverage."""

import os
import shutil
from test.services.test_admission_resources import api, linux_files

import pytest

pytestmark = pytest.mark.offline_database


def layout(proc, rows):
    (proc / "self/mountinfo").write_text("\n".join(rows) + "\n")


def value(proc, *, actual=20, overrides=None):
    overrides = overrides or {}
    return api().LinuxResourceSampler(
        proc_root=proc,
        affinity=lambda: frozenset({0, 1}),
        clock=lambda: 10,
        mount_of=lambda fd: overrides.get(os.fstat(fd).st_ino, actual),
    )


def select(sampler):
    with sampler._groups() as (directories, hidden):
        return tuple(d.path for d in directories), hidden


def tree_ids(path, mount_id):
    return {p.stat().st_ino: mount_id for p in [path, *path.rglob("*")]}


def test_widest_visible_root_wins_over_extra_subroot_alias(linux_files, tmp_path):
    proc, group = linux_files
    alias = tmp_path / "leaf-alias"
    shutil.copytree(group / "worker", alias)
    layout(
        proc,
        [
            f"20 19 0:25 / {group} rw - cgroup2 cgroup2 rw",
            f"21 19 0:25 /worker {alias} rw - cgroup2 cgroup2 rw",
        ],
    )
    paths, hidden = select(value(proc))
    assert paths == (group / "worker", group)
    assert hidden is False


@pytest.mark.parametrize("filesystem", ["cgroup2", "tmpfs"])
def test_same_point_overlay_hides_original_candidate(linux_files, filesystem):
    proc, group = linux_files
    layout(
        proc,
        [
            f"20 19 0:25 / {group} rw - cgroup2 cgroup2 rw",
            f"30 20 0:26 /unrelated {group} rw - {filesystem} none rw",
        ],
    )
    with pytest.raises(api()._ReadingError) as error:
        select(value(proc, actual=30))
    assert error.value.issue == "unsupported_cgroup"


def test_overmount_inside_leaf_path_invalidates_that_candidate(linux_files):
    proc, group = linux_files
    layout(
        proc,
        [
            f"20 19 0:25 / {group} rw - cgroup2 cgroup2 rw",
            f"30 20 0:26 / {group / 'worker'} rw - tmpfs none rw",
        ],
    )
    with pytest.raises(api()._ReadingError) as error:
        select(value(proc, overrides={(group / "worker").stat().st_ino: 30}))
    assert error.value.issue == "unsupported_cgroup"


def test_descendant_of_hidden_mount_is_not_a_visible_candidate(linux_files, tmp_path):
    proc, group = linux_files
    outer = group.parent
    layout(
        proc,
        [
            f"10 9 0:24 / {outer} rw - ext4 none rw",
            f"20 10 0:25 / {group} rw - cgroup2 cgroup2 rw",
            f"30 10 0:26 / {outer} rw - tmpfs none rw",
        ],
    )
    with pytest.raises(api()._ReadingError) as error:
        select(value(proc, actual=30))
    assert error.value.issue == "unsupported_cgroup"


def test_visible_alias_still_works_when_other_mount_is_hidden(linux_files, tmp_path):
    proc, group = linux_files
    alias = tmp_path / "visible"
    shutil.copytree(group, alias)
    layout(
        proc,
        [
            f"20 19 0:25 / {group} rw - cgroup2 cgroup2 rw",
            f"30 20 0:26 / {group} rw - tmpfs none rw",
            f"40 19 0:25 / {alias} rw - cgroup2 cgroup2 rw",
        ],
    )
    paths, hidden = select(value(proc, actual=30, overrides=tree_ids(alias, 40)))
    assert paths == (alias / "worker", alias)
    assert hidden is False


def test_topmost_matching_cgroup_mount_is_accessible(linux_files):
    proc, group = linux_files
    layout(
        proc,
        [
            f"20 19 0:25 /unrelated {group} rw - cgroup2 cgroup2 rw",
            f"30 20 0:25 / {group} rw - cgroup2 cgroup2 rw",
        ],
    )
    assert select(value(proc, actual=30)) == ((group / "worker", group), False)


def test_file_bind_mount_is_rejected_before_controller_data(linux_files):
    proc, group = linux_files
    reader = value(proc, overrides={(group / "worker/cpu.max").stat().st_ino: 90})
    sample = reader.sample(storage=api().StorageObservation(10, 8))
    assert sample.observation.coverage == "unknown"
    assert "invalid_cgroup_path" in sample.issues


def test_verified_descriptors_close_on_success_and_exception(linux_files):
    proc, _ = linux_files
    reader = value(proc)
    for fail in (False, True):
        descriptors = []
        try:
            with reader._groups() as (directories, hidden):
                descriptors = [d.descriptor for d in directories]
                assert descriptors and not hidden
                assert all(os.fstat(fd) for fd in descriptors)
                if fail:
                    raise RuntimeError("controlled-body-failure")
        except RuntimeError:
            assert fail
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)


@pytest.mark.parametrize("view", ["cgroup", "mountinfo"])
def test_changed_view_after_pinned_reads_is_unknown(linux_files, monkeypatch, view):
    proc, _ = linux_files
    reader = value(proc)
    original = reader._group

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (proc / "self" / view).write_text("changed-view\n")
        return result

    monkeypatch.setattr(reader, "_group", changed)
    sample = reader.sample(storage=api().StorageObservation(10, 8))
    assert sample.observation.coverage == "unknown"
    assert "invalid_cgroup_path" in sample.issues
    assert reader._previous is None


@pytest.mark.parametrize(
    "payload", ["", "mnt_id: nope\n", "mnt_id: 0\n", "mnt_id: 20\nmnt_id: 20\n"]
)
def test_missing_or_malformed_descriptor_mount_is_denied(linux_files, payload):
    proc, group = linux_files
    fdinfo = proc / "self/fdinfo"
    fdinfo.mkdir()
    reader = api().LinuxResourceSampler(proc_root=proc)
    descriptor = os.open(group, os.O_PATH | os.O_DIRECTORY)
    try:
        (fdinfo / str(descriptor)).write_text(payload)
        with pytest.raises(ValueError):
            reader._descriptor_mount(descriptor)
    finally:
        os.close(descriptor)


def test_default_descriptor_reader_uses_real_kernel_mount_id(linux_files):
    proc, group = linux_files
    (proc / "self/fdinfo").symlink_to("/proc/self/fdinfo", target_is_directory=True)
    reader = api().LinuxResourceSampler(proc_root=proc)
    descriptor = os.open(group, os.O_PATH | os.O_DIRECTORY)
    try:
        actual = reader._descriptor_mount(descriptor)
        expected = next(
            line.split()[1]
            for line in (proc / "self/fdinfo" / str(descriptor)).read_text().splitlines()
            if line.startswith("mnt_id:")
        )
        assert type(actual) is int and actual > 0 and actual == int(expected)
    finally:
        os.close(descriptor)


def test_deeper_valid_fallback_still_reports_hidden_ancestors(linux_files, tmp_path):
    proc, group = linux_files
    alias = tmp_path / "leaf-only"
    shutil.copytree(group / "worker", alias)
    layout(
        proc,
        [
            f"20 19 0:25 / {group} rw - cgroup2 cgroup2 rw",
            f"21 19 0:25 /worker {alias} rw - cgroup2 cgroup2 rw",
        ],
    )
    assert select(value(proc, actual=30, overrides=tree_ids(alias, 21))) == ((alias,), True)


def test_group_reads_use_pinned_directory_after_path_replacement(linux_files, tmp_path):
    proc, group = linux_files
    reader = value(proc)
    with reader._groups() as (directories, _):
        leaf = directories[0]
        old_inode = os.fstat(leaf.descriptor).st_ino
        previous = tmp_path / "old-leaf"
        leaf.path.rename(previous)
        shutil.copytree(previous, leaf.path)
        (leaf.path / "memory.max").write_text("1048576")
        measured = reader._group(leaf)
        assert measured.identity[1] == old_inode
        assert measured.memory_limit == 400 * 1024**2
        assert leaf.path.stat().st_ino != old_inode


@pytest.mark.parametrize("kind", ["fifo", "oversize"])
def test_unexpected_controller_source_is_bounded_and_unknown(linux_files, kind):
    proc, group = linux_files
    path = group / "worker/memory.current"
    if kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    else:
        path.write_text("9" * 65537)
    result = value(proc).sample(storage=api().StorageObservation(10, 8))
    assert result.observation.coverage == "unknown"
    assert result.observation.memory_available_bytes is None


def test_aborted_descriptor_probe_closes_open_directory(linux_files):
    proc, group = linux_files
    seen = []

    def aborted(fd):
        seen.append(fd)
        raise RuntimeError("controlled-probe-abort")

    reader = api().LinuxResourceSampler(proc_root=proc, mount_of=aborted)
    with pytest.raises(RuntimeError, match="controlled-probe-abort"):
        select(reader)
    assert len(seen) == 1
    with pytest.raises(OSError):
        os.fstat(seen[0])
