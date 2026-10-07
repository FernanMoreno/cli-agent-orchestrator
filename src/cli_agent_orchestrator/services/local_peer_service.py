"""Local CAO peer discovery, pairing, task coordination, and recovery."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import requests
from sqlalchemy.exc import IntegrityError

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import local_peer_auth
from cli_agent_orchestrator.services.local_peer_identity import (
    LocalPeerUnavailableError,
    LocalProcessIdentity,
    current_process_identity,
    signed_peer_headers,
)
from cli_agent_orchestrator.services.local_peer_registry import (
    LocalPeerRecord,
    ProfileAlreadyRunningError,
    ProjectWriteLeaseBusy,
    acquire_project_write_lease,
    get_instance,
    list_instances,
    list_project_write_leases,
    project_write_lease,
    register_instance,
    update_project_write_lease,
)

logger = logging.getLogger(__name__)

PEER_REQUEST_TIMEOUT = (2.0, 20.0)
MAX_TASK_MESSAGE = 32768
MAX_RESULT_OUTPUT = 16000


class LocalPeerError(ValueError):
    """Invalid project, pairing, or coordinated task request."""


class LocalPeerConflict(LocalPeerError):
    """An idempotency key or project write boundary conflicts with prior state."""


class LocalPeerUnavailable(LocalPeerError):
    """The selected local peer is not reachable at its verified loopback endpoint."""


def _loopback_request(method: str, url: str, **kwargs):
    """Call a validated loopback peer without honoring HTTP proxy variables."""
    parsed = urlsplit(url)
    try:
        host = parsed.hostname
        address = ipaddress.ip_address(host) if host else None
    except ValueError as error:
        raise LocalPeerUnavailable("peer requests must use a literal loopback address") from error
    if parsed.scheme != "http" or address is None or not address.is_loopback:
        raise LocalPeerUnavailable("peer requests must use a literal loopback address")
    with requests.Session() as client:
        client.trust_env = False
        return client.request(method, url, **kwargs)


def project_binding(path: str | Path) -> dict[str, str | None]:
    """Resolve one existing project root and its optional shared Git identity."""
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise LocalPeerError("project path must be an existing directory")
    git_common_dir = None
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        git_root = Path(top).resolve(strict=True)
        root = git_root
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = root / common_path
        git_common_dir = str(common_path.resolve(strict=True))
    except FileNotFoundError:
        # CAO can coordinate projects without Git when Git is not installed.
        pass
    except subprocess.CalledProcessError as error:
        if "not a git repository" not in (error.stderr or "").lower():
            raise LocalPeerError("could not verify the project's Git identity") from error
    except subprocess.TimeoutExpired as error:
        raise LocalPeerError("could not identify the project's Git root") from error

    identity = json.dumps(
        {"canonical_root": str(root), "git_common_dir": git_common_dir},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "project_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "canonical_root": str(root),
        "git_common_dir": git_common_dir,
    }


def resolve_project_path(canonical_root: str, relative_path: str | None) -> str:
    """Resolve a task working directory under its paired project root."""
    root = Path(canonical_root).resolve(strict=True)
    if relative_path is None or relative_path in ("", "."):
        return str(root)
    candidate_path = Path(relative_path)
    if candidate_path.is_absolute() or any(part == ".." for part in candidate_path.parts):
        raise LocalPeerError("task working directory must be project-relative")
    candidate = (root / candidate_path).resolve(strict=True)
    if not candidate.is_dir() or not candidate.is_relative_to(root):
        raise LocalPeerError("task working directory escapes the authorized project")
    return str(candidate)


def _public_identity() -> dict[str, object]:
    identity = current_process_identity()
    return {
        "instance_id": identity.instance_id,
        "process_generation": identity.process_generation,
        "pid": identity.pid,
        "process_started_at": identity.process_started_at,
        "display_name": identity.display_name,
        "loopback_host": identity.loopback_host,
        "loopback_port": identity.loopback_port,
        "public_key": identity.public_key,
    }


def _verify_live_peer(peer: LocalPeerRecord) -> dict[str, object]:
    """Challenge a registry candidate over a constructed loopback URL."""
    try:
        response = _loopback_request(
            "GET",
            f"{peer.base_url}/local-coordination/identity",
            timeout=(1.5, 3.0),
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise LocalPeerUnavailable("local CAO peer did not confirm its identity")
        body = response.json()
    except (requests.RequestException, ValueError) as error:
        raise LocalPeerUnavailable("local CAO peer is not reachable over loopback") from error
    if not isinstance(body, dict) or (
        body.get("instance_id") != peer.instance_id
        or body.get("process_generation") != peer.process_generation
        or body.get("loopback_port") != peer.loopback_port
        or not isinstance(body.get("public_key"), str)
        or len(str(body.get("public_key"))) < 40
    ):
        raise LocalPeerUnavailable("local CAO peer identity changed")
    return body


def current_server_identity() -> LocalProcessIdentity:
    """Resolve this profile's live CAO server identity for CLI and API callers."""
    caller = current_process_identity()
    server = get_instance(caller.instance_id)
    if server is None:
        raise LocalPeerUnavailable("the CAO server for this profile is not running locally")
    if server.pid == os.getpid() and server.process_generation == caller.process_generation:
        return caller

    live_identity = _verify_live_peer(server)
    if live_identity.get("public_key") != caller.public_key:
        raise LocalPeerUnavailable("the active CAO server does not match this local profile")
    return LocalProcessIdentity(
        instance_id=server.instance_id,
        process_generation=server.process_generation,
        pid=server.pid,
        process_started_at=server.process_started_at,
        display_name=server.display_name,
        loopback_host=server.loopback_host,
        loopback_port=server.loopback_port,
        public_key=str(live_identity["public_key"]),
    )


def local_identity() -> dict[str, object]:
    """Return this identity and restore its row if startup registration was missed."""
    try:
        identity = current_process_identity()
        # A candidate registry scan may have dropped this row after a transient
        # process-fence mismatch. Republish without pruning while serving the
        # probe; stale-row pruning itself performs identity HTTP checks outside
        # its SQLite write transaction.
        register_instance(identity, prune_stale=False)
    except (LocalPeerUnavailableError, ProfileAlreadyRunningError) as error:
        raise LocalPeerUnavailable(
            "this CAO server has not registered its local identity"
        ) from error
    return _public_identity()


def verified_peer_candidates() -> list[dict[str, object]]:
    current_id = str(current_process_identity().instance_id)
    candidates = []
    for peer in list_instances():
        if peer.instance_id == current_id:
            continue
        try:
            identity = _verify_live_peer(peer)
        except LocalPeerUnavailable:
            continue
        candidates.append(identity)
    return candidates


