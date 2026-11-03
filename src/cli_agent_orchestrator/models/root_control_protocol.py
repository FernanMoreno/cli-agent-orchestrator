"""Strict bounded frames for the private Root control channel."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Literal

ROOT_CONTROL_FD_ENV = "CAO_INTERNAL_ROOT_CONTROL_FD"
ROOT_CONTROL_PROTOCOL_VERSION = 1
ROOT_CONTROL_ACTIVATION_MAX_BYTES = 32 * 1024
ROOT_CONTROL_MAX_FRAME_BYTES = 48 * 1024
ROOT_CONTROL_MAX_COMMAND_BYTES = 4096
ROOT_CONTROL_ERROR_CODE = "root_control_unavailable"

RootControlRequestType = Literal["activation", "refresh", "stop"]
RootControlReplyType = Literal["ready", "refresh", "stopping", "error"]

_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_SEQUENCE = 2**63


def _sequence(value: object) -> None:
    if type(value) is not int or not 0 <= value < _MAX_SEQUENCE:
        raise ValueError("root control sequence is invalid")


def _canonical_json(value: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("root control frame is invalid") from error


def _parse_frame(value: object) -> dict[str, object]:
    if type(value) is not bytes or not value or len(value) > ROOT_CONTROL_MAX_FRAME_BYTES:
        raise ValueError("root control frame length is invalid")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("root control frame has duplicate fields")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            value.decode("ascii"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("root control frame JSON is invalid") from error
    if type(parsed) is not dict or _canonical_json(parsed) != value:
        raise ValueError("root control frame is not canonical")
    return parsed


@dataclass(frozen=True)
class RootControlRequest:
    """One activation or typed command from the funded parent."""

    sequence: int
    type: RootControlRequestType
    packet: bytes | None = None

    def __post_init__(self) -> None:
        _sequence(self.sequence)
        if self.type == "activation":
            if (
                self.sequence != 0
                or type(self.packet) is not bytes
                or not self.packet
                or len(self.packet) > ROOT_CONTROL_ACTIVATION_MAX_BYTES
            ):
                raise ValueError("root control activation request is invalid")
        elif self.type in {"refresh", "stop"}:
            if self.sequence == 0 or self.packet is not None:
                raise ValueError("root control command request is invalid")
        else:
            raise ValueError("root control request type is invalid")


@dataclass(frozen=True)
class RootControlReply:
    """One typed acknowledgement; it is not evidence of physical exit."""

    sequence: int
    type: RootControlReplyType
    reason: str | None = None
    code: str | None = None

    def __post_init__(self) -> None:
        _sequence(self.sequence)
        if self.type == "ready":
            valid = self.sequence == 0 and self.reason is None and self.code is None
        elif self.type == "refresh":
            valid = (
                self.sequence > 0
                and type(self.reason) is str
                and _REASON.fullmatch(self.reason) is not None
                and self.code is None
            )
        elif self.type == "stopping":
            valid = self.sequence > 0 and self.reason is None and self.code is None
        elif self.type == "error":
            valid = (
                self.reason is None
                and type(self.code) is str
                and self.code == ROOT_CONTROL_ERROR_CODE
            )
        else:
            valid = False
        if not valid:
            raise ValueError("root control reply is invalid")


def encode_root_control_request(request: RootControlRequest) -> bytes:
    if type(request) is not RootControlRequest:
        raise ValueError("root control request is invalid")
    frame: dict[str, object] = {
        "version": ROOT_CONTROL_PROTOCOL_VERSION,
        "sequence": request.sequence,
        "type": request.type,
    }
    if request.type == "activation":
        assert request.packet is not None
        frame["packet"] = base64.b64encode(request.packet).decode("ascii")
    encoded = _canonical_json(frame)
    if len(encoded) > ROOT_CONTROL_MAX_FRAME_BYTES:
        raise ValueError("root control frame exceeds its limit")
    if request.type != "activation" and len(encoded) > ROOT_CONTROL_MAX_COMMAND_BYTES:
        raise ValueError("root control command exceeds its limit")
    return encoded


def decode_root_control_request(value: object) -> RootControlRequest:
    if type(value) is not bytes:
        raise ValueError("root control request frame is invalid")
    frame = _parse_frame(value)
    sequence = frame.get("sequence")
    frame_type = frame.get("type")
    if (
        type(frame.get("version")) is not int
        or frame["version"] != ROOT_CONTROL_PROTOCOL_VERSION
        or type(sequence) is not int
        or type(frame_type) is not str
    ):
        raise ValueError("root control request schema is invalid")
    request_type = frame_type
    if request_type == "activation":
        if set(frame) != {"version", "sequence", "type", "packet"}:
            raise ValueError("root control activation schema is invalid")
        if type(frame["packet"]) is not str:
            raise ValueError("root control activation encoding is invalid")
        try:
            packet = base64.b64decode(frame["packet"].encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ValueError("root control activation encoding is invalid") from error
        if (
            not packet
            or len(packet) > ROOT_CONTROL_ACTIVATION_MAX_BYTES
            or base64.b64encode(packet).decode("ascii") != frame["packet"]
        ):
            raise ValueError("root control activation encoding is not canonical")
        request = RootControlRequest(sequence, "activation", packet)
    elif request_type in {"refresh", "stop"}:
        if set(frame) != {"version", "sequence", "type"}:
            raise ValueError("root control command schema is invalid")
        if request_type == "refresh":
            request = RootControlRequest(sequence, "refresh")
        else:
            request = RootControlRequest(sequence, "stop")
    else:
        raise ValueError("root control request type is invalid")
    if len(value) > ROOT_CONTROL_MAX_COMMAND_BYTES and request.type != "activation":
        raise ValueError("root control command exceeds its limit")
    return request


def encode_root_control_reply(reply: RootControlReply) -> bytes:
    if type(reply) is not RootControlReply:
        raise ValueError("root control reply is invalid")
    frame: dict[str, object] = {
        "version": ROOT_CONTROL_PROTOCOL_VERSION,
        "sequence": reply.sequence,
        "type": reply.type,
    }
    if reply.type == "refresh":
        frame["reason"] = reply.reason
    elif reply.type == "error":
        frame["code"] = reply.code
    encoded = _canonical_json(frame)
    if len(encoded) > ROOT_CONTROL_MAX_COMMAND_BYTES:
        raise ValueError("root control reply exceeds its limit")
    return encoded


def decode_root_control_reply(value: object) -> RootControlReply:
    if type(value) is not bytes:
        raise ValueError("root control reply frame is invalid")
    frame = _parse_frame(value)
    sequence = frame.get("sequence")
    frame_type = frame.get("type")
    if (
        type(frame.get("version")) is not int
        or frame["version"] != ROOT_CONTROL_PROTOCOL_VERSION
        or type(sequence) is not int
        or type(frame_type) is not str
    ):
        raise ValueError("root control reply schema is invalid")
    reply_type = frame_type
    if reply_type == "ready":
        if set(frame) != {"version", "sequence", "type"}:
            raise ValueError("root control ready schema is invalid")
        reply = RootControlReply(sequence, "ready")
    elif reply_type == "refresh":
        if set(frame) != {"version", "sequence", "type", "reason"}:
            raise ValueError("root control refresh schema is invalid")
        reason = frame["reason"]
        if type(reason) is not str:
            raise ValueError("root control refresh reason is invalid")
        reply = RootControlReply(sequence, "refresh", reason=reason)
    elif reply_type == "stopping":
        if set(frame) != {"version", "sequence", "type"}:
            raise ValueError("root control stopping schema is invalid")
        reply = RootControlReply(sequence, "stopping")
    elif reply_type == "error":
        if set(frame) != {"version", "sequence", "type", "code"}:
            raise ValueError("root control error schema is invalid")
        code = frame["code"]
        if type(code) is not str:
            raise ValueError("root control error code is invalid")
        reply = RootControlReply(sequence, "error", code=code)
    else:
        raise ValueError("root control reply type is invalid")
    if len(value) > ROOT_CONTROL_MAX_COMMAND_BYTES and reply.type != "ready":
        raise ValueError("root control reply exceeds its limit")
    return reply
