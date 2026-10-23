"""Test-only production API worker with one private, content-free status probe."""

import argparse
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


def provider_snapshot(provider, current, history, terminal, model, deadline):
    """Observe the same registered production instance against fresh backend history."""
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    if current() is not provider:
        raise ValueError("probe_provider_changed")
    output = history(terminal.session_name, terminal.name, tail_lines=400)
    if current() is not provider:
        raise ValueError("probe_provider_changed")
    status = provider.get_status(output)
    stable = current() is provider
    observed = time.monotonic()
    return {
        "provider_instance_stable": stable,
        "provider_major_exact": type(getattr(provider, "_cli_major", None)) is int
        and provider._cli_major == 1,
        "provider_model_exact": getattr(provider, "_model", None) == model,
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
    if type(value) is not dict or set(value) != {"terminal_id", "model", "deadline", "nonce"}:
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
    return value


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
        )

    app.add_api_route("/__v1/human-status", observe, methods=["GET"], include_in_schema=False)
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
