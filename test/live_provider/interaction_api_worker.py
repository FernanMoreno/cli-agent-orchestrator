"""Owned child process serving the production API app for live acceptance."""

from __future__ import annotations

import argparse
import os
import stat
import threading
from pathlib import Path
from test.live_provider.interaction_agui_restart import (
    ack_identity_diagnostics,
    append_observation,
    read_observations,
)
from typing import Any, Mapping


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _question_versions(values: object) -> list[dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        raise RuntimeError("invalid_interaction_identity")
    result: list[dict[str, object]] = []
    for value in values:
        question_id = _field(value, "question_id")
        version = _field(value, "version")
        if (
            not isinstance(question_id, str)
            or not question_id
            or not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
        ):
            raise RuntimeError("invalid_interaction_identity")
        result.append({"question_id": question_id, "version": version})
    return result


def _private_observation_path() -> Path:
    run_root_value = os.environ.get("CAO_RUN_ROOT")
    audit_value = os.environ.get("CAO_INTERACTION_AUDIT_FILE")
    if not run_root_value or not audit_value:
        raise RuntimeError("interaction_observation_path_missing")
    run_root = Path(run_root_value).absolute()
    audit_path = Path(audit_value).absolute()
    if run_root.is_symlink() or audit_path.is_symlink() or audit_path.parent != run_root:
        raise RuntimeError("unsafe_interaction_observation_path")
    try:
        mode = stat.S_IMODE(run_root.stat().st_mode)
    except OSError as exc:
        raise RuntimeError("unsafe_interaction_observation_root") from exc
    if mode != 0o700:
        raise RuntimeError("unsafe_interaction_observation_root")
    return audit_path


def _observe_service(service: Any, audit_path: Path) -> None:
    observations = read_observations(audit_path)
    created = next(
        (item for item in reversed(observations) if item["event"] == "group_created"),
        None,
    )
    created_group_id = created.get("group_id") if created is not None else None
    created_questions = created.get("questions", []) if created is not None else []
    if not isinstance(created_questions, list):
        raise RuntimeError("invalid_interaction_observation")
    state: dict[str, object] = {
        "created_group_id": created_group_id,
        "created_questions": created_questions,
        "waited_group_id": None,
        "waited_answers": None,
    }
    lock = threading.RLock()

    create_group = service.create_group

    def observed_create_group(*args: object, **kwargs: object) -> object:
        result = create_group(*args, **kwargs)
        group_id = _field(result, "group_id")
        questions = _question_versions(_field(result, "questions"))
        if not isinstance(group_id, str) or not group_id:
            raise RuntimeError("invalid_interaction_identity")
        with lock:
            state["created_group_id"] = group_id
            state["created_questions"] = questions
            append_observation(
                audit_path,
                {"event": "group_created", "group_id": group_id, "questions": questions},
            )
        return result

    wait_for_answers = service.wait_for_answers

    async def observed_wait_for_answers(*args: object, **kwargs: object) -> object:
        result = await wait_for_answers(*args, **kwargs)
        group_id = _field(result, "group_id")
        result_state = _field(result, "state")
        if not isinstance(group_id, str) or result_state not in {
            "pending",
            "ready",
            "expired",
            "cancelled",
            "stale",
            "cycle_blocked",
        }:
            raise RuntimeError("invalid_interaction_wait_result")
        answers = _field(result, "answers")
        answer_versions = _question_versions(answers) if result_state == "ready" else []
        with lock:
            if result_state == "ready":
                state["waited_group_id"] = group_id
                state["waited_answers"] = (
                    tuple(answers) if isinstance(answers, (list, tuple)) else ()
                )
            append_observation(
                audit_path,
                {
                    "event": "wait_returned",
                    "group_id": group_id,
                    "state": result_state,
                    "question_versions": answer_versions,
                },
            )
        return result

    acknowledge_answers = service.acknowledge_answers

    def observed_acknowledge_answers(
        capability: object, group_id: str, acknowledgements: object
    ) -> object:
        with lock:
            diagnostics = ack_identity_diagnostics(
                created_group_id=state["created_group_id"],
                created_questions=state["created_questions"],
                waited_group_id=state["waited_group_id"],
                waited_answers=state["waited_answers"],
                ack_group_id=group_id,
                acknowledgements=acknowledgements,
            )
        try:
            result = acknowledge_answers(capability, group_id, acknowledgements)
        except BaseException:
            diagnostics["ack_succeeded"] = False
            append_observation(audit_path, {"event": "ack_identity", **diagnostics})
            raise
        diagnostics["ack_succeeded"] = True
        append_observation(audit_path, {"event": "ack_identity", **diagnostics})
        return result

    service.create_group = observed_create_group
    service.wait_for_answers = observed_wait_for_answers
    service.acknowledge_answers = observed_acknowledge_answers


def create_production_app() -> object:
    """Bind the real application routes to a fresh service for this process."""
    from cli_agent_orchestrator.api import main as api_main
    from cli_agent_orchestrator.clients.interaction_repository import InteractionRepository
    from cli_agent_orchestrator.clients.postgres.config import DatabaseTarget
    from cli_agent_orchestrator.services.interactions.service import InteractionService

    home_value = os.environ.get("CAO_HOME_DIR")
    if not home_value:
        raise RuntimeError("cao_home_missing")
    home = Path(home_value)
    target = DatabaseTarget.from_settings(scope="main", home=home)
    service = InteractionService(InteractionRepository(target))
    service.initialize()
    _observe_service(service, _private_observation_path())
    api_main.app.state.interaction_service = service
    return api_main.app


def run_worker(port: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid_api_worker_port")
    if os.environ.get("CAO_API_HOST") != "127.0.0.1" or os.environ.get("CAO_API_PORT") != str(port):
        raise RuntimeError("api_worker_bind_mismatch")
    app = create_production_app()
    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        access_log=False,
        lifespan="off",
        server_header=False,
        date_header=False,
    )
    uvicorn.Server(config).run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    run_worker(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
