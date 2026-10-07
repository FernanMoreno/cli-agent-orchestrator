#!/usr/bin/env python3
"""Install the complete local CAO application in Docker, preserving personal state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / ".local/share/cao-personal"
SOCKET = Path("/var/run/docker.sock")
LABEL = "org.cao.personal.root"
INSTANCE_LABEL = "org.cao.personal.installation"


def mount_argument(source: Path, destination: Path | None = None, *, readonly=False) -> str:
    source = source.absolute()
    destination = (destination or source).absolute()
    if any(character in str(path) for path in (source, destination) for character in ",\n\r\x00"):
        raise ValueError("invalid mount path")
    return f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")


def private_directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError(f"private owner-only directory required: {path}")


def container_arguments(root, image, name, config, mounts, *, port=None):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", name):
        raise ValueError("invalid container name")
    home = root.with_name(root.name + "-container-home")
    sockets = root.with_name(root.name + "-container-sockets")
    private_directory(home)
    private_directory(sockets)
    arguments = [
        "run",
        "--detach",
        "--init",
        "--name",
        name,
        "--restart",
        "unless-stopped",
        "--stop-timeout",
        "30",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--group-add",
        str(SOCKET.stat().st_gid),
        "--label",
        f"{LABEL}={root}",
        "--publish",
        f"127.0.0.1:{config['server_port'] if port is None else port}:8080",
        "--env",
        f"HOME={Path.home()}",
        "--env",
        f"CAO_PERSONAL_ROOT={root}",
        "--env",
        f"CAO_HOME_DIR={root / 'cao'}",
        "--mount",
        mount_argument(root),
        "--mount",
        mount_argument(home, Path.home()),
        "--mount",
        mount_argument(sockets, root.with_name(root.name + "-sockets")),
        "--mount",
        mount_argument(SOCKET),
    ]
    projects = [mount["target"] for mount in mounts if mount.get("purpose") == "project"]
    if projects:
        arguments += ["--env", "CAO_REGISTERED_PROJECTS=" + json.dumps(projects)]
    for mount in mounts:
        if mount.get("purpose") == "obsidian-vault":
            arguments += ["--env", "CLAUDE_OBSIDIAN_VAULT=" + mount["target"]]
        arguments += [
            "--mount",
            mount_argument(
                Path(mount["source"]), Path(mount["target"]), readonly=mount.get("readonly", False)
            ),
        ]
    return arguments + [image]


def write_manifest(path: Path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as writer:
            json.dump(value, writer, indent=2)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_home(image, root):
    """Create writable HOME parents before Docker creates nested mount points as root."""
    home = root.with_name(root.name + "-container-home")
    private_directory(home)
    code = (
        "import os,sys;from pathlib import Path;root=Path('/home-state');"
        "paths=['.local','.local/share','.local/state','.cache','.config','.local/share/playwright-mcp'];"
        "uid,gid=map(int,sys.argv[1:]);"
        "\nfor relative in paths:\n"
        " p=root/relative\n"
        " if p.is_symlink(): raise ValueError('HOME parent symlink rejected')\n"
        " p.mkdir(mode=0o700,parents=True,exist_ok=True);os.chown(p,uid,gid)\n"
        "lib=root/'.local/share/playwright-mcp/lib'\n"
        "if lib.is_symlink():\n"
        " if os.readlink(lib)!='/usr/lib/x86_64-linux-gnu': raise ValueError('unexpected browser library link')\n"
        "else:\n"
        " if lib.exists(): lib.rmdir()\n"
        " lib.symlink_to('/usr/lib/x86_64-linux-gnu')\n"
    )
    docker(
        [
            "run",
            "--rm",
            "--user",
            "0:0",
            "--mount",
            mount_argument(home, Path("/home-state")),
            "--entrypoint",
            "python",
            image,
            "-c",
            code,
            str(os.getuid()),
            str(os.getgid()),
        ]
    )


def cutover(stop_previous, restore_previous, start_candidate, stop_candidate, verify, commit):
    candidate_attempted = False
    try:
        stop_previous()
        candidate_attempted = True
        start_candidate()
        verify()
        commit()
    except BaseException:
        try:
            if candidate_attempted:
                stop_candidate()
        finally:
            restore_previous()
        raise


def docker(arguments, *, check=True, input_file=None, stream=False):
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError(
            "Install Docker and enable its Linux/WSL integration before running install.sh"
        )
    env = os.environ.copy()
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    env["DOCKER_HOST"] = f"unix://{SOCKET}"
    result = subprocess.run(
        [executable, *arguments],
        env=env,
        stdin=input_file,
        stdout=None if stream else subprocess.PIPE,
        stderr=None if stream else subprocess.PIPE,
        text=True,
        timeout=1800 if arguments[0] == "build" else 120 if arguments[0] == "run" else 45,
    )
    if check and result.returncode:
        # Arguments contain no credentials; do not echo daemon responses carrying owner data.
        raise RuntimeError(f"Docker {arguments[0]} failed (exit {result.returncode})")
    return result


def inspect(name):
    result = docker(["inspect", name], check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def image_id(tag):
    result = docker(["image", "inspect", "--format", "{{.Id}}", tag])
    value = result.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RuntimeError("Docker returned an invalid image ID")
    return value


def build_archive(destination: Path):
    """An explicit context also protects legacy builders that ignore per-file ignores."""
    excluded = {
        "node_modules",
        "__pycache__",
        "target",
        "web_ui",
        "_bin",
        "apps_static",
        "test-results",
        "playwright-report",
        "dist",
    }
    files = [
        Path(name)
        for name in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "LICENSE",
            "NOTICE",
            "test/fixtures/work_contract_v1.json",
        )
    ]
    files += [
        Path("scripts") / name
        for name in (
            "hatch_build_tui_tag.py",
            "personal_deployment.py",
            "local_work_docker_demo.py",
            "docker_personal_runtime.py",
            "docker_windows_mcp.py",
        )
    ]
    for directory in ("src", "web", "cao_mcp_apps", "docker/personal"):
        for current, directories, names in os.walk(REPOSITORY / directory):
            directories[:] = [
                name for name in directories if name not in excluded and not name.startswith(".")
            ]
            files += [
                (Path(current) / name).relative_to(REPOSITORY)
                for name in names
                if not name.startswith((".env", "cao-tui"))
                and not name.endswith((".pyc", ".pem", ".db", ".sqlite3"))
            ]
    with tarfile.open(destination, "w:gz") as archive:
        for relative in files:
            source = REPOSITORY / relative
            if source.is_symlink():
                raise ValueError(f"source build context refuses symlink: {relative}")
            archive.add(source, arcname=str(relative), recursive=False)


def build(tag, *, worker=False):
    source = REPOSITORY / "src/cli_agent_orchestrator/backends/docker_work_supervisor.c"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="cao-image-") as scratch:
        archive = Path(scratch) / "context.tar"
        build_archive(archive)
        with archive.open("rb") as reader:
            docker(
                [
                    "build",
                    "--target",
                    "worker-rootfs" if worker else "application",
                    "--build-arg",
                    f"CAO_WORK_SUPERVISOR_SOURCE_SHA256={digest}",
                    "-f",
                    "docker/personal/Dockerfile",
                    "--tag",
                    tag,
                    "-",
                ],
                input_file=reader,
                stream=True,
            )
    return image_id(tag)


def admin(image, root, arguments):
    return docker(
        [
            "run",
            "--rm",
            "--init",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            f"HOME={root}",
            "--env",
            f"CAO_HOME_DIR={root / 'cao'}",
            "--mount",
            mount_argument(root.parent),
            "--entrypoint",
            "python",
            image,
            "/opt/cao/scripts/personal_deployment.py",
            "--root",
            str(root),
            *arguments,
        ]
    )


def config_for(root):
    private_directory(root)
    for file in (root / "deployment.json", root / "issuer.pem"):
        if file.is_symlink() or file.stat().st_uid != os.getuid() or file.stat().st_mode & 0o077:
            raise ValueError("deployment files must remain private and owned by the operator")
    config = json.loads((root / "deployment.json").read_text())
    if config.get("host") != "127.0.0.1" or any(
        type(config.get(key)) is not int or not 1024 <= config[key] <= 65535 or config[key] == 8080
        for key in ("server_port", "jwks_port")
    ):
        raise ValueError("local deployment ports required; 8080 is reserved for the internal proxy")
    return config


def identity_fingerprint(root):
    config = config_for(root)
    identity = {
        key: config.get(key)
        for key in ("issuer", "audience", "subject", "scopes", "kid", "browser_login")
    }
    return hashlib.sha256(
        (root / "issuer.pem").read_bytes() + json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()


def windows_mcp_mount():
    """Windows Studio remains an external integration; CAO itself stays in Docker."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 uses the declared runtime dependency.
        import tomli as tomllib

    config_path = Path.home() / ".codex/config.toml"
    if not config_path.exists():
        return None
    config = tomllib.loads(config_path.read_text())
    arguments = [
        server.get("args", [])
        for server in config.get("mcp_servers", {}).values()
        if server.get("command") == "cmd.exe"
    ]
    if not arguments:
        return None
    executable = shutil.which("cmd.exe")
    if not executable:
        raise RuntimeError("configured Windows MCP requires WSL cmd.exe before migration")
    root = Path.home() / ".local/share/cao-windows-mcp"
    private_directory(root)
    previous = (root / "commands.json").read_text() if (root / "commands.json").exists() else None
    script = (REPOSITORY / "scripts/docker_windows_mcp.py").read_bytes()
    changed = not (root / "relay.py").exists() or (root / "relay.py").read_bytes() != script
    write_manifest(root / "commands.json", {"executable": executable, "arguments": arguments})
    changed |= previous != (root / "commands.json").read_text()
    (root / "relay.py").write_bytes(script)
    (root / "relay.py").chmod(0o600)
    unit = Path.home() / ".config/systemd/user/cao-windows-mcp.service"
    unit.parent.mkdir(parents=True, exist_ok=True)

    def quote(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    content = (
        "[Unit]\nDescription=CAO external Windows MCP stdio relay\n"
        "[Service]\nExecStart="
        + quote(sys.executable)
        + " "
        + quote(root / "relay.py")
        + " --serve "
        + quote(root)
        + "\nRestart=on-failure\nUMask=0077\n"
        "[Install]\nWantedBy=default.target\n"
    )
    if not unit.exists() or unit.read_text() != content:
        unit.write_text(content)
        changed = True
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", unit.name], check=True, capture_output=True
    )
    if changed:
        subprocess.run(
            ["systemctl", "--user", "restart", unit.name], check=True, capture_output=True
        )
    deadline = time.monotonic() + 10
    while not (root / "mcp.sock").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Windows MCP relay failed to become ready")
        time.sleep(0.1)
    return {"source": str(root), "target": "/opt/cao/windows-mcp", "readonly": True}


