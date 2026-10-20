"""Portable receipt and ownership-cleanup contract for private provider harnesses."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

CLEANUP_SECONDS = 25.0

TERMINAL_ID_RE = re.compile(r"[0-9a-f]{8}\Z")


_RECEIPT_STATES = {
    "planned",
    "acknowledged",
    "sent",
    "running",
    "succeeded",
    "failed",
    "reconcile",
    "cancelled",
}


CLEANUP_DIAGNOSTIC_STAGES = frozenset(
    {
        "input_validation",
        "children_list",
        "child_delete",
        "parent_delete",
        "session_delete",
        "session_absence_poll",
    }
)


CLEANUP_DIAGNOSTIC_KINDS = frozenset(
    {
        "invalid_input",
        "deadline",
        "request_exception",
        "http_status",
        "receipt_parse",
    }
)


@dataclass(frozen=True)
class NativeReceipt:
    receipt_id: str
    parent_terminal_id: str
    terminal_id: str
    state: str
    cleanup_completed: bool


def _valid_receipt_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


class OperatorFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = (
            code
            if code
            in {
                "pin_mismatch",
                "result_invalid",
                "receipt_invalid",
                "cleanup_incomplete",
                "setup_failed",
                "parent_failed",
                "tool_missing",
                "handoff_failed",
                "join_failed",
            }
            else "operator_failure"
        )
        super().__init__(self.code)


def receipt_rows(content: Mapping[str, Any], parent_terminal_id: str) -> tuple[NativeReceipt, ...]:
    if not isinstance(content, Mapping):
        raise OperatorFailure("receipt_invalid")
    rows = content.get("children")
    if (
        content.get("success") is not True
        or content.get("parent_terminal_id") != parent_terminal_id
        or not isinstance(parent_terminal_id, str)
        or not TERMINAL_ID_RE.fullmatch(parent_terminal_id)
        or not isinstance(rows, list)
    ):
        raise OperatorFailure("receipt_invalid")
    result: list[NativeReceipt] = []
    seen_ids: set[str] = set()
    seen_terminals: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise OperatorFailure("receipt_invalid")
        receipt_id = row.get("id")
        row_parent_id = row.get("parent_terminal_id")
        terminal_id, state = row.get("terminal_id"), row.get("state")
        if (
            not _valid_receipt_id(receipt_id)
            or receipt_id in seen_ids
            or row_parent_id != parent_terminal_id
            or not isinstance(terminal_id, str)
            or not TERMINAL_ID_RE.fullmatch(terminal_id)
            or terminal_id in seen_terminals
            or not isinstance(state, str)
            or state not in _RECEIPT_STATES
            or "cleanup_completed_at" not in row
        ):
            raise OperatorFailure("receipt_invalid")
        if state in {"planned", "acknowledged", "sent", "running", "reconcile"}:
            state = "active"
        seen_ids.add(receipt_id)
        seen_terminals.add(terminal_id)
        result.append(
            NativeReceipt(
                receipt_id,
                parent_terminal_id,
                terminal_id,
                state,
                row.get("cleanup_completed_at") is not None,
            )
        )
    return tuple(result)


def _note_cleanup_failure(
    diagnostic: list[tuple[str, str, int | None]] | None,
    stage: str,
    kind: str,
    status_code: int | None = None,
) -> None:
    """Keep only the first closed-vocabulary cleanup failure, without response data."""
    if diagnostic is None or diagnostic:
        return
    if stage not in CLEANUP_DIAGNOSTIC_STAGES or kind not in CLEANUP_DIAGNOSTIC_KINDS:
        return
    if status_code is not None and (type(status_code) is not int or not 100 <= status_code <= 599):
        return
    diagnostic.append((stage, kind, status_code))


def cleanup_owned(
    api: str,
    parent_id: str | None,
    session_name: str,
    *,
    diagnostic: list[tuple[str, str, int | None]] | None = None,
) -> bool:
    """Clean this fresh parent's children, parent, and session using its 25-second deadline.

    The optional private diagnostic sink records the first failed boundary using
    only a fixed stage, fixed failure kind, and an HTTP status integer. Cleanup
    request order, response handling, deadlines, and the boolean result remain
    unchanged.
    """
    import requests

    parsed = urlsplit(api)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        _note_cleanup_failure(diagnostic, "input_validation", "invalid_input")
        return False
    if parent_id is not None and (
        not isinstance(parent_id, str) or not TERMINAL_ID_RE.fullmatch(parent_id)
    ):
        _note_cleanup_failure(diagnostic, "input_validation", "invalid_input")
        return False
    if not re.fullmatch(r"mcp-stdio-[0-9a-f]{12}", session_name):
        _note_cleanup_failure(diagnostic, "input_validation", "invalid_input")
        return False
    deadline = time.monotonic() + CLEANUP_SECONDS
    owned: set[str] = set()
    complete = True

    def request(stage: str, method: str, url: str):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _note_cleanup_failure(diagnostic, stage, "deadline")
            return None
        try:
            return requests.request(
                method,
                url,
                timeout=(min(1.0, remaining), min(3.0, remaining)),
                allow_redirects=False,
            )
        except requests.RequestException:
            _note_cleanup_failure(diagnostic, stage, "request_exception")
            return None

    if parent_id is not None:
        response = request("children_list", "GET", f"{api}/terminals/{parent_id}/children")
        if response is None:
            complete = False
        elif response.status_code != 200:
            _note_cleanup_failure(diagnostic, "children_list", "http_status", response.status_code)
            complete = False
        else:
            try:
                receipts = receipt_rows(
                    {
                        "success": True,
                        "parent_terminal_id": parent_id,
                        "children": response.json(),
                    },
                    parent_id,
                )
                owned = {
                    row.terminal_id
                    for row in receipts
                    if row.parent_terminal_id == parent_id
                    and not row.cleanup_completed
                    and TERMINAL_ID_RE.fullmatch(row.terminal_id)
                }
            except Exception:
                _note_cleanup_failure(
                    diagnostic, "children_list", "receipt_parse", response.status_code
                )
                complete = False
    for child_id in sorted(owned):
        if not isinstance(child_id, str) or not TERMINAL_ID_RE.fullmatch(child_id):
            complete = False
            continue
        response = request("child_delete", "DELETE", f"{api}/terminals/{child_id}")
        if response is None:
            complete = False
        elif response.status_code not in {200, 204, 404}:
            _note_cleanup_failure(diagnostic, "child_delete", "http_status", response.status_code)
            complete = False
    if parent_id is not None:
        response = request("parent_delete", "DELETE", f"{api}/terminals/{parent_id}")
        if response is None:
            complete = False
        elif response.status_code not in {200, 204, 404}:
            _note_cleanup_failure(diagnostic, "parent_delete", "http_status", response.status_code)
            complete = False
    session_url = f"{api}/sessions/{quote(session_name, safe='')}"
    response = request("session_delete", "DELETE", session_url)
    if response is None:
        complete = False
    elif response.status_code not in {200, 204, 404}:
        _note_cleanup_failure(diagnostic, "session_delete", "http_status", response.status_code)
        complete = False
    absent = False
    last_poll_status: int | None = None
    while time.monotonic() < deadline:
        response = request("session_absence_poll", "GET", session_url)
        if response is None:
            break
        last_poll_status = response.status_code
        if response.status_code == 404:
            absent = True
            break
        if response.status_code != 200:
            _note_cleanup_failure(
                diagnostic, "session_absence_poll", "http_status", response.status_code
            )
            break
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    if not absent and diagnostic is not None and not diagnostic:
        _note_cleanup_failure(diagnostic, "session_absence_poll", "deadline", last_poll_status)
    return complete and absent


def cleanup_diagnostic_properties(
    diagnostic: list[tuple[str, str, int | None]] | None,
) -> tuple[str, str, str]:
    """Project the first fixed cleanup diagnostic into safe scalar properties."""
    if not diagnostic:
        return "none", "none", "none"
    stage, kind, status_code = diagnostic[0]
    if stage not in CLEANUP_DIAGNOSTIC_STAGES or kind not in CLEANUP_DIAGNOSTIC_KINDS:
        raise AssertionError("cleanup_diagnostic_invalid") from None
    if status_code is not None and (type(status_code) is not int or not 100 <= status_code <= 599):
        raise AssertionError("cleanup_diagnostic_invalid") from None
    return stage, kind, str(status_code) if status_code is not None else "none"
