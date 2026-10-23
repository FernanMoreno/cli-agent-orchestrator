"""Test-only production API worker with one private, content-free status probe."""

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import stat
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace


def native_launch_model_exact(
    identities, binary, model, agent, alive, *, proc_root=Path("/proc"), peer_agents=None
):
    """Return booleans only for the exact owned native executable and argv."""
    try:
        expected = binary.stat()
        if peer_agents is not None and (
            type(peer_agents) is not frozenset
            or not peer_agents
            or agent not in peer_agents
            or any(type(name) is not str or not name for name in peer_agents)
        ):
            return False
        expected_agents = {
            os.fsencode(name) for name in (peer_agents if peer_agents is not None else {agent})
        }
        observed_agents = []
        for identity in identities:
            if identity.uid != os.getuid() or not alive(identity):
                return False
            process = proc_root / str(identity.pid)
            try:
                executable = (process / "exe").stat()
            except FileNotFoundError:
                return False
            if (executable.st_dev, executable.st_ino) != (expected.st_dev, expected.st_ino):
                continue
            with (process / "cmdline").open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536 or not raw.endswith(b"\0"):
                return False
            argv = raw[:-1].split(b"\0")
            arguments = {}
            for flag in (b"--model", b"--agent"):
                if argv.count(flag) != 1:
                    return False
                index = argv.index(flag)
                if index == 0 or index + 1 >= len(argv):
                    return False
                arguments[flag] = argv[index + 1]
            if (
                arguments[b"--model"] != os.fsencode(model)
                or arguments[b"--agent"] not in expected_agents
            ):
                return False
            if not alive(identity):
                return False
            observed_agents.append(arguments[b"--agent"])
        return sorted(observed_agents) == sorted(expected_agents)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def owned_model_exact(provider, model, binding):
    """Read only the original active launch through its exact restored owner."""
    from cli_agent_orchestrator.utils.opencode_ownership import _read_private

    try:
        ownership = provider._config_ownership()
        if (
            binding.get("launch_model_observed") is not True
            or not ownership.managed
            or not ownership._snapshot
            or ownership.terminal_id != provider.terminal_id
            or getattr(provider, "_model", None) not in (None, model)
        ):
            return False
        with ownership._transaction() as available:
            if not available:
                return False
            document = ownership._read()
            if (
                type(document) is not dict
                or document.get("phase") != "active"
                or type(document.get("claim")) is not dict
                or document["claim"] != ownership._bound
                or document["claim"].get("major") != 1
            ):
                return False
            root = ownership._root(document["claim"])
            if root is None:
                return False
            marker = _read_private(root / ".cao-ownership.json")
            if marker is None or hashlib.sha256(marker).hexdigest() != binding["ownership_sha256"]:
                return False
            with os.fdopen(
                os.open(root / "opencode.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
            ) as stream:
                before = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != os.getuid()
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_nlink != 1
                    or (before.st_dev, before.st_ino)
                    != (binding["config_device"], binding["config_inode"])
                    or before.st_size > 2097152
                ):
                    return False
                raw = stream.read(2097153)
                after = os.fstat(stream.fileno())
                stable_fields = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_nlink",
                    "st_uid",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if (
                    any(getattr(before, key) != getattr(after, key) for key in stable_fields)
                    or len(raw) != before.st_size
                    or hashlib.sha256(raw).hexdigest() != binding["config_sha256"]
                ):
                    return False
            config = json.loads(raw)
            agents = config.get("agent")
            profile = provider._agent_profile
            if (
                type(agents) is not dict
                or set(agents) != {profile}
                or type(agents[profile]) is not dict
            ):
                return False
            override = binding["launch_model"]
            if type(override) is not str or override != model or config.get("model") != override:
                return False
            effective = override or agents[profile].get("model") or config.get("model")
            return effective == model
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return False


def provider_snapshot(provider, current, history, terminal, model, deadline, *, model_identity):
    """Observe the same registered production instance against fresh backend history."""
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    if current() is not provider:
        raise ValueError("probe_provider_changed")
    output = history(terminal.session_name, terminal.name, tail_lines=400)
    if current() is not provider:
        raise ValueError("probe_provider_changed")
    status = provider.get_status(output)
    model_exact = model_identity()
    stable = current() is provider
    observed = time.monotonic()
    return {
        "provider_instance_stable": stable,
        "provider_major_exact": type(getattr(provider, "_cli_major", None)) is int
        and provider._cli_major == 1,
        "provider_model_exact": model_exact is True,
        "provider_status": status.name if isinstance(status, TerminalStatus) else "UNKNOWN",
        "observed_monotonic": observed,
        "deadline_met": observed < deadline,
        "raw_text_exported": False,
    }