def runtime_mounts(workspaces, readonly_workspaces=()):
    mounts = []
    for relative in (
        ".claude",
        ".claude.json",
        ".codex",
        ".agents",
        ".config/opencode",
        ".local/share/opencode",
        ".cache/ms-playwright",
    ):
        source = Path.home() / relative
        if source.exists():
            mounts.append(
                {
                    "source": str(source),
                    "target": str(source),
                    "readonly": relative in (".cache/ms-playwright", ".agents"),
                }
            )
    bridge = windows_mcp_mount()
    if bridge:
        mounts.append(bridge)
    opencode = shutil.which("opencode")
    if opencode:
        mounts.append(
            {
                "source": str(Path(opencode).resolve()),
                "target": "/opt/cao/host-tools/opencode",
                "readonly": True,
            }
        )
    projects = {}
    for supplied, readonly in [*((p, False) for p in workspaces), *((p, True) for p in readonly_workspaces)]:
        path = supplied.expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"workspace directory does not exist: {path}")
        for previous, mode in projects.items():
            if readonly != mode and (path.is_relative_to(previous) or previous.is_relative_to(path)):
                raise ValueError("conflicting workspace modes for overlapping projects")
        projects[path] = readonly
    mounts.extend(
        {"source": str(path), "target": str(path), "readonly": readonly, "purpose": "project"}
        for path, readonly in projects.items()
    )
    vault = os.environ.get("CLAUDE_OBSIDIAN_VAULT")
    if vault:
        path = Path(vault).expanduser().absolute()
        if not path.is_dir():
            raise ValueError("configured Obsidian vault must exist before migration")
        mounts.append({"source": str(path), "target": str(path), "purpose": "obsidian-vault"})
    return mounts


