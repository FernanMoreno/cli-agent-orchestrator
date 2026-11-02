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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from cli_agent_orchestrator.services.resource_process_supervisor import (
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)

if TYPE_CHECKING:
    from cli_agent_orchestrator.clients.postgres import Connection
    from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository


def launch_bridge(
    *, env: Mapping[str, str] | None = None, sql_broker: bool = False
) -> ResourceProcessSupervisor:
    if type(sql_broker) is not bool:
        raise ValueError("Bridge SQL broker mode must be explicit")
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
        **({"first_sql_grant": True, "sql_broker": True} if sql_broker else {}),
    )


@contextmanager
def funded_bridge(
    *,
    channel: Connection,
    repository: SqlIntentRepository,
    journal_id: str,
    intent_id: str,
    session_reservation: object,
    env: Mapping[str, str] | None = None,
) -> Iterator[ResourceProcessSupervisor]:
    """Launch inside the caller's still-active original funding scope.

    The caller schedules serve_child_sql_request on this same worker. This
    context creates no thread, control connection, root credit or refund.
    Exceptional startup retains the exact owner on the original exception.
    The child environment must preserve the parent's canonical database target;
    a different target is refused by the authenticated handshake, before SQL.
    """
    from cli_agent_orchestrator.clients.database import main_target
    from cli_agent_orchestrator.clients.postgres import Connection
    from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository

    if type(channel) is not Connection or type(repository) is not SqlIntentRepository:
        raise TypeError("Bridge requires an original funded channel and journal")
    if session_reservation is None:
        raise ValueError("Bridge requires a prepaid child session reservation")
    for value in (journal_id, intent_id):
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError("Bridge requires canonical journal and intent identities")
    channel._check_funded_worker()
    target = main_target()
    if target.scope != "main" or channel.target != target or repository.target != target:
        raise ValueError("Bridge funding requires the canonical main destination")
    owner: ResourceProcessSupervisor | None = None
    first_failure: BaseException | None = None
    try:
        owner = launch_bridge(env=env, sql_broker=True)
        owner.receive_binding_with_child_grant(
            channel=channel,
            repository=repository,
            journal_id=journal_id,
            intent_id=intent_id,
            destination=target,
            session_reservation=session_reservation,
        )
        yield owner
    except BaseException as failure:
        if owner is None:
            retained = getattr(failure, "resource_process_owner", None)
            if type(retained) is ResourceProcessSupervisor:
                owner = retained
        if owner is not None:
            setattr(failure, "resource_process_owner", owner)
        first_failure = failure
        raise
    finally:
        if owner is not None:
            try:
                try:
                    owner.send_signal(signal.SIGTERM)
                finally:
                    observation = owner.wait()
                    if observation is None and first_failure is None:
                        raise ResourceProcessUnproven("Bridge exit observation is unavailable")
                    owner.close()
            except BaseException as cleanup_failure:
                if first_failure is None:
                    setattr(cleanup_failure, "resource_process_owner", owner)
                    raise
                first_failure.add_note(
                    "Bridge child cleanup is unconfirmed; its owner and credits remain retained."
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
