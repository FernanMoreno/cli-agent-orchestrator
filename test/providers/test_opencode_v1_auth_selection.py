"""Native v1 must not replace the active vendor key with stale legacy JSON."""

import asyncio
import json
import os
from pathlib import Path
from test.providers.test_opencode_profile_initialization import launch

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_config

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


class ExportProcess:
    def __init__(self, out, err, code):
        self.output = out.encode(), err.encode()
        self.code = code
        self.returncode = None

    async def communicate(self):
        self.returncode = self.code
        return self.output


@pytest.fixture
def auth_store(launch, monkeypatch):
    instance, _, _ = launch
    instance._model = "opencode-go/exact-model"
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    config = {"mcp": {}, "agent": {}}
    monkeypatch.setattr(opencode_config, "read_config", lambda: config)
    directory = Path(os.environ["XDG_DATA_HOME"]) / "opencode"
    directory.mkdir(parents=True)
    legacy = directory / "auth.json"
    legacy.write_text(
        json.dumps(
            {
                "opencode-go": {"type": "api", "key": "old-synthetic"},
                "other": {"type": "api", "key": "unselected-synthetic"},
            }
        )
    )

    def create(values=None, *, custom=False, wal=False, session_only=False, malformed=False):
        db = directory / ("operator-alternate.db" if custom else "opencode.db")
        if custom:
            monkeypatch.setenv("OPENCODE_DB", str(db))
        db.write_bytes(b"synthetic opaque vendor credential store")
        db.chmod(0o600)
        if wal:
            Path(str(db) + "-wal").write_bytes(b"synthetic opaque vendor write-ahead state")
        rows = (
            values
            if values is not None
            else [
                ("opencode-go", {"type": "key", "key": "inactive-synthetic"}, 0),
                ("opencode-go", {"type": "key", "key": "active-synthetic"}, 1),
                ("other", {"type": "key", "key": "other-active-synthetic"}, 1),
            ]
        )

        async def export(*command, env, cwd, **kwargs):
            assert list(command[:4]) == ["/opt/bin/opencode", "db", "--format", "json"]
            assert len(command) == 5 and "SELECT" in command[4]
            copied = Path(env["OPENCODE_DB"])
            assert copied.parent.name == "auth-source" and copied.read_bytes() == db.read_bytes()
            assert Path(cwd) == copied.parent
            assert Path(env["HOME"]).is_relative_to(copied.parent)
            assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
            assert "OPENCODE_AUTH_CONTENT" not in env
            assert "ANTHROPIC_API_KEY" not in env
            assert kwargs.get("start_new_session") is True
            assert kwargs["stdout"] == asyncio.subprocess.PIPE
            assert kwargs["stderr"] == asyncio.subprocess.PIPE
            if session_only:
                result = [{"kind": "summary", "total": 0, "value": None}]
            elif malformed:
                return ExportProcess("", "vendor store format error", 1)
            else:
                query = command[4]
                provider = (
                    "opencode-go"
                    if "'opencode-go'" in query
                    else "other" if "'other'" in query else None
                )
                selected = [value for name, value, active in rows if name == provider and active]
                result = [{"kind": "summary", "total": len(rows), "value": None}]
                result += [
                    {
                        "kind": "selected",
                        "total": None,
                        "value": value if isinstance(value, str) else json.dumps(value),
                    }
                    for value in selected[:2]
                ]
            return ExportProcess(json.dumps(result), "", 0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", export)
        return db

    return config, legacy, create


@pytest.mark.parametrize("custom,wal", [(False, False), (False, True), (True, True)])
async def test_v1_stages_only_active_selected_key_and_preserves_source(
    launch, auth_store, custom, wal
):
    instance, backend, _ = launch
    _, legacy, create = auth_store
    db = create(custom=custom, wal=wal)
    before = {p: p.read_bytes() for p in [db, Path(str(db) + "-wal"), legacy] if p.exists()}
    assert await instance.initialize()
    private = instance._v1_config_dir
    leaf = private / "data/opencode/auth.json"
    assert json.loads(leaf.read_text()) == {
        "opencode-go": {"type": "api", "key": "active-synthetic"}
    }
    assert leaf.stat().st_mode & 0o077 == 0
    assert not (private / "auth-source").exists()
    assert all(p.read_bytes() == value for p, value in before.items())
    assert "active-synthetic" not in backend.send_keys.call_args.args[2]
    instance.cleanup()
    assert not private.exists() and all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("selection", ["flag", "agent", "global"])
async def test_v1_active_key_uses_effective_model_precedence(launch, auth_store, selection):
    instance, _, _ = launch
    config, _, create = auth_store
    create()
    config["model"] = "other/global-model"
    if selection == "agent":
        instance._model = None
        (opencode_cli.OPENCODE_AGENTS_DIR / "reviewer.md").write_text(
            "---\ndescription: Reviewer\nmodel: opencode-go/agent-model\n---\nReview."
        )
    elif selection == "global":
        instance._model = None
    else:
        config["agent"]["reviewer"] = {"model": "other/agent-model"}
    assert await instance.initialize()
    actual = json.loads((instance._v1_config_dir / "data/opencode/auth.json").read_text())
    expected = (
        {"other": {"type": "api", "key": "other-active-synthetic"}}
        if selection == "global"
        else {"opencode-go": {"type": "api", "key": "active-synthetic"}}
    )
    assert actual == expected


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "multiple",
        "oauth",
        "bad-json",
        "bad-key",
        "bad-type",
        "malformed-table",
        "unknown-model",
        "inactive",
    ],
)
async def test_v1_current_store_failure_never_falls_back_to_legacy(launch, auth_store, case):
    instance, backend, _ = launch
    _, legacy, create = auth_store
    before = legacy.read_bytes()
    value = {"type": "key", "key": "active-synthetic"}
    rows = [("opencode-go", value, 1)]
    if case == "missing":
        rows = [("other", value, 1)]
    elif case == "inactive":
        rows = [("opencode-go", value, 0)]
    elif case == "multiple":
        rows *= 2
    elif case == "oauth":
        rows = [
            (
                "opencode-go",
                {"type": "oauth", "access": "synthetic", "refresh": "synthetic", "expires": 1},
                1,
            )
        ]
    elif case == "bad-json":
        rows = [("opencode-go", "invalid-json", 1)]
    elif case == "bad-key":
        rows = [("opencode-go", {"type": "key", "key": 1}, 1)]
    elif case == "bad-type":
        rows = [("opencode-go", {"type": "unknown", "key": "synthetic"}, 1)]
    elif case == "unknown-model":
        instance._model = None
    create(rows, malformed=case == "malformed-table")
    with pytest.raises(RuntimeError, match="OpenCode v1.*(credential|model|OAuth)"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None and legacy.read_bytes() == before


async def test_v1_session_database_keeps_legacy_auth_behavior(launch, auth_store):
    instance, _, _ = launch
    _, legacy, create = auth_store
    create(session_only=True)
    assert await instance.initialize()
    assert (instance._v1_config_dir / "data/opencode/auth.json").read_bytes() == legacy.read_bytes()


async def test_v1_explicit_inline_auth_retains_priority_over_current_store(
    launch, auth_store, monkeypatch
):
    instance, _, _ = launch
    _, _, create = auth_store
    create()
    inline = {"opencode-go": {"type": "api", "key": "explicit-synthetic"}}
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", json.dumps(inline))
    assert await instance.initialize()
    assert json.loads((instance._v1_config_dir / "data/opencode/auth.json").read_text()) == inline


async def test_v1_malformed_inline_falls_back_to_active_store_not_old_json(
    launch, auth_store, monkeypatch
):
    instance, _, _ = launch
    _, _, create = auth_store
    create()
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "invalid-json")
    assert await instance.initialize()
    assert json.loads((instance._v1_config_dir / "data/opencode/auth.json").read_text()) == {
        "opencode-go": {"type": "api", "key": "active-synthetic"}
    }


