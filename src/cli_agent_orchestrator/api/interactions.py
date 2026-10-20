"""HTTP transport for durable agent interactions.

Human routes always use a sealed :class:`Principal`; agent routes use only the
per-incarnation interaction capability.  The shared MCP service bearer is a
transport credential and is never treated as a human or agent identity.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, Union
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    _extract_bearer,
    get_current_principal,
    get_local_bearer,
    is_verified_principal,
)
from cli_agent_orchestrator.security.local_token import ascii_token_matches
from cli_agent_orchestrator.services.interactions.models import (
    AgentActor,
    AnswerAck,
    CreateGroupRequest,
    InteractionAnswerPayload,
)
from cli_agent_orchestrator.services.interactions.service import (
    InteractionCapacityExceeded,
    InteractionConflict,
    InteractionError,
    InteractionForbidden,
    InteractionInvalid,
    InteractionNotFound,
    InteractionService,
    InteractionStale,
)


class _InteractionAPIRoute(APIRoute):
    """Keep opaque ACK receipts out of pre-endpoint validation responses."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()
        if self.name != "acknowledge_interaction_answers":
            return route_handler

        async def handle_ack_request(request: Request) -> Any:
            try:
                return await route_handler(request)
            except RequestValidationError:
                return _safe_problem(InteractionInvalid("answer receipt is malformed"))

        return handle_ack_request


