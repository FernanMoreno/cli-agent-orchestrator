"""Tests for the opencode_cli branch of the install command."""

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import frontmatter
import pytest
from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.install import install

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def install_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opencode_test_environment
) -> Dict[str, Any]:
    """Redirect all filesystem paths used by the install command to tmp_path."""
    local_store = tmp_path / "agent-store"
    context_dir = tmp_path / "agent-context"
    opencode_agents = tmp_path / "opencode_cli" / "agents"
    opencode_config = tmp_path / "opencode_cli" / "opencode.json"

    local_store.mkdir(parents=True)
    context_dir.mkdir(parents=True)
    # opencode_agents intentionally NOT pre-created — install must mkdir it.

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.profile_store.LOCAL_AGENT_STORE_DIR", local_store
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles.LOCAL_AGENT_STORE_DIR", local_store
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.install_service.AGENT_CONTEXT_DIR", context_dir
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.install_service.OPENCODE_AGENTS_DIR", opencode_agents
    )
    # Installation owns this alias after the shared launch defaults; the
    # provider's command-building alias can keep its separate private path.
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.opencode_config.OPENCODE_CONFIG_FILE", opencode_config
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.get_agent_dirs", lambda: {}
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs", lambda: []
    )
    # Suppress ensure_skills_symlink filesystem side-effects in install unit tests.
    # The symlink helper's own behaviour is covered by test_opencode_config.py.
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.install_service.ensure_skills_symlink", lambda: None
    )

    return {
        "local_store": local_store,
        "context_dir": context_dir,
        "agents_dir": opencode_agents,
        "config_file": opencode_config,
    }


def _write_profile(
    profile_path: Path,
    *,
    name: str = "test-agent",
    description: str = "Test agent",
    mcp_servers: str = "",
    extra_frontmatter: str = "",
    body: str = "You are a helpful agent.",
) -> None:
    """Write a minimal agent profile .md file."""
    mcp_block = f"mcpServers:\n{mcp_servers}" if mcp_servers else ""
    profile_path.write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra_frontmatter}{mcp_block}\n---\n{body}\n",
        encoding="utf-8",
    )


def _help_lists_subcommand(help_output: str, subcommand: str) -> bool:
    """Return whether an OpenCode help page advertises one exact subcommand."""
    # v1 uses yargs Commands; v2 uses a SUBCOMMANDS table.
    if re.search(
        rf"(?m)^\s*opencode\s+(?:(?:debug|agent)\s+)?{re.escape(subcommand)}(?:\s|$)", help_output
    ):
        return True
    lines = help_output.splitlines()
    try:
        section_start = next(i for i, line in enumerate(lines) if line.strip() == "SUBCOMMANDS")
    except StopIteration:
        return False

    for line in lines[section_start + 1 :]:
        if not line.strip():
            continue
        if not line[0].isspace():
            break
        names = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
        if subcommand in {name.strip() for name in names.split(",")}:
            return True
    return False


def _wait_for_service_password(profile, service, *, deadline, now=None, sleep=None):
    """Wait for the private startup receipt within the existing shared deadline."""
    if now is None:
        now = time.monotonic
    if sleep is None:
        sleep = time.sleep
    while now() < deadline and service.poll() is None:
        try:
            for service_file in profile.rglob("service.json"):
                try:
                    info = json.loads(service_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeError):
                    continue
                if (
                    isinstance(info, dict)
                    and isinstance(info.get("password"), str)
                    and info["password"]
                ):
                    if now() < deadline and service.poll() is None:
                        return info["password"]
                    return None
        except OSError:
            # The private service can publish/rename directories during startup.
            # A transient scan failure is still bounded by the same deadline.
            pass
        remaining = deadline - now()
        if remaining > 0:
            sleep(min(0.1, remaining))
    return None


