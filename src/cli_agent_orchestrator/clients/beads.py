"""Optional bounded bd metadata adapter; external status never proves Work results."""

from __future__ import annotations

import builtins
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


class BeadsError(RuntimeError):
    def __init__(self, kind: str, *, uncertain: bool = False):
        super().__init__(kind)
        self.kind = kind
        self.uncertain = uncertain


@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    priority: int = 2
    status: str = "open"
    assignee: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    parent_id: str | None = None
    blocked_by: builtins.list[str] = field(default_factory=list)
    labels: builtins.list[str] = field(default_factory=list)
    type: str = "task"
    notes: str = ""


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("invalid bead identity")
    return value


def _value(value: str, *, maximum: int = 32768) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value.encode("utf-8")) > maximum:
        raise ValueError("invalid bounded bead text")
    return value


class BeadsClient:
    def __init__(
        self,
        working_dir: str | Path,
        *,
        binary: str = "bd",
        timeout: float = 10,
        output_limit: int = 2 * 1024 * 1024,
        dialect: str = "legacy",
    ):
        if working_dir is None:
            raise ValueError("explicit bead workspace required")
        source = Path(working_dir)
        if source.is_symlink():
            raise ValueError("bead workspace cannot be a link")
        self.working_dir = source.resolve(strict=True)
        if not self.working_dir.is_dir():
            raise ValueError("bead workspace must be a directory")
        metadata = self.working_dir / ".beads"
        if metadata.is_symlink() or not metadata.is_dir():
            raise BeadsError("beads_workspace_uninitialized")
        resolved_binary = shutil.which(binary)
        if resolved_binary is None:
            raise BeadsError("beads_binary_missing")
        self.binary = resolved_binary
        if not 0 < timeout <= 60 or not 256 <= output_limit <= 2 * 1024 * 1024:
            raise ValueError("invalid bead process bounds")
        if dialect not in {"legacy", "modern"}:
            raise BeadsError("beads_cli_unsupported")
        self.dialect = dialect
        self.timeout, self.output_limit = timeout, output_limit
        info = self.working_dir.stat()
        self._identity = (info.st_dev, info.st_ino)
        metadata_info = metadata.stat()
        self._metadata_identity = (metadata_info.st_dev, metadata_info.st_ino)

    def _run_bd(self, *args: str, mutation: bool = False) -> str:
        metadata = self.working_dir / ".beads"
        try:
            info = self.working_dir.lstat()
            metadata_info = metadata.lstat()
        except OSError:
            raise BeadsError("beads_workspace_changed") from None
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino) != self._identity
            or not stat.S_ISDIR(metadata_info.st_mode)
            or (metadata_info.st_dev, metadata_info.st_ino) != self._metadata_identity
        ):
            raise BeadsError("beads_workspace_changed")
        _value("".join(args), maximum=128 * 1024)
        env = dict(os.environ)
        for env_key in ("CAO_RUNTIME_TOKEN", "CAO_RUNTIME_TOKEN_FILE", "CAO_RUNTIME_TOKEN_PATH"):
            env.pop(env_key, None)
        try:
            process = subprocess.Popen(
                [self.binary, *(["--no-daemon"] if self.dialect == "legacy" else []), *args],
                cwd=self.working_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        except OSError as error:
            raise BeadsError("beads_process_unavailable") from error
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        output, used = bytearray(), 0
        deadline = time.monotonic() + self.timeout
        selector.register(process.stdout, selectors.EVENT_READ, True)
        selector.register(process.stderr, selectors.EVENT_READ, False)
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BeadsError("beads_timeout", uncertain=mutation)
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    used += len(chunk)
                    if used > self.output_limit:
                        raise BeadsError("beads_output_limit", uncertain=mutation)
                    if key.data:
                        output.extend(chunk)
            remaining = deadline - time.monotonic()
            try:
                code = process.wait(timeout=max(0.001, remaining))
            except subprocess.TimeoutExpired:
                raise BeadsError("beads_timeout", uncertain=mutation) from None
            if code != 0:
                raise BeadsError("beads_cli_failed", uncertain=mutation)
            try:
                return output.decode("utf-8", errors="strict").strip()
            except UnicodeDecodeError:
                raise BeadsError("beads_invalid_utf8", uncertain=mutation) from None
        finally:
            # Bound and reap the whole process group on timeout/output/error;
            # child pipe writers must not keep an abandoned operation alive.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            selector.close()
            process.stdout.close()
            process.stderr.close()

    def _run_bd_json(self, *args: str, mutation: bool = False):
        raw = self._run_bd(*args, "--json", mutation=mutation)
        try:
            value = json.loads(raw)
            if not isinstance(value, (list, dict)):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise BeadsError("beads_invalid_json", uncertain=mutation) from None

    def _issue_to_task(self, value: dict) -> Task:
        if not isinstance(value, dict):
            raise BeadsError("beads_invalid_json")
        try:
            identity, title = _id(value["id"]), _value(value["title"], maximum=512)
            priority = value.get("priority", 2)
            if type(priority) is not int or priority not in range(5):
                raise ValueError()
            status = value.get("status", "open")
            if status not in {"open", "in_progress", "closed", "blocked", "deferred"}:
                raise ValueError()
            labels = value.get("labels") or []
            if not isinstance(labels, list) or len(labels) > 128:
                raise ValueError()
            labels = [_value(label, maximum=256) for label in labels]
            parent = value.get("parent") or value.get("parent_id")
            parent = _id(parent) if parent else None
            dependencies = value.get("dependencies") or []
            if not isinstance(dependencies, list) or len(dependencies) > 256:
                raise ValueError()
            blocked = [
                _id(dep.get("depends_on_id") or dep["id"])
                for dep in dependencies
                if dep.get("dependency_type", dep.get("type")) == "blocks"
            ]
            return Task(
                id=identity,
                title=title,
                description=_value(value.get("description") or ""),
                priority=priority,
                status="wip" if status == "in_progress" else status,
                parent_id=parent,
                blocked_by=blocked,
                labels=labels,
                assignee=value.get("assignee"),
                created_at=value.get("created_at"),
                updated_at=value.get("updated_at"),
                closed_at=value.get("closed_at"),
                type=value.get("issue_type") or "task",
                notes=_value(value.get("notes") or ""),
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            raise BeadsError("beads_invalid_json") from None

    def _tasks(self, value) -> builtins.list[Task]:
        if not isinstance(value, list) or len(value) > 4096:
            raise BeadsError("beads_invalid_json")
        return [self._issue_to_task(item) for item in value]

    def list(self, status: str | None = None, priority: int | None = None) -> builtins.list[Task]:
        args = ["list", "--limit=4096"]
        if status:
            if status not in {"open", "wip", "closed", "blocked", "deferred"}:
                raise ValueError("invalid status")
            args.append("--status=" + ("in_progress" if status == "wip" else status))
        if priority is not None:
            if type(priority) is not int or priority not in range(5):
                raise ValueError("invalid priority")
            args.append("--priority=" + str(priority))
        tasks = self._tasks(self._run_bd_json(*args))
        if priority is not None:
            tasks = [task for task in tasks if task.priority == priority]
        return sorted(tasks, key=lambda task: (task.priority, task.created_at or "", task.id))

    def get(self, task_id: str) -> Task | None:
        value = self._run_bd_json("show", _id(task_id))
        values = value if isinstance(value, list) else [value]
        if not values:
            return None
        if len(values) != 1:
            raise BeadsError("beads_invalid_json")
        task = self._issue_to_task(values[0])
        if task.id != task_id:
            raise BeadsError("beads_invalid_json")
        return task

    def ready(self, parent_id: str | None = None) -> builtins.list[Task]:
        tasks = self._tasks(self._run_bd_json("ready", "--limit=4096"))
        return [task for task in tasks if parent_id is None or task.parent_id == _id(parent_id)]

    def next(self, priority: int | None = None) -> Task | None:
        tasks = self.ready()
        if priority is not None:
            tasks = [task for task in tasks if task.priority == priority]
        return tasks[0] if tasks else None

    def add(
        self,
        title: str,
        description: str = "",
        priority: int = 2,
        *,
        labels: builtins.list[str] | None = None,
        parent_id: str | None = None,
        issue_type: str = "task",
        external_key: str | None = None,
    ) -> Task:
        if type(priority) is not int or priority not in range(5):
            raise ValueError("invalid priority")
        args = [
            "create",
            "--title=" + _value(title, maximum=512),
            "--description=" + _value(description),
            "--priority=" + str(priority),
            "--type=" + _id(issue_type),
        ]
        if parent_id:
            args.append("--parent=" + _id(parent_id))
        if labels:
            args.append("--labels=" + ",".join(_value(label, maximum=256) for label in labels))
        if external_key:
            args.append("--external-ref=" + _value(external_key, maximum=256))
        value = self._run_bd_json(*args, mutation=True)
        values = value if isinstance(value, list) else [value]
        if len(values) != 1:
            raise BeadsError("beads_invalid_json", uncertain=True)
        return self._issue_to_task(values[0])

    def create_child(
        self, parent_id: str, title: str, description: str = "", priority: int = 2
    ) -> Task:
        return self.add(title, description, priority, parent_id=_id(parent_id))

    def get_children(self, parent_id: str) -> builtins.list[Task]:
        return [task for task in self.list() if task.parent_id == _id(parent_id)]

    def update(self, task_id: str, **changes) -> Task | None:
        permitted = {"title", "description", "priority", "status", "assignee", "notes"}
        if set(changes) - permitted or not changes:
            raise ValueError("invalid bead update")
        args = ["update", _id(task_id)]
        for key, value in changes.items():
            if key == "priority":
                if type(value) is not int or value not in range(5):
                    raise ValueError("invalid priority")
                value = str(value)
            if key == "status":
                if value not in {"open", "wip", "closed", "blocked", "deferred"}:
                    raise ValueError("invalid status")
                value = "in_progress" if value == "wip" else value
            args.append("--" + key + "=" + _value(value))
        self._run_bd(*args, mutation=True)
        return self.get(task_id)

    def wip(self, task_id: str, assignee: str | None = None) -> Task | None:
        return self.update(
            task_id, status="wip", **({"assignee": assignee} if assignee is not None else {})
        )

    def close(self, task_id: str) -> Task | None:
        self._run_bd("close", _id(task_id), mutation=True)
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        self._run_bd("delete", _id(task_id), "--force", mutation=True)
        return True

    def get_comments(self, task_id: str) -> builtins.list[dict]:
        value = self._run_bd_json("comments", _id(task_id))
        if (
            not isinstance(value, list)
            or len(value) > 512
            or any(not isinstance(item, dict) for item in value)
        ):
            raise BeadsError("beads_invalid_json")
        return value

    def add_comment(self, task_id: str, comment: str) -> bool:
        self._run_bd("comments", "add", _id(task_id), "--", _value(comment), mutation=True)
        return True

    def add_dependency(self, task_id: str, depends_on_id: str) -> bool:
        if task_id == depends_on_id:
            raise ValueError("self dependency")
        self._run_bd("dep", "add", _id(task_id), _id(depends_on_id), mutation=True)
        return True

    def remove_dependency(self, task_id: str, depends_on_id: str) -> bool:
        self._run_bd("dep", "remove", _id(task_id), _id(depends_on_id), mutation=True)
        return True

    def update_notes(self, task_id: str, notes: str) -> Task | None:
        return self.update(task_id, notes=notes)

    def add_label(self, task_id: str, label: str) -> bool:
        label = _value(label, maximum=256)
        if not label or "," in label or "\n" in label:
            raise ValueError("invalid bead label")
        self._run_bd("label", "add", _id(task_id), "--", label, mutation=True)
        return True

    def remove_label(self, task_id: str, label: str) -> bool:
        self._run_bd(
            "label", "remove", _id(task_id), "--", _value(label, maximum=256), mutation=True
        )
        return True


def extract_label_value(labels, prefix):
    return next(
        (value[len(prefix) + 1 :] for value in labels or [] if value.startswith(prefix + ":")), None
    )


def extract_context_files(labels):
    return [value.split(":", 1)[1] for value in labels or [] if value.startswith("context:")]


def _ancestors(task, client):
    seen = set()
    for _ in range(16):
        if task.id in seen:
            raise ValueError("bead ancestry cycle")
        seen.add(task.id)
        yield task
        if not task.parent_id:
            return
        task = client.get(task.parent_id)
        if task is None:
            raise ValueError("bead parent missing")
    raise ValueError("bead ancestry limit")


def resolve_workspace(task, beads_client, default=None):
    for current in _ancestors(task, beads_client):
        value = extract_label_value(current.labels, "workspace")
        if value:
            root = Path(beads_client.working_dir).resolve(strict=True)
            resolved = (root / value).resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError("bead workspace escapes registered root")
            return str(resolved)
    return default


def resolve_context_files(task, beads_client):
    root = Path(beads_client.working_dir).resolve(strict=True)
    result, seen = [], set()
    for current in _ancestors(task, beads_client):
        for value in extract_context_files(current.labels):
            try:
                path = (root / value).resolve(strict=True)
            except (OSError, RuntimeError):
                raise ValueError("bead context missing or unsafe") from None
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("bead context escapes registered root")
            if path not in seen:
                if len(result) >= 32 or path.stat().st_size > 65536:
                    raise ValueError("bead context limit")
                seen.add(path)
                result.append(str(path))
    return result