router = APIRouter(tags=["interactions"], route_class=_InteractionAPIRoute)
_AGENT_CAPABILITY_HEADER = "X-CAO-Interaction-Capability"
_UUID_PATH_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_QuestionIdPath = Annotated[str, Path(pattern=_UUID_PATH_PATTERN)]
_GroupIdPath = Annotated[str, Path(pattern=_UUID_PATH_PATTERN)]
_Lifecycle = Literal[
    "open", "answered", "expired", "cancelled", "stale", "manual_only", "reconciled_external"
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _OptionRequest(_StrictModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=1024)


class _QuestionRequest(_StrictModel):
    client_key: Optional[str] = Field(default=None, min_length=1, max_length=128)
    kind: Literal["text", "single_choice", "multi_choice", "choice_or_text", "approval"]
    prompt: str = Field(min_length=1, max_length=16384)
    required: bool
    allow_free_text: bool = False
    options: list[_OptionRequest] = Field(default_factory=list, max_length=32)

    @field_validator("prompt")
    @classmethod
    def _prompt_utf8_cap(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 16384:
            raise ValueError("prompt exceeds 16384 UTF-8 bytes")
        return value

    @model_validator(mode="after")
    def _question_shape(self):
        option_ids = [option.id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option ids must be unique")
        if any(len(option.label.encode("utf-8")) > 1024 for option in self.options):
            raise ValueError("option labels exceed 1024 UTF-8 bytes")
        if self.kind == "text" and self.options:
            raise ValueError("text questions cannot declare options")
        if self.kind == "single_choice" and self.allow_free_text:
            raise ValueError("single_choice does not allow free text")
        if self.allow_free_text and self.kind not in {"text", "choice_or_text"}:
            raise ValueError("free text is valid only for text or choice_or_text")
        if self.kind in {"single_choice", "multi_choice", "approval"} and not self.options:
            raise ValueError("choice questions require declared options")
        if self.kind == "choice_or_text" and not self.allow_free_text and not self.options:
            raise ValueError("choice_or_text requires options or free text")
        if self.kind == "approval" and not self.required:
            raise ValueError("approval questions are always required")
        return self


class _CreateGroupBody(_StrictModel):
    questions: list[_QuestionRequest] = Field(min_length=1, max_length=20)
    expires_in_seconds: int = Field(default=1800, ge=1, le=86400)
    wait_for_required: bool = True


class _TextAnswer(_StrictModel):
    kind: Literal["text"]
    text: str = Field(max_length=4096)


class _OptionsAnswer(_StrictModel):
    kind: Literal["options"]
    option_ids: list[str] = Field(min_length=1, max_length=32)

    @field_validator("option_ids")
    @classmethod
    def _unique_options(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("option_ids must be unique")
        return value


class _FreeTextAnswer(_StrictModel):
    kind: Literal["free_text"]
    text: str = Field(max_length=4096)


_Answer = Annotated[
    Union[_TextAnswer, _OptionsAnswer, _FreeTextAnswer], Field(discriminator="kind")
]


class _AnswerBody(_StrictModel):
    expected_version: int = Field(ge=1)
    response: _Answer


class _ReasonBody(_StrictModel):
    reason: str = Field(min_length=1, max_length=512)


class _ReconcileBody(_StrictModel):
    outcome: Literal[
        "answered_outside_cao", "no_effect_proven", "remains_uncertain", "dialog_still_open"
    ]
    evidence_code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$",
    )
    note: Optional[str] = Field(default=None, max_length=512)


class _ContentOwnerBody(_StrictModel):
    expected_incarnation_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


class _DelegateBody(_StrictModel):
    expected_assignment_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=512)
    expires_in_seconds: int = Field(default=60, ge=1, le=300)


class _WaitBody(_StrictModel):
    wait_seconds: int = Field(ge=0, le=30)
    after_event_sequence: Optional[int] = Field(default=None, ge=0)


class _AckReceipt(_StrictModel):
    question_id: UUID
    version: int = Field(ge=1)
    receipt: str = Field(min_length=32, max_length=128)


class _AckBody(_StrictModel):
    receipts: list[_AckReceipt] = Field(min_length=1, max_length=20)


class _EscalateBody(_StrictModel):
    reason_code: Literal[
        "human_decision", "insufficient_authority", "supervisor_unavailable", "deadline"
    ]
    detail: Optional[str] = Field(default=None, max_length=512)


_VERSION_ETAG = re.compile(r'^"([1-9][0-9]*)"$')


def get_interaction_service(request: Request) -> InteractionService:
    """Resolve the app-scoped service, with the process singleton as fallback."""
    service = getattr(request.app.state, "interaction_service", None)
    if isinstance(service, InteractionService):
        return service
    if service is not None:
        raise RuntimeError("app.state.interaction_service must be an InteractionService")
    from cli_agent_orchestrator.services.interactions.service import (
        get_default_interaction_service,
    )

    return get_default_interaction_service()


def _safe_problem(exc: Exception) -> JSONResponse:
    """Map service failures without serializing internal exception arguments."""
    mapping: tuple[tuple[type[BaseException], int, str, str], ...] = (
        (InteractionForbidden, 403, "forbidden", "Interaction authority is required."),
        (InteractionNotFound, 404, "not_found", "Interaction was not found."),
        (InteractionConflict, 409, "conflict", "The interaction conflicts with current state."),
        (InteractionStale, 410, "stale", "The interaction source or version is no longer current."),
        (InteractionInvalid, 422, "invalid", "The interaction request is invalid."),
        (
            InteractionCapacityExceeded,
            429,
            "capacity_exceeded",
            "Interaction capacity is full; no active request was evicted.",
        ),
    )
    code, detail, status_code = "unavailable", "Interaction service is unavailable.", 503
    for exception_type, mapped_status, mapped_code, mapped_detail in mapping:
        if isinstance(exc, exception_type):
            status_code, code, detail = mapped_status, mapped_code, mapped_detail
            break
    payload: dict[str, Any] = {"code": code, "detail": detail, "current_version": None}
    current_version = getattr(exc, "current_version", None)
    if isinstance(current_version, int) and current_version > 0:
        payload["current_version"] = current_version
    return JSONResponse(status_code=status_code, content=payload)


async def _invoke(service: InteractionService, method: str, *args: Any, **kwargs: Any) -> Any:
    """Run synchronous durable operations off the event loop and normalize errors."""
    try:
        operation = getattr(service, method)
        if inspect.iscoroutinefunction(operation):
            result = await operation(*args, **kwargs)
        else:
            result = await asyncio.to_thread(operation, *args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
    except InteractionError as exc:
        return _safe_problem(exc)
    except Exception as exc:
        # Exception messages may include caller supplied content or secrets.
        # Keep the failure path useful without putting those values in logs or responses.
        return _safe_problem(exc)
    if method == "create_group" and hasattr(result, "replayed"):
        # CreatedGroup intentionally excludes this internal field from normal
        # serialization; preserve it only long enough to choose 201 vs 200.
        encoded = jsonable_encoder(result, exclude_none=False)
        if isinstance(encoded, dict):
            encoded["replayed"] = bool(result.replayed)
            return encoded
    return jsonable_encoder(result, exclude_none=False)


def _check_response(result: Any) -> Any:
    if isinstance(result, JSONResponse):
        return result
    return result


def _answer_receipt_wire(value: Any) -> Any:
    """Project ledger receipt fields onto the closed HTTP contract."""
    if value is None:
        return None
    receipt = jsonable_encoder(value, exclude_none=False)
    allowed = (
        "question_id",
        "version",
        "answer_id",
        "lifecycle",
        "delivery_state",
        "accepted_at",
        "replayed",
    )
    return {key: receipt.get(key) for key in allowed}


def _metadata_wire(value: Any) -> dict[str, Any]:
    """Return only safe, contract-defined metadata fields."""
    item = jsonable_encoder(value, exclude_none=False)
    source = item.get("source") or {}
    task = item.get("task")
    recipient = item.get("recipient") or {"kind": "unassigned", "label": None, "reason": "unknown"}
    kind = item.get("kind")
    # The ledger keeps its conservative internal ``unknown`` observation kind
    # so it cannot be mistaken for one of the typed explicit question kinds.
    # The public contract names that safe, native-only notice explicitly.
    if item.get("origin") == "native" and kind == "unknown":
        kind = "native_unknown"
    recipient_kind = recipient.get("kind")
    if recipient_kind == "agent":
        recipient_kind = "supervisor"
    result = {
        "question_id": item.get("question_id"),
        "version": item.get("version"),
        "group_id": item.get("group_id"),
        "lifecycle": item.get("lifecycle"),
        "delivery_state": item.get("delivery_state"),
        "source": {
            "terminal_id": source.get("terminal_id"),
            "incarnation_id": source.get("incarnation_id"),
            "provider": source.get("provider"),
            "provider_version": source.get("provider_version"),
            "execution_mode": source.get("execution_mode"),
        },
        "task": (
            {
                "work_item_id": task.get("work_item_id"),
                "attempt_id": task.get("attempt_id"),
                "session_name": task.get("session_name"),
            }
            if task is not None
            else None
        ),
        "kind": kind,
        "required": item.get("required"),
        "recipient": {
            "kind": recipient_kind,
            "label": recipient.get("label"),
            "reason": recipient.get("reason"),
        },
        "group_progress": item.get("group_progress"),
        "origin": item.get("origin"),
        "can_answer": bool(item.get("can_answer", False)),
        "manual_reason": item.get("manual_reason"),
        "created_at": item.get("created_at"),
        "expires_at": item.get("expires_at"),
    }
    return result


def _delegation_wire(value: Any) -> dict[str, Any] | None:
    """Project internal delegation evidence to the closed public DTO."""
    if not isinstance(value, dict):
        return None
    terminal_id = value.get("terminal_id") or value.get("supervisor_terminal_id")
    assignment_id = value.get("assignment_id")
    expires_at = value.get("expires_at")
    if not isinstance(terminal_id, str) or not isinstance(assignment_id, str):
        return None
    if not isinstance(expires_at, str):
        return None
    state = value.get("state")
    if state not in {"active", "revoked", "expired"}:
        if value.get("revoked_at") is not None:
            state = "revoked"
        else:
            try:
                deadline = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                state = "expired" if deadline <= datetime.now(timezone.utc) else "active"
            except ValueError:
                return None
    return {
        "terminal_id": terminal_id,
        "assignment_id": assignment_id,
        "expires_at": expires_at,
        "state": state,
    }


def _detail_wire(value: Any) -> dict[str, Any]:
    """Project a protected detail to the flattened, closed response schema."""
    item = jsonable_encoder(value, exclude_none=False)
    result = _metadata_wire(item)
    result.update(
        {
            "prompt": item.get("prompt"),
            "options": item.get("options", []),
            "allow_free_text": bool(item.get("allow_free_text", False)),
            "answer_receipt": _answer_receipt_wire(item.get("answer_receipt")),
            "delegation_candidate": item.get("delegation_candidate"),
            "delegation": _delegation_wire(item.get("delegation")),
        }
    )
    return result


def _page_wire(value: Any, *, detail: bool = False) -> dict[str, Any]:
    page = jsonable_encoder(value, exclude_none=False)
    items = page.get("items", []) if isinstance(page, dict) else []
    mapper = _detail_wire if detail else _metadata_wire
    return {"items": [mapper(item) for item in items], "next_cursor": page.get("next_cursor")}


def _created_group_wire(value: Any) -> tuple[dict[str, Any], bool]:
    group = jsonable_encoder(value, exclude_none=False)
    questions = []
    for question in group.get("questions", []):
        questions.append(
            {
                "question_id": question.get("question_id"),
                "version": question.get("version", 1),
                "required": bool(question.get("required", False)),
                "lifecycle": question.get("lifecycle", group.get("lifecycle", "open")),
                "expires_at": question.get("expires_at", group.get("expires_at")),
            }
        )
    replayed = bool(group.get("replayed", False))
    return (
        {
            "group_id": group.get("group_id"),
            "lifecycle": group.get("lifecycle", "open"),
            "questions": questions,
            "wait_required": any(question["required"] for question in questions),
        },
        replayed,
    )


def _wait_result_wire(value: Any) -> dict[str, Any]:
    result = jsonable_encoder(value, exclude_none=False)
    state = result.get("state")
    answers = []
    # A non-ready poll is deliberately body-free, even if the repository
    # returns an earlier optional answer alongside a newly pending question.
    if state == "ready":
        for answer in result.get("answers", []):
            response = jsonable_encoder(answer.get("response"), exclude_none=False)
            if response is None:
                response = {
                    "kind": answer.get("kind"),
                    "text": answer.get("text"),
                    "option_ids": answer.get("option_ids", []),
                }
            # The domain payload stores nullable text and empty option_ids for
            # convenience. The closed HTTP union exposes only the fields for
            # the selected response kind.
            answer_kind = response.get("kind")
            if answer_kind == "options":
                response = {
                    "kind": "options",
                    "option_ids": response.get("option_ids", []),
                }
            elif answer_kind in {"text", "free_text"}:
                response = {"kind": answer_kind, "text": response.get("text")}
            answers.append(
                {
                    "question_id": answer.get("question_id"),
                    "version": answer.get("version"),
                    "receipt": answer.get("receipt"),
                    "response": response,
                }
            )
    wire = {
        "group_id": result.get("group_id"),
        "state": state,
        "answers": answers,
        "next_poll_after_ms": result.get("next_poll_after_ms", 1000),
    }
    if "next_event_sequence" in result:
        wire["next_event_sequence"] = result["next_event_sequence"]
    return wire


def _capability_wire(value: Any) -> dict[str, Any]:
    capabilities = jsonable_encoder(value, exclude_none=False)
    if not isinstance(capabilities, list):
        capabilities = [capabilities]
    items = []
    for capability in capabilities:
        items.append(
            {
                "provider": capability.get("provider"),
                "provider_version": capability.get("provider_version"),
                "execution_mode": capability.get("execution_mode"),
                "emits_explicit": capability.get("emits_explicit", False),
                "detects_native": capability.get("detects_native", False),
                "responds_native": capability.get(
                    "responds_native", capability.get("responses_native", [])
                ),
                "manual_reason": capability.get("manual_reason"),
                "required_mcp_tools": capability.get("required_mcp_tools", []),
            }
        )
    return {"items": items}


def _scope(principal: Principal, scope: str) -> None:
    allowed = {scope, SCOPE_ADMIN}
    if scope == SCOPE_READ:
        # Match the existing require_any_scope(read, write, admin) read shape;
        # metadata access still grants no protected prompt/answer content.
        allowed.add(SCOPE_WRITE)
    if not principal.scopes.intersection(allowed):
        raise HTTPException(status_code=403, detail=f"Forbidden: requires {scope}")


async def _human_principal(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Require a sealed principal and reject the configured service bearer."""
    if request.headers.get(_AGENT_CAPABILITY_HEADER):
        raise HTTPException(status_code=403, detail="agent capability is not a human identity")
    if not is_verified_principal(principal):
        raise HTTPException(status_code=401, detail="verified human principal required")
    presented = _extract_bearer(request.headers.get("Authorization"))
    service_token = get_local_bearer()
    if presented and service_token and ascii_token_matches(presented, service_token):
        raise HTTPException(status_code=403, detail="service bearer is not a human identity")
    return principal


async def optional_creation_principal(request: Request) -> Optional[Principal]:
    """Capture trusted creator identity without tightening legacy launch routes.

    Existing launch APIs support auth-off deployments beyond the local-only
    identity route. In those contexts a failed Principal resolution means the
    new interaction source remains unowned; it does not reject the launch.
    The configured MCP service bearer is never recorded as a human owner.
    """
    if request.headers.get(_AGENT_CAPABILITY_HEADER):
        return None
    try:
        principal = await get_current_principal(request, request.headers.get("Authorization"))
    except HTTPException:
        return None
    if not is_verified_principal(principal):
        return None
    presented = _extract_bearer(request.headers.get("Authorization"))
    service_token = get_local_bearer()
    if presented and service_token and ascii_token_matches(presented, service_token):
        return None
    return principal


def _capability(request: Request) -> str:
    capability = request.headers.get(_AGENT_CAPABILITY_HEADER)
    if not capability:
        raise HTTPException(status_code=401, detail="interaction capability required")
    if len(capability) > 512:
        raise HTTPException(status_code=401, detail="interaction capability is invalid")
    return capability


def _version_from_etag(value: str) -> int:
    match = _VERSION_ETAG.fullmatch(value)
    if match is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_if_match",
                "detail": 'If-Match must be a strong quoted question version, such as "1".',
                "current_version": None,
            },
        )
    return int(match.group(1))


def _match_versions(header_value: str, body_version: int) -> int:
    header_version = _version_from_etag(header_value)
    if header_version != body_version:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "version_header_mismatch",
                "detail": "If-Match and expected_version must identify the same question version.",
                "current_version": None,
            },
        )
    return header_version


@router.get("/interactions")
async def list_interactions(
    request: Request,
    status_filter: Annotated[Optional[list[_Lifecycle]], Query(alias="status")] = None,
    terminal_id: Optional[str] = None,
    work_item_id: Optional[str] = None,
    recipient: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_READ)
    service = get_interaction_service(request)
    filters = {
        "status": status_filter,
        "terminal_id": terminal_id,
        "work_item_id": work_item_id,
        "recipient": recipient,
    }
    result = await _invoke(
        service, "list_metadata", principal, filters=filters, cursor=cursor, limit=limit
    )
    if isinstance(result, JSONResponse):
        return result
    result = _page_wire(result)
    return _check_response(result)


@router.put("/interactions/sources/{terminal_id}/content-owner")
async def designate_interaction_content_owner(
    terminal_id: str,
    body: _ContentOwnerBody,
    request: Request,
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_ADMIN)
    service = get_interaction_service(request)
    result = await _invoke(
        service,
        "designate_content_owner",
        principal,
        terminal_id,
        body.expected_incarnation_id,
        body.reason,
    )
    if isinstance(result, JSONResponse):
        return result
    # The service result contains no credential. Keep this response intentionally small.
    source = result.get("source", result) if isinstance(result, dict) else {}
    return {
        "terminal_id": terminal_id,
        "incarnation_id": source.get("incarnation_id", body.expected_incarnation_id),
        "status": "content_owner_designated",
    }


@router.get("/interactions/{question_id}")
async def get_interaction(
    question_id: _QuestionIdPath,
    request: Request,
    expected_version: Optional[int] = Query(default=None, ge=1),
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_READ)
    result = await _invoke(
        get_interaction_service(request), "get_detail", principal, question_id, expected_version
    )
    if isinstance(result, JSONResponse):
        return result
    return _detail_wire(result)


@router.post("/interactions/{question_id}/delegation")
async def delegate_interaction_question(
    question_id: _QuestionIdPath,
    body: _DelegateBody,
    request: Request,
    if_match: Annotated[str, Header(alias="If-Match")],
    principal: Principal = Depends(_human_principal),
) -> Any:
    """Grant one reviewed clarification to its current related supervisor."""
    _scope(principal, SCOPE_WRITE)
    version = _version_from_etag(if_match)
    service = get_interaction_service(request)
    result = await _invoke(
        service,
        "delegate_question",
        principal,
        question_id,
        version,
        expected_assignment_id=body.expected_assignment_id,
        reason=body.reason,
        expires_in_seconds=body.expires_in_seconds,
    )
    if isinstance(result, JSONResponse):
        return result
    detail = await _invoke(service, "get_detail", principal, question_id)
    return detail if isinstance(detail, JSONResponse) else _detail_wire(detail)


@router.delete("/interactions/{question_id}/delegation")
async def revoke_interaction_delegation(
    question_id: _QuestionIdPath,
    body: _ReasonBody,
    request: Request,
    if_match: Annotated[str, Header(alias="If-Match")],
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_WRITE)
    version = _version_from_etag(if_match)
    service = get_interaction_service(request)
    result = await _invoke(
        service,
        "revoke_delegation",
        principal,
        question_id,
        version,
        reason=body.reason,
    )
    if isinstance(result, JSONResponse):
        return result
    detail = await _invoke(service, "get_detail", principal, question_id)
    return detail if isinstance(detail, JSONResponse) else _detail_wire(detail)


@router.post("/interactions/{question_id}/answers")
async def accept_interaction_answer(
    question_id: _QuestionIdPath,
    body: _AnswerBody,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
    if_match: Annotated[str, Header(alias="If-Match")],
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_WRITE)
    version = _match_versions(if_match, body.expected_version)
    payload = InteractionAnswerPayload.model_validate(body.response.model_dump(mode="json"))
    result = await _invoke(
        get_interaction_service(request),
        "accept_answer",
        principal,
        question_id,
        version,
        payload,
        idempotency_key,
    )
    if isinstance(result, JSONResponse):
        return result
    result = _answer_receipt_wire(result)
    return JSONResponse(status_code=200 if result.get("replayed", False) else 201, content=result)


@router.post("/interactions/{question_id}/cancel")
async def cancel_interaction(
    question_id: _QuestionIdPath,
    body: _ReasonBody,
    request: Request,
    if_match: Annotated[str, Header(alias="If-Match")],
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_WRITE)
    version = _version_from_etag(if_match)
    return _check_response(
        await _invoke(
            get_interaction_service(request), "cancel", principal, question_id, version, body.reason
        )
    )


@router.post("/interactions/{question_id}/reconcile")
async def reconcile_interaction(
    question_id: _QuestionIdPath,
    body: _ReconcileBody,
    request: Request,
    if_match: Annotated[str, Header(alias="If-Match")],
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_WRITE)
    version = _version_from_etag(if_match)
    return _check_response(
        await _invoke(
            get_interaction_service(request),
            "reconcile",
            principal,
            question_id,
            version,
            body.outcome,
            body.evidence_code,
            body.note,
        )
    )


@router.get("/interactions/{question_id}/history")
async def interaction_history(
    question_id: _QuestionIdPath,
    request: Request,
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_READ)
    result = await _invoke(get_interaction_service(request), "history", principal, question_id)
    if isinstance(result, JSONResponse):
        return result
    return {"events": jsonable_encoder(result, exclude_none=False)}


@router.get("/interaction-wait-graph")
async def interaction_wait_graph(
    request: Request,
    principal: Principal = Depends(_human_principal),
) -> Any:
    _scope(principal, SCOPE_READ)
    result = await _invoke(get_interaction_service(request), "wait_graph", principal)
    if isinstance(result, JSONResponse):
        return result
    return _check_response(result)


@router.get("/interaction-capabilities")
async def interaction_capabilities(
    request: Request,
    provider: Optional[str] = None,
    provider_version: Optional[str] = None,
    execution_mode: Optional[str] = None,
) -> Any:
    """Capability matrix; callers may authenticate as a human or current agent."""
    service = get_interaction_service(request)
    capability = request.headers.get(_AGENT_CAPABILITY_HEADER)
    if capability:
        try:
            agent_actor = await asyncio.to_thread(service.authenticate_agent, capability)
        except InteractionError as exc:
            return _safe_problem(exc)
        actor: Principal | AgentActor = agent_actor
    else:
        try:
            principal = await get_current_principal(request, request.headers.get("Authorization"))
        except HTTPException:
            raise
        if not is_verified_principal(principal):
            raise HTTPException(status_code=401, detail="verified principal required")
        presented = _extract_bearer(request.headers.get("Authorization"))
        service_token = get_local_bearer()
        if presented and service_token and ascii_token_matches(presented, service_token):
            raise HTTPException(status_code=403, detail="service bearer is not a human identity")
        _scope(principal, SCOPE_READ)
        actor = principal
    result = await _invoke(
        service,
        "capabilities",
        actor=actor,
        provider=provider,
        provider_version=provider_version,
        execution_mode=execution_mode,
    )
    if isinstance(result, JSONResponse):
        return result
    return _capability_wire(result)


@router.post("/agent/interactions/groups", status_code=status.HTTP_201_CREATED)
async def create_interaction_group(
    body: _CreateGroupBody,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> Any:
    capability = _capability(request)
    group_request = CreateGroupRequest.model_validate(body.model_dump(mode="json"))
    result = await _invoke(
        get_interaction_service(request),
        "create_group",
        capability,
        group_request,
        operation_key=idempotency_key,
    )
    if isinstance(result, JSONResponse):
        return result
    wire, replayed = _created_group_wire(result)
    return JSONResponse(status_code=200 if replayed else 201, content=wire)


@router.get("/agent/interactions")
async def agent_list_interactions(
    request: Request,
    status_filter: Annotated[Optional[list[_Lifecycle]], Query(alias="status")] = None,
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> Any:
    capability = _capability(request)
    result = await _invoke(
        get_interaction_service(request),
        "agent_list",
        capability,
        filters={"status": status_filter},
        cursor=cursor,
        limit=limit,
    )
    if isinstance(result, JSONResponse):
        return result
    return _page_wire(result)


@router.get("/agent/interactions/{question_id}")
async def agent_get_interaction(question_id: _QuestionIdPath, request: Request) -> Any:
    result = await _invoke(
        get_interaction_service(request), "agent_get", _capability(request), question_id
    )
    return result if isinstance(result, JSONResponse) else _detail_wire(result)


@router.post("/agent/interactions/{question_id}/answers")
async def agent_answer_interaction(
    question_id: _QuestionIdPath,
    body: _AnswerBody,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
    if_match: Annotated[str, Header(alias="If-Match")],
) -> Any:
    version = _match_versions(if_match, body.expected_version)
    payload = InteractionAnswerPayload.model_validate(body.response.model_dump(mode="json"))
    result = await _invoke(
        get_interaction_service(request),
        "agent_answer",
        _capability(request),
        question_id,
        version,
        payload,
        idempotency_key,
    )
    if isinstance(result, JSONResponse):
        return result
    result = _answer_receipt_wire(result)
    return JSONResponse(status_code=200 if result.get("replayed", False) else 201, content=result)


@router.post("/agent/interactions/{question_id}/escalate")
async def escalate_interaction(
    question_id: _QuestionIdPath,
    body: _EscalateBody,
    request: Request,
) -> Any:
    return _check_response(
        await _invoke(
            get_interaction_service(request),
            "escalate",
            _capability(request),
            question_id,
            body.reason_code,
            body.detail,
        )
    )


@router.post("/agent/interactions/groups/{group_id}/wait")
async def wait_for_interaction_answers(
    group_id: _GroupIdPath,
    body: _WaitBody,
    request: Request,
) -> Any:
    result = await _invoke(
        get_interaction_service(request),
        "wait_for_answers",
        _capability(request),
        group_id,
        timeout_seconds=body.wait_seconds,
        after_event_sequence=body.after_event_sequence,
    )
    return result if isinstance(result, JSONResponse) else _wait_result_wire(result)


@router.post("/agent/interactions/groups/{group_id}/ack")
async def acknowledge_interaction_answers(
    group_id: _GroupIdPath,
    body: _AckBody,
    request: Request,
) -> Any:
    try:
        receipts = [
            AnswerAck.model_validate(item.model_dump(mode="json")) for item in body.receipts
        ]
    except ValidationError:
        # Give conversion-time receipt failures the same fixed result as the
        # route-level handler uses for pre-endpoint request validation.
        return _safe_problem(InteractionInvalid("answer receipt is malformed"))
    return _check_response(
        await _invoke(
            get_interaction_service(request),
            "acknowledge_answers",
            _capability(request),
            group_id,
            receipts,
        )
    )
