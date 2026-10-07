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
        "previous_container": previous,
        "previous_host_active": previous is None,
        "previous_host_enabled": previous is None,
        "previous_manifest": {"container": "cao-personal", "image": "old", "mounts": []},
    }
    module.write_manifest(root / "docker-installation.json", manifest)
    monkeypatch.setattr(module, "config_for", lambda root: {"server_port": 9889})
    monkeypatch.setattr(module, "wait_healthy", lambda *a, **kw: None)
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
    assert calls[-1] == ["start", "cao-personal"]


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
    assert calls.index(["host", "stop"]) < calls.index(["start", "cao-personal"])
    assert ["host", "disable"] in calls


def test_successful_host_rollback_records_host_lifecycle(tmp_path, monkeypatch):
    module, args = rollback_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "docker", lambda *a, **kw: None)
    calls = []
    monkeypatch.setattr(module, "systemctl", lambda command, **kw: calls.append(command) or True)
    module.manage(args)
    manifest = json.loads((args.root / "docker-installation.json").read_text())
    assert manifest["mode"] == "host"
    args.command = "stop"
    module.manage(args)
    assert calls[-1] == "stop"


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
