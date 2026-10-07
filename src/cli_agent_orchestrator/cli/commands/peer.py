"""Operator commands for pairing and coordinating local CAO profiles."""

import json
from typing import cast

import click

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


@peer.command("pair")
@click.argument("instance_id")
@click.option(
    "--project",
    "project_path",
    required=True,
    type=click.Path(exists=True, file_okay=False),
    help="Shared local project directory.",
)
def pair_peer(instance_id: str, project_path: str) -> None:
    """Create a one-use pairing code for another active local CAO profile."""
    try:
        challenge = local_peer_service.initiate_pairing(instance_id, project_path)
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        raise click.ClickException(str(error)) from error
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
def accept_peer_pairing(challenge_id: str) -> None:
    """Review and accept a one-use pairing invitation from another CAO."""
    try:
        challenge = local_peer_auth.get_candidate_challenge(challenge_id)
    except local_peer_auth.LocalPeerAuthError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Instancia que solicita acceso: {challenge['initiator_display_name']}")
    click.echo(f"Identidad: {challenge['initiator_instance_id']}")
    click.echo(f"Proyecto: {challenge['canonical_root']}")
    click.echo(f"Acciones solicitadas: {', '.join(cast(list[str], challenge['requested_scopes']))}")
    if not click.confirm(
        "¿Autorizas este emparejamiento para el proyecto indicado?", default=False
    ):
        raise click.ClickException("Emparejamiento rechazado; no se creó permiso.")
    code = click.prompt("Código temporal", hide_input=True, confirmation_prompt=False)
    try:
        receipt = local_peer_service.accept_pairing(challenge_id, code)
    except (local_peer_service.LocalPeerError, local_peer_auth.LocalPeerAuthError) as error:
        raise click.ClickException(str(error)) from error
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
