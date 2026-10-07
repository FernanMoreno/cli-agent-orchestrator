"""Opt-in real image, HTTP/cookie, restart, upgrade and rollback acceptance.

CAO_PERSONAL_DOCKER_ACCEPTANCE=1 pytest test/integration/test_docker_personal_installation.py
Requires the built cao-personal:local image and the local Linux Docker daemon.
"""

import importlib.util
import os
import subprocess
from pathlib import Path

import httpx
import pytest


@pytest.mark.integration
def test_real_installation_cookie_persistence_upgrade_and_rollback(tmp_path, monkeypatch):
    if os.environ.get("CAO_PERSONAL_DOCKER_ACCEPTANCE") != "1":
        pytest.skip("set CAO_PERSONAL_DOCKER_ACCEPTANCE=1 for real application image acceptance")
    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "docker_install_acceptance", repo / "scripts/docker_install.py"
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    name = "cao-install-test-" + os.urandom(6).hex()
    root = tmp_path / "state"
    port = 19891
    commands = [str(repo / "install.sh"), "--root", str(root), "--name", name, "--port", str(port)]

    def run(command="install", *, image="cao-personal:local"):
        subprocess.run(
            [*commands, command, "--no-build", "--image", image],
            check=True,
            capture_output=True,
            text=True,
            timeout=240,
        )

    upgraded = "cao-personal-acceptance:" + os.urandom(6).hex()
    origin = f"http://127.0.0.1:{port}"
    client = httpx.Client(
        base_url=origin,
        headers={"Origin": origin, "X-CAO-Browser": "1"},
        trust_env=False,
        timeout=10,
    )
    try:
        run()
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
        subprocess.run(
            ["docker", "build", "--tag", upgraded, "-"],
            input="FROM cao-personal:local\nLABEL org.cao.acceptance.upgrade=true\n",
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        run(image=upgraded)
        assert client.get("/sessions").status_code == 200
        run("rollback")
        assert client.get("/sessions").status_code == 200
        assert installer.inspect(name)["Image"] == installer.image_id("cao-personal:local")
        assert installer.identity_fingerprint(root) == identity
        run()
        assert client.get("/sessions").status_code == 200
        from types import SimpleNamespace

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
                    )
                )
        assert client.get("/sessions").status_code == 200
        assert installer.inspect(name)["Image"] == installer.image_id("cao-personal:local")
    finally:
        client.close()
        result = installer.docker(
            ["ps", "--all", "--filter", f"label={installer.LABEL}={root}", "--format", "{{.ID}}"],
            check=False,
        )
        for container in result.stdout.split():
            installer.docker(["rm", "--force", container], check=False)
        installer.docker(["image", "rm", upgraded], check=False)
