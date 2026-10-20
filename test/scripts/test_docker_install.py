"""Docker deployment boundaries and reversible service cutover."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["1.18.32", "2.0.18", "3.0.0"])
def test_provider_preflight_matches_opencode_supported_launch_versions(
    tmp_path, monkeypatch, version
):
    import sys
    from types import SimpleNamespace

    from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend

    module = load("docker_install")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("claude", "codex", "opencode"):
        command = tools / name
        command.write_text("#!/bin/sh\necho " + version + "\n")
        command.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(DockerWorkBackend, "_validate_engine_and_image", lambda self: None)
    monkeypatch.setitem(
        sys.modules, "docker_personal_runtime", SimpleNamespace(check_mcp_integrations=lambda: None)
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(
        module,
        "container_arguments",
        lambda *a, **kw: [
            "run",
            "--detach",
            "--name",
            "check",
            "--restart",
            "no",
            "--publish",
            "unused",
            "image",
        ],
    )

    def execute_check(arguments):
        position = arguments.index("-c")
        monkeypatch.setattr(sys, "argv", ["-c", *arguments[position + 2 :]])
        exec(arguments[position + 1], {})

    monkeypatch.setattr(module, "docker", execute_check)
    if version.startswith("3."):
        with pytest.raises(ValueError, match="unsupported OpenCode"):
            module.validate_integrations("image", tmp_path, {"image_id": "sha256:" + "a" * 64}, [])
    else:
        module.validate_integrations("image", tmp_path, {"image_id": "sha256:" + "a" * 64}, [])


def test_proxy_preserves_browser_authority_and_streaming():
    config = load("docker_personal_runtime").proxy_config(9889)
    assert "listen 8080" in config
    assert "proxy_pass http://127.0.0.1:9889" in config
    assert "proxy_set_header Host $http_host" in config
    for header in ("Forwarded", "X-Forwarded-For", "X-Real-IP", "X-Forwarded-Proto"):
        assert f'proxy_set_header {header} "";' in config
    assert "proxy_buffering off" in config
    assert "proxy_set_header Upgrade $http_upgrade" in config
    assert "access_log off" in config


@pytest.mark.parametrize("port", [True, 0, 65536, "9889; listen 80"])
def test_proxy_rejects_invalid_upstream_port(port):
    with pytest.raises(ValueError):
        load("docker_personal_runtime").proxy_config(port)


def test_container_publishes_only_localhost_and_preserves_paths(tmp_path, monkeypatch):
    module = load("docker_install")
    socket = tmp_path / "docker.sock"
    socket.touch()
    monkeypatch.setattr(module, "SOCKET", socket)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    args = module.container_arguments(
        root, "sha256:" + "a" * 64, "cao-personal", {"server_port": 9889}, []
    )
    assert args[args.index("--publish") + 1] == "127.0.0.1:9889:8080"
    assert f"type=bind,src={root},dst={root}" in args
    assert "--privileged" not in args
    assert "--init" in args
    assert "--restart" in args
    assert not any("TOKEN=" in argument or "PASSWORD=" in argument for argument in args)


@pytest.mark.offline_database
def test_managed_postgres_runs_as_private_data_owner(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    module = load("docker_install")
    data = tmp_path / "postgres-data"
    module.private_directory(data)
    password = tmp_path / "postgres-password"
    password.write_text("synthetic-test-password")
    calls = []
    inspected = 0

    def fake_inspect(_name):
        nonlocal inspected
        inspected += 1
        if inspected == 1:
            return None
        return {"NetworkSettings": {"Networks": {"private-network": {"Aliases": ["main-db"]}}}}

    def fake_docker(arguments, **_kwargs):
        calls.append(list(arguments))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module, "inspect", fake_inspect)
    monkeypatch.setattr(module, "docker", fake_docker)

    module._ensure_postgres_container(
        "cao-main-pg-test",
        "private-network",
        "main-db",
        data,
        password,
        "cao_owner",
        "cao",
        {"org.cao.test.owner": "private-test"},
    )

    run = calls[0]
    assert run[0] == "run"
    assert run[run.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert run[run.index("--mount") + 1] == module.mount_argument(data, Path("/var/lib/postgresql"))
    assert "PGDATA=/var/lib/postgresql/18/docker" in run
    assert data.stat().st_mode & 0o777 == 0o700


def _fake_managed_postgres_readiness(module, monkeypatch, tmp_path, *, existing, never_ready=False):
    from types import SimpleNamespace

    name = "cao-main-pg-readiness-test"
    network = "private-network"
    alias = "main-db"
    owner = "cao_owner"
    database = "cao"
    labels = {"org.cao.test.owner": "private-test"}
    container = {
        "Config": {"Labels": labels, "Image": module.POSTGRES_IMAGE},
        "State": {"Running": True},
        "NetworkSettings": {"Networks": {network: {"Aliases": [alias]}}},
    }
    inspect_count = 0
    phase = {"name": "temporary_socket_only_bootstrap"}
    clock = {"seconds": 0, "sleeps": []}
    readiness_calls = []
    readiness_codes = []
    run_calls = []

    def fake_inspect(_name):
        nonlocal inspect_count
        inspect_count += 1
        if existing or inspect_count > 1:
            return container
        return None

    def fake_docker(arguments, **_kwargs):
        call = list(arguments)
        if call[0] == "run":
            run_calls.append(call)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if call[:2] == ["exec", name] and "pg_isready" in call:
            readiness_calls.append(call)
            index = call.index("pg_isready")
            options = call[index + 1 :]
            host = None
            for position, option in enumerate(options[:-1]):
                if option in ("--host", "-h"):
                    host = options[position + 1]
                    break
            if host is None:
                ready = True  # The pinned entrypoint's temporary server accepts socket probes.
            else:
                ready = host == "127.0.0.1" and phase["name"] == "final_tcp_ready"
            code = 0 if ready else 1
            readiness_codes.append(code)
            return SimpleNamespace(returncode=code, stdout="", stderr="")
        raise AssertionError(f"unexpected Docker command category: {call[0]}")

    def fake_monotonic():
        return clock["seconds"]

    def fake_sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["seconds"] += seconds
        if not never_ready:
            phase["name"] = "final_tcp_ready"

    monkeypatch.setattr(module, "inspect", fake_inspect)
    monkeypatch.setattr(module, "docker", fake_docker)
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=fake_monotonic, sleep=fake_sleep),
    )
    return {
        "name": name,
        "network": network,
        "alias": alias,
        "owner": owner,
        "database": database,
        "labels": labels,
        "phase": phase,
        "clock": clock,
        "readiness_calls": readiness_calls,
        "readiness_codes": readiness_codes,
        "run_calls": run_calls,
        "data": tmp_path / "postgres-data",
        "password_file": tmp_path / "postgres-password",
    }


@pytest.mark.offline_database
@pytest.mark.parametrize("existing", [False, True], ids=["new-container", "existing-container"])
def test_managed_postgres_waits_for_final_tcp_readiness(tmp_path, monkeypatch, existing):
    module = load("docker_install")
    state = _fake_managed_postgres_readiness(module, monkeypatch, tmp_path, existing=existing)

    module._ensure_postgres_container(
        state["name"],
        state["network"],
        state["alias"],
        state["data"],
        state["password_file"],
        state["owner"],
        state["database"],
        state["labels"],
    )

    expected_probe = [
        "exec",
        state["name"],
        "pg_isready",
        "--host",
        "127.0.0.1",
        "--username",
        state["owner"],
        "--dbname",
        state["database"],
    ]
    assert state["phase"]["name"] == "final_tcp_ready"
    assert state["readiness_calls"] == [expected_probe, expected_probe]
    assert state["readiness_codes"] == [1, 0]
    assert state["clock"]["sleeps"] == [1]
    assert len(state["run_calls"]) == (0 if existing else 1)


@pytest.mark.offline_database
@pytest.mark.parametrize("existing", [False, True], ids=["new-container", "existing-container"])
def test_managed_postgres_readiness_timeout_preserves_120_second_bound(
    tmp_path, monkeypatch, existing
):
    module = load("docker_install")
    state = _fake_managed_postgres_readiness(
        module, monkeypatch, tmp_path, existing=existing, never_ready=True
    )

    with pytest.raises(RuntimeError, match="managed PostgreSQL did not become ready"):
        module._ensure_postgres_container(
            state["name"],
            state["network"],
            state["alias"],
            state["data"],
            state["password_file"],
            state["owner"],
            state["database"],
            state["labels"],
        )

    expected_probe = [
        "exec",
        state["name"],
        "pg_isready",
        "--host",
        "127.0.0.1",
        "--username",
        state["owner"],
        "--dbname",
        state["database"],
    ]
    assert state["clock"]["seconds"] == 120
    assert state["clock"]["sleeps"] == [1] * 120
    assert state["readiness_calls"] == [expected_probe] * 120
    assert state["readiness_codes"] == [1] * 120


@pytest.mark.offline_database
@pytest.mark.parametrize("database_created", [False, True])
def test_database_admin_uses_profile_network(tmp_path, monkeypatch, database_created):
    from types import SimpleNamespace

    module = load("docker_install")
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    calls = []

    monkeypatch.setattr(
        module,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="linux/amd64\n", returncode=0),
    )
    monkeypatch.setattr(module, "image_id", lambda _image: "sha256:" + "a" * 64)
    monkeypatch.setattr(module, "config_for", lambda _root: {"server_port": 19891})
    monkeypatch.setattr(
        module,
        "ensure_database",
        lambda *_args, **_kwargs: ("cao-profile-private", database_created, None, None),
    )

    class AdminCalled(Exception):
        pass

    def capture_admin(image, path, arguments, **kwargs):
        calls.append((image, path, arguments, kwargs))
        if arguments == ["configure-personal-scheduler"]:
            raise AdminCalled

    monkeypatch.setattr(module, "admin", capture_admin)
    with pytest.raises(AdminCalled):
        module.install(
            SimpleNamespace(
                root=root,
                image="cao-image:test",
                no_build=True,
                port=19891,
                workspace=[],
                workspace_readonly=[],
                database_config=None,
            )
        )

    assert len(calls) == 2
    assert calls[0][2] == ["bootstrap-database" if database_created else "verify-database"]
    assert calls[1][2] == ["configure-personal-scheduler"]
    assert all(call[3].get("network") == "cao-profile-private" for call in calls)


@pytest.mark.offline_database
def test_isolated_cleanup_uses_private_peer_host_identity(tmp_path, monkeypatch):
    import hashlib
    import os
    import socket
    import uuid
    from types import SimpleNamespace

    from cli_agent_orchestrator.services.local_peers.host_identity import current_host_identity

    module = load("docker_install")
    private_home = tmp_path / "operator-home"
    private_home.mkdir(mode=0o700)
    os.chmod(private_home, 0o700)
    monkeypatch.setenv("HOME", str(private_home))
    host_identity = current_host_identity(private_home / ".aws/cli-agent-orchestrator/local-peers")

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    instance_id = uuid.uuid4().hex
    (root / "deployment.json").write_text(
        json.dumps({"host": "127.0.0.1", "instance_id": instance_id})
    )
    peer_root = root.with_name(root.name + "-isolated-peer")
    peer_root.mkdir(mode=0o700)
    store_id = str(uuid.uuid4())
    peer_name = f"cao-peer-pg-{host_identity.host_id}-{instance_id[:8]}"
    (peer_root / "service.json").write_text(
        json.dumps(
            {
                "container": peer_name,
                "owner_id": host_identity.host_id,
                "store_id": store_id,
                "isolated": True,
            }
        )
    )
    (peer_root / "service.json").chmod(0o600)

    peer_labels = {
        module.DATABASE_SERVICE_LABEL: "peer",
        module.DATABASE_OWNER_LABEL: host_identity.host_id,
        module.DATABASE_INSTANCE_LABEL: store_id,
    }
    network_name = "cao-profile-" + instance_id[:12] + "-preflight"
    network_labels = {
        module.DATABASE_SERVICE_LABEL: "preflight-network",
        module.DATABASE_INSTANCE_LABEL: instance_id,
        module.DATABASE_OWNER_LABEL: hashlib.sha256(
            f"{socket.gethostname()}:{os.getuid()}".encode()
        ).hexdigest()[:16],
    }
    docker_calls = []

    def inspect(name):
        if name == peer_name:
            return {"Config": {"Labels": peer_labels}}
        return None

    def inspect_network(name):
        if name == network_name:
            return {"Labels": network_labels}
        return None

    monkeypatch.setattr(module, "inspect", inspect)
    monkeypatch.setattr(module, "inspect_network", inspect_network)
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: docker_calls.append(list(args)) or SimpleNamespace(returncode=0),
    )

    module._cleanup_isolated_database(root)

    assert docker_calls == [
        ["rm", "--force", peer_name],
        ["network", "rm", network_name],
    ]
    assert not peer_root.exists()


@pytest.mark.offline_database
@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_archive_admin_mounts_validated_docker_socket(tmp_path, monkeypatch, operation):
    import socket
    from types import SimpleNamespace

    module = load("docker_install")
    docker_socket = tmp_path / "docker.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(docker_socket))
    monkeypatch.setattr(module, "SOCKET", docker_socket)
    root = tmp_path / "deployment"
    root.mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )

    try:
        module.admin("sha256:" + "a" * 64, root, [operation, str(tmp_path / "archive")])
        command = calls[0]
        assert command[command.index("--group-add") + 1] == str(docker_socket.stat().st_gid)
        mount = command[command.index("--mount") + 1]
        assert mount == module.mount_argument(
            docker_socket, Path("/var/run/docker.sock"), readonly=True
        )
    finally:
        server.close()


@pytest.mark.offline_database
def test_restore_admin_uses_private_tmpfs_home_for_cold_browser_imports(tmp_path, monkeypatch):
    import os
    import shutil
    import socket
    import subprocess
    import sys
    import tempfile
    from types import SimpleNamespace

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    module = load("docker_install")
    socket_directory = Path(tempfile.mkdtemp(prefix="cao-restore-"))
    os.chmod(socket_directory, 0o700)
    docker_socket = socket_directory / "d.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(docker_socket))
    monkeypatch.setattr(module, "SOCKET", docker_socket)
    calls = []
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )

    destination = tmp_path / "unpublished-profile"
    assert not destination.exists()
    source = tmp_path / "browser-source"
    source.mkdir(mode=0o700)
    (source / "cao").mkdir(mode=0o700)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (source / "issuer.pem").write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (source / "issuer.pem").chmod(0o600)

    database_config = tmp_path / "restore-target-config.json"
    backup = tmp_path / "backup"
    backup.mkdir(mode=0o700)
    backup_mount = Path("/run/cao-backup")
    image = "sha256:" + "a" * 64
    network = "cao-profile-0123456789ab"
    module.admin(
        image,
        destination,
        ["restore", str(backup_mount), "--database-config", str(database_config)],
        network=network,
        database_config=database_config,
        readonly_mounts=iter(((backup, backup_mount),)),
    )
    restore_command = calls.pop()

    def env_value(command, name):
        for index, argument in enumerate(command[:-1]):
            if argument == "--env" and command[index + 1].startswith(name + "="):
                return command[index + 1].split("=", 1)[1]
        raise AssertionError(f"missing {name} Docker environment")

    home = env_value(restore_command, "HOME")
    cao_home = env_value(restore_command, "CAO_HOME_DIR")
    assert home == str(module._RESTORE_ADMIN_HOME)
    assert home != str(destination)
    assert cao_home != str(destination / "cao")
    assert cao_home == str(Path(home) / "cao")
    assert env_value(restore_command, "CAO_DATABASE_CONFIG_FILE") == str(database_config)
    assert restore_command[restore_command.index("--network") + 1] == network
    assert module.mount_argument(destination.parent) in restore_command
    assert module.mount_argument(backup, backup_mount, readonly=True) in restore_command
    assert restore_command[restore_command.index("--user") + 1] == (f"{os.getuid()}:{os.getgid()}")
    assert restore_command[restore_command.index("--root") + 1] == str(destination)
    assert "--database-config" in restore_command
    assert str(database_config) in restore_command

    tmpfs = restore_command[restore_command.index("--tmpfs") + 1]
    tmpfs_path, *options = tmpfs.split(":")
    option_values = set(options[0].split(","))
    assert tmpfs_path == home
    assert {
        "rw",
        "noexec",
        "nosuid",
        "nodev",
        "size=16m",
        f"uid={os.getuid()}",
        f"gid={os.getgid()}",
        "mode=0700",
    } <= option_values

    legacy_destination = tmp_path / "legacy-unpublished-profile"
    legacy_environment = os.environ.copy()
    legacy_environment["HOME"] = str(legacy_destination)
    legacy_environment["CAO_HOME_DIR"] = str(legacy_destination / "cao")
    legacy_environment["CAO_DATABASE_CONFIG_FILE"] = str(database_config)
    src = Path(__file__).resolve().parents[2] / "src"
    legacy_environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(src), legacy_environment.get("PYTHONPATH")))
    )
    browser_probe = """