def read_scope(path, root):
    if not path.is_absolute() or path.parent != root or root.is_symlink():
        raise ValueError("probe_scope_invalid")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        st = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_nlink != 1
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_size > 8192
        ):
            raise ValueError("probe_scope_invalid")
        value = json.load(stream)
    if type(value) is not dict or set(value) != {
        "terminal_id",
        "model",
        "deadline",
        "nonce",
        "config_sha256",
        "config_device",
        "config_inode",
        "ownership_sha256",
        "launch_model",
        "launch_model_observed",
    }:
        raise ValueError("probe_scope_invalid")
    if any(
        type(value[k]) is not str or not value[k] for k in ("terminal_id", "model", "nonce")
    ) or type(value["deadline"]) not in (int, float):
        raise ValueError("probe_scope_invalid")
    if (
        not math.isfinite(value["deadline"])
        or value["deadline"] <= 0
        or re.fullmatch(r"[0-9a-f]{32}", value["nonce"]) is None
        or re.fullmatch(r"[0-9a-f]{8}", value["terminal_id"]) is None
    ):
        raise ValueError("probe_scope_invalid")
    if value.get("launch_model_observed") is not True:
        raise ValueError("probe_scope_invalid")
    if type(value["launch_model"]) is not str or value["launch_model"] != value["model"]:
        raise ValueError("probe_scope_invalid")
    if any(
        type(value[k]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[k]) is None
        for k in ("config_sha256", "ownership_sha256")
    ) or any(
        type(value[k]) is not int or value[k] < minimum
        for k, minimum in (("config_device", 0), ("config_inode", 1))
    ):
        raise ValueError("probe_scope_invalid")
    return value


def register_private_probe(app, observe):
    """Keep this worker-only route reachable before the production root UI mount."""
    app.add_api_route("/__v1/human-status", observe, methods=["GET"], include_in_schema=False)
    app.router.routes.insert(0, app.router.routes.pop())


def main():
    from test.live_provider.interaction_api_worker import (
        _observe_service,
        _private_observation_path,
    )

    import uvicorn
    from fastapi import HTTPException, Request

    from cli_agent_orchestrator.api.main import app
    from cli_agent_orchestrator.backends.registry import get_backend
    from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider
    from cli_agent_orchestrator.services import terminal_service

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if os.environ.get("CAO_API_HOST") != "127.0.0.1" or os.environ.get("CAO_API_PORT") != str(
        args.port
    ):
        raise RuntimeError("probe_worker_bind_mismatch")
    root = Path(os.environ["CAO_RUN_ROOT"])
    scope_path = Path(os.environ["CAO_V1_HUMAN_SCOPE_FILE"])
    production_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def observed_lifespan(application):
        async with production_lifespan(application):
            _observe_service(application.state.interaction_service, _private_observation_path())
            yield

    app.router.lifespan_context = observed_lifespan

    def observe(request: Request):
        try:
            scope = read_scope(scope_path, root)
            if not hmac.compare_digest(request.headers.get("x-caos-probe", ""), scope["nonce"]):
                raise ValueError("probe_scope_invalid")
        except (ValueError, OSError):
            raise HTTPException(status_code=403, detail="probe_scope_invalid") from None
        data = terminal_service.get_terminal(scope["terminal_id"])
        terminal = SimpleNamespace(session_name=data["session_name"], name=data["name"])
        provider = terminal_service.provider_manager.get_provider(scope["terminal_id"])
        if not isinstance(provider, OpenCodeCliProvider):
            raise HTTPException(status_code=409, detail="probe_provider_unavailable")
        return provider_snapshot(
            provider,
            lambda: terminal_service.provider_manager.get_provider(scope["terminal_id"]),
            get_backend().get_history,
            terminal,
            scope["model"],
            scope["deadline"],
            model_identity=lambda: owned_model_exact(provider, scope["model"], scope),
        )

    register_private_probe(app, observe)
    uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            lifespan="on",
            access_log=False,
            log_level="critical",
        )
    ).run()


if __name__ == "__main__":
    main()
