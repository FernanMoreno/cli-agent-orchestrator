"""Current resource policy for interaction content and supervisor delegation."""

from __future__ import annotations

import json
import time
from typing import Any

from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    is_verified_principal,
)
from cli_agent_orchestrator.services.interactions.models import InteractionForbidden


class InteractionPolicy:
    """Fail-closed policy; caller hints never create a content relationship."""

    @staticmethod
    def require_principal(principal: object, *, write: bool = False) -> Principal:
        if not is_verified_principal(principal):
            raise InteractionForbidden("a verified Principal is required")
        scopes = principal.scopes
        permitted = {SCOPE_ADMIN, SCOPE_WRITE} if write else {SCOPE_ADMIN, SCOPE_WRITE, SCOPE_READ}
        if not (scopes & permitted):
            raise InteractionForbidden("Principal lacks interaction scope")
        return principal

    @staticmethod
    def require_admin(principal: object) -> Principal:
        principal = InteractionPolicy.require_principal(principal, write=True)
        if SCOPE_ADMIN not in principal.scopes:
            raise InteractionForbidden("cao:admin is required")
        return principal

    @staticmethod
    def require_owner(principal: object, source: Any) -> Principal:
        principal = InteractionPolicy.require_principal(principal)
        if source["owner_principal_id"] is None or source["owner_principal_id"] != principal.id:
            raise InteractionForbidden("Principal does not own interaction content")
        return principal

    @staticmethod
    def assignment_parent(connection, child_source: Any) -> Any | None:
        """Return the parent source only when an immutable assignment proves it."""
        assignment_id = child_source["assignment_id"]
        parent_terminal_id = child_source["parent_terminal_id"]
        parent_incarnation_id = child_source["parent_incarnation_id"]
        if not assignment_id or not parent_terminal_id or not parent_incarnation_id:
            return None
        row = connection.execute(
            "SELECT parent_terminal_id,parent_incarnation_id,state,result_json "
            "FROM assignment_intents WHERE assignment_id=%s",
            (assignment_id,),
        ).fetchone()
        if row is None or row["state"] != "submitted":
            return None
        try:
            result = json.loads(row["result_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not (
            result.get("success") is True
            and result.get("terminal_id") == child_source["terminal_id"]
            and result.get("assignment_id") == assignment_id
            and row["parent_terminal_id"] == parent_terminal_id
            and row["parent_incarnation_id"] == parent_incarnation_id
        ):
            return None
        terminal = connection.execute(
            "SELECT tmux_session,session_incarnation_id FROM terminals WHERE id=%s",
            (parent_terminal_id,),
        ).fetchone()
        if terminal is None or terminal["session_incarnation_id"] != parent_incarnation_id:
            return None
        current = connection.execute(
            "SELECT incarnation_id FROM session_incarnations WHERE session_name=%s",
            (terminal["tmux_session"],),
        ).fetchone()
        if current is not None and current["incarnation_id"] != parent_incarnation_id:
            return None
        parent = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s "
            "AND revoked_at IS NULL",
            (parent_terminal_id, parent_incarnation_id),
        ).fetchone()
        if parent is None or parent["owner_principal_id"] is None:
            return None
        return parent

    @classmethod
    def delegation_candidate(cls, connection, question: Any) -> dict[str, str] | None:
        if question["source_kind"] != "explicit" or question["kind"] in {
            "approval",
            "credential",
            "unknown",
        }:
            return None
        source = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (question["terminal_id"], question["incarnation_id"]),
        ).fetchone()
        if source is None:
            return None
        parent = cls.assignment_parent(connection, source)
        if parent is None:
            return None
        return {
            "terminal_id": parent["terminal_id"],
            "incarnation_id": parent["incarnation_id"],
            "assignment_id": source["assignment_id"],
            "owner_principal_id": parent["owner_principal_id"],
        }

    @classmethod
    def active_delegation(cls, connection, question: Any, now: float | None = None) -> Any | None:
        now = time.time() if now is None else now
        delegation = connection.execute(
            "SELECT * FROM interaction_delegations WHERE question_id=%s AND question_version=%s "
            "ORDER BY created_at DESC,delegation_id DESC LIMIT 1",
            (question["question_id"], question["version"]),
        ).fetchone()
        if (
            delegation is None
            or delegation["revoked_at"] is not None
            or delegation["expires_at"] <= now
        ):
            return None
        source = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (question["terminal_id"], question["incarnation_id"]),
        ).fetchone()
        candidate = cls.delegation_candidate(connection, question)
        if (
            source is None
            or candidate is None
            or source["revoked_at"] is not None
            or source["turn_generation"] != question["turn_generation"]
            or (delegation["owner_principal_id"] != source["owner_principal_id"])
            or delegation["owner_principal_id"] != candidate["owner_principal_id"]
            or (candidate["terminal_id"], candidate["incarnation_id"], candidate["assignment_id"])
            != (
                delegation["supervisor_terminal_id"],
                delegation["supervisor_incarnation_id"],
                delegation["assignment_id"],
            )
        ):
            return None
        return delegation

    @classmethod
    def agent_can_read(cls, connection, actor: Any, question: Any, source: Any) -> bool:
        if (
            actor.terminal_id == source["terminal_id"]
            and actor.incarnation_id == source["incarnation_id"]
        ):
            return True
        delegation = cls.active_delegation(connection, question)
        return bool(
            delegation
            and actor.terminal_id == delegation["supervisor_terminal_id"]
            and actor.incarnation_id == delegation["supervisor_incarnation_id"]
        )

    @classmethod
    def agent_can_answer(cls, connection, actor: Any, question: Any, source: Any) -> bool:
        if (
            question["human_required"]
            or question["source_kind"] != "explicit"
            or question["kind"] in {"approval", "credential", "unknown"}
        ):
            return False
        delegation = cls.active_delegation(connection, question)
        return bool(
            delegation
            and actor.terminal_id == delegation["supervisor_terminal_id"]
            and actor.incarnation_id == delegation["supervisor_incarnation_id"]
        )

    @classmethod
    def metadata_visible_to_agent(cls, connection, actor: Any, question: Any, source: Any) -> bool:
        return cls.agent_can_read(connection, actor, question, source)
