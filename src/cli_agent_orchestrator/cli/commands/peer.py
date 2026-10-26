"""Operator commands for pairing and coordinating local CAO profiles."""

import json
import sys
from contextlib import nullcontext, redirect_stdout
from typing import NoReturn, cast

import click
from pydantic import ValidationError

from cli_agent_orchestrator.models.peer_cli import PeerAcceptOutput, PeerErrorOutput, PeerPairOutput
from cli_agent_orchestrator.services import local_peer_auth, local_peer_service


@click.group()
def peer():
    """Discover and authorize CAO instances running on this computer."""


@peer.command("list")
@click.option("--project", "project_path", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON.")
def list_peers(project_path: str | None, as_json: bool) -> None:
    """List active local CAO instances and project-scoped grants."""
    try:
        project_id = (
            str(local_peer_service.project_binding(project_path)["project_id"])
            if project_path
            else None
        )
    except (local_peer_service.LocalPeerError, OSError) as error:
        raise click.ClickException(str(error)) from error
    candidates = local_peer_service.verified_peer_candidates()
    grants = local_peer_service.list_local_peers(project_id=project_id)
    result = {"instances": candidates, "grants": grants}
    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    click.echo("Instancias locales activas:")
    if not candidates:
        click.echo("  (ninguna)")
    for candidate in candidates:
        click.echo(
            f"  {candidate['display_name']}  {candidate['instance_id']}  "
            f"{candidate['loopback_host']}:{candidate['loopback_port']}"
        )
    click.echo("Permisos de pares:")
    if not grants:
        click.echo("  (ninguno)")
    for grant in grants:
        state = "revocado" if grant["revoked_at"] else "activo"
        online = "en línea" if grant["online"] else "sin conexión"
        click.echo(
            f"  {grant['peer_display_name']}  {grant['peer_instance_id']}  "
            f"proyecto={grant['project_id']}  {state}, {online}  "
            f"acciones={','.join(cast(list[str], grant['scopes']))}"
        )


def _pairing_json_error(operation: str, code: str) -> NoReturn:
    output = PeerErrorOutput.model_validate(
        {
            "schema": "cao.peer.error.v1",
            "success": False,
            "error": {"operation": operation, "code": code},
        }
    )
    click.echo(output.model_dump_json(by_alias=True))
    raise click.exceptions.Exit(1)


def _emit_pairing_success(model, schema: str, data, operation: str) -> None:
    try:
        output = model.model_validate({"schema": schema, "success": True, "data": data})
    except ValidationError:
        _pairing_json_error(operation, "pairing_result_invalid")
    click.echo(output.model_dump_json(by_alias=True))


@peer.command("pair")
@click.argument("instance_id")
@click.option(
    "--project",
    "project_path",
    required=True,
    type=click.Path(exists=True, file_okay=False),
    help="Shared local project directory.",
)
@click.option("--json", "as_json", is_flag=True, help="Print versioned machine-readable JSON.")
def pair_peer(instance_id: str, project_path: str, as_json: bool) -> None:
    """Create a one-use pairing code for another active local CAO profile."""
    try:
        challenge = local_peer_service.initiate_pairing(instance_id, project_path)
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        if as_json:
            _pairing_json_error("pair", "pairing_failed")
        raise click.ClickException(str(error)) from error
    if as_json:
        _emit_pairing_success(PeerPairOutput, "cao.peer.pair.v1", challenge, "pair")
        return
    click.echo(f"Par: {challenge['peer_display_name']} ({challenge['peer_instance_id']})")
    click.echo(f"Proyecto: {challenge['canonical_root']}")
    click.echo(f"Acciones solicitadas: {', '.join(cast(list[str], challenge['requested_scopes']))}")
    click.echo(f"ID de emparejamiento: {challenge['challenge_id']}")
    click.echo(f"Código temporal (válido 5 minutos): {challenge['code']}")
    click.echo(
        "En el perfil destino ejecuta: cao peer accept "
        f"{challenge['challenge_id']} y escribe el código temporal."
    )


@peer.command("accept")
@click.argument("challenge_id")
@click.option("--json", "as_json", is_flag=True, help="Print versioned machine-readable JSON.")
def accept_peer_pairing(challenge_id: str, as_json: bool) -> None:
    """Review and accept a one-use pairing invitation from another CAO."""
    try:
        challenge = local_peer_auth.get_candidate_challenge(challenge_id)
    except local_peer_auth.LocalPeerAuthError as error:
        if as_json:
            _pairing_json_error("accept", "pairing_failed")
        raise click.ClickException(str(error)) from error
    click.echo(f"Instancia que solicita acceso: {challenge['initiator_display_name']}", err=as_json)
    click.echo(f"Identidad: {challenge['initiator_instance_id']}", err=as_json)
    click.echo(f"Proyecto: {challenge['canonical_root']}", err=as_json)
    click.echo(
        f"Acciones solicitadas: {', '.join(cast(list[str], challenge['requested_scopes']))}",
        err=as_json,
    )
    try:
        # Click may echo visible confirmation input to stdout even with err=True.
        # Keep the entire prompt interaction off the machine-result stream.
        with redirect_stdout(sys.stderr) if as_json else nullcontext():
            confirmed = click.confirm(
                "¿Autorizas este emparejamiento para el proyecto indicado?",
                default=False,
                err=as_json,
            )
            if confirmed:
                code = click.prompt(
                    "Código temporal", hide_input=True, confirmation_prompt=False, err=as_json
                )
    except click.Abort:
        if as_json:
            _pairing_json_error("accept", "pairing_aborted")
        raise
    if not confirmed:
        if as_json:
            _pairing_json_error("accept", "pairing_declined")
        raise click.ClickException("Emparejamiento rechazado; no se creó permiso.")
    try:
        receipt = local_peer_service.accept_pairing(challenge_id, code)
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        if as_json:
            _pairing_json_error("accept", "pairing_failed")
        raise click.ClickException(str(error)) from error
    if as_json:
        _emit_pairing_success(PeerAcceptOutput, "cao.peer.accept.v1", receipt, "accept")
        return
    click.echo(
        f"Emparejado con {receipt['peer_display_name']} para el proyecto "
        f"{receipt['project_id']}."
    )


@peer.command("status")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON.")
def task_status(task_id: str, as_json: bool) -> None:
    """Read or reconcile a coordinated local peer task."""
    try:
        receipt = local_peer_service.task_from_peer(task_id)
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        click.echo(f"Tarea {receipt['task_id']}: {receipt['state']}")
        if receipt.get("terminal_id"):
            click.echo(f"Terminal: {receipt['terminal_id']}")
        result = receipt.get("result")
        if isinstance(result, dict):
            if result.get("output"):
                click.echo(str(result["output"]))
            if result.get("requires_reconcile") or receipt["state"] in {"interrupted", "reconcile"}:
                click.echo("La tarea requiere revisión antes de continuar o repetirla.")
        if receipt.get("error"):
            click.echo(f"Detalle: {receipt['error']}")


@peer.command("reconcile")
@click.argument("task_id")
def reconcile_task(task_id: str) -> None:
    """Release a retained project lease after the operator checks the worker stopped."""
    try:
        receipt = local_peer_service.task_from_peer(task_id)
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        raise click.ClickException(str(error)) from error
    if receipt.get("state") in {
        "succeeded",
        "failed",
        "cancelled",
    } and local_peer_service.receipt_confirms_writer_stopped(receipt):
        click.echo(f"La tarea ya está resuelta: {receipt['state']}.")
        return
    click.echo(f"Tarea: {receipt['task_id']}")
    click.echo(f"Proyecto: {receipt['project_id']}")
    click.echo(f"Estado actual: {receipt['state']}")
    if receipt.get("terminal_id"):
        click.echo(f"Terminal asociada: {receipt['terminal_id']}")
    if not click.confirm(
        "¿Has detenido el trabajador y revisado que ya no modificará el proyecto?",
        default=False,
    ):
        raise click.ClickException("Reconciliación cancelada; el lease sigue retenido.")
    try:
        result = local_peer_service.reconcile_local_task_after_operator_review(task_id)
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        "Lease liberado tras confirmación. El resultado de la tarea sigue en revisión; "
        f"estado={result['state']}."
    )


@peer.command("revoke")
@click.argument("instance_id")
@click.option(
    "--project",
    "project_path",
    required=True,
    type=click.Path(exists=True, file_okay=False),
    help="Project whose peer permission should be revoked.",
)
def revoke_peer(instance_id: str, project_path: str) -> None:
    """Revoke a peer capability for one local project."""
    try:
        binding = local_peer_service.project_binding(project_path)
    except (local_peer_service.LocalPeerError, OSError) as error:
        raise click.ClickException(str(error)) from error
    if not click.confirm(
        f"¿Revocas el permiso de {instance_id} para {binding['canonical_root']}?", default=False
    ):
        raise click.ClickException("Revocación cancelada.")
    try:
        result = local_peer_service.revoke_peer(instance_id, str(binding["project_id"]))
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        raise click.ClickException(str(error)) from error
    if not result["revoked"]:
        click.echo("No había un permiso activo para ese par y proyecto.")
    elif result["remote_revoked"]:
        click.echo("Permiso revocado en ambos perfiles.")
    else:
        click.echo("Permiso local revocado; el perfil par no respondió para revocarlo allí.")
