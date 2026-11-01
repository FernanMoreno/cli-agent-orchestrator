"""Opt-in Bridge launcher: python -m cli_agent_orchestrator.runtime_channel.bridge_supervisor.

The generic supervisor proves only the owned process exit. No SQL credit is
returned here; an independently funded durable custodian is still required.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
from collections.abc import Mapping
from pathlib import Path

from cli_agent_orchestrator.services.resource_process_supervisor import (
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)


def launch_bridge(*, env: Mapping[str, str] | None = None) -> ResourceProcessSupervisor:
    environment = dict(os.environ if env is None else env)
    policy = environment.get("CAO_ADMISSION_POLICY_FILE", "")
    if (
        environment.get("CAO_ADMISSION_MODE") != "shared"
        or not policy.strip()
        or not Path(policy).is_absolute()
    ):
        raise ValueError("supervised Bridge requires shared admission and a private policy")
    from cli_agent_orchestrator.clients.database import main_target

    target = main_target()
    return ResourceProcessSupervisor.launch(
        [sys.executable, "-m", "cli_agent_orchestrator.runtime_channel.bridge"],
        expected_store_id=target.store_id,
        expected_target_identity_sha256=hashlib.sha256(
            json.dumps(target.identity, separators=(",", ":")).encode()
        ).hexdigest(),
        env=environment,
    )


def main() -> int:
    # Install before launch: a signal during post-Popen identity capture must
    # follow the same retained-owner handoff. Further signals cannot interrupt
    # the physical join already in progress.
    requested = False

    def shutdown(signum: int, _frame: object) -> None:
        nonlocal requested
        if not requested:
            requested = True
            raise SystemExit(128 + signum)

    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[signum] = signal.signal(signum, shutdown)
        return _run_owned_bridge()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _run_owned_bridge() -> int:
    owner: ResourceProcessSupervisor | None = None
    first_failure: BaseException | None = None
    try:
        owner = launch_bridge()
        owner.receive_binding()
        observation = owner.wait()
        if observation is None:
            raise ResourceProcessUnproven("Bridge exit observation is unavailable")
        return (
            observation.returncode if observation.returncode >= 0 else 128 - observation.returncode
        )
    except BaseException as failure:
        if owner is None:
            retained = getattr(failure, "resource_process_owner", None)
            if type(retained) is ResourceProcessSupervisor:
                owner = retained
        first_failure = failure
        raise
    finally:
        if owner is not None:
            try:
                try:
                    owner.send_signal(signal.SIGTERM)
                finally:
                    owner.wait()
                    owner.close()
            except BaseException:
                if first_failure is None:
                    raise
                first_failure.add_note(
                    "Bridge child cleanup is unconfirmed; credits remain charged."
                )


if __name__ == "__main__":
    raise SystemExit(main())
