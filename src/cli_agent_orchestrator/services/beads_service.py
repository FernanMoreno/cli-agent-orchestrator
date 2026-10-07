"""Registered optional external task workspaces; no initialization on startup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from cli_agent_orchestrator.clients.beads import BeadsClient, BeadsError, Task


@dataclass(frozen=True)
class BeadsWorkspace:
    id: str
    root: Path
    dialect: str
    identity: tuple[int, int]
    revision: str


def registered_workspaces() -> dict[str, BeadsWorkspace]:
    if os.environ.get("CAO_ENABLE_BEADS", "").lower() not in {"1", "true", "yes"}:
        raise BeadsError("beads_disabled")
    source = os.environ.get("CAO_BEADS_WORKSPACES_FILE")
    if not source:
        raise BeadsError("beads_workspace_configuration_missing")
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError()
            raw = stream.read(65537)
        rows = json.loads(raw)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
            raise ValueError()
        revision = hashlib.sha256(raw.encode()).hexdigest()
        result = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"id", "root", "dialect"}:
                raise ValueError()
            identity = row["id"]
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", identity) or identity in result:
                raise ValueError()
            root = Path(row["root"])
            if not root.is_absolute() or root.is_symlink():
                raise ValueError()
            root = root.resolve(strict=True)
            if not root.is_dir():
                raise ValueError()
            info = root.stat()
            dialect = row.get("dialect", "legacy")
            if dialect not in {"legacy", "modern"}:
                raise ValueError()
            result[identity] = BeadsWorkspace(
                identity, root, dialect, (info.st_dev, info.st_ino), revision
            )
        return result
    except (OSError, ValueError, KeyError, TypeError):
        raise BeadsError("beads_workspace_configuration_invalid") from None


def client_for(workspace_id: str) -> tuple[BeadsWorkspace, BeadsClient]:
    workspace = registered_workspaces().get(workspace_id)
    if workspace is None:
        raise BeadsError("beads_workspace_unknown")
    return workspace, BeadsClient(
        workspace.root, binary=os.environ.get("CAO_BEADS_BINARY", "bd"), dialect=workspace.dialect
    )


def capabilities() -> dict:
    try:
        workspaces = registered_workspaces()
        import shutil

        available = shutil.which(os.environ.get("CAO_BEADS_BINARY", "bd")) is not None
        return {
            "enabled": True,
            "available": available,
            "error_kind": None if available else "beads_binary_missing",
            "workspaces": [
                {"id": value.id, "revision": value.revision, "dialect": value.dialect}
                for value in workspaces.values()
            ],
        }
    except BeadsError as error:
        return {
            "enabled": error.kind != "beads_disabled",
            "available": False,
            "error_kind": error.kind,
            "workspaces": [],
        }


def task_dto(task: Task | None) -> dict:
    if task is None:
        raise TypeError("asdict() should be called on dataclass instances")
    value = asdict(task)
    value["external_status"] = value.pop("status")
    value["work_verified_completed"] = False
    value["material_hash"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def decompose_preview(text: str) -> dict:
    if not isinstance(text, str) or len(text.encode()) > 32768:
        raise ValueError("decomposition text limit")
    titles = [
        re.sub(r"^(?:[-*•]|[0-9]+[.)])\s*", "", line.strip())
        for line in text.splitlines()
        if line.strip()
    ]
    if not 1 <= len(titles) <= 32 or any(len(title.encode()) > 512 for title in titles):
        raise ValueError("decomposition task limit")
    tasks = [
        {"client_step_key": str(index), "title": title, "description": "", "priority": 2}
        for index, title in enumerate(titles)
    ]
    return {
        "tasks": tasks,
        "draft_hash": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest(),
    }


class BeadsConflict(ValueError):
    pass


def _operation_identity(owner: str, workspace: BeadsWorkspace, key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", key):
        raise ValueError("invalid operation key")
    return "beads_" + hashlib.sha256(json.dumps([owner, workspace.id, key]).encode()).hexdigest()


def _operation_dto(row) -> dict:
    if row.result_json is not None:
        return cast(dict[str, Any], json.loads(row.result_json))
    return {
        "operation_id": row.operation_id,
        "state": "uncertain",
        "error_kind": "beads_write_uncertain",
    }


def inspect_operation(operation_id: str, *, owner: str) -> dict:
    from cli_agent_orchestrator.clients import database

    with database.SessionLocal() as db:
        row = (
            db.query(database.BeadsOperationModel)
            .filter_by(operation_id=operation_id, owner=owner)
            .first()
        )
        if row is None:
            raise BeadsConflict("operation not found")
        return _operation_dto(row)


def _get_operation(identity: str, request_hash: str) -> dict | None:
    from cli_agent_orchestrator.clients import database

    with database.SessionLocal() as db:
        row = db.query(database.BeadsOperationModel).filter_by(operation_id=identity).first()
        if row is None:
            return None
        if row.request_hash != request_hash:
            raise BeadsConflict("operation key material changed")
        return _operation_dto(row)


def _claim_operation(identity, digest, workspace, owner, action):
    from sqlalchemy.exc import IntegrityError

    from cli_agent_orchestrator.clients import database

    with database.SessionLocal() as db:
        row = database.BeadsOperationModel(
            operation_id=identity,
            owner=owner,
            workspace_id=workspace.id,
            workspace_identity=json.dumps([workspace.revision, *workspace.identity]),
            action=action,
            request_hash=digest,
            state="claimed",
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return False
        return True


def _finish_operation(identity: str, result: dict) -> dict:
    from cli_agent_orchestrator.clients import database

    with database.SessionLocal() as db:
        row = db.query(database.BeadsOperationModel).filter_by(operation_id=identity).one()
        if row.result_json is None:
            row.state, row.result_json = result["state"], json.dumps(result, sort_keys=True)
            db.commit()
        return _operation_dto(row)


def _validate_values(action, values):
    fields = {
        "create": {"title", "description", "priority", "labels", "parent_id", "issue_type"},
        "update": {"title", "description", "priority", "status", "notes"},
        "close": set(),
        "delete": set(),
        "comment": {"comment"},
        "notes": {"notes"},
        "label_add": {"label"},
        "label_remove": {"label"},
        "dep_add": {"depends_on"},
        "dep_remove": {"depends_on"},
    }
    if action not in fields or not isinstance(values, dict) or set(values) - fields[action]:
        raise ValueError("invalid metadata operation")
    if len(json.dumps(values).encode()) > 65536:
        raise ValueError("metadata input limit")
    if action == "create" and not isinstance(values.get("title"), str):
        raise ValueError("title required")
    required = {
        "comment": "comment",
        "notes": "notes",
        "label_add": "label",
        "label_remove": "label",
        "dep_add": "depends_on",
        "dep_remove": "depends_on",
    }
    if action in required and not isinstance(values.get(required[action]), str):
        raise ValueError("operation value required")

    for key, value in values.items():
        if key == "priority":
            if type(value) is not int or value not in range(5):
                raise ValueError("invalid priority")
        elif key == "labels":
            if not isinstance(value, list) or len(value) > 32:
                raise ValueError("invalid labels")
            for label in value:
                if (
                    not isinstance(label, str)
                    or not label
                    or len(label.encode()) > 256
                    or any(c in label for c in ",\n\x00")
                ):
                    raise ValueError("invalid label")
        else:
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("invalid text")
            maximum = 512 if key == "title" else 256 if key == "label" else 32768
            if len(value.encode()) > maximum:
                raise ValueError("text limit")
            if key in {"title", "label", "depends_on", "parent_id"} and not value:
                raise ValueError("empty value")
            if key in {"depends_on", "parent_id"} and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value
            ):
                raise ValueError("invalid task identity")
            if key == "status" and value not in {"open", "wip", "closed", "blocked", "deferred"}:
                raise ValueError("invalid status")
            if key == "issue_type" and value not in {"task", "epic", "bug", "feature", "chore"}:
                raise ValueError("invalid issue type")
            if key == "label" and any(c in value for c in ",\n"):
                raise ValueError("invalid label")
    if action == "update" and not values:
        raise ValueError("empty update")


def _check_dependency_graph(adapter, task_id, depends_on):
    if task_id == depends_on:
        raise BeadsConflict("dependency cycle")
    tasks = {task.id: task for task in adapter.list()}
    if task_id not in tasks or depends_on not in tasks:
        raise BeadsConflict("dependency outside workspace")
    pending, seen = [depends_on], set()
    while pending:
        current = pending.pop()
        if current == task_id:
            raise BeadsConflict("dependency cycle")
        if current in seen:
            continue
        seen.add(current)
        if len(seen) > 256:
            raise ValueError("dependency graph limit")
        if current not in tasks:
            raise BeadsConflict("dependency outside workspace")
        pending.extend(tasks[current].blocked_by)


def metadata_operation(
    workspace_id: str,
    *,
    owner: str,
    operation_key: str,
    action: str,
    values: dict,
    task_id: str | None = None,
    expected_hash: str | None = None,
    _expected_workspace_identity: str | None = None,
    _expected_task_content: dict | None = None,
) -> dict:
    """Claim every write before running bd, serialize CAS across server processes."""
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    _validate_values(action, values)
    workspace, adapter = client_for(workspace_id)
    if _expected_workspace_identity is not None:
        pinned = json.dumps(
            [workspace.revision, *workspace.identity], sort_keys=True, separators=(",", ":")
        )
        if pinned != _expected_workspace_identity:
            raise BeadsConflict("beads_assignment_workspace_changed")
    identity = _operation_identity(owner, workspace, operation_key)
    material = [workspace.revision, *workspace.identity, action, values, task_id, expected_hash]
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    lock_identity = "beads-" + hashlib.sha256(str(workspace.root).encode()).hexdigest()
    with terminal_dispatch_lock(constants.DATABASE_FILE, lock_identity):
        previous = _get_operation(identity, digest)
        if previous is not None:
            return previous
        if action != "create":
            if task_id is None or expected_hash is None:
                raise ValueError("task identity and material hash required")
            task = adapter.get(task_id)
            if task is None:
                raise BeadsConflict("task missing")
            if _expected_task_content is not None:
                current = task_dto(task)
                if any(
                    current.get(key) != value
                    for key, value in _expected_task_content.items()
                    if key not in ("material_hash", "external_status", "work_verified_completed")
                ):
                    raise BeadsConflict("beads_assignment_task_changed")
            if task_dto(task)["material_hash"] != expected_hash:
                raise BeadsConflict("task material changed")
        elif values.get("parent_id") and adapter.get(values["parent_id"]) is None:
            raise BeadsConflict("parent outside workspace")
        if action == "dep_add":
            _check_dependency_graph(adapter, task_id, values["depends_on"])
        if not _claim_operation(identity, digest, workspace, owner, action):
            previous = _get_operation(identity, digest)
            assert previous is not None
            return previous
        try:
            if action == "create":
                # Initial create carries its immutable token; a lost result can
                # be inspected without issuing a second create.
                kwargs = dict(values)
                kwargs["labels"] = [*(values.get("labels") or []), "cao-op:" + identity]
                task = adapter.add(**kwargs)
                result = task_dto(task)
            elif action == "update":
                assert task_id is not None
                result = task_dto(adapter.update(task_id, **values))
            elif action == "close":
                assert task_id is not None
                result = task_dto(adapter.close(task_id))
            elif action == "delete":
                assert task_id is not None
                result = {"deleted": adapter.delete(task_id)}
            elif action == "comment":
                assert task_id is not None
                result = {"added": adapter.add_comment(task_id, values["comment"])}
            elif action == "notes":
                assert task_id is not None
                result = task_dto(adapter.update_notes(task_id, values["notes"]))
            elif action in {"label_add", "label_remove"}:
                assert task_id is not None
                result = {
                    "updated": (
                        adapter.add_label if action == "label_add" else adapter.remove_label
                    )(task_id, values["label"])
                }
            elif action in {"dep_add", "dep_remove"}:
                assert task_id is not None
                result = {
                    "updated": (
                        adapter.add_dependency if action == "dep_add" else adapter.remove_dependency
                    )(task_id, values["depends_on"])
                }
            receipt = {"operation_id": identity, "state": "applied", "result": result}
        except Exception as error:
            # A CLI may have committed before losing stdout. Even an extraction
            # failure is uncertain; retain the operation and never create again.
            receipt = {
                "operation_id": identity,
                "state": "uncertain",
                "error_kind": (
                    error.kind if isinstance(error, BeadsError) else "beads_write_uncertain"
                ),
            }
        return _finish_operation(identity, receipt)


def bulk_create(
    workspace_id: str,
    *,
    owner: str,
    operation_key: str,
    tasks: list[dict],
    draft_hash: str,
    epic: dict | None = None,
    sequential: bool = False,
) -> dict:
    """Resume each external mutation by its immutable key; unknown writes stop the batch."""
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 32:
        raise ValueError("task count limit")
    canonical = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    if canonical != draft_hash:
        raise BeadsConflict("draft changed")
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("invalid task")
        values = {key: value for key, value in task.items() if key != "client_step_key"}
        if "parent_id" in values:
            raise ValueError("bulk parent is explicit epic only")
        _validate_values("create", values)
    if epic is not None:
        _validate_values("create", {**epic, "issue_type": "epic"})
    workspace, _ = client_for(workspace_id)
    identity = _operation_identity(owner, workspace, operation_key)
    material_hash = hashlib.sha256(
        json.dumps(
            [workspace.revision, *workspace.identity, tasks, draft_hash, epic, sequential],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    with terminal_dispatch_lock(constants.DATABASE_FILE, "beads-bulk-" + identity):
        retained = _get_operation(identity, material_hash)
        if retained is not None and retained["state"] == "applied":
            return retained
        if retained is None:
            _claim_operation(identity, material_hash, workspace, owner, "bulk_create")
    # Fixed length derived keys retain entire caller key and draft/epic identity.
    root_key = hashlib.sha256(
        json.dumps([operation_key, draft_hash, epic, sequential], sort_keys=True).encode()
    ).hexdigest()
    receipts, parent_id = [], None
    if epic is not None:
        parent = metadata_operation(
            workspace_id,
            owner=owner,
            operation_key=root_key + ":epic",
            action="create",
            values={**epic, "issue_type": "epic"},
        )
        receipts.append(parent)
        if parent["state"] != "applied":
            return {"state": "partial", "receipts": receipts}
        parent_id = parent["result"]["id"]
    previous = None
    for index, task in enumerate(tasks):
        values = {key: value for key, value in task.items() if key != "client_step_key"}
        if parent_id:
            values["parent_id"] = parent_id
        receipt = metadata_operation(
            workspace_id,
            owner=owner,
            operation_key=root_key + ":" + str(index),
            action="create",
            values=values,
        )
        receipts.append(receipt)
        if receipt["state"] != "applied":
            return {"state": "partial", "epic_id": parent_id, "receipts": receipts}
        current = receipt["result"]["id"]
        if sequential and previous is not None:
            _, adapter = client_for(workspace_id)
            task_now = adapter.get(current)
            if task_now is None:
                raise BeadsConflict("created task missing")
            dependency = metadata_operation(
                workspace_id,
                owner=owner,
                operation_key=root_key + ":dep:" + str(index),
                action="dep_add",
                values={"depends_on": previous},
                task_id=current,
                expected_hash=receipt["result"]["material_hash"],
            )
            receipts.append(dependency)
            if dependency["state"] != "applied":
                return {"state": "partial", "epic_id": parent_id, "receipts": receipts}
        previous = current
    return _finish_operation(
        identity,
        {"operation_id": identity, "state": "applied", "epic_id": parent_id, "receipts": receipts},
    )


def epic_status(workspace_id: str, epic_id: str) -> dict:
    _, adapter = client_for(workspace_id)
    parent = adapter.get(epic_id)
    if parent is None or parent.type != "epic":
        raise BeadsConflict("epic not found")
    children = adapter.get_children(epic_id)
    ready_ids = {task.id for task in adapter.ready(epic_id)}
    return {
        "epic": task_dto(parent),
        "children": [task_dto(task) for task in children],
        "ready_ids": sorted(ready_ids),
        "external_closed_count": sum(task.status == "closed" for task in children),
        "total": len(children),
        "work_verified_completed_count": 0,
    }


def reconcile_operation(operation_id: str, *, owner: str) -> dict:
    """Observe a CLI create token without replaying any write or accepting Work completion."""
    from cli_agent_orchestrator.clients import database

    with database.SessionLocal() as db:
        row = (
            db.query(database.BeadsOperationModel)
            .filter_by(operation_id=operation_id, owner=owner)
            .first()
        )
        if row is None:
            raise BeadsConflict("operation not found")
        retained, workspace_id, action, pinned = (
            _operation_dto(row),
            row.workspace_id,
            row.action,
            row.workspace_identity,
        )
    workspace, adapter = client_for(workspace_id)
    if json.dumps([workspace.revision, *workspace.identity]) != pinned:
        raise BeadsConflict("workspace changed")
    if action != "create":
        return {**retained, "observation": "manual_inspection_required"}
    matching = [task for task in adapter.list() if "cao-op:" + operation_id in task.labels]
    if len(matching) > 1:
        raise BeadsConflict("ambiguous external create token")
    return {
        **retained,
        "observation": "created_task_found" if matching else "no_verifiable_task_found",
        "observed_task": task_dto(matching[0]) if matching else None,
    }
