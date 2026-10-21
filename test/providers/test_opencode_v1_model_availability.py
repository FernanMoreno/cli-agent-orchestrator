"""An unavailable explicit v1 model must never silently use another model."""

import asyncio
import json
import shlex
from pathlib import Path
from test.providers.test_opencode_profile_initialization import launch
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_config

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]
MODEL = "opencode-go/longcat-2.5-preview-free"


class NativeProcess:
    def __init__(self, output, code=0):
        self.returncode = code
        self.output = output

    async def communicate(self):
        return self.output, b"private vendor diagnostic must not escape"


@pytest.fixture
def model_checker(tmp_path, monkeypatch):
    instance = opencode_cli.OpenCodeCliProvider(
        "abcdef12", "private", "private", "reviewer", model=MODEL
    )
    instance._v1_config_dir = tmp_path
    instance._launch_runtime = ("/opt/pinned/opencode", {"PATH": "/opt/pinned:/usr/bin:/bin"})
    calls = []

    def install(output, code=0):
        async def spawn(*args, **kwargs):
            calls.append((args, kwargs))
            assert args[:2] == ("/bin/sh", "-c")
            words = shlex.split(args[2])
            assert words[-3:] == ["/opt/pinned/opencode", "models", "opencode-go"]
            assert "--agent" not in words and "--model" not in words
            assert "-u" in words and "OPENCODE_AUTH_CONTENT" in words
            assert "OPENCODE_CONFIG=" + str(tmp_path / "opencode.json") in words
            assert "XDG_DATA_HOME=" + str(tmp_path / "data") in words
            assert kwargs["cwd"] == str(tmp_path)
            assert kwargs["start_new_session"] is True
            assert kwargs["stdout"] == asyncio.subprocess.PIPE
            assert kwargs["stderr"] == asyncio.subprocess.PIPE
            return NativeProcess(output, code)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    return instance, calls, install


@pytest.mark.parametrize(
    "output,code",
    [
        (b"opencode/big-pickle\n", 0),
        (b"", 1),
        (MODEL.encode() + b"-other\n", 0),
        (MODEL.encode() + b"\n", 1),
        (b"\xff", 0),
    ],
)
async def test_missing_or_invalid_inventory_refuses_without_raw_error(model_checker, output, code):
    instance, calls, install = model_checker
    install(output, code)
    with pytest.raises(ValueError, match="model.*unavailable") as error:
        await instance._validate_v1_model_available(MODEL, timeout=1)
    assert "private vendor" not in str(error.value)
    assert len(calls) == 1


async def test_exact_inventory_matches_preserve_private_native_launch(model_checker, monkeypatch):
    instance, calls, install = model_checker
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", '{"operator":"must be unset by native launch"}')
    install(("other/model\n" + MODEL + "\n").encode())
    await instance._validate_v1_model_available(MODEL, timeout=1)
    assert len(calls) == 1


@pytest.mark.parametrize("model", ["invalid", "opencode-go/", "/model", "bad provider/model", True])
async def test_bad_model_selector_never_starts_native_cli(model_checker, model):
    instance, calls, install = model_checker
    install(b"")
    with pytest.raises(ValueError, match="provider/model"):
        await instance._validate_v1_model_available(model, timeout=1)
    assert not calls


@pytest.mark.parametrize("source", ["flag", "agent", "global"])
async def test_effective_model_checked_before_backend_and_after_private_auth(
    launch, monkeypatch, source
):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    config = (
        {"mcp": {}, "agent": {"reviewer": {"model": MODEL}}}
        if source == "agent"
        else {"mcp": {}, "model": MODEL}
    )
    monkeypatch.setattr(opencode_config, "read_config", lambda: config)
    if source == "flag":
        instance._model = MODEL
        config["model"] = "other/model"
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "{}")

    async def validate(model, *, timeout):
        assert model == MODEL and timeout > 0
        assert json.loads((instance._v1_config_dir / "data/opencode/auth.json").read_text()) == {}
        backend.send_keys.assert_not_called()

    check = AsyncMock(side_effect=validate)
    monkeypatch.setattr(instance, "_validate_v1_model_available", check)
    assert await instance.initialize()
    check.assert_awaited_once()
    backend.send_keys.assert_called_once()


