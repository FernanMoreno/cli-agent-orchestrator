"""Opt-in real image, HTTP/cookie, restart, upgrade and rollback acceptance.

CAO_PERSONAL_DOCKER_ACCEPTANCE=1 pytest test/integration/test_docker_personal_installation.py
Requires a built image and the local Linux Docker daemon. Set
CAO_PERSONAL_DOCKER_IMAGE to select a private image; it defaults to
cao-personal:local.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

_DEFAULT_PERSONAL_IMAGE = "cao-personal:local"
_PERSONAL_IMAGE_ENV = "CAO_PERSONAL_DOCKER_IMAGE"
_IMAGE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}\Z")


def _resolve_personal_image(installer, environ):
    reference = environ.get(_PERSONAL_IMAGE_ENV, _DEFAULT_PERSONAL_IMAGE)
    if not _IMAGE_REFERENCE.fullmatch(reference):
        raise ValueError("invalid CAO_PERSONAL_DOCKER_IMAGE image reference")
    return reference, installer.image_id(reference)


def _new_test_image_tag(installer, prefix):
    for _ in range(3):
        reference = f"{prefix}:{os.urandom(12).hex()}"
        result = installer.docker(["image", "inspect", reference], check=False)
        if result.returncode != 0:
            return reference
    raise RuntimeError("could not allocate a unique personal acceptance image tag")


def _existing_image_id(installer, reference):
    result = installer.docker(["image", "inspect", "--format", "{{.Id}}", reference], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _container_fingerprint(installer, name):
    container = installer.inspect(name)
    if container is None:
        return None
    return container["Id"], container["Image"], container["State"]["Status"]


def _private_test_home(tmp_path, monkeypatch):
    private_home = tmp_path / "operator-home"
    private_home.mkdir(mode=0o700)
    os.chmod(private_home, 0o700)
    monkeypatch.setenv("HOME", str(private_home))
    return private_home


def _cleanup_owned_database_resources(installer, root, private_home):
    deployment_path = root / "deployment.json"
    if not deployment_path.is_file() or deployment_path.is_symlink():
        return
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    instance_id = deployment.get("instance_id")
    if not isinstance(instance_id, str) or not re.fullmatch(r"[0-9a-f]{32}", instance_id):
        raise AssertionError("test deployment does not have a valid private instance identity")

    expected_containers = {
        "cao-main-pg-"
        + instance_id[:12]: {
            "org.cao.personal.database.service": "main-auth",
            "org.cao.personal.database.owner": instance_id,
            "org.cao.personal.database.instance": instance_id,
        }
    }
    peer_root = private_home / ".local/share/cao-peer-postgres"
    peer_settings_path = peer_root / "service.json"
    if peer_settings_path.is_file() and not peer_settings_path.is_symlink():
        peer = json.loads(peer_settings_path.read_text(encoding="utf-8"))
        if peer.get("isolated") is not False:
            raise AssertionError("test peer store is not the expected private shared-peer profile")
        expected_containers[peer["container"]] = {
            "org.cao.personal.database.service": "peer",
            "org.cao.personal.database.owner": peer["owner_id"],
            "org.cao.personal.database.instance": peer["store_id"],
        }

    for name, expected_labels in expected_containers.items():
        container = installer.inspect(name)
        if container is None:
            continue
        labels = container.get("Config", {}).get("Labels") or {}
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            raise AssertionError("refusing to remove a PostgreSQL container not owned by this test")
        installer.docker(["rm", "--force", container["Id"]])

    network_name = "cao-profile-" + instance_id[:12]
    network = installer.inspect_network(network_name)
    if network is not None:
        labels = network.get("Labels") or {}
        expected_owner = hashlib.sha256(
            f"{socket.gethostname()}:{os.getuid()}".encode()
        ).hexdigest()[:16]
        if (
            labels.get("org.cao.personal.database.service") != "profile-network"
            or labels.get("org.cao.personal.database.instance") != instance_id
            or labels.get("org.cao.personal.database.owner") != expected_owner
        ):
            raise AssertionError("refusing to remove a network not owned by this test")
        installer.docker(["network", "rm", network_name])

    for owned_path, expected_parent in (
        (root / "cao/postgres", root / "cao"),
        (peer_root, private_home / ".local/share"),
    ):
        if owned_path.exists() or owned_path.is_symlink():
            if owned_path.is_symlink() or owned_path.parent != expected_parent:
                raise AssertionError(
                    "refusing to remove PostgreSQL state outside this test profile"
                )
            shutil.rmtree(owned_path)


def test_personal_image_selector_defaults_and_pins_reference():
    class FakeInstaller:
        def __init__(self):
            self.references = []

        def image_id(self, reference):
            self.references.append(reference)
            return f"sha256:{len(self.references):064x}"

    installer = FakeInstaller()
    default_reference, default_id = _resolve_personal_image(installer, {})
    selected_reference, selected_id = _resolve_personal_image(
        installer, {_PERSONAL_IMAGE_ENV: "cao-personal-spec016:test-image"}
    )

    assert default_reference == _DEFAULT_PERSONAL_IMAGE
    assert selected_reference == "cao-personal-spec016:test-image"
    assert installer.references == [default_reference, selected_reference]
    assert default_id != selected_id

    with pytest.raises(ValueError, match="invalid CAO_PERSONAL_DOCKER_IMAGE"):
        _resolve_personal_image(installer, {_PERSONAL_IMAGE_ENV: "cao-personal:test\nRUN id"})


@pytest.mark.offline_database
def test_managed_acceptance_home_is_private_and_child_inherited(tmp_path, monkeypatch):
    private_home = _private_test_home(tmp_path, monkeypatch)
    from cli_agent_orchestrator.services.local_peers.host_identity import current_host_identity

    assert Path.home() == private_home
    assert os.environ["HOME"] == str(private_home)
    assert private_home.stat().st_mode & 0o077 == 0
    assert not (private_home / ".local/share/cao-peer-postgres").exists()
    peer_identity_dir = private_home / ".aws/cli-agent-orchestrator/local-peers"
    peer_identity = current_host_identity(peer_identity_dir)
    assert current_host_identity(peer_identity_dir) == peer_identity
    assert (peer_identity_dir / "host-id").stat().st_mode & 0o777 == 0o600
    assert current_host_identity(private_home / "other-profile-peer-identity").host_id != (
        peer_identity.host_id
    )


@pytest.mark.integration
def test_real_installation_cookie_persistence_upgrade_and_rollback(tmp_path, monkeypatch):
    if os.environ.get("CAO_PERSONAL_DOCKER_ACCEPTANCE") != "1":
        pytest.skip("set CAO_PERSONAL_DOCKER_ACCEPTANCE=1 for real application image acceptance")
    private_home = _private_test_home(tmp_path, monkeypatch)
    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "docker_install_acceptance", repo / "scripts/docker_install.py"
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    image_reference, expected_image_id = _resolve_personal_image(installer, os.environ)
    name = "cao-install-test-" + os.urandom(6).hex()
    root = tmp_path / "state"
    port = 19891
    commands = [str(repo / "install.sh"), "--root", str(root), "--name", name, "--port", str(port)]
    worker_tag = _new_test_image_tag(installer, "cao-personal-acceptance-worker")
    protected_image_ids = {
        _DEFAULT_PERSONAL_IMAGE: _existing_image_id(installer, _DEFAULT_PERSONAL_IMAGE),
        "cao-personal-worker:local": _existing_image_id(installer, "cao-personal-worker:local"),
    }
    operator_container = _container_fingerprint(installer, "cao-personal")
    worker_image_id = None

    def run(command="install", *, image=expected_image_id):
        subprocess.run(
            [*commands, command, "--no-build", "--image", image],
            check=True,
            capture_output=True,
            text=True,
            timeout=240,
        )

    def install_fresh_root():
        nonlocal worker_image_id
        original_build = installer.build

        def build_with_private_worker_tag(tag, *, worker=False):
            nonlocal worker_image_id
            if worker:
                if tag != "cao-personal-worker:local":
                    raise AssertionError("installer requested an unexpected worker image tag")
                worker_image_id = original_build(worker_tag, worker=True)
                return worker_image_id
            return original_build(tag, worker=worker)

        with monkeypatch.context() as patch:
            patch.setattr(installer, "build", build_with_private_worker_tag)
            installer.install(
                SimpleNamespace(
                    root=root,
                    name=name,
                    image=expected_image_id,
                    no_build=True,
                    port=port,
                    workspace=[repo],
                    workspace_readonly=[],
                    database_config=None,
                )
            )
        if worker_image_id is None or installer.image_id(worker_tag) != worker_image_id:
            raise AssertionError("private worker image tag did not resolve to its built image ID")
        source_path = repo / "src/cli_agent_orchestrator/backends/docker_work_supervisor.c"
        expected_worker_source = hashlib.sha256(source_path.read_bytes()).hexdigest()
        label = installer.docker(
            [
                "image",
                "inspect",
                "--format",
                '{{ index .Config.Labels "org.cao.work.supervisor.source_sha256" }}',
                worker_image_id,
            ]
        ).stdout.strip()
        if label != expected_worker_source:
            raise AssertionError("private worker image does not match the current source digest")

    upgrade_base = _new_test_image_tag(installer, "cao-personal-acceptance-base")
    upgraded = _new_test_image_tag(installer, "cao-personal-acceptance")
    origin = f"http://127.0.0.1:{port}"
    client = httpx.Client(
        base_url=origin,
        headers={"Origin": origin, "X-CAO-Browser": "1"},
        trust_env=False,
        timeout=10,
    )
    try:
        install_fresh_root()
        assert installer.inspect(name)["Image"] == expected_image_id
        run()  # idempotent installation
        assert client.get("/").status_code == 200
        assert client.get("/sessions").status_code == 401
        code = "import sys;sys.path.insert(0,'/opt/cao/scripts');import personal_deployment as p;from pathlib import Path;print(p.mint_token(Path(sys.argv[1])))"
        token = installer.docker(["exec", name, "python", "-c", code, str(root)]).stdout.strip()
        response = client.post(
            "/auth/setup",
            headers={"Authorization": "Bearer " + token},
            json={"username": "docker-proof", "password": "Test123456", "remember": True},
        )
        assert response.status_code == 200
        assert client.get("/sessions").status_code == 200
        assert (
            client.get("/sessions", headers={"Origin": "http://untrusted.invalid"}).status_code
            == 403
        )
        assert client.get("/sessions", headers={"X-Forwarded-For": "192.0.2.1"}).status_code == 200
        identity = installer.identity_fingerprint(root)
        run("stop")
        with pytest.raises(httpx.TransportError):
            client.get("/")
        run("start")
        assert client.get("/sessions").status_code == 200
        assert installer.identity_fingerprint(root) == identity
        installer.docker(["image", "tag", expected_image_id, upgrade_base])
        assert installer.image_id(upgrade_base) == expected_image_id
        upgrade_context = tmp_path / "upgrade.Dockerfile"
        upgrade_context.write_text(f"FROM {upgrade_base}\nLABEL org.cao.acceptance.upgrade=true\n")
        with upgrade_context.open("rb") as upgrade_reader:
            installer.docker(["build", "--tag", upgraded, "-"], input_file=upgrade_reader)
        assert installer.image_id(upgrade_base) == expected_image_id
        assert installer.image_id(image_reference) == expected_image_id
        run(image=upgraded)
        assert installer.inspect(name)["Image"] == installer.image_id(upgraded)
        assert client.get("/sessions").status_code == 200
        run("rollback")
        assert client.get("/sessions").status_code == 200
        assert installer.inspect(name)["Image"] == expected_image_id
        assert installer.identity_fingerprint(root) == identity
        run()
        assert client.get("/sessions").status_code == 200

        healthy = installer.wait_healthy

        def fail_candidate(candidate, port, **kwargs):
            if candidate == name and installer.inspect(name)["Image"] == installer.image_id(
                upgraded
            ):
                raise RuntimeError("acceptance injected candidate health failure")
            return healthy(candidate, port, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(installer, "wait_healthy", fail_candidate)
            with pytest.raises(RuntimeError, match="injected candidate health failure"):
                installer.install(
                    SimpleNamespace(
                        root=root,
                        name=name,
                        image=upgraded,
                        no_build=True,
                        port=port,
                        workspace=[repo],
                        workspace_readonly=[],
                        database_config=None,
                    )
                )
        assert client.get("/sessions").status_code == 200
        assert installer.inspect(name)["Image"] == expected_image_id
    finally:
        client.close()
        result = installer.docker(
            ["ps", "--all", "--filter", f"label={installer.LABEL}={root}", "--format", "{{.ID}}"],
            check=False,
        )
        for container in result.stdout.split():
            installer.docker(["rm", "--force", container], check=False)
        _cleanup_owned_database_resources(installer, root, private_home)
        installer.docker(["image", "rm", upgraded], check=False)
        installer.docker(["image", "rm", upgrade_base], check=False)
        installer.docker(["image", "rm", worker_tag], check=False)
        for reference, image_id in protected_image_ids.items():
            assert _existing_image_id(installer, reference) == image_id
        assert _container_fingerprint(installer, "cao-personal") == operator_container
