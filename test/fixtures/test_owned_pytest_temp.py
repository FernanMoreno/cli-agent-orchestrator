"""Keep native IPC fixtures below Unix socket limits without changing test gates."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from test.fixtures.owned_pytest_temp import create_owned_pytest_temp
from test.postgres.fixtures import reset_postgres_worker_targets

import pytest

pytestmark = pytest.mark.offline_database


@pytest.fixture
def workspace(request):
    value = create_owned_pytest_temp()
    request.addfinalizer(lambda: shutil.rmtree(value.root, ignore_errors=True))
    return value


def test_short_workspace_preserves_every_gate_argument_and_input_environment(workspace):
    args = ["-n", "8", "--dist", "loadfile", "--cov-fail-under=87", "test/"]
    environment = {"CAO_API_PORT": "1", "DOCKER_HOST": "unix:///var/run/docker.sock"}
    actual_args, actual_env = workspace.configure(args, environment)
    assert actual_args == args + ["--basetemp", str(workspace.basetemp)]
    assert actual_env == {**environment, "TMPDIR": str(workspace.tmpdir)}
    assert environment == {"CAO_API_PORT": "1", "DOCKER_HOST": "unix:///var/run/docker.sock"}
    assert args == ["-n", "8", "--dist", "loadfile", "--cov-fail-under=87", "test/"]
    assert workspace.root.stat().st_mode & 0o777 == 0o700


def test_native_worker_socket_can_bind_with_maximal_pytest_directory_name(workspace):
    directory = workspace.basetemp / "popen-gw7" / ("test_" + "x" * 25 + "0")
    directory.mkdir(parents=True)
    path = directory / "receiver-control.sock"
    assert len(os.fsencode(path)) < 108
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
    assert path.is_socket()


@pytest.mark.parametrize("argument", ["--basetemp", "--basetemp=/unowned"])
def test_caller_cannot_silently_override_owned_basetemp(workspace, argument):
    with pytest.raises(ValueError, match="basetemp"):
        workspace.configure([argument], {})


def test_replaced_temporary_directory_is_rejected_without_touching_foreign_data(
    workspace, tmp_path
):
    sentinel = tmp_path / "foreign"
    sentinel.write_text("preserve")
    workspace.basetemp.rmdir()
    workspace.tmpdir.rmdir()
    workspace.tmpdir.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="private"):
        workspace.configure([], {})
    assert sentinel.read_text() == "preserve"


def test_nonprivate_root_is_rejected_before_execution(workspace):
    workspace.root.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        workspace.configure([], {})


def test_ambient_long_tmpdir_is_not_used(monkeypatch, tmp_path, request):
    monkeypatch.setenv("TMPDIR", str(tmp_path / ("long-" * 30)))
    value = create_owned_pytest_temp()
    request.addfinalizer(lambda: shutil.rmtree(value.root, ignore_errors=True))
    assert value.root.parent == Path("/tmp")
    assert len(os.fsencode(value.root)) <= 24


def test_pytest_basetemp_is_confined_beneath_its_stable_tmpdir(workspace):
    assert workspace.basetemp.is_relative_to(workspace.tmpdir)
    assert workspace.basetemp != workspace.tmpdir


def test_actual_vault_factory_accepts_the_fresh_child_temporary_namespace(
    workspace, postgres_worker_targets
):
    reset_postgres_worker_targets(postgres_worker_targets)
    directory = workspace.basetemp / "popen-gw7" / "test_vault_contract0"
    directory.mkdir(parents=True)
    _, environment = workspace.configure([], dict(os.environ))
    environment["CAO_DATABASE_CONFIG_FILE"] = str(postgres_worker_targets.config_file)
    environment["CAO_HOME_DIR"] = str(postgres_worker_targets.home)
    code = (
        "import sys; from pathlib import Path; "
        "from test.fixtures.vault_factory import build_vault_fixture; "
        "fixture = build_vault_fixture(Path(sys.argv[1])); "
        "assert fixture.root.is_dir()"
    )
    child = subprocess.run(
        [sys.executable, "-c", code, str(directory)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stderr