async def test_unavailable_effective_model_cleans_before_launch(launch, monkeypatch):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    instance._model = MODEL
    monkeypatch.setattr(
        instance,
        "_validate_v1_model_available",
        AsyncMock(side_effect=ValueError("requested model is unavailable")),
    )
    with pytest.raises(ValueError, match="unavailable"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None
    assert not list((home / "opencode-v1").glob("terminal-*"))


async def test_probe_preserves_runtime_provider_env_and_removes_database_authority(
    model_checker, monkeypatch
):
    instance, calls, install = model_checker
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", "/private/database-config.json")
    monkeypatch.setenv("SYNTHETIC_API_KEY", "synthetic-env-key")
    install((MODEL + "\n").encode())
    await instance._validate_v1_model_available(MODEL, timeout=1)
    environment = calls[0][1]["env"]
    assert "CAO_DATABASE_CONFIG_FILE" not in environment
    assert environment["SYNTHETIC_API_KEY"] == "synthetic-env-key"
    assert environment["PATH"] == "/opt/pinned:/usr/bin:/bin"


async def test_default_without_explicit_model_does_not_probe(launch, monkeypatch):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    check = AsyncMock()
    monkeypatch.setattr(instance, "_validate_v1_model_available", check)
    assert await instance.initialize()
    check.assert_not_awaited()
    backend.send_keys.assert_called_once()


@pytest.mark.parametrize("state", ["no-private", "v2"])
async def test_probe_refuses_without_owned_native_v1_configuration(model_checker, state):
    instance, calls, install = model_checker
    install(b"")
    if state == "no-private":
        instance._v1_config_dir = None
    else:
        instance._cli_major = 2
    with pytest.raises(ValueError, match="private native v1"):
        await instance._validate_v1_model_available(MODEL, timeout=1)
    assert not calls


@pytest.mark.parametrize("failure", ["cancel", "timeout", "force-kill"])
async def test_probe_lifecycle_is_async_and_reaps_its_group(model_checker, monkeypatch, failure):
    import signal

    instance, _, _ = model_checker
    entered = asyncio.Event()
    events = []

    class HangingProcess:
        pid = 987654321
        returncode = None
        waits = 0

        async def communicate(self):
            entered.set()
            if failure == "timeout":
                raise asyncio.TimeoutError()
            await asyncio.Future()

        async def wait(self):
            self.waits += 1
            events.append("wait")
            if failure == "force-kill" and self.waits == 1:
                raise asyncio.TimeoutError()
            self.returncode = -signal.SIGKILL if failure == "force-kill" else -signal.SIGTERM
            return self.returncode

    process = HangingProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(opencode_cli.os, "killpg", lambda pid, signum: events.append((pid, signum)))
    task = asyncio.create_task(instance._validate_v1_model_available(MODEL, timeout=1))
    await asyncio.wait_for(entered.wait(), 1)
    if failure != "timeout":
        # Reaching here while communicate is waiting proves loop responsiveness.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match="timed out"):
            await task
    assert events[0] == (process.pid, signal.SIGTERM)
    assert process.returncode is not None
    assert events[-1] == "wait"
    if failure == "force-kill":
        assert events == [
            (process.pid, signal.SIGTERM),
            "wait",
            (process.pid, signal.SIGKILL),
            "wait",
        ]


async def test_cancelled_inventory_cleans_private_provider_state(launch, monkeypatch):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    instance._model = MODEL
    monkeypatch.setattr(
        instance, "_validate_v1_model_available", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None


async def test_oversized_native_inventory_is_refused(model_checker):
    instance, _, install = model_checker
    install((MODEL + "\n").encode() + b"x" * (1024 * 1024))
    with pytest.raises(ValueError, match="model.*unavailable"):
        await instance._validate_v1_model_available(MODEL, timeout=1)
