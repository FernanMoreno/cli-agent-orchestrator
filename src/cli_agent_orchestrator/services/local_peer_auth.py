"""Pairing and project-scoped capabilities for trusted local CAO peers."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services.local_peer_identity import peer_public_key_bytes

DEFAULT_PEER_SCOPES = frozenset(
    {"task:submit", "task:status", "task:cancel", "peer:revoke", "session:read"}
)
PAIRING_TTL_SECONDS = 300


class LocalPeerAuthError(PermissionError):
    """Pairing or peer-capability validation failed."""


class PairingExpiredError(LocalPeerAuthError):
    pass


class PairingMismatchError(LocalPeerAuthError):
    pass


class PeerScopeDeniedError(LocalPeerAuthError):
    pass


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_pairing_code() -> str:
    return secrets.token_urlsafe(32)


def _validate_public_key(value: str) -> str:
    try:
        peer_public_key_bytes(value)
    except (ValueError, TypeError) as error:
        raise PairingMismatchError("peer signing key is invalid") from error
    return value


def _grant_row(
    session,
    *,
    peer_instance_id: str,
    project_id: str,
    peer_display_name: str,
    peer_public_key: str,
    scopes: set[str],
) -> None:
    if not scopes or not scopes <= DEFAULT_PEER_SCOPES:
        raise PairingMismatchError("pairing requested unsupported peer actions")
    existing = (
        session.query(database.LocalPeerGrantModel)
        .filter_by(peer_instance_id=peer_instance_id, project_id=project_id)
        .first()
    )
    peer_public_key = _validate_public_key(peer_public_key)
    if existing is not None and existing.revoked_at is None:
        if existing.peer_public_key == peer_public_key:
            return
        raise PairingMismatchError("peer is already authorized for this project; revoke it first")
    if existing is None:
        existing = database.LocalPeerGrantModel(
            grant_id=str(uuid4()),
            peer_instance_id=peer_instance_id,
            project_id=project_id,
            peer_display_name=peer_display_name,
            peer_public_key=peer_public_key,
            scopes_json=json.dumps(sorted(scopes)),
        )
        session.add(existing)
    else:
        existing.peer_display_name = peer_display_name
        existing.peer_public_key = peer_public_key
        existing.scopes_json = json.dumps(sorted(scopes))
        existing.revoked_at = None
        existing.created_at = datetime.now(timezone.utc)


def create_initiator_challenge(
    *,
    challenge_id: str,
    code: str,
    initiator_instance_id: str,
    initiator_process_generation: str,
    initiator_display_name: str,
    initiator_public_key: str,
    initiator_loopback_port: int,
    candidate_instance_id: str,
    candidate_process_generation: str,
    candidate_display_name: str,
    candidate_public_key: str,
    project_id: str,
    canonical_root: str,
    git_common_dir: str | None,
    scopes: set[str],
) -> None:
    if not scopes or not scopes <= DEFAULT_PEER_SCOPES:
        raise PairingMismatchError("pairing requested unsupported peer actions")
    now = time.time()
    with database.SessionLocal() as session:
        session.add(
            database.LocalPeerChallengeModel(
                challenge_id=challenge_id,
                role="initiator",
                code_hash=hash_secret(code),
                initiator_instance_id=initiator_instance_id,
                initiator_process_generation=initiator_process_generation,
                initiator_display_name=initiator_display_name,
                initiator_public_key=_validate_public_key(initiator_public_key),
                initiator_loopback_port=initiator_loopback_port,
                candidate_instance_id=candidate_instance_id,
                candidate_process_generation=candidate_process_generation,
                candidate_display_name=candidate_display_name,
                candidate_public_key=_validate_public_key(candidate_public_key),
                project_id=project_id,
                canonical_root=canonical_root,
                git_common_dir=git_common_dir,
                requested_scopes_json=json.dumps(sorted(scopes)),
                expires_at=now + PAIRING_TTL_SECONDS,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise PairingMismatchError("pairing challenge already exists") from None


def receive_candidate_challenge(payload: dict[str, object]) -> dict[str, object]:
    """Store an incoming challenge without authorizing its initiator."""
    challenge_id = str(payload["challenge_id"])
    candidate = str(payload["candidate_instance_id"])
    current_generation = str(payload["candidate_process_generation"])
    initiator = str(payload["initiator_instance_id"])
    initiator_public_key = _validate_public_key(str(payload["initiator_public_key"]))
    candidate_public_key = _validate_public_key(str(payload["candidate_public_key"]))
    scopes = payload["requested_scopes"]
    if candidate != payload["current_instance_id"]:
        raise PairingMismatchError("pairing challenge names a different candidate instance")
    if not isinstance(scopes, list) or not scopes or not set(scopes) <= DEFAULT_PEER_SCOPES:
        raise PairingMismatchError("pairing requested unsupported peer actions")
    code = str(payload["code"])
    if len(code) < 32 or len(code) > 256:
        raise PairingMismatchError("pairing code has an invalid length")
    now = time.time()
    with database.SessionLocal() as session:
        existing = session.get(database.LocalPeerChallengeModel, challenge_id)
        if existing is not None:
            if (
                existing.role != "candidate"
                or existing.code_hash != hash_secret(code)
                or existing.initiator_instance_id != initiator
                or existing.candidate_instance_id != candidate
                or existing.initiator_process_generation
                != str(payload["initiator_process_generation"])
                or existing.initiator_public_key != initiator_public_key
                or existing.project_id != str(payload["project_id"])
                or existing.candidate_public_key != candidate_public_key
                or json.loads(cast(str, existing.requested_scopes_json)) != sorted(set(scopes))
            ):
                raise PairingMismatchError("pairing challenge conflicts with existing state")
            return _challenge_public(existing)
        expires_at = min(
            float(str(payload.get("expires_at") or now + PAIRING_TTL_SECONDS)),
            now + PAIRING_TTL_SECONDS,
        )
        if expires_at <= now:
            raise PairingExpiredError("pairing challenge expired")
        row = database.LocalPeerChallengeModel(
            challenge_id=challenge_id,
            role="candidate",
            code_hash=hash_secret(code),
            initiator_instance_id=initiator,
            initiator_process_generation=str(payload["initiator_process_generation"]),
            initiator_display_name=str(payload["initiator_display_name"]),
            initiator_public_key=initiator_public_key,
            initiator_loopback_port=int(str(payload["initiator_loopback_port"])),
            candidate_instance_id=candidate,
            candidate_process_generation=current_generation,
            candidate_display_name=str(payload["candidate_display_name"]),
            candidate_public_key=candidate_public_key,
            project_id=str(payload["project_id"]),
            canonical_root=str(payload["canonical_root"]),
            git_common_dir=(str(payload["git_common_dir"]) if payload["git_common_dir"] else None),
            requested_scopes_json=json.dumps(sorted(set(scopes))),
            expires_at=expires_at,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise PairingMismatchError("pairing challenge already exists") from None
        return _challenge_public(row)


def _challenge_public(row) -> dict[str, object]:
    return {
        "challenge_id": row.challenge_id,
        "role": row.role,
        "initiator_instance_id": row.initiator_instance_id,
        "initiator_process_generation": row.initiator_process_generation,
        "initiator_display_name": row.initiator_display_name,
        "initiator_public_key": row.initiator_public_key,
        "initiator_loopback_port": row.initiator_loopback_port,
        "candidate_instance_id": row.candidate_instance_id,
        "candidate_process_generation": row.candidate_process_generation,
        "candidate_display_name": row.candidate_display_name,
        "candidate_public_key": row.candidate_public_key,
        "project_id": row.project_id,
        "canonical_root": row.canonical_root,
        "git_common_dir": row.git_common_dir,
        "requested_scopes": json.loads(row.requested_scopes_json),
        "expires_at": row.expires_at,
        "consumed_at": row.consumed_at,
    }


def get_candidate_challenge(challenge_id: str, code: str | None = None) -> dict[str, object]:
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerChallengeModel, challenge_id)
        if row is None or row.role != "candidate":
            raise PairingMismatchError("pairing challenge not found")
        if row.expires_at <= time.time():
            raise PairingExpiredError("pairing challenge expired")
        if row.consumed_at is not None:
            raise PairingMismatchError("pairing challenge was already used")
        if code is not None and not hmac.compare_digest(row.code_hash, hash_secret(code)):
            raise PairingMismatchError("pairing code does not match")
        return _challenge_public(row)


def accept_at_initiator(
    *,
    challenge_id: str,
    code: str,
    candidate_instance_id: str,
    candidate_process_generation: str,
    candidate_display_name: str,
    candidate_public_key: str,
    project_id: str,
) -> None:
    """Pin the candidate's public key into a scoped grant after one-use approval."""
    candidate_public_key = _validate_public_key(candidate_public_key)
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerChallengeModel, challenge_id)
        if row is None or row.role != "initiator":
            raise PairingMismatchError("pairing challenge not found")
        if (
            row.candidate_instance_id != candidate_instance_id
            or row.candidate_process_generation != candidate_process_generation
            or row.candidate_public_key != candidate_public_key
            or row.project_id != project_id
        ):
            raise PairingMismatchError("pairing identity or project does not match")
        if not hmac.compare_digest(cast(str, row.code_hash), hash_secret(code)):
            raise PairingMismatchError("pairing code does not match")
        if row.consumed_at is not None:
            grant = (
                session.query(database.LocalPeerGrantModel)
                .filter_by(peer_instance_id=candidate_instance_id, project_id=project_id)
                .first()
            )
            if (
                grant is not None
                and grant.revoked_at is None
                and grant.peer_public_key == candidate_public_key
            ):
                return None
            raise PairingMismatchError("pairing challenge was already used")
        if row.expires_at <= time.time():
            raise PairingExpiredError("pairing challenge expired")
        scopes = set(json.loads(row.requested_scopes_json))
        _grant_row(
            session,
            peer_instance_id=candidate_instance_id,
            project_id=project_id,
            peer_display_name=candidate_display_name,
            peer_public_key=candidate_public_key,
            scopes=scopes,
        )
        row.consumed_at = time.time()
        session.commit()
        return None