import importlib.util
import json
import sys
from pathlib import Path

script, source_text = sys.argv[1:]
spec = importlib.util.spec_from_file_location("personal_deployment_probe", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source = Path(source_text)
config = {
    "host": "127.0.0.1",
    "server_port": 19889,
    "jwks_port": 19890,
    "image_id": "sha256:" + "a" * 64,
    "issuer": "https://issuer.example.test/install",
    "subject": "restore-test-operator",
    "audience": "cao",
    "scopes": ["cao:read", "cao:write", "cao:admin"],
    "browser_login": {
        "enabled": True,
        "canonical_origin": "http://127.0.0.1:19889",
        "transport_policy": "loopback_http",
        "installation_id": "b" * 32,
    },
}
config["browser_login"]["binding"] = module._local_binding(source, config)
module._write(source / "deployment.json", json.dumps(config).encode())
module._config(source)
print("browser-config-verified")
"""

    def run_browser_probe(environment):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                browser_probe,
                str(Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"),
                str(source),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    try:
        legacy_result = run_browser_probe(legacy_environment)
        assert legacy_result.returncode == 0, legacy_result.stderr
        assert legacy_result.stdout == "browser-config-verified\n"
        assert (legacy_destination / "cao" / "fifos").is_dir()
        shutil.rmtree(legacy_destination)

        container_filesystem = tmp_path / "container-filesystem"
        container_filesystem.mkdir(mode=0o700)
        container_home = Path(home)
        container_cao_home = Path(cao_home)
        assert container_cao_home.is_relative_to(container_home)
        scratch_home = container_filesystem / container_home.relative_to("/")
        scratch_home.mkdir(mode=0o700, parents=True)
        isolated_cao_home = scratch_home / container_cao_home.relative_to(container_home)
        isolated_environment = os.environ.copy()
        isolated_environment["HOME"] = str(scratch_home)
        isolated_environment["CAO_HOME_DIR"] = str(isolated_cao_home)
        isolated_environment["CAO_DATABASE_CONFIG_FILE"] = str(database_config)
        isolated_environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(src), isolated_environment.get("PYTHONPATH")))
        )
        isolated_result = run_browser_probe(isolated_environment)
        assert isolated_result.returncode == 0, isolated_result.stderr
        assert isolated_result.stdout == "browser-config-verified\n"
        assert not destination.exists()
        assert isolated_cao_home.stat().st_mode & 0o077 == 0

        module.admin(image, source, ["backup", str(backup)])
        backup_command = calls[-1]
        assert "--tmpfs" not in backup_command
        assert env_value(backup_command, "HOME") == str(source)
        assert env_value(backup_command, "CAO_HOME_DIR") == str(source / "cao")
        assert env_value(backup_command, "CAO_DATABASE_CONFIG_FILE") == str(
            source / "cao" / "database-config.json"
        )
    finally:
        server.close()
        docker_socket.unlink(missing_ok=True)
        socket_directory.rmdir()


@pytest.mark.offline_database
@pytest.mark.parametrize(
    "overlap",
    [
        "destination-inside",
        "destination-relative-inside",
        "destination-symlink-alias",
        "source-inside",
        "source-relative-inside",
        "config-inside",
        "config-relative-inside",
        "readonly-target-inside",
        "source-contains",
        "readonly-target-contains",
    ],
)
def test_restore_admin_rejects_paths_overlapping_private_tmpfs(tmp_path, monkeypatch, overlap):
    import os
    import socket
    import tempfile
    from types import SimpleNamespace

    module = load("docker_install")
    socket_directory = Path(tempfile.mkdtemp(prefix="cao-overlap-"))
    os.chmod(socket_directory, 0o700)
    docker_socket = socket_directory / "d.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(docker_socket))
    monkeypatch.setattr(module, "SOCKET", docker_socket)
    calls = []
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )

    scratch = module._RESTORE_ADMIN_HOME
    destination = tmp_path / "unpublished"
    source = tmp_path / "backup"
    config = tmp_path / "database-config.json"
    readonly_source = tmp_path / "readonly"
    readonly_source.mkdir(mode=0o700)
    readonly_target = Path("/run/cao-backup")

    if overlap == "destination-inside":
        destination = scratch / "unpublished"
    elif overlap == "destination-relative-inside":
        destination = Path("../../run/cao-admin-home/unpublished")
    elif overlap == "destination-symlink-alias":
        alias = tmp_path / "restore-home-alias"
        alias.symlink_to(scratch, target_is_directory=True)
        destination = alias / "unpublished"
    elif overlap == "source-inside":
        source = scratch / "backup"
    elif overlap == "source-relative-inside":
        source = Path("../../run/cao-admin-home/backup")
    elif overlap == "config-inside":
        config = scratch / "database-config.json"
    elif overlap == "config-relative-inside":
        config = Path("../../run/cao-admin-home/database-config.json")
    elif overlap == "readonly-target-inside":
        readonly_target = scratch / "backup"
    elif overlap == "source-contains":
        source = Path("/run")
    elif overlap == "readonly-target-contains":
        readonly_target = Path("/run")

    try:
        with pytest.raises(ValueError, match="overlap.*private restore home"):
            module.admin(
                "sha256:" + "a" * 64,
                destination,
                ["restore", str(source), "--database-config", str(config)],
                database_config=config,
                readonly_mounts=iter(((readonly_source, readonly_target),)),
            )
        assert calls == []
    finally:
        server.close()
        docker_socket.unlink(missing_ok=True)
        socket_directory.rmdir()


@pytest.mark.offline_database
def test_restore_admin_allows_root_parent_that_contains_private_tmpfs(tmp_path, monkeypatch):
    import os
    import socket
    import tempfile
    from types import SimpleNamespace

    module = load("docker_install")
    socket_directory = Path(tempfile.mkdtemp(prefix="cao-parent-"))
    os.chmod(socket_directory, 0o700)
    docker_socket = socket_directory / "d.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(docker_socket))
    monkeypatch.setattr(module, "SOCKET", docker_socket)
    calls = []
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )
    destination = Path("/restore-profile")
    config = tmp_path / "database-config.json"
    source = tmp_path / "backup"
    source.mkdir(mode=0o700)
    readonly_source = tmp_path / "readonly"
    readonly_source.mkdir(mode=0o700)
    try:
        module.admin(
            "sha256:" + "a" * 64,
            destination,
            ["restore", str(source), "--database-config", str(config)],
            database_config=config,
            readonly_mounts=((readonly_source, Path("/run/cao-backup")),),
        )
        assert len(calls) == 1
        assert module.mount_argument(Path("/")) in calls[0]
        assert calls[0][calls[0].index("--root") + 1] == str(destination)
    finally:
        server.close()
        docker_socket.unlink(missing_ok=True)
        socket_directory.rmdir()


@pytest.mark.offline_database
def test_non_archive_admin_does_not_receive_docker_socket(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = load("docker_install")
    module.SOCKET = tmp_path / "missing-docker.sock"
    root = tmp_path / "deployment"
    root.mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )

    module.admin("sha256:" + "a" * 64, root, ["bootstrap-database"])

    assert "--group-add" not in calls[0]
    assert module.mount_argument(module.SOCKET, Path("/var/run/docker.sock")) not in calls[0]


@pytest.mark.offline_database
def test_archive_admin_rejects_non_socket_docker_path(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = load("docker_install")
    bad_socket = tmp_path / "docker.sock"
    bad_socket.write_text("not a socket")
    module.SOCKET = bad_socket
    calls = []
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )
    root = tmp_path / "deployment"
    root.mkdir(mode=0o700)

    with pytest.raises(RuntimeError, match="Docker socket path"):
        module.admin("sha256:" + "a" * 64, root, ["backup", str(tmp_path / "archive")])

    assert calls == []


@pytest.mark.offline_database
def test_archive_admin_rejects_symlinked_docker_socket(tmp_path, monkeypatch):
    import socket
    from types import SimpleNamespace

    module = load("docker_install")
    real_socket = tmp_path / "real.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(real_socket))
    linked_socket = tmp_path / "docker.sock"
    linked_socket.symlink_to(real_socket)
    module.SOCKET = linked_socket
    calls = []
    monkeypatch.setattr(
        module,
        "docker",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )
    root = tmp_path / "deployment"
    root.mkdir(mode=0o700)

    try:
        with pytest.raises(RuntimeError, match="Docker socket path"):
            module.admin("sha256:" + "a" * 64, root, ["backup", str(tmp_path / "archive")])
    finally:
        server.close()

    assert calls == []


@pytest.mark.offline_database
def test_postgres_bootstrap_password_file_is_idempotent(tmp_path):
    module = load("docker_install")
    password_file = tmp_path / "postgres-password"
    password = "synthetic-managed-bootstrap-password"

    module._password_file(password_file, password)
    expected = (password + "\n").encode()
    assert password_file.read_bytes() == expected
    module._password_file(password_file, password)
    assert password_file.read_bytes() == expected

    with pytest.raises(ValueError, match="bootstrap secret differs"):
        module._password_file(password_file, password + "different")

    password_file.write_bytes(expected + b"\n")
    with pytest.raises(ValueError, match="bootstrap secret differs"):
        module._password_file(password_file, password)


def test_bad_mount_cannot_inject_docker_options(tmp_path):
    module = load("docker_install")
    with pytest.raises(ValueError, match="mount"):
        module.mount_argument(tmp_path / "bad,readonly=false")


def test_failed_cutover_stops_candidate_and_restores_previous():
    module = load("docker_install")
    events = []

    def action(name):
        return lambda: events.append(name)

    def fail():
        events.append("verify")
        raise RuntimeError("frontend unavailable")

    with pytest.raises(RuntimeError, match="frontend unavailable"):
        module.cutover(
            action("stop previous"),
            action("restore previous"),
            action("start candidate"),
            action("stop candidate"),
            fail,
            action("commit"),
        )
    assert events == [
        "stop previous",
        "start candidate",
        "verify",
        "stop candidate",
        "restore previous",
    ]


def test_failed_candidate_start_also_restores_previous():
    module = load("docker_install")
    events = []

    def fail():
        raise RuntimeError("Docker refused startup")

    with pytest.raises(RuntimeError):
        module.cutover(
            lambda: events.append("stop"),
            lambda: events.append("restore"),
            fail,
            lambda: events.append("clean candidate"),
            lambda: events.append("verify"),
            lambda: events.append("commit"),
        )
    assert events == ["stop", "clean candidate", "restore"]


def test_failed_previous_stop_never_removes_previous_container():
    module = load("docker_install")
    events = []

    def fail():
        events.append("stop previous")
        raise RuntimeError("rename refused")

    with pytest.raises(RuntimeError):
        module.cutover(
            fail,
            lambda: events.append("restore previous"),
            lambda: events.append("start candidate"),
            lambda: events.append("remove candidate"),
            lambda: events.append("verify"),
            lambda: events.append("commit"),
        )
    assert events == ["stop previous", "restore previous"]


def test_private_manifest_is_atomic_and_contains_no_authority(tmp_path):
    module = load("docker_install")
    path = tmp_path / "installation.json"
    module.write_manifest(path, {"container": "cao-personal", "image": "sha256:" + "a" * 64})
    assert path.stat().st_mode & 0o077 == 0
    assert json.loads(path.read_text())["container"] == "cao-personal"
    assert len(list(tmp_path.iterdir())) == 1


def rollback_fixture(tmp_path, monkeypatch, *, previous=None):
    from types import SimpleNamespace

    module = load("docker_install")
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    manifest = {
        "container": "cao-personal",
        "image": "new",
        "mounts": [],
        "container_instance": "current-instance",
        "previous_container": previous,
        "previous_host_active": previous is None,
        "previous_host_enabled": previous is None,
        "previous_manifest": {
            "container": "cao-personal",
            "image": "old",
            "mounts": [],
            "container_instance": "previous-instance",
        },
    }
    module.write_manifest(root / "docker-installation.json", manifest)
    monkeypatch.setattr(module, "config_for", lambda root: {"server_port": 9889})
    monkeypatch.setattr(module, "wait_healthy", lambda *a, **kw: None)
    current_id = "a" * 64
    previous_id = "b" * 64
    states = {
        current_id: {
            "Id": current_id,
            "Config": {
                "Labels": {
                    module.LABEL: str(root),
                    module.INSTANCE_LABEL: "current-instance",
                }
            },
        }
    }
    if previous:
        states[previous_id] = {
            "Id": previous_id,
            "Config": {
                "Labels": {
                    module.LABEL: str(root),
                    module.INSTANCE_LABEL: "previous-instance",
                }
            },
        }
    names = {"cao-personal": current_id}
    if previous:
        names[previous] = previous_id

    def inspect(name):
        return states.get(names.get(name))

    def docker(arguments, **_kwargs):
        if arguments[0] == "rename":
            container_id, destination = arguments[1:]
            for existing_name, existing_id in tuple(names.items()):
                if existing_id == container_id:
                    del names[existing_name]
            names[destination] = container_id
        return None

    monkeypatch.setattr(module, "inspect", inspect)
    monkeypatch.setattr(module, "docker", docker)
    return module, SimpleNamespace(root=root, command="rollback")


def test_rollback_rename_failure_restarts_current(tmp_path, monkeypatch):
    module, args = rollback_fixture(tmp_path, monkeypatch)
    calls = []

    def docker(arguments, **kw):
        calls.append(arguments)
        if arguments[0] == "rename":
            raise RuntimeError("rename refused")

    monkeypatch.setattr(module, "docker", docker)
    with pytest.raises(RuntimeError, match="rename refused"):
        module.manage(args)
    assert calls[-1] == ["start", "a" * 64]


def test_rollback_failed_host_start_stops_host_before_container(tmp_path, monkeypatch):
    module, args = rollback_fixture(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(module, "docker", lambda a, **kw: calls.append(a))

    def systemctl(command, **kw):
        calls.append(["host", command])
        if command == "start":
            raise RuntimeError("host health failed")
        return True

    monkeypatch.setattr(module, "systemctl", systemctl)
    with pytest.raises(RuntimeError, match="host health failed"):
        module.manage(args)
    assert ["host", "stop"] in calls
    assert calls.index(["host", "stop"]) < calls.index(["start", "a" * 64])
    assert ["host", "disable"] in calls


def test_successful_host_rollback_records_host_lifecycle(tmp_path, monkeypatch):
    module, args = rollback_fixture(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(module, "systemctl", lambda command, **kw: calls.append(command) or True)
    module.manage(args)
    manifest = json.loads((args.root / "docker-installation.json").read_text())
    assert manifest["mode"] == "host"
    args.command = "stop"
    module.manage(args)
    assert calls[-1] == "stop"


@pytest.mark.parametrize("unowned", ["current", "previous"])
def test_rollback_refuses_reused_unowned_container_names_before_mutation(
    tmp_path, monkeypatch, unowned
):
    module, args = rollback_fixture(tmp_path, monkeypatch, previous="cao-personal-previous")
    calls = []
    original_inspect = module.inspect

    def inspect(name):
        state = original_inspect(name)
        if (unowned == "current" and name == "cao-personal") or (
            unowned == "previous" and name == "cao-personal-previous"
        ):
            state["Config"]["Labels"][module.LABEL] = "/operator/other-deployment"
        return state

    monkeypatch.setattr(module, "inspect", inspect)
    monkeypatch.setattr(module, "docker", lambda arguments, **kw: calls.append(arguments))

    with pytest.raises(RuntimeError, match="another deployment"):
        module.manage(args)

    assert calls == []


def test_rollback_mutates_inspected_ids_when_a_name_is_reused_after_preflight(
    tmp_path, monkeypatch
):
    module, args = rollback_fixture(tmp_path, monkeypatch, previous="cao-personal-previous")
    current_id = "a" * 64
    previous_id = "b" * 64
    unrelated_id = "c" * 64
    names = {"cao-personal": current_id, "cao-personal-previous": previous_id}
    states = {
        current_id: module.inspect("cao-personal"),
        previous_id: module.inspect("cao-personal-previous"),
        unrelated_id: {
            "Id": unrelated_id,
            "Config": {"Labels": {module.LABEL: "/operator/other-deployment"}},
        },
    }
    calls = []
    raced = False

    def inspect(name):
        container_id = names.get(name)
        return states.get(container_id)

    def docker(arguments, **_kwargs):
        nonlocal raced
        calls.append(arguments)
        operation, container_id, *tail = arguments
        if operation == "stop" and not raced:
            # Simulate the inspected previous name being reassigned before mutation.
            names["cao-personal-previous"] = unrelated_id
            raced = True
        if operation == "rename":
            destination = tail[0]
            for existing_name, existing_id in tuple(names.items()):
                if existing_id == container_id:
                    del names[existing_name]
            names[destination] = container_id
        return None

    monkeypatch.setattr(module, "inspect", inspect)
    monkeypatch.setattr(module, "docker", docker)

    module.manage(args)

    assert calls[0] == ["stop", current_id]
    assert calls[1][:2] == ["rename", current_id]
    assert calls[1][2].startswith("cao-personal-rolled-back-")
    assert calls[2:] == [
        ["rename", previous_id, "cao-personal"],
        ["start", previous_id],
    ]
    assert names["cao-personal-previous"] == unrelated_id
    assert names["cao-personal"] == previous_id


def test_build_context_excludes_native_binary_and_credentials(tmp_path, monkeypatch):
    import tarfile

    module = load("docker_install")
    repo = tmp_path / "repo"
    for name in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "NOTICE",
        "test/fixtures/work_contract_v1.json",
        "scripts/hatch_build_tui_tag.py",
        "scripts/personal_deployment.py",
        "scripts/local_work_docker_demo.py",
        "scripts/docker_personal_runtime.py",
        "scripts/docker_windows_mcp.py",
        "src/cli_agent_orchestrator/cao-tui",
        "src/cli_agent_orchestrator/code.py",
        "web/.env",
        "web/node_modules/auth.json",
    ):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("canary")
    monkeypatch.setattr(module, "REPOSITORY", repo)
    archive = tmp_path / "context.tar.gz"
    module.build_archive(archive)
    with tarfile.open(archive) as reader:
        names = reader.getnames()
    assert "src/cli_agent_orchestrator/code.py" in names
    assert "src/cli_agent_orchestrator/cao-tui" not in names
    assert "web/.env" not in names
    assert "web/node_modules/auth.json" not in names


def test_windows_bridge_rejects_unregistered_command_and_argument_changes(tmp_path):
    module = load("docker_windows_mcp")
    allowed = [["/c", "exact registered command"]]
    assert module.authorized(["/c", "exact registered command"], allowed)
    assert not module.authorized(["/c", "arbitrary command"], allowed)
    assert not module.authorized(["/c", "exact registered command", "extra"], allowed)


def test_windows_bridge_forwards_stdout_stderr_and_exit(tmp_path):
    import socket
    import struct
    import subprocess
    import sys
    import threading

    module = load("docker_windows_mcp")
    server, client = socket.socketpair()
    thread = threading.Thread(
        target=module.handle,
        args=(
            server,
            {
                "executable": sys.executable,
                "arguments": [
                    ["-c", "import sys;print(sys.stdin.read());print('diagnostic',file=sys.stderr)"]
                ],
            },
        ),
    )
    thread.start()
    request = json.dumps(
        ["-c", "import sys;print(sys.stdin.read());print('diagnostic',file=sys.stderr)"]
    ).encode()
    client.sendall(struct.pack("!I", len(request)) + request + b"MCP input")
    client.shutdown(socket.SHUT_WR)
    frames = {}
    while True:
        kind, size = struct.unpack("!BI", module.receive(client, 5))
        frames[kind] = frames.get(kind, b"") + module.receive(client, size)
        if kind == 3:
            break
    thread.join(timeout=5)
    client.close()
    assert frames[1] == b"MCP input\n"
    assert frames[2] == b"diagnostic\n"
    assert frames[3] == b"0"


@pytest.mark.parametrize("kind", ["fastcgi", "uwsgi", "scgi"])
def test_nginx_all_temporary_directories_are_writable(kind):
    config = load("docker_personal_runtime").proxy_config(9889)
    assert f"{kind}_temp_path /tmp/cao-nginx/{kind};" in config


def test_health_requests_include_browser_origin(monkeypatch):
    module = load("docker_install")
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"healthy"

    class Opener:
        def open(self, request, **kw):
            requests.append(request)
            return Response()

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    module.request_status(9889, "/sessions")
    assert requests[0].get_header("Origin") == "http://127.0.0.1:9889"


def test_windows_mcp_preflight_uses_initialize_response_not_shutdown_exit(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    module = load("docker_personal_runtime")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text(
        '[mcp_servers.studio]\ncommand="cmd.exe"\nargs=["/c","registered"]\n'
    )
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)

    def run(arguments, **kw):
        assert not kw.get("text")
        if kw.get("check"):
            raise subprocess.CalledProcessError(1, arguments)
        return SimpleNamespace(
            stdout=b'{"jsonrpc":"2.0","id":1,"result":{}}\n',
            stderr=b"Windows OEM diagnostic \xa2",
            returncode=1,
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    module.check_mcp_integrations()


def test_playwright_mounts_browser_but_uses_image_libraries(tmp_path, monkeypatch):
    module = load("docker_install")
    for name in (".cache/ms-playwright", ".local/share/playwright-mcp/lib"):
        (tmp_path / name).mkdir(parents=True)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(module, "windows_mcp_mount", lambda: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    mounts = module.runtime_mounts([])
    assert any(m["source"].endswith("/.cache/ms-playwright") and m["readonly"] for m in mounts)
    assert not any(m["source"].endswith("/playwright-mcp/lib") for m in mounts)


def test_windows_bridge_reaps_child_ignoring_stdin_eof(monkeypatch):
    import socket
    import struct
    import sys
    import threading

    module = load("docker_windows_mcp")
    monkeypatch.setattr(module, "SHUTDOWN_GRACE", 0.05, raising=False)
    server, client = socket.socketpair()
    arguments = ["-c", "import time;time.sleep(1.5)"]
    thread = threading.Thread(
        target=module.handle,
        args=(server, {"executable": sys.executable, "arguments": [arguments]}),
        daemon=True,
    )
    thread.start()
    request = json.dumps(arguments).encode()
    client.sendall(struct.pack("!I", len(request)) + request)
    client.shutdown(socket.SHUT_WR)
    try:
        thread.join(timeout=0.5)
        assert not thread.is_alive(), "EOF-ignoring child was not reaped"
    finally:
        client.close()
        thread.join(timeout=2)


def test_host_rollback_account_reset_uses_host_runtime(tmp_path, monkeypatch):
    module, args = rollback_fixture(tmp_path, monkeypatch)
    module.write_manifest(
        args.root / "docker-installation.json", {"mode": "host", "container": "cao-personal"}
    )
    args.command = "account-reset"
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda a, **kw: calls.append(a))
    module.manage(args)
    assert calls[0][0].endswith("/bin/python")
    assert "exec" not in calls[0]


def test_admin_sets_durable_home_before_binding_validation(tmp_path, monkeypatch):
    module = load("docker_install")
    calls = []
    monkeypatch.setattr(module, "docker", lambda a, **kw: calls.append(a))
    root = tmp_path / "state"
    module.admin("image", root, ["backup", str(tmp_path / "backup")])
    assert f'CAO_HOME_DIR={root / "cao"}' in calls[0]
    assert f"HOME={root}" in calls[0]


def test_windows_bridge_reaps_descendant_after_parent_exits(monkeypatch):
    import socket
    import struct
    import sys
    import threading

    module = load("docker_windows_mcp")
    monkeypatch.setattr(module, "SHUTDOWN_GRACE", 0.05)
    server, client = socket.socketpair()
    arguments = [
        "-c",
        'import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(1.5)"])',
    ]
    thread = threading.Thread(
        target=module.handle,
        args=(server, {"executable": sys.executable, "arguments": [arguments]}),
        daemon=True,
    )
    thread.start()
    request = json.dumps(arguments).encode()
    client.sendall(struct.pack("!I", len(request)) + request)
    client.shutdown(socket.SHUT_WR)
    try:
        thread.join(timeout=0.5)
        assert not thread.is_alive(), "orphan descendant retained the output pipes"
    finally:
        client.close()
        thread.join(timeout=2)


def test_install_rejects_retired_sqlite_options_before_filesystem_or_install_lock(
    tmp_path, monkeypatch
):
    import sys

    module = load("docker_install")
    root = tmp_path / "missing-parent" / "profile"
    monkeypatch.setattr(sys, "argv", ["docker_install.py"])

    for option in (
        "--import-sqlite",
        "--source-stopped",
        "--main-source=/missing/main.db",
        "--auth-source",
        "--peer-source=/missing/peer.db",
    ):
        with pytest.raises(SystemExit):
            module.main(["--root", str(root), option])
        assert not root.parent.exists()
        assert not root.with_name(root.name + "-installation.lock").exists()


def test_database_initialization_rejects_unknown_existing_state_before_network(
    tmp_path, monkeypatch
):
    installer = load("docker_install")
    deployment = load("personal_deployment")
    root = tmp_path / "profile"
    deployment.initialize(root, image_id="sha256:" + "a" * 64)
    marker = root / "cao" / "unrecognized-state"
    marker.write_bytes(b"preserve me")
    marker.chmod(0o600)
    monkeypatch.setattr(
        installer,
        "_ensure_network",
        lambda *_args, **_kwargs: pytest.fail("network side effect before unknown-state rejection"),
    )

    with pytest.raises(RuntimeError, match="unrecognized existing database state"):
        installer.ensure_database(root)

    assert marker.read_bytes() == b"preserve me"


def test_personal_cli_rejects_retired_sqlite_command_before_filesystem_or_lock(tmp_path):
    module = load("personal_deployment")
    root = tmp_path / "missing-parent" / "profile"

    with pytest.raises(SystemExit):
        module.main(["--root", str(root), "import-sqlite", "--source-stopped"])

    assert not root.parent.exists()
    assert not (root / "service.lock").exists()


@pytest.mark.offline_database
@pytest.mark.parametrize(
    "worker_image_reference",
    [None, "sha256:" + "b" * 64],
)
def test_personal_init_persists_validated_worker_image_reference(
    tmp_path, monkeypatch, worker_image_reference
):
    import sys

    module = load("personal_deployment")
    root = tmp_path / "profile"
    arguments = [
        "personal_deployment.py",
        "--root",
        str(root),
        "init",
        "--image-id",
        "sha256:" + "a" * 64,
    ]
    if worker_image_reference is not None:
        arguments += ["--worker-image-reference", worker_image_reference]
    monkeypatch.setattr(sys, "argv", arguments)

    module.main()

    config = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    assert config.get("worker_image_reference") == worker_image_reference
    assert module._config(root).get("worker_image_reference") == worker_image_reference


@pytest.mark.offline_database
@pytest.mark.parametrize(
    "reference",
    [
        "../worker:tag",
        "not-private:tag",
        "cao-personal-worker-import:import-0123456789abcdef0123",
        "x" * 300,
    ],
)
def test_personal_init_rejects_invalid_worker_image_reference(tmp_path, monkeypatch, reference):
    import sys

    module = load("personal_deployment")
    root = tmp_path / "profile"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "personal_deployment.py",
            "--root",
            str(root),
            "init",
            "--image-id",
            "sha256:" + "a" * 64,
            "--worker-image-reference",
            reference,
        ],
    )
    with pytest.raises(ValueError, match="worker image reference"):
        module.main()
    assert not root.exists()


@pytest.mark.offline_database
def test_personal_recovery_archive_dump_omits_comments(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = load("personal_deployment")
    calls = []

    def run(_root, _target, executable, arguments, *, output=None, input_path=None):
        calls.append((executable, arguments))
        assert executable == "pg_dump"
        assert output is not None
        output.write(b"test-archive")

    monkeypatch.setattr(module, "_run_database_client", run)
    destination = tmp_path / "main.dump"
    module.PersonalDeploymentArchiveRunner(tmp_path).dump(
        SimpleNamespace(scope="main", schema="private_main"),
        destination,
        snapshot_id="test-snapshot-id",
    )
    assert "--no-comments" in calls[0][1]
    assert destination.read_bytes() == b"test-archive"
    assert destination.stat().st_mode & 0o077 == 0


@pytest.mark.offline_database
def test_personal_backup_omits_comments_from_every_scope_dump(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = load("personal_deployment")
    root = tmp_path / "profile"
    module.initialize(root, image_id="sha256:" + "a" * 64)
    targets = {
        scope: SimpleNamespace(
            host="127.0.0.1",
            port=5432,
            database=f"cao_{scope}",
            schema=f"cao_{scope}",
            store_id=f"store-{scope}",
        )
        for scope in ("main", "auth", "peer")
    }
    calls = []

    monkeypatch.setattr(module, "verify_database", lambda _root: None)
    monkeypatch.setattr(
        module,
        "_database_groups",
        lambda _root: (targets, tuple((scope,) for scope in targets)),
    )
    monkeypatch.setattr(module, "_component_manifest", lambda _scope: [])

    def run(_root, _target, executable, arguments, *, output=None, input_path=None):
        calls.append((executable, arguments))
        assert executable == "pg_dump"
        assert output is not None
        output.write(b"test-archive")

    monkeypatch.setattr(module, "_run_database_client", run)

    backup = tmp_path / "backup"
    module.backup(root, backup)

    assert len(calls) == 3
    assert all("--no-comments" in arguments for _executable, arguments in calls)
    manifest = json.loads((backup / "backup-manifest.json").read_text(encoding="utf-8"))
    assert {scope for dump in manifest["dumps"] for scope in dump["scopes"]} == {
        "main",
        "auth",
        "peer",
    }
