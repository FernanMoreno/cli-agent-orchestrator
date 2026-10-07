"""Beads commands use the authenticated CAO API, including metadata writes."""

import json
from urllib.parse import quote

import click
import requests

from cli_agent_orchestrator.constants import API_BASE_URL, MCP_REQUEST_TIMEOUT
from cli_agent_orchestrator.security.auth import get_local_bearer


def _request(method, path, body=None):
    token = get_local_bearer()
    try:
        response = requests.request(
            method,
            API_BASE_URL + path,
            json=body,
            headers={"Authorization": "Bearer " + token} if token else {},
            timeout=MCP_REQUEST_TIMEOUT,
        )
        value = response.json()
    except (requests.RequestException, ValueError):
        raise click.ClickException(
            "Task response unavailable; inspect the retained operation before retrying."
        ) from None
    if not response.ok:
        raise click.ClickException(str(value.get("detail", "request refused")))
    click.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _path(workspace):
    return "/beads/workspaces/" + quote(workspace, safe="")


@click.group()
def tasks():
    """Inspect external tasks and submit durable metadata operations."""


@tasks.command("capabilities")
def capabilities():
    _request("GET", "/beads/capabilities")


@tasks.command("list")
@click.option("--workspace", required=True)
@click.option("--status", type=click.Choice(["open", "wip", "closed", "blocked", "deferred"]))
@click.option("--priority", type=click.IntRange(0, 4))
def list_tasks(workspace, status, priority):
    from urllib.parse import urlencode

    query = urlencode(
        {
            key: value
            for key, value in {"status": status, "priority": priority}.items()
            if value is not None
        }
    )
    _request("GET", _path(workspace) + "/tasks" + ("?" + query if query else ""))


@tasks.command("show")
@click.argument("task_id")
@click.option("--workspace", required=True)
def show(workspace, task_id):
    _request("GET", _path(workspace) + "/tasks/" + quote(task_id, safe=""))


@tasks.command("ready")
@click.option("--workspace", required=True)
@click.option("--epic")
def ready(workspace, epic):
    _request(
        "GET", _path(workspace) + "/ready" + ("?epic_id=" + quote(epic, safe="") if epic else "")
    )


@tasks.command("mutate")
@click.option("--workspace", required=True)
@click.argument("file", type=click.File("r", encoding="utf-8"))
def mutate(workspace, file):
    """Submit a mutation JSON with operation_key and expected task material_hash."""
    try:
        body = json.load(file)
    except ValueError:
        raise click.ClickException("Invalid JSON") from None
    _request("POST", _path(workspace) + "/mutations", body)


@tasks.command("operation")
@click.argument("operation_id")
@click.option("--reconcile", is_flag=True)
def operation(operation_id, reconcile):
    _request(
        "POST" if reconcile else "GET",
        "/beads/operations/" + quote(operation_id, safe="") + ("/reconcile" if reconcile else ""),
    )


@tasks.command("decompose")
@click.argument("file", type=click.File("r", encoding="utf-8"))
def decompose(file):
    _request("POST", "/beads/decompose-preview", {"text": file.read(32769)})


@tasks.command("bulk-create")
@click.option("--workspace", required=True)
@click.argument("file", type=click.File("r", encoding="utf-8"))
def bulk_create(workspace, file):
    try:
        body = json.load(file)
    except ValueError:
        raise click.ClickException("Invalid JSON") from None
    _request("POST", _path(workspace) + "/bulk-create", body)


@tasks.command("epic")
@click.argument("epic_id")
@click.option("--workspace", required=True)
def epic(workspace, epic_id):
    _request("GET", _path(workspace) + "/epics/" + quote(epic_id, safe=""))


@tasks.command("comments")
@click.argument("task_id")
@click.option("--workspace", required=True)
def comments(workspace, task_id):
    _request("GET", _path(workspace) + "/tasks/" + quote(task_id, safe="") + "/comments")


@tasks.command("context")
@click.argument("task_id")
@click.option("--workspace", required=True)
def context(workspace, task_id):
    _request("GET", _path(workspace) + "/tasks/" + quote(task_id, safe="") + "/context")


@tasks.command("prepare")
@click.argument("task_id")
@click.option("--workspace", required=True)
@click.argument("file", type=click.File("r", encoding="utf-8"))
def prepare_work(workspace, task_id, file):
    """Freeze task material and criteria using existing Work seed selections."""
    try:
        body = json.load(file)
    except ValueError:
        raise click.ClickException("Invalid JSON") from None
    _request(
        "POST", _path(workspace) + "/tasks/" + quote(task_id, safe="") + "/plans:prepare", body
    )


@tasks.command("assignment")
@click.argument("binding_id")
def assignment(binding_id):
    _request("GET", "/beads/assignments/" + quote(binding_id, safe=""))


@tasks.command("start")
@click.argument("binding_id")
@click.option("--plan-id", required=True)
@click.option("--run-id", required=True)
def start_work(binding_id, plan_id, run_id):
    _request(
        "POST",
        "/beads/assignments/" + quote(binding_id, safe="") + "/start",
        {"expected_plan_id": plan_id, "run_id": run_id},
    )


@tasks.command("unassign")
@click.argument("binding_id")
def unassign(binding_id):
    _request("POST", "/beads/assignments/" + quote(binding_id, safe="") + "/unassign")


@tasks.command("close-verified")
@click.argument("binding_id")
@click.option("--operation-key", required=True)
@click.option("--expected-hash", required=True)
def close_verified(binding_id, operation_key, expected_hash):
    _request(
        "POST",
        "/beads/assignments/" + quote(binding_id, safe="") + "/close-task",
        {"operation_key": operation_key, "expected_hash": expected_hash},
    )