def finish_candidate_pairing(challenge_id: str) -> None:
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerChallengeModel, challenge_id)
        if row is None or row.role != "candidate":
            raise PairingMismatchError("pairing challenge not found")
        if row.consumed_at is not None:
            grant = (
                session.query(database.LocalPeerGrantModel)
                .filter_by(peer_instance_id=row.initiator_instance_id, project_id=row.project_id)
                .first()
            )
            if (
                grant is not None
                and grant.revoked_at is None
                and grant.peer_public_key == row.initiator_public_key
            ):
                return
            raise PairingMismatchError("pairing challenge was already used")
        if row.expires_at <= time.time():
            raise PairingExpiredError("pairing challenge expired")
        scopes = set(json.loads(row.requested_scopes_json))
        _grant_row(
            session,
            peer_instance_id=row.initiator_instance_id,
            project_id=row.project_id,
            peer_display_name=row.initiator_display_name,
            peer_public_key=row.initiator_public_key,
            scopes=scopes,
        )
        row.consumed_at = time.time()
        session.commit()


def authenticate_peer(
    *,
    peer_instance_id: str,
    peer_process_generation: str,
    project_id: str,
    signature: str,
    method: str,
    path: str,
    query: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
    body: bytes,
    required_scope: str,
    task_id: str | None = None,
    requester_terminal_id: str | None = None,
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{64}", body_sha256) is None:
        raise LocalPeerAuthError("peer request body signature is invalid")
    actual_body_hash = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(body_sha256, actual_body_hash):
        raise LocalPeerAuthError("peer request body signature does not match")
    with database.SessionLocal() as session:
        grant = (
            session.query(database.LocalPeerGrantModel)
            .filter_by(peer_instance_id=peer_instance_id, project_id=project_id)
            .first()
        )
        if grant is None:
            raise LocalPeerAuthError("peer is not authorized for this project")
        from cli_agent_orchestrator.services.local_peer_identity import verify_peer_signature

        if not verify_peer_signature(
            public_key=cast(str, grant.peer_public_key),
            method=method,
            path=path,
            query=query,
            project_id=project_id,
            instance_id=peer_instance_id,
            process_generation=peer_process_generation,
            timestamp=timestamp,
            nonce=nonce,
            body_sha256=body_sha256,
            signature=signature,
        ):
            raise LocalPeerAuthError("peer request signature is invalid or expired")
        scopes = set(json.loads(cast(str, grant.scopes_json)))
        if required_scope not in scopes:
            raise PeerScopeDeniedError("peer does not have the required action")
        if grant.revoked_at is not None:
            retained_status = (
                required_scope in {"task:status", "task:cancel"} and task_id is not None
            )
            owned_task = None
            if retained_status:
                owned_task = (
                    session.query(database.LocalPeerTaskModel)
                    .filter_by(
                        task_id=task_id,
                        source_instance_id=peer_instance_id,
                        project_id=project_id,
                        requester_terminal_id=requester_terminal_id,
                    )
                    .first()
                )
            if owned_task is None:
                raise LocalPeerAuthError("peer authorization has been revoked")
        session.execute(
            delete(database.LocalPeerRequestNonceModel).where(
                database.LocalPeerRequestNonceModel.expires_at < time.time()
            )
        )
        try:
            session.add(
                database.LocalPeerRequestNonceModel(
                    peer_instance_id=peer_instance_id,
                    nonce=nonce,
                    expires_at=time.time() + 300,
                )
            )
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise LocalPeerAuthError("peer request has already been used") from error
        return {
            "peer_instance_id": grant.peer_instance_id,
            "project_id": grant.project_id,
            "scopes": sorted(scopes),
            "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
        }


def list_peer_grants(*, project_id: str | None = None, include_revoked: bool = False) -> list[dict]:
    with database.SessionLocal() as session:
        query = session.query(database.LocalPeerGrantModel)
        if project_id is not None:
            query = query.filter_by(project_id=project_id)
        if not include_revoked:
            query = query.filter(database.LocalPeerGrantModel.revoked_at.is_(None))
        return [
            {
                "peer_instance_id": row.peer_instance_id,
                "project_id": row.project_id,
                "peer_display_name": row.peer_display_name,
                "scopes": json.loads(cast(str, row.scopes_json)),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            }
            for row in query.order_by(database.LocalPeerGrantModel.peer_display_name).all()
        ]


def get_peer_grant(*, peer_instance_id: str, project_id: str, include_revoked: bool = False):
    with database.SessionLocal() as session:
        query = session.query(database.LocalPeerGrantModel).filter_by(
            peer_instance_id=peer_instance_id, project_id=project_id
        )
        if not include_revoked:
            query = query.filter(database.LocalPeerGrantModel.revoked_at.is_(None))
        return query.first()


def revoke_peer_grant(*, peer_instance_id: str, project_id: str) -> bool:
    with database.SessionLocal() as session:
        row = (
            session.query(database.LocalPeerGrantModel)
            .filter_by(peer_instance_id=peer_instance_id, project_id=project_id)
            .first()
        )
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(timezone.utc)
        session.commit()
        return True