def request_status(port, path, *, token=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Origin": f"http://127.0.0.1:{port}"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with opener.open(
            urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers), timeout=5
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def wait_healthy(name, port, *, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name is None:
            if not systemctl("is-active", check=False):
                raise RuntimeError("previous host service exited before becoming healthy")
        else:
            state = inspect(name)
            if state is None or not state["State"]["Running"]:
                raise RuntimeError("application container exited before becoming healthy")
        try:
            status, html = request_status(port, "/")
            if (
                status == 200
                and b"<html" in html.lower()
                and request_status(port, "/health")[0] == 200
            ):
                if (
                    request_status(port, "/auth/config")[0] == 200
                    and request_status(port, "/sessions")[0] == 401
                ):
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError("frontend/API/authentication did not become healthy within 120 seconds")


def validate_integrations(image, root, config, mounts):
    args = container_arguments(
        root, image, "cao-check-" + uuid.uuid4().hex[:12], config, mounts, port=""
    )
    # One-shot check: neither publish ports nor launch application processes.
    for flag in ("--detach",):
        args.remove(flag)
    for flag in ("--name", "--restart", "--publish"):
        position = args.index(flag)
        del args[position : position + 2]
    args.insert(1, "--rm")
    args[-1:-1] = ["--entrypoint", "python"]
    code = (
        "import sys,subprocess; "
        "sys.path.insert(0,'/opt/cao/scripts'); "
        "from docker_personal_runtime import check_mcp_integrations; "
        "from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend; "
        "from cli_agent_orchestrator.providers.opencode_cli import "
        "detect_opencode_major, resolve_opencode_runtime; "
        "DockerWorkBackend(image_ref=sys.argv[1])._validate_engine_and_image(); "
        "opencode_runtime=resolve_opencode_runtime() if 'opencode' in sys.argv[2:] else None; "
        "[detect_opencode_major(opencode_runtime) if name=='opencode' else "
        "subprocess.run([name,'--version'],check=True,stdout=subprocess.DEVNULL) "
        "for name in sys.argv[2:]]; check_mcp_integrations()"
    )
    providers = ["claude", "codex"] + (["opencode"] if shutil.which("opencode") else [])
    docker(args + ["-c", code, config["image_id"], *providers])


def systemctl(*arguments, check=True):
    result = subprocess.run(
        ["systemctl", "--user", *arguments, "cao-personal.service"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and result.returncode:
        raise RuntimeError("could not change the previous personal service")
    return result.returncode == 0


def preflight(image, config):
    with tempfile.TemporaryDirectory(prefix="cao-container-preflight-") as scratch:
        root = Path(scratch) / "state"
        admin(
            image,
            root,
            [
                "init",
                "--image-id",
                config["image_id"],
                "--port",
                str(config["server_port"]),
                "--jwks-port",
                str(config["jwks_port"]),
            ],
        )
        name = "cao-preflight-" + uuid.uuid4().hex[:12]
        try:
            prepare_home(image, root)
            docker(container_arguments(root, image, name, config, [], port=""))
            port = int(inspect(name)["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"])
            wait_healthy(name, port)
        finally:
            docker(["rm", "--force", name], check=False)
            for suffix in ("-container-home", "-container-sockets"):
                shutil.rmtree(root.with_name(root.name + suffix), ignore_errors=True)


def install(args):
    root = args.root
    daemon = docker(["info", "--format", "{{.OSType}}/{{.Architecture}}"]).stdout.strip()
    if daemon not in ("linux/x86_64", "linux/amd64"):
        raise RuntimeError("the local Work deployment requires a Linux amd64 Docker engine")
    print("Construyendo imagen de aplicación...", flush=True)
    image = image_id(args.image) if args.no_build else build(args.image)
    if not root.exists():
        if not 1024 <= args.port <= 65534 or args.port in (8079, 8080):
            raise ValueError("choose an API port from 1024 to 65534, excluding 8079/8080")
        root.parent.mkdir(parents=True, exist_ok=True)
        worker = build("cao-personal-worker:local", worker=True)
        admin(
            image,
            root,
            [
                "init",
                "--image-id",
                worker,
                "--port",
                str(args.port),
                "--jwks-port",
                str(args.port + 1),
            ],
        )
    config = config_for(root)
    fingerprint = identity_fingerprint(root)
    manifest_path = root / "docker-installation.json"
    previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    mounts = runtime_mounts(args.workspace, getattr(args, "workspace_readonly", ()))
    prepare_home(image, root)
    current = inspect(args.name)
    if current and current["Config"].get("Labels", {}).get(LABEL) != str(root):
        raise RuntimeError("container name belongs to another deployment")
    if (
        current
        and current["Image"] == image
        and previous_manifest
        and previous_manifest.get("mounts") == mounts
    ):
        if not current["State"]["Running"]:
            docker(["start", args.name])
        wait_healthy(args.name, config["server_port"])
        print(f"Instalación ya activa: http://127.0.0.1:{config['server_port']}/")
        return
    print("Comprobando imagen, proveedores y almacenamiento antes del corte...", flush=True)
    validate_integrations(image, root, config, mounts)
    preflight(image, config)
    host_service = root == DEFAULT_ROOT
    host_active = host_service and systemctl("is-active", check=False)
    host_enabled = host_service and systemctl("is-enabled", check=False)
    previous_name = args.name + "-previous-" + uuid.uuid4().hex[:8] if current else None
    backup_path = root.with_name(
        root.name + "-backup-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    )
    container_stopped = False
    container_parked = False
    host_stopped = False
    instance = uuid.uuid4().hex

    def stop_previous():
        nonlocal container_stopped, container_parked, host_stopped
        if current:
            container_stopped = True
            docker(["stop", args.name])
            docker(["rename", args.name, previous_name])
            container_parked = True
        if host_active:
            host_stopped = True
            systemctl("stop")

    def restore_previous():
        if container_parked:
            docker(["rename", previous_name, args.name])
        if container_stopped:
            docker(["start", args.name])
            wait_healthy(args.name, config["server_port"])
        if host_enabled:
            systemctl("enable")
        if host_stopped:
            systemctl("start")
            wait_healthy(None, config["server_port"])
        if previous_manifest is not None:
            write_manifest(manifest_path, previous_manifest)
        else:
            manifest_path.unlink(missing_ok=True)

    def start_candidate():
        admin(image, root, ["backup", str(backup_path)])
        arguments = container_arguments(root, image, args.name, config, mounts)
        arguments[-1:-1] = ["--label", f"{INSTANCE_LABEL}={instance}"]
        docker(arguments)

    def stop_candidate():
        state = inspect(args.name)
        if state and state["Config"].get("Labels", {}).get(INSTANCE_LABEL) == instance:
            docker(["rm", "--force", args.name])

    def verify():
        wait_healthy(args.name, config["server_port"])
        if identity_fingerprint(root) != fingerprint:
            raise RuntimeError("deployment identity changed during migration")
        code = "import sys;sys.path.insert(0,'/opt/cao/scripts');import personal_deployment as p;from pathlib import Path;print(p.mint_token(Path(sys.argv[1])))"
        token = docker(["exec", args.name, "python", "-c", code, str(root)]).stdout.strip()
        if request_status(config["server_port"], "/sessions", token=token)[0] != 200:
            raise RuntimeError("preserved operator identity cannot use the protected API")

    def commit():
        if host_enabled:
            systemctl("disable")
        write_manifest(
            manifest_path,
            {
                "version": 1,
                "container": args.name,
                "image": image,
                "mounts": mounts,
                "backup": str(backup_path),
                "previous_container": previous_name,
                "previous_manifest": previous_manifest,
                "previous_host_active": bool(host_active),
                "previous_host_enabled": bool(host_enabled),
            },
        )
        renew_browser_link(args.name, root)

    cutover(
        stop_previous,
        restore_previous,
        start_candidate,
        stop_candidate,
        verify,
        commit,
    )
    print(f"Instalado: http://127.0.0.1:{config['server_port']}/\nCopia privada: {backup_path}")


def renew_browser_link(name, root):
    code = "import sys;sys.path.insert(0,'/opt/cao/scripts');import personal_deployment as p;from pathlib import Path;p.write_browser_link(Path(sys.argv[1]))"
    docker(["exec", name, "python", "-c", code, str(root)])


def manage(args):
    config = config_for(args.root)
    manifest = json.loads((args.root / "docker-installation.json").read_text())
    name = manifest["container"]
    if args.command == "status":
        if manifest.get("mode") == "host":
            print("running" if systemctl("is-active", check=False) else "stopped")
            return
        state = inspect(name)
        print("running" if state and state["State"]["Running"] else "stopped")
    elif args.command in ("start", "stop"):
        if manifest.get("mode") == "host":
            systemctl(args.command)
            return
        docker([args.command, name])
        if args.command == "start":
            wait_healthy(name, config["server_port"])
    elif args.command == "rollback":
        previous = manifest.get("previous_container")
        if not previous and not manifest.get("previous_host_active"):
            raise RuntimeError(
                "no previous deployment is recorded; the private backup remains available"
            )
        if manifest.get("mode") == "host":
            raise RuntimeError("the previous host deployment has already been restored")
        parked = name + "-rolled-back-" + uuid.uuid4().hex[:8]
        stopped = renamed = previous_renamed = host_attempted = False
        try:
            stopped = True
            docker(["stop", name])
            docker(["rename", name, parked])
            renamed = True
            if previous:
                docker(["rename", previous, name])
                previous_renamed = True
                docker(["start", name])
                wait_healthy(name, config["server_port"])
                restored = dict(manifest.get("previous_manifest") or {})
                restored.update(
                    container=name,
                    previous_container=parked,
                    previous_manifest=manifest,
                    mode="docker",
                )
            else:
                host_attempted = True
                if manifest.get("previous_host_enabled"):
                    systemctl("enable")
                systemctl("start")
                wait_healthy(None, config["server_port"])
                restored = dict(
                    manifest,
                    mode="host",
                    previous_container=None,
                    previous_host_active=False,
                    previous_host_enabled=False,
                    parked_container=parked,
                )
            write_manifest(args.root / "docker-installation.json", restored)
        except BaseException:
            if host_attempted:
                systemctl("stop", check=False)
                systemctl("disable", check=False)
            if previous_renamed:
                docker(["stop", name], check=False)
                docker(["rename", name, previous])
            if renamed:
                docker(["rename", parked, name])
            if stopped:
                docker(["start", name])
            raise
        print("Despliegue anterior restaurado; datos y copia privada conservados.")
    elif args.command == "account-reset":
        if manifest.get("mode") == "host":
            runtime = args.root.with_name(args.root.name + "-runtime")
            subprocess.run(
                [
                    str(runtime / "bin/python"),
                    str(runtime / "personal_deployment.py"),
                    "--root",
                    str(args.root),
                    "account-reset",
                ],
                check=True,
            )
            return
        subprocess.run(
            [
                shutil.which("docker"),
                "exec",
                "-it",
                name,
                "python",
                "/opt/cao/scripts/personal_deployment.py",
                "--root",
                str(args.root),
                "account-reset",
            ],
            check=True,
        )
    elif args.command == "open":
        if manifest.get("mode") == "host":
            raise RuntimeError("use the previous host deployment web command after rollback")
        renew_browser_link(name, args.root)
        link = args.root / "web-link.txt"
        powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        if powershell.exists():
            subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$url = [Console]::In.ReadToEnd().Trim(); Start-Process $url",
                ],
                input=link.read_text(),
                text=True,
                check=True,
            )
        else:
            import webbrowser

            webbrowser.open(link.read_text().strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="install",
        choices=("install", "status", "start", "stop", "rollback", "account-reset", "open"),
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--name", default="cao-personal")
    parser.add_argument("--image", default="cao-personal:local")
    parser.add_argument("--port", type=int, default=9889)
    parser.add_argument("--workspace", type=Path, action="append", default=[REPOSITORY])
    parser.add_argument("--workspace-readonly", type=Path, action="append", default=[])
    parser.add_argument(
        "--no-build", action="store_true", help="reuse an already-built local image"
    )
    args = parser.parse_args()
    args.root = args.root.expanduser().absolute()
    args.root.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        args.root.with_name(args.root.name + "-installation.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        (install if args.command == "install" else manage)(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "Instalación interrumpida; recuperación ejecutada si había un corte activo.",
            file=sys.stderr,
        )
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Instalación interrumpida: {error}", file=sys.stderr)
        sys.exit(1)