async def test_v1_current_vendor_database_symlink_is_refused(launch, auth_store):
    instance, backend, _ = launch
    _, _, create = auth_store
    db = create()
    original = db.with_name("real-vendor.db")
    db.rename(original)
    db.symlink_to(original)
    with pytest.raises(RuntimeError, match="credential|state"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None and original.exists()


@pytest.mark.parametrize("mode", ["cancel", "timeout", "force-stop"])
async def test_v1_export_stops_before_private_cleanup_and_keeps_control_responsive(
    launch, auth_store, monkeypatch, mode
):
    import signal

    instance, backend, _ = launch
    _, legacy, create = auth_store
    db = create()
    before = db.read_bytes(), legacy.read_bytes()
    started = asyncio.Event()
    signals = []
    snapshot = []

    class PendingExport:
        pid = 987654321
        returncode = None
        waits = 0

        async def communicate(self):
            started.set()
            if mode != "cancel":
                raise asyncio.TimeoutError()
            await asyncio.Event().wait()

        async def wait(self):
            assert snapshot[0].exists(), "Do not delete credentials while the reader is alive"
            self.waits += 1
            if mode == "force-stop" and self.waits == 1:
                raise asyncio.TimeoutError()
            self.returncode = -15
            return self.returncode

    child = PendingExport()

    async def spawn(*args, **kwargs):
        snapshot.append(Path(kwargs["env"]["OPENCODE_DB"]).parent)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    task = asyncio.create_task(instance.initialize())
    await asyncio.wait_for(started.wait(), 5)
    if mode == "cancel":
        # This progresses while the export is pending, with no wall-clock fudge.
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        with pytest.raises(RuntimeError, match="credential export.*timed out"):
            await asyncio.wait_for(task, 5)
    expected = [(child.pid, signal.SIGTERM)]
    if mode == "force-stop":
        expected.append((child.pid, signal.SIGKILL))
    assert signals == expected and child.returncode is not None
    assert not snapshot[0].exists() and instance._owned_v1_config_dir is None
    backend.send_keys.assert_not_called()
    assert (db.read_bytes(), legacy.read_bytes()) == before


@pytest.mark.parametrize(
    "case",
    ["boolean-total", "no-summary", "bad-kind", "bad-shape", "oversized", "wrong-missing-table"],
)
async def test_v1_export_refuses_corrupt_envelopes_and_unrelated_store_errors(
    launch, auth_store, monkeypatch, case
):
    instance, backend, _ = launch
    _, _, create = auth_store
    create()
    original = asyncio.create_subprocess_exec

    async def corrupt(*args, **kwargs):
        process = await original(*args, **kwargs)
        if case == "wrong-missing-table":
            process.code = 1
            process.output = b"", b"no such table: credential_shadow"
        elif case == "oversized":
            process.output = b" " * (1024 * 1024 + 1), b""
        else:
            rows = json.loads(process.output[0])
            if case == "boolean-total":
                rows[0]["total"] = True
            elif case == "no-summary":
                rows = rows[1:]
            elif case == "bad-kind":
                rows[0]["kind"] = "unknown"
            else:
                rows[0]["unexpected"] = True
            process.output = json.dumps(rows).encode(), b""
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", corrupt)
    with pytest.raises(RuntimeError, match="OpenCode v1.*credential"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None