def _verify_registry_identity(
    instance_id: str, generation: str, port: int
) -> tuple[LocalPeerRecord, dict[str, object]]:
    peer = get_instance(instance_id)
    if peer is None or peer.process_generation != generation or peer.loopback_port != port:
        raise local_peer_auth.PairingMismatchError("local peer process identity changed")
    live_identity = _verify_live_peer(peer)
    return peer, live_identity


def verify_live_process(instance_id: str, generation: str) -> dict[str, object]:
    """Public check used by pairing acceptance before granting a capability."""
    peer = get_instance(instance_id)
    if peer is None or peer.process_generation != generation:
        raise LocalPeerUnavailable("local CAO process identity changed")
    return _verify_live_peer(peer)


def _store_project_binding(binding: dict[str, str | None]) -> None:
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerProjectModel, binding["project_id"])
        if row is not None and row.canonical_root != binding["canonical_root"]:
            raise LocalPeerConflict("project identity conflicts with an existing binding")
        if row is None:
            session.add(
                database.LocalPeerProjectModel(
                    project_id=binding["project_id"],
                    canonical_root=binding["canonical_root"],
                    git_common_dir=binding["git_common_dir"],
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise LocalPeerConflict("project binding changed concurrently") from None


def initiate_pairing(
    peer_instance_id: str,
    project_path: str,
    *,
    scopes: set[str] | None = None,
) -> dict[str, object]:
    """Create an initiator challenge and deliver it only to a verified peer."""
    peer = get_instance(peer_instance_id)
    if peer is None:
        raise LocalPeerUnavailable("selected CAO instance is not active locally")
    identity = current_server_identity()
    if peer.instance_id == identity.instance_id:
        raise LocalPeerError("a CAO profile cannot pair with itself")
    candidate = _verify_live_peer(peer)
    binding = project_binding(project_path)
    try:
        project_response = _loopback_request(
            "GET",
            f"{peer.base_url}/local-coordination/projects/verify",
            params={
                "project_path": binding["canonical_root"],
                "expected_project_id": binding["project_id"],
            },
            timeout=(1.5, 3.0),
            allow_redirects=False,
        )
        project_response.raise_for_status()
        project_receipt = project_response.json()
    except (requests.RequestException, ValueError) as error:
        raise LocalPeerUnavailable("candidate could not verify the shared project") from error
    if project_receipt.get("matches") is not True:
        raise local_peer_auth.PairingMismatchError(
            "both CAO profiles must resolve the same project"
        )
    chosen_scopes = set(scopes or local_peer_auth.DEFAULT_PEER_SCOPES)
    challenge_id = str(uuid4())
    code = local_peer_auth.new_pairing_code()
    _store_project_binding(binding)
    local_peer_auth.create_initiator_challenge(
        challenge_id=challenge_id,
        code=code,
        initiator_instance_id=identity.instance_id,
        initiator_process_generation=identity.process_generation,
        initiator_display_name=identity.display_name,
        initiator_public_key=identity.public_key,
        initiator_loopback_port=identity.loopback_port,
        candidate_instance_id=peer.instance_id,
        candidate_process_generation=peer.process_generation,
        candidate_display_name=peer.display_name,
        candidate_public_key=str(candidate["public_key"]),
        project_id=str(binding["project_id"]),
        canonical_root=str(binding["canonical_root"]),
        git_common_dir=binding["git_common_dir"],
        scopes=chosen_scopes,
    )
    body = {
        "challenge_id": challenge_id,
        "code": code,
        "expires_at": time.time() + local_peer_auth.PAIRING_TTL_SECONDS,
        "initiator_instance_id": identity.instance_id,
        "initiator_process_generation": identity.process_generation,
        "initiator_display_name": identity.display_name,
        "initiator_public_key": identity.public_key,
        "initiator_loopback_port": identity.loopback_port,
        "candidate_instance_id": peer.instance_id,
        "candidate_process_generation": peer.process_generation,
        "candidate_display_name": peer.display_name,
        "candidate_public_key": candidate["public_key"],
        "project_id": binding["project_id"],
        "canonical_root": binding["canonical_root"],
        "git_common_dir": binding["git_common_dir"],
        "requested_scopes": sorted(chosen_scopes),
    }
    try:
        response = _loopback_request(
            "POST",
            f"{peer.base_url}/local-coordination/pairings",
            json=body,
            timeout=PEER_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        receipt = response.json()
    except (requests.RequestException, ValueError) as error:
        raise LocalPeerUnavailable(
            "pairing invitation was not confirmed; its one-use challenge will expire automatically"
        ) from error
    if (
        receipt.get("challenge_id") != challenge_id
        or receipt.get("project_id") != binding["project_id"]
    ):
        raise local_peer_auth.PairingMismatchError(
            "candidate returned a different pairing challenge"
        )
    return {
        "challenge_id": challenge_id,
        "code": code,
        "expires_at": body["expires_at"],
        "peer_instance_id": candidate["instance_id"],
        "peer_display_name": candidate["display_name"],
        "project_id": binding["project_id"],
        "canonical_root": binding["canonical_root"],
        "requested_scopes": sorted(chosen_scopes),
    }


def receive_pairing_invitation(payload: dict[str, object]) -> dict[str, object]:
    """Validate the origin's current identity and shared project before storing."""
    identity = current_server_identity()
    if (
        payload.get("candidate_instance_id") != identity.instance_id
        or payload.get("candidate_process_generation") != identity.process_generation
    ):
        raise local_peer_auth.PairingMismatchError("pairing invitation targets another CAO process")
    origin, origin_identity = _verify_registry_identity(
        str(payload.get("initiator_instance_id")),
        str(payload.get("initiator_process_generation")),
        int(str(payload.get("initiator_loopback_port"))),
    )
    if origin.display_name != payload.get("initiator_display_name"):
        raise local_peer_auth.PairingMismatchError("pairing origin display identity changed")
    if origin_identity.get("public_key") != payload.get("initiator_public_key"):
        raise local_peer_auth.PairingMismatchError("pairing origin signing identity changed")
    if payload.get("candidate_public_key") != identity.public_key:
        raise local_peer_auth.PairingMismatchError("pairing candidate signing identity changed")
    binding = project_binding(str(payload.get("canonical_root")))
    if binding["project_id"] != payload.get("project_id") or binding[
        "git_common_dir"
    ] != payload.get("git_common_dir"):
        raise local_peer_auth.PairingMismatchError(
            "both CAO profiles must resolve the same project"
        )
    _store_project_binding(binding)
    sanitized = dict(payload)
    sanitized.update(
        {
            "current_instance_id": identity.instance_id,
            "candidate_instance_id": identity.instance_id,
            "candidate_process_generation": identity.process_generation,
            "candidate_display_name": identity.display_name,
            "candidate_public_key": identity.public_key,
            "canonical_root": binding["canonical_root"],
            "git_common_dir": binding["git_common_dir"],
            "project_id": binding["project_id"],
        }
    )
    return local_peer_auth.receive_candidate_challenge(sanitized)


def accept_pairing(challenge_id: str, code: str) -> dict[str, object]:
    """Complete the candidate half of a pairing after an operator confirms it."""
    challenge = local_peer_auth.get_candidate_challenge(challenge_id, code)
    identity = current_server_identity()
    if (
        challenge["candidate_instance_id"] != identity.instance_id
        or challenge["candidate_process_generation"] != identity.process_generation
    ):
        raise local_peer_auth.PairingMismatchError(
            "pairing challenge belongs to another CAO process"
        )
    binding = project_binding(str(challenge["canonical_root"]))
    if binding["project_id"] != challenge["project_id"]:
        raise local_peer_auth.PairingMismatchError("paired project identity changed")
    initiator, _ = _verify_registry_identity(
        str(challenge["initiator_instance_id"]),
        str(challenge["initiator_process_generation"]),
        int(str(challenge["initiator_loopback_port"])),
    )
    body = {
        "code": code,
        "candidate_instance_id": identity.instance_id,
        "candidate_process_generation": identity.process_generation,
        "candidate_display_name": identity.display_name,
        "candidate_public_key": identity.public_key,
        "project_id": binding["project_id"],
    }
    try:
        response = _loopback_request(
            "POST",
            f"{initiator.base_url}/local-coordination/pairings/{challenge_id}/accept",
            json=body,
            timeout=PEER_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        receipt = response.json()
    except (requests.RequestException, ValueError) as error:
        raise LocalPeerUnavailable("pairing origin could not confirm acceptance") from error
    if receipt.get("accepted") is not True or receipt.get("project_id") != binding["project_id"]:
        raise local_peer_auth.PairingMismatchError("pairing origin returned an invalid receipt")
    local_peer_auth.finish_candidate_pairing(challenge_id)
    return {
        "peer_instance_id": initiator.instance_id,
        "peer_display_name": initiator.display_name,
        "project_id": binding["project_id"],
        "scopes": challenge["requested_scopes"],
    }


def list_local_peers(*, project_id: str | None = None) -> list[dict[str, object]]:
    grants = local_peer_auth.list_peer_grants(project_id=project_id, include_revoked=True)
    active_records = {}
    for item in list_instances():
        try:
            identity = _verify_live_peer(item)
        except LocalPeerUnavailable:
            continue
        active_records[item.instance_id] = (item, identity)
    output = []
    for grant in grants:
        active = active_records.get(str(grant["peer_instance_id"]))
        record = active[0] if active else None
        live_identity = active[1] if active else None
        grant_row = local_peer_auth.get_peer_grant(
            peer_instance_id=str(grant["peer_instance_id"]),
            project_id=str(grant["project_id"]),
            include_revoked=True,
        )
        identity_matches = bool(
            record is not None
            and grant_row is not None
            and live_identity is not None
            and live_identity.get("public_key") == grant_row.peer_public_key
        )
        output.append(
            {
                **grant,
                "online": identity_matches,
                "loopback_url": (
                    record.base_url if identity_matches and record is not None else None
                ),
                "identity_matches_grant": identity_matches,
            }
        )
    return output


def _task_hash(
    *,
    source_instance_id: str,
    target_instance_id: str,
    project_id: str,
    operation_key: str,
    agent_profile: str,
    message: str,
    relative_working_directory: str,
    use_worktree: bool,
    requester_terminal_id: str | None = None,
) -> str:
    material = json.dumps(
        {
            "source_instance_id": source_instance_id,
            "target_instance_id": target_instance_id,
            "project_id": project_id,
            "operation_key": operation_key,
            "agent_profile": agent_profile,
            "message": message,
            "relative_working_directory": relative_working_directory,
            "use_worktree": use_worktree,
            "requester_terminal_id": requester_terminal_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _task_dict(row) -> dict[str, object]:
    return {
        "task_id": row.task_id,
        "source_instance_id": row.source_instance_id,
        "target_instance_id": row.target_instance_id,
        "requester_terminal_id": row.requester_terminal_id,
        "project_id": row.project_id,
        "operation_key": row.operation_key,
        "assignment_id": row.assignment_id,
        "terminal_id": row.terminal_id,
        "state": row.state,
        "result": json.loads(row.result_json) if row.result_json else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _load_project(project_id: str):
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerProjectModel, project_id)
        if row is None:
            raise LocalPeerError("project is not linked to this CAO profile")
        current = project_binding(str(row.canonical_root))
        if current["project_id"] != project_id or current["git_common_dir"] != row.git_common_dir:
            raise LocalPeerConflict("project path or Git identity changed after pairing")
        return {
            "project_id": row.project_id,
            "canonical_root": row.canonical_root,
            "git_common_dir": row.git_common_dir,
        }


def _outbound_peer(peer_instance_id: str, project_id: str, scope: str):
    grant = local_peer_auth.get_peer_grant(
        peer_instance_id=peer_instance_id, project_id=project_id, include_revoked=True
    )
    if grant is None:
        raise local_peer_auth.LocalPeerAuthError("local CAO peer is not paired for this project")
    if scope not in set(json.loads(grant.scopes_json)):
        raise local_peer_auth.PeerScopeDeniedError("local CAO peer grant lacks this action")
    if grant.revoked_at is not None and scope not in {"task:status", "task:cancel"}:
        raise local_peer_auth.LocalPeerAuthError("local CAO peer grant has been revoked")
    peer = get_instance(peer_instance_id)
    if peer is None:
        raise LocalPeerUnavailable("paired CAO process is not active locally")
    live_identity = _verify_live_peer(peer)
    if live_identity.get("public_key") != grant.peer_public_key:
        raise local_peer_auth.LocalPeerAuthError("local CAO peer signing identity changed")
    identity = current_server_identity()
    return peer, identity


def _signed_headers(*, method: str, path: str, project_id: str, body: bytes = b"", query: str = ""):
    return signed_peer_headers(
        method=method,
        path=path,
        query=query,
        project_id=project_id,
        body=body,
        identity=current_server_identity(),
    )


def _ensure_task_request(
    *,
    peer_instance_id: str,
    project_id: str,
    operation_key: str,
    agent_profile: str,
    message: str,
    relative_working_directory: str | None,
    use_worktree: bool | None,
    requester_terminal_id: str | None = None,
) -> tuple[dict[str, object], str, str, bool]:
    if not isinstance(operation_key, str) or not 8 <= len(operation_key) <= 128:
        raise LocalPeerError("operation_key must contain between 8 and 128 characters")
    if any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
        for char in operation_key
    ):
        raise LocalPeerError("operation_key contains unsupported characters")
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_TASK_MESSAGE:
        raise LocalPeerError("task message must be nonempty and within the size limit")
    if not isinstance(agent_profile, str) or not agent_profile.strip() or len(agent_profile) > 128:
        raise LocalPeerError("agent profile is invalid")
    project = _load_project(project_id)
    relative_path = relative_working_directory or "."
    working_directory = resolve_project_path(str(project["canonical_root"]), relative_path)
    git_project = project["git_common_dir"] is not None
    requested_worktree = git_project if use_worktree is None else use_worktree
    if requested_worktree != git_project:
        raise LocalPeerError(
            "Git peer tasks require an isolated worktree; non-Git tasks use a shared write lease"
        )
    if requester_terminal_id is not None and (
        len(requester_terminal_id) != 8
        or any(char not in "0123456789abcdef" for char in requester_terminal_id)
    ):
        raise LocalPeerError("requesting terminal identity is invalid")
    source = current_server_identity()
    request_hash = _task_hash(
        source_instance_id=source.instance_id,
        target_instance_id=peer_instance_id,
        project_id=project_id,
        operation_key=operation_key,
        agent_profile=agent_profile.strip(),
        message=message,
        relative_working_directory=relative_path,
        use_worktree=requested_worktree,
        requester_terminal_id=requester_terminal_id,
    )
    return project, request_hash, working_directory, requested_worktree


def _claim_local_task(
    *,
    task_id: str,
    source_instance_id: str,
    requester_terminal_id: str | None,
    target_instance_id: str,
    project_id: str,
    operation_key: str,
    request_hash: str,
    use_worktree: bool,
    requester_principal_id: str | None = None,
) -> tuple[dict[str, object], bool]:
    with database.SessionLocal() as session:
        existing = (
            session.query(database.LocalPeerTaskModel)
            .filter_by(
                source_instance_id=source_instance_id,
                requester_terminal_id=requester_terminal_id,
                project_id=project_id,
                operation_key=operation_key,
            )
            .first()
        )
        if existing is not None:
            if (
                requester_principal_id is not None
                and existing.requester_principal_id != requester_principal_id
            ):
                raise LocalPeerConflict("operation_key belongs to another authenticated principal")
            if existing.request_hash != request_hash or existing.task_id != task_id:
                raise LocalPeerConflict("operation_key was reused with different task material")
            return _task_dict(existing), False
        row = database.LocalPeerTaskModel(
            task_id=task_id,
            source_instance_id=source_instance_id,
            target_instance_id=target_instance_id,
            requester_terminal_id=requester_terminal_id,
            requester_principal_id=requester_principal_id,
            project_id=project_id,
            operation_key=operation_key,
            request_hash=request_hash,
            use_worktree=use_worktree,
            state="accepted",
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            recovered = session.get(database.LocalPeerTaskModel, task_id)
            if (
                recovered is None
                or recovered.request_hash != request_hash
                or (
                    requester_principal_id is not None
                    and recovered.requester_principal_id != requester_principal_id
                )
            ):
                raise LocalPeerConflict(
                    "coordinated task key conflicts with existing state"
                ) from None
            return _task_dict(recovered), False
        return _task_dict(row), True


def _update_task(task_id: str, **updates) -> dict[str, object]:
    allowed = {
        "assignment_id",
        "terminal_id",
        "state",
        "result_json",
    }
    if set(updates) - allowed:
        raise ValueError("unsupported local peer task update")
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerTaskModel, task_id)
        if row is None:
            raise LocalPeerError("coordinated task was not found")
        # One conditional SQL write chooses the terminal winner across threads
        # and processes. A delayed status/cancel response cannot replace it.
        changes: dict[Any, Any] = dict(updates)
        changes["updated_at"] = datetime.now(timezone.utc)
        session.query(database.LocalPeerTaskModel).filter(
            database.LocalPeerTaskModel.task_id == task_id,
            database.LocalPeerTaskModel.state.notin_({"succeeded", "failed", "cancelled"}),
        ).update(changes, synchronize_session=False)
        session.commit()
        session.refresh(row)
        return _task_dict(row)


def submit_task_to_peer(
    *,
    peer_instance_id: str,
    project_id: str,
    operation_key: str,
    agent_profile: str,
    message: str,
    relative_working_directory: str | None = None,
    use_worktree: bool | None = None,
    requester_terminal_id: str | None = None,
    requester_principal_id: str | None = None,
) -> dict[str, object]:
    project, request_hash, _, chosen_worktree = _ensure_task_request(
        peer_instance_id=peer_instance_id,
        project_id=project_id,
        operation_key=operation_key,
        agent_profile=agent_profile,
        message=message,
        relative_working_directory=relative_working_directory,
        use_worktree=use_worktree,
        requester_terminal_id=requester_terminal_id,
    )
    source = current_server_identity()
    grant = local_peer_auth.get_peer_grant(peer_instance_id=peer_instance_id, project_id=project_id)
    if grant is None or "task:submit" not in set(json.loads(grant.scopes_json)):
        raise local_peer_auth.PeerScopeDeniedError(
            "local CAO peer grant does not allow task submission"
        )
    with database.SessionLocal() as session:
        row = (
            session.query(database.LocalPeerTaskModel)
            .filter_by(
                source_instance_id=source.instance_id,
                requester_terminal_id=requester_terminal_id,
                project_id=project_id,
                operation_key=operation_key,
            )
            .first()
        )
    task_id = str(row.task_id) if row is not None else str(uuid4())
    local_receipt, _ = _claim_local_task(
        task_id=task_id,
        source_instance_id=source.instance_id,
        requester_terminal_id=requester_terminal_id,
        target_instance_id=peer_instance_id,
        project_id=project_id,
        operation_key=operation_key,
        request_hash=request_hash,
        use_worktree=chosen_worktree,
        requester_principal_id=requester_principal_id,
    )
    if local_receipt["state"] in {"succeeded", "failed", "cancelled"}:
        return local_receipt
    peer, _ = _outbound_peer(peer_instance_id, project_id, "task:submit")
    body = {
        "task_id": task_id,
        "source_instance_id": source.instance_id,
        "source_process_generation": source.process_generation,
        "requester_terminal_id": requester_terminal_id,
        "project_id": project_id,
        "operation_key": operation_key,
        "request_hash": request_hash,
        "agent_profile": agent_profile.strip(),
        "message": message,
        "relative_working_directory": relative_working_directory or ".",
        "use_worktree": chosen_worktree,
    }
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request_path = "/local-coordination/tasks"
    headers = _signed_headers(
        method="POST", path=request_path, project_id=project_id, body=body_bytes
    )
    headers["Content-Type"] = "application/json"
    try:
        response = _loopback_request(
            "POST",
            f"{peer.base_url}{request_path}",
            data=body_bytes,
            headers=headers,
            timeout=PEER_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code == 409:
            detail = response.json().get("detail", {})
            if detail.get("kind") == "project_busy":
                return {**local_receipt, "state": "accepted", "error": "project_busy"}
        response.raise_for_status()
        remote_receipt = response.json()
    except (requests.RequestException, ValueError) as error:
        return _update_task(task_id, state="reconcile") | {"error": "reconcile_required"}
    return _update_task(
        task_id,
        assignment_id=remote_receipt.get("assignment_id"),
        terminal_id=remote_receipt.get("terminal_id"),
        state=str(remote_receipt.get("state", "reconcile")),
        result_json=(
            json.dumps(remote_receipt.get("result"), sort_keys=True)
            if remote_receipt.get("result") is not None
            else None
        ),
    )


async def accept_incoming_task(payload: dict[str, object], *, registry) -> dict[str, object]:
    """Durably claim an authorized task before provisioning its worker."""
    identity = current_server_identity()
    source_id = str(payload["source_instance_id"])
    source_record = get_instance(source_id)
    if source_record is None:
        raise local_peer_auth.PairingMismatchError("source CAO process is not active locally")
    _verify_registry_identity(
        source_id, str(payload["source_process_generation"]), source_record.loopback_port
    )
    project_id = str(payload["project_id"])
    project = _load_project(project_id)
    relative_path = str(payload.get("relative_working_directory") or ".")
    working_directory = resolve_project_path(str(project["canonical_root"]), relative_path)
    requester_terminal_id = str(payload["requester_terminal_id"])
    use_worktree = bool(payload.get("use_worktree"))
    if use_worktree != (project["git_common_dir"] is not None):
        raise LocalPeerError("task write policy differs from the authorized project type")
    expected_hash = _task_hash(
        source_instance_id=source_id,
        target_instance_id=identity.instance_id,
        project_id=project_id,
        operation_key=str(payload["operation_key"]),
        agent_profile=str(payload["agent_profile"]),
        message=str(payload["message"]),
        relative_working_directory=relative_path,
        use_worktree=use_worktree,
        requester_terminal_id=requester_terminal_id,
    )
    if expected_hash != payload.get("request_hash"):
        raise LocalPeerConflict("task request hash does not match its content")
    task_id = str(payload["task_id"])
    try:
        __import__("uuid").UUID(task_id)
    except ValueError as error:
        raise LocalPeerError("task_id must be a UUID") from error
    # A replay after a lost response returns the durable task receipt.
    with database.SessionLocal() as session:
        existing = session.get(database.LocalPeerTaskModel, task_id)
        by_key = (
            session.query(database.LocalPeerTaskModel)
            .filter_by(
                source_instance_id=source_id,
                requester_terminal_id=requester_terminal_id,
                project_id=project_id,
                operation_key=str(payload["operation_key"]),
            )
            .first()
        )
        if by_key is not None:
            if by_key.task_id != task_id or by_key.request_hash != expected_hash:
                raise LocalPeerConflict("operation_key was reused with different task material")
            return refresh_local_task(task_id)
        if existing is not None:
            raise LocalPeerConflict("task_id is already owned by another request")

    owner = f"local-peer:{source_id}:{requester_terminal_id}:{project_id}"
    if not use_worktree:
        acquire_project_write_lease(
            project_id=project_id, task_id=task_id, owner_instance_id=identity.instance_id
        )
    row = database.LocalPeerTaskModel(
        task_id=task_id,
        source_instance_id=source_id,
        target_instance_id=identity.instance_id,
        requester_terminal_id=requester_terminal_id,
        project_id=project_id,
        operation_key=str(payload["operation_key"]),
        request_hash=expected_hash,
        use_worktree=use_worktree,
        state="accepted",
    )
    with database.SessionLocal() as session:
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.get(database.LocalPeerTaskModel, task_id)
            if existing is not None and (
                existing.request_hash == expected_hash
                and existing.source_instance_id == source_id
                and existing.requester_terminal_id == requester_terminal_id
                and existing.project_id == project_id
            ):
                return refresh_local_task(task_id)
            if not use_worktree:
                update_project_write_lease(
                    project_id=project_id,
                    task_id=task_id,
                    owner_instance_id=identity.instance_id,
                    state="reconcile",
                )
            raise LocalPeerConflict("coordinated task claim changed concurrently") from None

    from cli_agent_orchestrator.services.assignment_service import (
        LocalPeerAssignmentRequest,
        assign_local_peer_fresh,
        local_peer_assignment_id,
    )

    assignment_request = LocalPeerAssignmentRequest(
        operation_key=str(payload["operation_key"]),
        agent_profile=str(payload["agent_profile"]),
        message=str(payload["message"]),
        working_directory=working_directory,
        use_worktree=use_worktree,
        worktree_subdirectory=relative_path if use_worktree else None,
    )
    assignment_id = local_peer_assignment_id(
        owner=owner, operation_key=assignment_request.operation_key
    )
    # Persist the deterministic link before entering terminal creation. If this
    # process stops after the terminal's own idempotency row commits but before
    # this task receives the response, status can recover the terminal handle.
    _update_task(task_id, assignment_id=assignment_id)
    try:
        assignment = await assign_local_peer_fresh(
            assignment_request,
            owner=owner,
            registry=registry,
        )
    except Exception:
        if not use_worktree:
            update_project_write_lease(
                project_id=project_id,
                task_id=task_id,
                owner_instance_id=identity.instance_id,
                state="reconcile",
            )
        _update_task(task_id, state="reconcile")
        raise
    # The HTTP endpoint awaits the returned coroutine; leaving it unawaited here
    # would let the route lose the durable admission outcome.
    return _update_task(
        task_id,
        assignment_id=assignment.get("assignment_id"),
        terminal_id=assignment.get("terminal_id"),
        state="running" if assignment.get("state") == "submitted" else str(assignment.get("state")),
    )


def _find_task(task_id: str) -> tuple[dict[str, object], bool]:
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerTaskModel, task_id)
        if row is None:
            raise LocalPeerError("coordinated task was not found")
        return _task_dict(row), bool(row.use_worktree)


def _repair_terminal_receipt(
    task_id: str, *, operator_confirmed_stopped: bool = False
) -> dict[str, object]:
    """Repair old contradictory receipts without changing their terminal winner."""
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerTaskModel, task_id)
        if row is None:
            raise LocalPeerError("coordinated task was not found")
        if row.state not in {"succeeded", "failed", "cancelled"}:
            return _task_dict(row)
        original = cast(str | None, row.result_json)
        result = json.loads(original) if original else None
        coherent = isinstance(result, dict) and result.get("state") == row.state
        if coherent and not operator_confirmed_stopped:
            return _task_dict(row)
        repaired = dict(result) if isinstance(result, dict) else {}
        repaired["state"] = row.state
        repaired["terminal_id"] = row.terminal_id
        # The old result described another observation. Keep output, but do not
        # turn its status into proof that the winning task stopped writing.
        if not coherent:
            repaired.pop("terminal_status", None)
            repaired.pop("worker_stopped", None)
        if operator_confirmed_stopped:
            repaired["operator_confirmed_stopped"] = True
            repaired["worker_stopped"] = True
        session.query(database.LocalPeerTaskModel).filter(
            database.LocalPeerTaskModel.task_id == task_id,
            database.LocalPeerTaskModel.state == row.state,
            database.LocalPeerTaskModel.result_json == original,
        ).update({"result_json": json.dumps(repaired, sort_keys=True)}, synchronize_session=False)
        session.commit()
        session.refresh(row)
        return _task_dict(row)


def receipt_confirms_writer_stopped(task: dict[str, object]) -> bool:
    result = task.get("result")
    if not isinstance(result, dict) or result.get("state") != task.get("state"):
        return False
    return (
        result.get("worker_stopped") is True
        or (task["state"] == "succeeded" and result.get("terminal_status") == "completed")
        or (task["state"] == "failed" and result.get("terminal_status") == "error")
    )


def refresh_local_task(task_id: str) -> dict[str, object]:
    """Project terminal evidence into a durable peer-task receipt."""
    task, use_worktree = _find_task(task_id)
    if not task.get("terminal_id") and task.get("assignment_id"):
        idempotency = database.get_idempotency_record(str(task["assignment_id"]))
        if idempotency is not None:
            task = _update_task(task_id, terminal_id=idempotency.terminal_id)
    if task["state"] in {"succeeded", "failed", "cancelled"}:
        task = _repair_terminal_receipt(task_id)
        # Receipt and shared lease live in different SQLite databases. Retry the
        # exact-owner release after a crash/failure between their two commits.
        if (
            receipt_confirms_writer_stopped(task)
            and not use_worktree
            and task["target_instance_id"] == current_process_identity().instance_id
        ):
            update_project_write_lease(
                project_id=str(task["project_id"]),
                task_id=task_id,
                owner_instance_id=str(task["target_instance_id"]),
                state="released",
            )
        return task
    if not task.get("terminal_id"):
        return task

    from cli_agent_orchestrator.services import terminal_service
    from cli_agent_orchestrator.services.terminal_service import OutputMode

    terminal_id = str(task["terminal_id"])
    try:
        terminal = terminal_service.get_terminal(terminal_id)
    except (ValueError, FileNotFoundError):
        _update_task(
            task_id,
            state="interrupted",
            result_json=json.dumps(
                {
                    "state": "interrupted",
                    "terminal_id": terminal_id,
                    "requires_reconcile": True,
                },
                sort_keys=True,
            ),
        )
        if not use_worktree:
            update_project_write_lease(
                project_id=str(task["project_id"]),
                task_id=task_id,
                owner_instance_id=current_process_identity().instance_id,
                state="interrupted",
            )
        return _find_task(task_id)[0]

    terminal_state = str(terminal.get("status", "unknown"))
    if terminal_state == "completed":
        state = "succeeded"
    elif terminal_state == "error":
        state = "failed"
    elif terminal_state == "reconcile":
        state = "reconcile"
    elif terminal_state in {
        "idle",
        "processing",
        "waiting_user_answer",
        "waiting_quota",
    }:
        state = "running"
    else:
        state = "reconcile"

    result: dict[str, object] = {
        "state": state,
        "terminal_id": terminal_id,
        "terminal_status": terminal_state,
    }
    metadata = database.get_terminal_metadata(terminal_id) or {}
    working_directory = metadata.get("working_directory")
    if working_directory:
        project = _load_project(str(task["project_id"]))
        try:
            work_path = Path(working_directory).resolve(strict=True)
            root = Path(str(project["canonical_root"])).resolve(strict=True)
            result["worktree_path"] = str(work_path.relative_to(root)) if use_worktree else None
        except (OSError, ValueError):
            result["worktree_path"] = None
    if state in {"succeeded", "failed"}:
        try:
            output = terminal_service.get_output(terminal_id, OutputMode.LAST)
            result["output"] = output[-MAX_RESULT_OUTPUT:]
        except Exception:
            logger.info("Could not capture final local peer task output", exc_info=True)
    stored = _update_task(task_id, state=state, result_json=json.dumps(result, sort_keys=True))
    if not use_worktree:
        lease_state = (
            "released"
            if stored["state"] in {"succeeded", "failed", "cancelled"}
            else (
                "held"
                if stored["state"] == "running"
                else "interrupted" if stored["state"] == "interrupted" else "reconcile"
            )
        )
        update_project_write_lease(
            project_id=str(task["project_id"]),
            task_id=task_id,
            owner_instance_id=current_process_identity().instance_id,
            state=lease_state,
        )
    return stored


def reconcile_local_peer_tasks_at_startup() -> None:
    """Mark receipts whose worker/assignment was interrupted by server shutdown."""
    identity = current_process_identity()
    with database.SessionLocal() as session:
        candidates = (
            session.query(database.LocalPeerTaskModel)
            .filter(
                database.LocalPeerTaskModel.state.in_(
                    ["accepted", "running", "succeeded", "failed", "cancelled"]
                )
            )
            .all()
        )
        task_ids = [str(row.task_id) for row in candidates]

    # The write lease is in the cross-profile registry while task receipts are
    # in this profile's database, so a process can die between those commits.
    # No worker is created until AFTER its task row commits. Therefore a lease
    # owned by this profile with no corresponding task receipt is safe to release.
    for lease in list_project_write_leases(owner_instance_id=identity.instance_id):
        if lease["state"] == "released":
            continue
        with database.SessionLocal() as session:
            task_exists = (
                session.query(database.LocalPeerTaskModel.task_id)
                .filter_by(task_id=lease["task_id"], target_instance_id=identity.instance_id)
                .first()
                is not None
            )
        if not task_exists:
            update_project_write_lease(
                project_id=str(lease["project_id"]),
                task_id=str(lease["task_id"]),
                owner_instance_id=identity.instance_id,
                state="released",
            )

    for task_id in task_ids:
        try:
            task, use_worktree = _find_task(task_id)
            if task.get("terminal_id") or task.get("assignment_id"):
                recovered = refresh_local_task(task_id)
                if recovered.get("terminal_id"):
                    continue
                task, use_worktree = _find_task(task_id)
            if task.get("terminal_id"):
                continue
            _update_task(
                task_id,
                state="interrupted",
                result_json=json.dumps(
                    {
                        "state": "interrupted",
                        "requires_reconcile": True,
                        "reason": "CAO stopped before recording the worker terminal.",
                    },
                    sort_keys=True,
                ),
            )
            if not use_worktree:
                update_project_write_lease(
                    project_id=str(task["project_id"]),
                    task_id=task_id,
                    owner_instance_id=str(task["target_instance_id"]),
                    state="interrupted",
                )
        except Exception:
            logger.warning(
                "Could not reconcile local CAO peer task %s during startup", task_id, exc_info=True
            )


def reconcile_local_task_after_operator_review(task_id: str) -> dict[str, object]:
    """Release a fenced local project only after the operator confirms the worker stopped."""
    task, use_worktree = _find_task(task_id)
    instance_id = current_process_identity().instance_id
    if task["target_instance_id"] != instance_id:
        raise LocalPeerError("run reconciliation in the CAO profile that owns the worker")
    if task["state"] in {"succeeded", "failed", "cancelled"} and receipt_confirms_writer_stopped(
        task
    ):
        return refresh_local_task(task_id)

    terminal_id = task.get("terminal_id")
    if terminal_id:
        from cli_agent_orchestrator.services import terminal_service

        try:
            terminal = terminal_service.get_terminal(str(terminal_id))
        except (ValueError, FileNotFoundError):
            terminal = None
        if terminal is not None:
            terminal_state = str(terminal.get("status", "unknown"))
            if terminal_state in {
                "idle",
                "processing",
                "waiting_user_answer",
                "waiting_quota",
            }:
                raise LocalPeerConflict(
                    "the task terminal still exists; stop it and confirm its state before reconciling"
                )
            if terminal_state in {"completed", "error"} and task["state"] not in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                return refresh_local_task(task_id)

    if not use_worktree:
        lease = project_write_lease(str(task["project_id"]))
        if lease is not None:
            if lease["task_id"] != task_id or lease["owner_instance_id"] != instance_id:
                raise LocalPeerConflict("the project write lease belongs to another task")
            if not update_project_write_lease(
                project_id=str(task["project_id"]),
                task_id=task_id,
                owner_instance_id=instance_id,
                state="released",
            ):
                raise LocalPeerConflict("the project write lease changed during reconciliation")

    if task["state"] in {"succeeded", "failed", "cancelled"}:
        return _repair_terminal_receipt(task_id, operator_confirmed_stopped=True)
    receipt = {
        "state": "reconcile",
        "requires_reconcile": True,
        "operator_confirmed_stopped": True,
        "terminal_id": terminal_id,
        "note": "Worker stopped by operator; review its project changes and result manually.",
    }
    return _update_task(
        task_id,
        state="reconcile",
        result_json=json.dumps(receipt, sort_keys=True),
    )


def task_for_peer(
    task_id: str,
    *,
    peer_instance_id: str,
    project_id: str,
    requester_terminal_id: str | None,
) -> dict[str, object]:
    task, _ = _find_task(task_id)
    if (
        task["source_instance_id"] != peer_instance_id
        or task["target_instance_id"] != current_process_identity().instance_id
        or task["project_id"] != project_id
        or task.get("requester_terminal_id") != requester_terminal_id
    ):
        raise LocalPeerError("coordinated task was not found for this peer and project")
    return refresh_local_task(task_id)


def task_from_peer(task_id: str, *, requester_terminal_id: str | None = None) -> dict[str, object]:
    """Read a local task receipt, querying its target when this profile is source."""
    task, _ = _find_task(task_id)
    identity = current_server_identity()
    if (
        requester_terminal_id is not None
        and task.get("requester_terminal_id") != requester_terminal_id
    ):
        raise LocalPeerError("coordinated task does not belong to this calling terminal")
    if task["target_instance_id"] == identity.instance_id:
        return refresh_local_task(task_id)
    if task["source_instance_id"] != identity.instance_id:
        raise LocalPeerError("coordinated task does not belong to this CAO profile")
    if task["state"] in {"succeeded", "failed", "cancelled"}:
        return _repair_terminal_receipt(task_id)
    peer_id = str(task["target_instance_id"])
    peer, _ = _outbound_peer(peer_id, str(task["project_id"]), "task:status")
    requester_id = str(task.get("requester_terminal_id") or "")
    if not requester_id:
        return {**task, "state": "reconcile", "error": "requester_identity_missing"}
    path = f"/local-coordination/tasks/{task_id}"
    query = urlencode(
        [("project_id", str(task["project_id"])), ("requester_terminal_id", requester_id)]
    )
    headers = _signed_headers(
        method="GET", path=path, query=query, project_id=str(task["project_id"])
    )
    try:
        response = _loopback_request(
            "GET",
            f"{peer.base_url}{path}?{query}",
            headers=headers,
            timeout=PEER_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        remote_receipt = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.info("Local peer task status is unavailable; retaining its receipt")
        return {**task, "peer_state": "unavailable", "error": "peer_unavailable"}
    return _update_task(
        task_id,
        assignment_id=remote_receipt.get("assignment_id"),
        terminal_id=remote_receipt.get("terminal_id"),
        state=str(remote_receipt.get("state", "reconcile")),
        result_json=(
            json.dumps(remote_receipt.get("result"), sort_keys=True)
            if remote_receipt.get("result") is not None
            else None
        ),
    )


def cancel_task_at_peer(task_id: str) -> dict[str, object]:
    task, _ = _find_task(task_id)
    identity = current_server_identity()
    if task["source_instance_id"] != identity.instance_id:
        raise LocalPeerError("only the source CAO can request remote task cancellation")
    peer, _ = _outbound_peer(
        str(task["target_instance_id"]), str(task["project_id"]), "task:cancel"
    )
    requester_id = str(task.get("requester_terminal_id") or "")
    if not requester_id:
        raise LocalPeerError("requester terminal identity is missing")
    path = f"/local-coordination/tasks/{task_id}/cancel"
    query = urlencode(
        [("project_id", str(task["project_id"])), ("requester_terminal_id", requester_id)]
    )
    headers = _signed_headers(
        method="POST", path=path, query=query, project_id=str(task["project_id"])
    )
    try:
        response = _loopback_request(
            "POST",
            f"{peer.base_url}{path}?{query}",
            headers=headers,
            timeout=PEER_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        receipt = response.json()
    except (requests.RequestException, ValueError) as error:
        return {**task, "state": "reconcile", "error": "reconcile_required"}
    return _update_task(
        task_id,
        state=str(receipt.get("state", "reconcile")),
        result_json=(
            json.dumps(receipt.get("result"), sort_keys=True)
            if receipt.get("result") is not None
            else None
        ),
    )


def cancel_incoming_task(
    task_id: str,
    *,
    peer_instance_id: str,
    project_id: str,
    requester_terminal_id: str | None,
    registry,
) -> dict[str, object]:
    task, use_worktree = _find_task(task_id)
    if (
        task["source_instance_id"] != peer_instance_id
        or task["target_instance_id"] != current_process_identity().instance_id
        or task["project_id"] != project_id
        or task.get("requester_terminal_id") != requester_terminal_id
    ):
        raise LocalPeerError("coordinated task was not found for this peer and project")
    current = refresh_local_task(task_id)
    if current["state"] in {"succeeded", "failed", "cancelled"}:
        return current
    terminal_id = current.get("terminal_id")
    if not terminal_id:
        return _update_task(task_id, state="reconcile")
    from cli_agent_orchestrator.services import terminal_service

    stopped = terminal_service.delete_terminal(str(terminal_id), registry=registry)
    if not stopped:
        if not use_worktree:
            update_project_write_lease(
                project_id=project_id,
                task_id=task_id,
                owner_instance_id=current_process_identity().instance_id,
                state="reconcile",
            )
        return _update_task(task_id, state="reconcile")
    receipt = _update_task(
        task_id,
        state="cancelled",
        result_json=json.dumps(
            {"state": "cancelled", "terminal_id": str(terminal_id), "worker_stopped": True},
            sort_keys=True,
        ),
    )
    if not use_worktree:
        update_project_write_lease(
            project_id=project_id,
            task_id=task_id,
            owner_instance_id=current_process_identity().instance_id,
            state="released",
        )
    return receipt


def revoke_peer(peer_instance_id: str, project_id: str) -> dict[str, object]:
    """Block this profile immediately, then ask the peer to revoke its grant."""
    grant = local_peer_auth.get_peer_grant(
        peer_instance_id=peer_instance_id, project_id=project_id, include_revoked=True
    )
    if grant is None:
        return {"revoked": False, "remote_revoked": False}
    revoked = local_peer_auth.revoke_peer_grant(
        peer_instance_id=peer_instance_id, project_id=project_id
    )
    remote_revoked = False
    peer = get_instance(peer_instance_id)
    if peer is not None:
        identity = current_process_identity()
        path = f"/local-coordination/peers/{identity.instance_id}"
        query = urlencode([("project_id", project_id)])
        headers = _signed_headers(method="DELETE", path=path, query=query, project_id=project_id)
        try:
            _verify_live_peer(peer)
            response = _loopback_request(
                "DELETE",
                f"{peer.base_url}{path}?{query}",
                headers=headers,
                timeout=PEER_REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            remote_revoked = response.status_code in {200, 204, 403, 404}
        except (requests.RequestException, LocalPeerUnavailable):
            logger.info("Remote CAO peer is unavailable during revocation")
    return {
        "revoked": revoked or grant.revoked_at is not None,
        "local_revoked": True,
        "remote_revoked": remote_revoked,
    }


def project_session_snapshot(project_id: str) -> dict[str, object]:
    """Read local project sessions without leaking another project's terminals."""
    from cli_agent_orchestrator.services import session_service

    _load_project(project_id)
    result: dict[str, object] = {
        "project_id": project_id,
        "state": "empty",
        "sessions": [],
        "disappeared_count": 0,
    }
    sessions = []
    disappeared_count = 0
    try:
        # The generic listing deliberately turns backend errors into []. Read
        # the backend directly here so peer callers can distinguish downtime.
        from cli_agent_orchestrator.constants import SESSION_PREFIX

        for summary in session_service.get_backend().list_sessions():
            if not str(summary.get("id", "")).startswith(SESSION_PREFIX):
                continue
            try:
                snapshot = session_service.get_session(str(summary["id"]))
            except (ValueError, FileNotFoundError):
                disappeared_count += 1
                continue
            terminals = []
            for terminal in snapshot.get("terminals", []):
                cwd = terminal.get("working_directory")
                if not cwd:
                    continue
                try:
                    matches = project_binding(cwd)["project_id"] == project_id
                except (OSError, ValueError):
                    continue
                if matches:
                    terminals.append(terminal)
            if terminals:
                sessions.append({"session": snapshot["session"], "terminals": terminals})
    except Exception:
        logger.info("Local peer session backend unavailable", exc_info=True)
        result["state"] = "unavailable"
        return result
    result["disappeared_count"] = disappeared_count
    result["sessions"] = sessions
    result["state"] = "available" if sessions else "empty"
    return result


def sessions_from_peer(peer_instance_id: str, project_id: str) -> dict[str, object]:
    """Read one project snapshot with its dedicated peer capability."""
    unavailable = {
        "project_id": project_id,
        "state": "unavailable",
        "sessions": [],
        "disappeared_count": 0,
    }
    try:
        peer, _ = _outbound_peer(peer_instance_id, project_id, "session:read")
        path = "/local-coordination/sessions"
        query = urlencode([("project_id", project_id)])
        response = _loopback_request(
            "GET",
            f"{peer.base_url}{path}?{query}",
            headers=_signed_headers(method="GET", path=path, query=query, project_id=project_id),
            timeout=PEER_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
        snapshot = response.json()
        if snapshot.get("project_id") != project_id:
            raise ValueError("peer session snapshot project differs")
        if snapshot.get("state") not in {"available", "empty", "unavailable"} or not isinstance(
            snapshot.get("sessions"), list
        ):
            raise ValueError("peer session snapshot is invalid")
        return cast(dict[str, object], snapshot)
    except local_peer_auth.LocalPeerAuthError:
        raise
    except (LocalPeerUnavailable, requests.RequestException, ValueError):
        return unavailable
