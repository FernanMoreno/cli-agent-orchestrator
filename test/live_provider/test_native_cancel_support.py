"""Test the actual common sandbox assembly, without starting any process."""

import json
from test.live_provider import claude_cancel_gateway, opencode_cancel_gateway
from test.live_provider.native_cancel_support import sha

import pytest

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "module,args,native",
    [
        (
            claude_cancel_gateway,
            ["--model", "claude-sonnet-4-6", "--permission-mode", "default", "--tools", ""],
            "/tmp/claude-native",
        ),
        (
            opencode_cancel_gateway,
            ["mini", "--standalone", "--model", "opencode-go/glm-5.3-flash"],
            "/tmp/opencode-native",
        ),
    ],
)
def test_both_adapters_use_same_ordered_masks_and_explicit_child_environment(
    tmp_path, monkeypatch, module, args, native
):
    root = tmp_path
    home = root / "case/home"
    home.mkdir(parents=True)
    binary = root / "binary"
    binary.write_bytes(b"synthetic standalone executable; never executed")
    config = home / "database.json"
    config.write_text('{"synthetic":true}')
    (home / ".cao-postgres").mkdir()
    repo = str(tmp_path / "canonical-repo")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "repo": repo,
                "case_kind": "preflight",
                "binary": str(binary),
                "binary_sha256": sha(binary),
                "gateway_sha256": sha(module.__file__),
            }
        )
    )
    for key, value in {
        "HOME": str(home),
        "CAO_TERMINAL_ID": "1234abcd",
        "CAO_DATABASE_CONFIG_FILE": str(config),
        "CAO_AUTH_TOKEN": "synthetic-secret",
        "ANTHROPIC_AUTH_TOKEN": "synthetic-secret",
        "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-secret",
        "OPENCODE_AUTH_CONTENT": "synthetic-secret",
    }.items():
        monkeypatch.setenv(key, value)
    seen = {}

    class CapturedExec(Exception):
        pass

    def execve(binary, command, env):
        seen.update(binary=binary, command=command, env=env)
        raise CapturedExec

    monkeypatch.setattr(module.os, "execve", execve)
    with pytest.raises(CapturedExec):
        module.wrapper(root, args)
    command, env = seen["command"], seen["env"]
    assert seen["binary"] == "/usr/bin/bwrap"
    assert "--unshare-all" in command and "--die-with-parent" in command
    assert command.index("/home/felni") < command.index(str(home)) < command.index(str(config))
    assert command[command.index(str(config)) - 2 : command.index(str(config)) + 1] == [
        "--ro-bind",
        "/dev/null",
        str(config),
    ]
    assert native in command
    assert env["PYTHONPATH"] == repo and env["HOME"] == str(home)
    assert all("synthetic-secret" not in value for value in env.values())
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "CAO_AUTH_TOKEN" not in env
    records = list((root / "observations/1234abcd").glob("*/wrapper.json"))
    assert len(records) == 1 and records[0].stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("provider", ["codex", "opencode_cli"])
def test_driver_rejects_descriptor_for_another_provider_before_any_activation(tmp_path, provider):
    from test.live_provider.native_cancel_adapters import CLAUDE
    from test.live_provider.native_concurrent_cancel import run_native_cancel_case

    (tmp_path / "manifest.json").write_text(json.dumps({"provider": provider}))
    with pytest.raises(AssertionError):
        run_native_cancel_case(tmp_path, None, CLAUDE)
    assert set(p.name for p in tmp_path.iterdir()) == {"manifest.json"}


def test_claude_private_onboarding_has_no_login_material_and_uses_restricted_permissions(tmp_path):
    from test.live_provider.native_cancel_adapters import CLAUDE

    CLAUDE.configure(tmp_path)
    for name in (".claude.json", ".claude/.claude.json"):
        path = tmp_path / name
        config = json.loads(path.read_text())
        assert config["hasCompletedOnboarding"] is True
        assert config["customApiKeyResponses"] == {
            "approved": ["synthetic-local-control"[-20:]],
            "rejected": [],
        }
        assert path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".claude/.credentials.json").exists()
    assert CLAUDE.profile_options == "permissionMode: default\n"


@pytest.mark.parametrize("before", [0, 1, 4])
@pytest.mark.parametrize(
    "mutation", [None, "no_increment", "skipped", "false_success", "bool_sequence"]
)
def test_one_input_is_bound_to_exact_increment_and_recorded_without_retry(before, mutation):
    from test.live_provider.native_cancel_support import send_turn_once

    calls, records = [], []
    value = {"success": mutation != "false_success", "turn_sequence": before + 1}
    if mutation == "no_increment":
        value["turn_sequence"] = before
    elif mutation == "skipped":
        value["turn_sequence"] += 1
    elif mutation == "bool_sequence":
        value["turn_sequence"] = True

    def request(method, tid, suffix="", **kwargs):
        calls.append((method, tid, suffix, kwargs))
        return {"turn_sequence": before} if method == "GET" else value

    def run():
        return send_turn_once(
            request, "owned", "controlled input", lambda *args: records.append(args)
        )

    if mutation is None:
        assert run() is value
    else:
        with pytest.raises(AssertionError):
            run()
    assert records == [("owned", before, value)]
    assert [c[0] for c in calls] == ["GET", "POST"]
    assert calls[-1][3] == {"params": {"message": "controlled input"}}