def _run_v2_debug_agent_list(
    opencode: str,
    agent_id: str,
    agents_dir: Path,
    config_file: Path,
    private_home: Path,
) -> None:
    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    v2_config_dir = private_home / "cao" / "opencode-v2"
    source_config = json.loads(config_file.read_text(encoding="utf-8"))
    write_v2_configuration(
        v2_config_dir,
        source_agents=agents_dir,
        source_config=source_config,
        session_id="install-agent-list-test",
    )
    v2_config = json.loads((v2_config_dir / "opencode.json").read_text(encoding="utf-8"))
    assert v2_config["agents"][agent_id]["mode"] == "all"
    mcp_rules = [
        rule
        for rule in v2_config["agents"][agent_id]["permissions"]
        if rule["action"] == "cao-mcp-server_*"
    ]
    assert {rule["effect"] for rule in mcp_rules} == {"allow", "deny"}

    project_dir = private_home / "agent-list-project"
    project_dir.mkdir(parents=True)
    git = shutil.which("git")
    assert git is not None, "OpenCode agent listing requires git for project context"
    git_template = private_home / "git-template"
    git_template.mkdir()
    git_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(private_home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    subprocess.run(
        [git, "init", "--quiet", f"--template={git_template}", str(project_dir)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_env,
    )
    subprocess.run(
        [git, "-C", str(project_dir), "config", "user.name", "CAO Test"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_env,
    )
    subprocess.run(
        [git, "-C", str(project_dir), "config", "user.email", "cao-test@example.invalid"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_env,
    )
    subprocess.run(
        [git, "-C", str(project_dir), "config", "commit.gpgsign", "false"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_env,
    )
    (project_dir / "README.md").write_text(
        "Private OpenCode agent-list integration fixture.\n", encoding="utf-8"
    )
    subprocess.run(
        [git, "-C", str(project_dir), "add", "--", "README.md"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_env,
    )
    subprocess.run(
        [git, "-C", str(project_dir), "commit", "--quiet", "-m", "private fixture"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_env,
    )
    git_root = subprocess.run(
        [git, "-C", str(project_dir), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_env,
    ).stdout.strip()
    assert Path(git_root).resolve() == project_dir.resolve()

    bwrap = shutil.which("bwrap")
    if bwrap is None:
        pytest.skip("v2 agent listing requires bwrap user and network namespaces")

    probe = subprocess.run(
        [
            bwrap,
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--die-with-parent",
            "--new-session",
            "--tmpfs",
            "/",
            "--dir",
            "/usr",
            "--ro-bind",
            "/usr",
            "/usr",
            "--dir",
            "/lib",
            "--ro-bind",
            "/lib",
            "/lib",
            "--dir",
            "/lib64",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--dir",
            "/bin",
            "--ro-bind",
            "/bin",
            "/bin",
            "--dir",
            "/etc",
            "--ro-bind",
            "/etc",
            "/etc",
            "--dir",
            "/tmp",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--tmpfs",
            "/home",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--",
            "/usr/bin/true",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={"LC_ALL": "C"},
    )
    if probe.returncode != 0:
        reason = probe.stderr.strip() or f"exit status {probe.returncode}"
        pytest.skip(f"host cannot create required bwrap namespaces: {reason}")

    driver_path = private_home / "opencode-agent-list-driver.py"
    driver_path.write_text(
        inspect.getsource(_wait_for_service_password) + "\n" + textwrap.dedent("""\
            import json
            import os
            import signal
            import subprocess
            import sys
            import time
            import base64
            import urllib.error
            import urllib.parse
            import urllib.request
            from pathlib import Path

            opencode = "/opencode-bin/opencode"
            project = Path("/work")
            profile = Path(os.environ["OPENCODE_CONFIG_DIR"])
            service_log = (profile / "serve.log").open("wb")
            report = {
                "service_ready": False,
                "service_auth_ready": False,
                "service_context_warmed": False,
                "api_config_poll_count": 0,
                "api_agent_target_ready": False,
                "api_agent_poll_count": 0,
                "command_exit": None,
                "agent_count": 0,
                "target_found": False,
            }
            report_path = profile / "debug-agents-result.json"
            with __import__("socket").socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            service = subprocess.Popen(
                [opencode, "serve", "--hostname", "127.0.0.1", "--port", str(port), "--service"],
                cwd=project,
                env=os.environ.copy(),
                stdout=service_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                ready = False
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and service.poll() is None:
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/doc", timeout=0.25
                        ) as response:
                            ready = response.status == 200
                        if ready:
                            break
                    except OSError:
                        time.sleep(0.1)
                if not ready:
                    raise SystemExit("OpenCode service readiness failed")
                report["service_ready"] = True
                password = _wait_for_service_password(profile, service, deadline=deadline)
                if not password:
                    raise SystemExit("OpenCode service authentication was not initialized")
                report["service_auth_ready"] = True
                authorization = "Basic " + base64.b64encode(
                    ("opencode:" + password).encode()
                ).decode()
                query = urllib.parse.urlencode({"location[directory]": "/work"})
                route = "/api/config?" + query
                config_ready = False
                while time.monotonic() < deadline and service.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    report["api_config_poll_count"] += 1
                    request = urllib.request.Request(f"http://127.0.0.1:{port}{route}")
                    request.add_header("Authorization", authorization)
                    try:
                        with urllib.request.urlopen(
                            request, timeout=min(0.25, remaining)
                        ) as response:
                            if response.status == 200:
                                response.read()
                                report["service_context_warmed"] = True
                                config_ready = True
                                break
                    except urllib.error.HTTPError as error:
                        if error.code not in {502, 503, 504}:
                            raise
                        error.close()
                    except (OSError, TimeoutError, urllib.error.URLError):
                        pass
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(0.1, remaining))
                if not config_ready:
                    raise SystemExit("OpenCode service context readiness failed")
                agent_route = "/api/agent?" + query
                # Agent listing does not wait for configured-plugin activation; poll this same
                # location only within the readiness deadline already in progress.
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    report["api_agent_poll_count"] += 1
                    request = urllib.request.Request(f"http://127.0.0.1:{port}{agent_route}")
                    request.add_header("Authorization", authorization)
                    try:
                        with urllib.request.urlopen(request, timeout=min(0.25, remaining)) as response:
                            if response.status != 200:
                                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                                continue
                            report["service_context_warmed"] = True
                            body = response.read(1048577)
                        if len(body) <= 1048576:
                            payload = json.loads(body)
                            rows = payload.get("data") if isinstance(payload, dict) else None
                            if isinstance(rows, list):
                                report["api_agent_target_ready"] = any(
                                    isinstance(item, dict)
                                    and (item.get("id") == sys.argv[1] or item.get("name") == sys.argv[1])
                                    for item in rows
                                )
                    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
                        pass
                    if report["api_agent_target_ready"]:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(0.1, remaining))
                listed = subprocess.run(
                    [opencode, "debug", "agents"],
                    cwd=project,
                    env=os.environ.copy(),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                report["command_exit"] = listed.returncode
                if listed.returncode != 0:
                    raise SystemExit("OpenCode debug agents command failed")
                payload = json.loads(listed.stdout)
                if isinstance(payload, dict):
                    payload = payload.get("data", payload.get("agents", []))
                if not isinstance(payload, list):
                    raise SystemExit("OpenCode debug agents returned an unexpected shape")
                listed_ids = {
                    item.get("id") or item.get("name")
                    for item in payload
                    if isinstance(item, dict)
                }
                report["agent_count"] = len(listed_ids)
                report["target_found"] = sys.argv[1] in listed_ids
                if sys.argv[1] not in listed_ids:
                    raise SystemExit("translated CAO agent missing from OpenCode listing")
            finally:
                service_log.close()
                if service.poll() is None:
                    os.killpg(service.pid, signal.SIGTERM)
                    try:
                        service.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(service.pid, signal.SIGKILL)
                        service.wait(timeout=5)
                report_path.write_text(json.dumps(report), encoding="utf-8")
                report_path.chmod(0o600)
            """),
        encoding="utf-8",
    )
    driver_path.chmod(0o600)

    v2_data_home = v2_config_dir / "data"
    (v2_data_home / "opencode").mkdir(parents=True, mode=0o700)
    bwrap_env = {
        "HOME": "/home/felni/private-home",
        "PATH": "/opencode-bin:/usr/bin:/bin",
        "XDG_CONFIG_HOME": "/home/felni/private-home/xdg-config",
        "XDG_DATA_HOME": "/home/felni/private-home/cao/opencode-v2/data",
        "XDG_STATE_HOME": "/home/felni/private-home/xdg-state",
        "OPENCODE_CONFIG": "/home/felni/private-home/cao/opencode-v2/opencode.json",
        "OPENCODE_CONFIG_DIR": "/home/felni/private-home/cao/opencode-v2",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "DO_NOT_TRACK": "1",
        "CI": "1",
        "TERM": "dumb",
    }
    bwrap_args = [
        bwrap,
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--die-with-parent",
        "--new-session",
        "--tmpfs",
        "/",
        "--dir",
        "/usr",
        "--ro-bind",
        "/usr",
        "/usr",
        "--dir",
        "/lib",
        "--ro-bind",
        "/lib",
        "/lib",
        "--dir",
        "/lib64",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--dir",
        "/bin",
        "--ro-bind",
        "/bin",
        "/bin",
        "--dir",
        "/etc",
        "--ro-bind",
        "/etc",
        "/etc",
        "--dir",
        "/tmp",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/home",
        "--tmpfs",
        "/home",
        "--dir",
        "/home/felni",
        "--bind",
        str(private_home),
        "/home/felni/private-home",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/work",
        "--bind",
        str(project_dir),
        "/work",
        "--dir",
        "/opencode-bin",
        "--ro-bind",
        opencode,
        "/opencode-bin/opencode",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--chdir",
        "/work",
        "--clearenv",
    ]
    for key, value in bwrap_env.items():
        bwrap_args.extend(("--setenv", key, value))
    bwrap_args.extend(
        (
            "--",
            "/usr/bin/python3",
            "/home/felni/private-home/opencode-agent-list-driver.py",
            agent_id,
        )
    )
    listed = subprocess.run(
        bwrap_args,
        capture_output=True,
        text=True,
        timeout=45,
        env={"LC_ALL": "C"},
    )
    report_path = v2_config_dir / "debug-agents-result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    assert listed.returncode == 0, f"isolated OpenCode agent listing failed: {report}"
    assert report.get("service_ready") is True
    assert report.get("service_auth_ready") is True
    assert report.get("service_context_warmed") is True
    assert report.get("command_exit") == 0
    assert report.get("target_found") is True, f"translated CAO agent missing: {report}"


# ---------------------------------------------------------------------------
# Scenario (a): fresh install creates agent .md + fresh opencode.json
# ---------------------------------------------------------------------------


class TestFreshInstall:
    def test_exit_code_zero(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(install_workspace["local_store"] / "test-agent.md")

        result = runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        assert result.exit_code == 0, result.output

    def test_agent_md_written(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(install_workspace["local_store"] / "test-agent.md")

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        agent_file = install_workspace["agents_dir"] / "test-agent.md"
        assert agent_file.exists()

    def test_agent_md_has_valid_frontmatter(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            description="A developer agent",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        assert post.metadata["description"] == "A developer agent"
        assert post.metadata["mode"] == "all"
        assert "permission" in post.metadata

    def test_agent_md_has_body(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            body="You are a test sentinel agent.",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        # Body must contain the raw profile.system_prompt — NOT the baked skill catalog.
        assert "You are a test sentinel agent." in post.content
        # Skills are delivered via the native skills/ symlink; the catalog must NOT
        # be baked into the system prompt.
        assert "## Available Skills" not in post.content

    def test_ensure_skills_symlink_called(
        self, runner: CliRunner, install_workspace: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ):
        """ensure_skills_symlink() must be called once per opencode_cli install."""
        calls: list[int] = []
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.install_service.ensure_skills_symlink",
            lambda: calls.append(1),
        )
        _write_profile(install_workspace["local_store"] / "test-agent.md")

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        assert calls, "ensure_skills_symlink() was not called during opencode_cli install"

    def test_no_model_in_frontmatter(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        """model goes via --model at launch time, never in frontmatter."""
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            extra_frontmatter="model: anthropic/claude-sonnet-4-6\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        assert "model" not in post.metadata

    def test_agents_dir_auto_created(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(install_workspace["local_store"] / "test-agent.md")
        assert not install_workspace["agents_dir"].exists()

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        assert install_workspace["agents_dir"].exists()

    def test_no_opencode_json_without_mcp(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        """Scenario (e): agent without MCP servers must not create opencode.json."""
        _write_profile(install_workspace["local_store"] / "test-agent.md")

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        assert not install_workspace["config_file"].exists()

    def test_success_message_in_output(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(install_workspace["local_store"] / "test-agent.md")

        result = runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        assert "installed successfully" in result.output
        assert "opencode_cli agent:" in result.output


# ---------------------------------------------------------------------------
# Scenario (b): re-install is idempotent
# ---------------------------------------------------------------------------


class TestIdempotentInstall:
    def test_two_installs_produce_identical_agent_md(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        _write_profile(install_workspace["local_store"] / "test-agent.md")

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
        first = (install_workspace["agents_dir"] / "test-agent.md").read_bytes()

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
        second = (install_workspace["agents_dir"] / "test-agent.md").read_bytes()

        assert first == second

    def test_two_installs_produce_identical_opencode_json(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
        first = install_workspace["config_file"].read_bytes()

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
        second = install_workspace["config_file"].read_bytes()

        assert first == second


# ---------------------------------------------------------------------------
# Scenario (c): permission frontmatter always emits allow/deny (no ask)
# ---------------------------------------------------------------------------


class TestPermissionTranslation:
    def test_allowed_tools_emit_allow(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            extra_frontmatter="allowedTools:\n  - fs_read\n  - execute_bash\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        perm = post.metadata["permission"]
        assert perm["read"] == "allow"
        assert perm["bash"] == "allow"

    def test_never_emits_ask(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        """CAO owns the permission decision — ``ask`` must never be written."""
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            extra_frontmatter="allowedTools:\n  - fs_read\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        perm = post.metadata["permission"]
        assert "ask" not in perm.values()

    def test_wildcard_allows_all(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            extra_frontmatter="allowedTools:\n  - '*'\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        perm = post.metadata["permission"]
        assert all(v == "allow" for v in perm.values())

    def test_hardcoded_denies_always_present(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        """task/question/webfetch/websearch/codesearch are always denied (unless *)."""
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            extra_frontmatter="allowedTools:\n  - '@builtin'\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        perm = post.metadata["permission"]
        for tool in ("task", "question", "webfetch", "websearch", "codesearch"):
            assert perm[tool] == "deny", f"{tool} should always be deny"

    def test_unpermitted_cao_tools_emit_deny(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            extra_frontmatter="allowedTools:\n  - fs_read\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        post = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
        perm = post.metadata["permission"]
        assert perm["bash"] == "deny"
        assert perm["write"] == "deny"
        assert perm["edit"] == "deny"


# ---------------------------------------------------------------------------
# Scenario (d): MCP servers produce correct opencode.json blocks
# ---------------------------------------------------------------------------


class TestMcpWiring:
    def _mcp_profile(self, profile_path: Path) -> None:
        _write_profile(
            profile_path,
            name="test-agent",
            mcp_servers=("  cao-mcp-server:\n" "    command: cao-mcp-server\n" "    type: local\n"),
        )

    def test_mcp_server_added_to_top_level_mcp(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        self._mcp_profile(install_workspace["local_store"] / "test-agent.md")

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert "cao-mcp-server" in data["mcp"]

    def test_mcp_server_default_denied_in_top_level_tools(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        self._mcp_profile(install_workspace["local_store"] / "test-agent.md")

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert data["tools"]["cao-mcp-server*"] is False

    def test_mcp_server_re_enabled_per_agent(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        self._mcp_profile(install_workspace["local_store"] / "test-agent.md")

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert data["agent"]["test-agent"]["tools"]["cao-mcp-server*"] is True

    def test_multiple_mcp_servers(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            mcp_servers=("  srv-a:\n    command: srv-a\n" "  srv-b:\n    command: srv-b\n"),
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert data["tools"]["srv-a*"] is False
        assert data["tools"]["srv-b*"] is False
        agent_tools = data["agent"]["test-agent"]["tools"]
        assert agent_tools["srv-a*"] is True
        assert agent_tools["srv-b*"] is True


# ---------------------------------------------------------------------------
# Scenario (e): agent without MCP — already covered in TestFreshInstall
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scenario (f): existing user-authored entries in opencode.json are preserved
# ---------------------------------------------------------------------------


class TestPreserveExistingConfig:
    def test_user_mcp_entry_preserved(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        # Pre-write a config with a user-owned entry
        install_workspace["config_file"].parent.mkdir(parents=True, exist_ok=True)
        install_workspace["config_file"].write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "mcp": {"user-server": {"type": "local", "command": "user-srv"}},
                    "tools": {"user-server*": False},
                }
            ),
            encoding="utf-8",
        )
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert "user-server" in data["mcp"], "user-authored MCP entry must survive"
        assert data["tools"]["user-server*"] is False, "user tools entry must survive"
        assert "cao-mcp-server" in data["mcp"], "new entry must also be present"

    def test_user_agent_entry_preserved(self, runner: CliRunner, install_workspace: Dict[str, Any]):
        install_workspace["config_file"].parent.mkdir(parents=True, exist_ok=True)
        install_workspace["config_file"].write_text(
            json.dumps(
                {
                    "mcp": {"cao-mcp-server": {"command": "cao-mcp-server"}},
                    "tools": {"cao-mcp-server*": False},
                    "agent": {
                        "other-agent": {"tools": {"cao-mcp-server*": True}},
                    },
                }
            ),
            encoding="utf-8",
        )
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert "other-agent" in data["agent"], "pre-existing agent entry must survive"
        assert "test-agent" in data["agent"], "new agent entry must also be present"


# ---------------------------------------------------------------------------
# Scenario: agent ID parity (filename === opencode.json key), and the refusal of
# a namespaced frontmatter name
# ---------------------------------------------------------------------------


class TestAgentIdParity:
    """One sanitized agent ID must be used for both the .md filename and the
    ``agent.<id>.tools`` key in opencode.json, so the value passed to
    ``opencode --agent <id>`` at runtime lines up with its MCP grants."""

    def test_filename_and_config_key_agree(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            name="test-agent",
            mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n",
        )

        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        assert (install_workspace["agents_dir"] / "test-agent.md").exists()
        data = json.loads(install_workspace["config_file"].read_text())
        assert "test-agent" in data["agent"], "agent ID must be the opencode.json key"
        assert data["agent"]["test-agent"]["tools"]["cao-mcp-server*"] is True


class TestNamespacedNameIsRefused:
    """A frontmatter ``name:`` containing a path separator is no longer installable.

    Security fix for GHSA-6m35-gcf5-xm75: the resolved ``name:`` is
    attacker-controlled for URL installs and feeds the context-copy filename, so
    it is now validated as a single path segment. Namespaced names like
    ``my/agent`` are rejected as a consequence — they only ever worked when the
    intermediate context directory happened to already exist, and permitting them
    is exactly what let a hostile ``../..`` name escape.

    Replaces the former ``TestSlashSafeAgentId``, which asserted the now-removed
    behavior that such a profile installs successfully.
    """

    def _write_slash_profile(self, install_workspace: Dict[str, Any]) -> None:
        _write_profile(
            install_workspace["local_store"] / "my__agent.md",
            name="my/agent",
            mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n",
        )
        # The pre-fix code needed context_dir/my/ to exist for the write to land.
        # Create it deliberately, so the refusal below is provably the name
        # validation and not an incidental missing-parent-directory failure.
        (install_workspace["context_dir"] / "my").mkdir(parents=True, exist_ok=True)

    def test_install_reports_the_refusal(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        self._write_slash_profile(install_workspace)

        result = runner.invoke(install, ["my__agent", "--provider", "opencode_cli"])

        # NOTE: asserted on the message, not the exit code. `cao install` echoes
        # "Error: ..." and returns, so a failed install still exits 0 — a
        # pre-existing CLI bug (see cli/commands/install.py, `if not
        # result.success: ... return`) that is out of scope for this fix.
        assert "Error" in result.output
        assert "profile name" in result.output
        assert "my/agent" in result.output

    def test_no_agent_file_or_context_copy_is_written(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        self._write_slash_profile(install_workspace)

        runner.invoke(install, ["my__agent", "--provider", "opencode_cli"])

        assert not (install_workspace["agents_dir"] / "my__agent.md").exists()
        # nothing landed in the pre-created subdirectory either
        assert list((install_workspace["context_dir"] / "my").iterdir()) == []
        assert not install_workspace["config_file"].exists()


# ---------------------------------------------------------------------------
# Scenario: reinstalling without MCP strips stale agent.<id>.tools
# ---------------------------------------------------------------------------


class TestStaleMcpGrantsRemoved:
    def test_reinstall_without_mcp_removes_agent_tools(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        # First install: agent has an MCP server → agent.<id>.tools is written.
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n",
        )
        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert "test-agent" in data.get("agent", {}), "precondition: agent entry present"

        # Second install: same agent, MCP servers removed from the profile.
        _write_profile(
            install_workspace["local_store"] / "test-agent.md",
            mcp_servers="",
        )
        runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])

        data = json.loads(install_workspace["config_file"].read_text())
        assert "test-agent" not in data.get(
            "agent", {}
        ), "stale agent.<id>.tools entry must be removed on reinstall without MCP"


# ---------------------------------------------------------------------------
# Optional live smoke test: install files are in OpenCode's discovery directory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("CAO_TEST_OPENCODE_BINARY") is None and shutil.which("opencode") is None,
    reason="opencode binary not on PATH",
)
class TestOpencodeAgentListIntegration:
    """Verify an installed agent through the matching OpenCode runtime path."""

    def test_installed_agent_visible_in_opencode_list(
        self, runner: CliRunner, install_workspace: Dict[str, Any]
    ):
        local_store = install_workspace["local_store"]
        agents_dir = install_workspace["agents_dir"]
        config_file = install_workspace["config_file"]
        config_dir = agents_dir.parent
        agent_id = f"smoke-test-agent-{uuid.uuid4().hex[:12]}"

        _write_profile(
            local_store / f"{agent_id}.md",
            name=agent_id,
            mcp_servers="  cao-mcp-server:\n    command: /bin/false\n",
        )

        result = runner.invoke(install, [agent_id, "--provider", "opencode_cli"])
        assert result.exit_code == 0, result.output

        # First assert the installer-owned V1 files and grants. V2 translates this
        # profile into its own private per-terminal config before listing agents.
        agent_file = agents_dir / f"{agent_id}.md"
        assert agent_file.is_file()
        agent = frontmatter.loads(agent_file.read_text(encoding="utf-8"))
        assert agent.metadata["description"] == "Test agent"
        assert agent.metadata["mode"] == "all"

        assert config_file.parent == config_dir
        config = json.loads(config_file.read_text(encoding="utf-8"))
        assert config["mcp"]["cao-mcp-server"]["type"] == "local"
        assert config["tools"]["cao-mcp-server*"] is False
        assert config["agent"][agent_id]["tools"]["cao-mcp-server*"] is True

        opencode = os.environ.get("CAO_TEST_OPENCODE_BINARY") or shutil.which("opencode")
        assert opencode is not None

        private_home = install_workspace["local_store"].parent / "opencode-home"
        xdg_config = private_home / "xdg-config"
        xdg_data = private_home / "xdg-data"
        xdg_state = private_home / "xdg-state"
        for path in (private_home, xdg_config, xdg_data, xdg_state):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)

        env = {
            "HOME": str(private_home),
            "PATH": os.environ.get("PATH", ""),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_STATE_HOME": str(xdg_state),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "DO_NOT_TRACK": "1",
            "CI": "1",
            "TERM": "dumb",
        }

        root_help = subprocess.run(
            [opencode, "--help"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert root_help.returncode == 0, root_help.stderr

        legacy_env = {
            **env,
            "OPENCODE_CONFIG": str(config_file),
            "OPENCODE_CONFIG_DIR": str(config_dir),
        }

        if _help_lists_subcommand(root_help.stdout + root_help.stderr, "agent"):
            agent_help = subprocess.run(
                [opencode, "agent", "--help"],
                capture_output=True,
                text=True,
                env=legacy_env,
                timeout=15,
            )
            assert agent_help.returncode == 0, agent_help.stderr
            if _help_lists_subcommand(agent_help.stdout + agent_help.stderr, "list"):
                proc = subprocess.run(
                    [opencode, "agent", "list"],
                    capture_output=True,
                    text=True,
                    env=legacy_env,
                    timeout=60,
                )
                assert proc.returncode == 0, proc.stderr or proc.stdout
                assert re.search(rf"(?m)^\s*{re.escape(agent_id)}(?:\s|$)", proc.stdout)
                return

        if _help_lists_subcommand(root_help.stdout + root_help.stderr, "debug"):
            debug_help = subprocess.run(
                [opencode, "debug", "--help"],
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            assert debug_help.returncode == 0, debug_help.stderr
            debug_output = debug_help.stdout + debug_help.stderr
            if _help_lists_subcommand(debug_output, "agents"):
                _run_v2_debug_agent_list(opencode, agent_id, agents_dir, config_file, private_home)
                return
            if _help_lists_subcommand(debug_output, "agent"):
                details = subprocess.run(
                    [opencode, "debug", "agent", agent_id],
                    capture_output=True,
                    text=True,
                    env=legacy_env,
                    timeout=60,
                )
                assert details.returncode == 0, details.stderr or details.stdout
                resolved = json.loads(details.stdout)
                assert resolved["name"] == agent_id
                assert resolved["mode"] == "all"
                return

        pytest.skip(
            "This OpenCode version exposes no supported agent listing command; "
            "the installed profile and tool grants were validated above."
        )


@pytest.mark.parametrize("profile", ["unknown", None])
def test_native_install_rejects_invalid_policy_before_changing_artifacts(
    runner: CliRunner, install_workspace: Dict[str, Any], profile
):
    source = install_workspace["local_store"] / "test-agent.md"
    _write_profile(
        source,
        mcp_servers=(
            "  cao-mcp-server:\n    command: cao-mcp-server\n"
            f"    cao_tool_profile: {json.dumps(profile)}\n"
        ),
    )
    original_source = source.read_bytes()
    agent = install_workspace["agents_dir"] / "test-agent.md"
    agent.parent.mkdir(parents=True)
    agent.write_bytes(b"previous native agent\n")
    config = install_workspace["config_file"]
    config.write_text('{"mcp": {}, "agent": {}}\n')
    original_config = config.read_bytes()
    result = runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
    assert result.exit_code != 0
    assert "tool profile" in result.output
    assert agent.read_bytes() == b"previous native agent\n"
    assert config.read_bytes() == original_config
    assert source.read_bytes() == original_source
    assert list(install_workspace["context_dir"].iterdir()) == []


def test_native_translation_cannot_silently_discard_profile_metadata():
    from cli_agent_orchestrator.utils.opencode_config import translate_mcp_server_config

    with pytest.raises(ValueError, match="per-agent propagation"):
        translate_mcp_server_config(
            {"command": "cao-mcp-server", "cao_tool_profile": "interaction"}
        )


@pytest.mark.parametrize("field", ["env", "environment"])
@pytest.mark.parametrize(
    "marker", ["CAO_INTERNAL_MCP_TOOL_PROFILE", "CAO_INTERNAL_INTERACTION_PULL_FLOW_ONLY"]
)
def test_native_install_rejects_reserved_policy_in_shared_environment(
    runner: CliRunner, install_workspace: Dict[str, Any], field, marker
):
    source = install_workspace["local_store"] / "test-agent.md"
    _write_profile(
        source,
        mcp_servers=(
            f"  cao-mcp-server:\n    command: cao-mcp-server\n    {field}:\n      {marker}: '1'\n"
        ),
    )
    before = source.read_bytes()
    result = runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
    assert result.exit_code != 0
    assert "per-agent propagation" in result.output
    assert source.read_bytes() == before
    assert not install_workspace["agents_dir"].exists()
    assert not install_workspace["config_file"].exists()


def test_native_install_rejects_env_pairs_before_dict_coercion(
    runner: CliRunner, install_workspace: Dict[str, Any]
):
    source = install_workspace["local_store"] / "test-agent.md"
    _write_profile(
        source,
        mcp_servers=(
            "  cao-mcp-server:\n    command: cao-mcp-server\n    env:\n"
            "      - [CAO_INTERNAL_MCP_TOOL_PROFILE, collaboration]\n"
        ),
    )
    before = source.read_bytes()
    result = runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
    assert result.exit_code != 0
    assert "environment must be a mapping" in result.output
    assert source.read_bytes() == before
    assert not install_workspace["agents_dir"].exists()
    assert not install_workspace["config_file"].exists()


@pytest.mark.parametrize(
    ("allowed", "effect"),
    [
        ([], "deny"),
        (["@builtin"], "deny"),
        (["@cao-mcp-server"], "deny"),
        (["fs_*"], "deny"),
        (["*"], "allow"),
    ],
)
def test_installed_execute_policy_survives_v2_translation(
    runner, install_workspace, tmp_path, allowed, effect
):
    from cli_agent_orchestrator.utils.opencode_v2 import (
        _effective_permission,
        write_v2_configuration,
    )

    _write_profile(
        install_workspace["local_store"] / "test-agent.md",
        extra_frontmatter="allowedTools: " + json.dumps(allowed) + "\n",
    )
    installed = runner.invoke(install, ["test-agent", "--provider", "opencode_cli"])
    assert installed.exit_code == 0, installed.output
    agent = frontmatter.loads((install_workspace["agents_dir"] / "test-agent.md").read_text())
    assert agent.metadata["permission"]["execute"] == effect
    assert agent.metadata["permission"]["skill"] == "allow"
    assert agent.metadata["permission"]["todowrite"] == "allow"

    mcp_granted = "*" in allowed or "@cao-mcp-server" in allowed
    source_config = {
        "mcp": {"cao-mcp-server": {"type": "local", "command": ["cao-mcp-server"]}},
        "agent": {"test-agent": {"tools": {"cao-mcp-server*": mcp_granted}}},
    }
    destination = tmp_path / "translated-v2"
    write_v2_configuration(
        destination,
        source_agents=install_workspace["agents_dir"],
        source_config=source_config,
        session_id="execute-policy-test",
    )
    translated = json.loads((destination / "opencode.json").read_text())
    rules = translated["agents"]["test-agent"]["permissions"]
    assert {"action": "execute", "resource": "*", "effect": effect} in rules
    assert {"action": "skill", "resource": "*", "effect": "allow"} in rules
    assert {"action": "todowrite", "resource": "*", "effect": "allow"} in rules
    assert {
        "action": "cao-mcp-server_*",
        "resource": "*",
        "effect": "allow" if mcp_granted else "deny",
    } in rules
    assert translated["mcp"]["servers"]["cao-mcp-server"]["codemode"] is False
    assert _effective_permission(rules, "execute") == effect
    assert _effective_permission(rules, "skill") == "allow"
    assert _effective_permission(rules, "todowrite") == "allow"
    assert _effective_permission(rules, "cao-mcp-server_send_message") == (
        "allow" if mcp_granted else "deny"
    )
