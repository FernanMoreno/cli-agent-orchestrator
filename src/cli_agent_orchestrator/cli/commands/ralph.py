"""Ralph is an authenticated API client, never a separate local loop engine."""

import json
from pathlib import Path
from urllib.parse import quote

import click
import requests

from cli_agent_orchestrator.constants import API_BASE_URL, MCP_REQUEST_TIMEOUT
from cli_agent_orchestrator.security.auth import get_local_bearer


def call(method, path, body=None):
    token = get_local_bearer()
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        response = requests.request(
            method,
            API_BASE_URL + "/ralph" + path,
            json=body,
            headers=headers,
            timeout=MCP_REQUEST_TIMEOUT,
        )
        if not response.ok:
            raise click.ClickException(
                "Ralph refused: " + str(response.json().get("detail", response.status_code))
            )
        return response.json()
    except (requests.RequestException, ValueError):
        raise click.ClickException("Ralph API unavailable") from None


def render(value):
    click.echo(json.dumps(value, sort_keys=True, indent=2))


@click.group()
def ralph():
    """Prepare, run and inspect a bounded approved Work continuation."""


@ralph.command("template")
@click.option("--provider", required=True)
@click.option("--agent", required=True)
@click.option("--memory", type=click.Choice(["off", "exact-snapshot"]), default="exact-snapshot")
@click.option("--name")
def template(provider, agent, memory, name):
    render(
        call("POST", "/templates", dict(provider=provider, agent=agent, memory=memory, name=name))
    )


@ralph.command("prepare")
@click.argument("request_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def prepare(request_file):
    """Read task/criteria, existing targets and existing seed selectors JSON."""
    try:
        body = json.loads(request_file.read_text())
    except (ValueError, OSError):
        raise click.ClickException("Invalid request JSON") from None
    render(call("POST", "/plans:prepare", body))


@ralph.command("start")
@click.argument("prepared_id")
@click.option("--plan", required=True)
@click.option("--run-id", required=True)
def start(prepared_id, plan, run_id):
    render(
        call("POST", "/runs", dict(prepared_id=prepared_id, expected_plan_id=plan, run_id=run_id))
    )


@ralph.command("status")
@click.argument("identity")
def status(identity):
    render(call("GET", "/runs/" + quote(identity, safe="")))


@ralph.command("feedback")
@click.argument("identity")
@click.option("--request-id", required=True)
@click.option("--text", required=True)
def feedback(identity, request_id, text):
    render(
        call(
            "POST",
            "/runs/" + quote(identity, safe="") + "/feedback",
            dict(request_id=request_id, text=text),
        )
    )


for verb in ("stop", "complete", "resume"):

    def action(identity, _verb=verb):
        render(call("POST", "/runs/" + quote(identity, safe="") + "/" + _verb, {}))

    ralph.command(verb)(click.argument("identity")(action))
