"""Opt-in consumer for a signed pre-SQL root control activation.

The unselected legacy mode exchanges bounded lines over stdio. A private Root
control descriptor instead uses the authenticated endpoint and keeps protocol
traffic off stdio. Neither mode creates a key, credential or grant.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import math
import os
import signal
import stat
import sys
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import FrameType
from typing import BinaryIO, Iterator, TextIO

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.models.root_control_activation import (
    ROOT_CONTROL_ACTIVATION_MAX_BYTES,
)
from cli_agent_orchestrator.models.root_control_protocol import ROOT_CONTROL_FD_ENV
from cli_agent_orchestrator.services.admission_bootstrap import scoped_process_bootstrap
from cli_agent_orchestrator.services.admission_settings import (
    AdmissionPolicyLoader,
    read_private_operator_policy,
)
from cli_agent_orchestrator.services.original_control_owner import OriginalControlUnavailable
from cli_agent_orchestrator.services.resource_process_supervisor import publish_resource_binding
from cli_agent_orchestrator.services.root_control_activation import (
    RootControlActivationUnavailable,
    prepare_original_control_owner,
)
from cli_agent_orchestrator.services.root_control_transport import (
    RootControlChannelEOF,
    RootControlChildEndpoint,
    RootControlTransportUnavailable,
    open_root_control_endpoint,
)
from cli_agent_orchestrator.services.root_sql_control import current_root_sql_subject
from cli_agent_orchestrator.utils.process_sql_origin import SqlStartupPermissionUnavailable

_MAX_TRUST_BYTES = 16 * 1024
_MAX_COMMAND_BYTES = 4096
_MAX_CLOSE_TIMEOUT_S = 30.0


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _read_private_document(path: Path, *, maximum_bytes: int) -> Mapping[str, object]:
    if not path.is_absolute():
        raise ValueError("configuration paths must be absolute")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o077
            or before.st_size > maximum_bytes
        ):
            raise PermissionError("configuration must be a bounded private owner file")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            block = os.read(descriptor, min(4096, maximum_bytes + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
        after = os.fstat(descriptor)
        if len(payload) > maximum_bytes or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("configuration changed while being read")
    finally:
        os.close(descriptor)
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("configuration JSON is invalid") from error
    if type(document) is not dict or any(type(key) is not str for key in document):
        raise ValueError("configuration must be a JSON object")
    return document


def _read_trusted_keys(path: Path) -> Mapping[str, Ed25519PublicKey]:
    document = _read_private_document(path, maximum_bytes=_MAX_TRUST_BYTES)
    if (
        set(document) != {"version", "keys"}
        or type(document["version"]) is not int
        or document["version"] != 1
        or type(document["keys"]) is not dict
        or not document["keys"]
        or len(document["keys"]) > 64
    ):
        raise ValueError("trusted-key document schema is invalid")
    keys: dict[str, Ed25519PublicKey] = {}
    for key_id, encoded in document["keys"].items():
        if type(key_id) is not str or type(encoded) is not str or len(key_id) > 64:
            raise ValueError("trusted-key entry is invalid")
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ValueError("trusted-key entry is invalid") from error
        if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError("trusted-key entry is invalid")
        keys[key_id] = Ed25519PublicKey.from_public_bytes(raw)
    return keys


def _read_activation_line(stream: BinaryIO) -> bytes:
    value = stream.readline(ROOT_CONTROL_ACTIVATION_MAX_BYTES + 2)
    if not value or not value.endswith(b"\n") or len(value) > ROOT_CONTROL_ACTIVATION_MAX_BYTES + 1:
        raise ValueError("activation line is absent or exceeds its limit")
    packet = value[:-1]
    if not packet:
        raise ValueError("activation line is empty")
    return packet


def _parse_command_line(value: bytes) -> str:
    if type(value) is not bytes or not value or len(value) > _MAX_COMMAND_BYTES:
        raise ValueError("command line is invalid")
    try:
        command = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("command line is invalid") from error
    if type(command) is not dict or set(command) != {"type"} or type(command["type"]) is not str:
        raise ValueError("command line schema is invalid")
    if command["type"] not in {"refresh", "stop"}:
        raise ValueError("unsupported root control command")
    return command["type"]


def _write_line(stream: TextIO, payload: Mapping[str, object]) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    stream.write("\n")
    stream.flush()


@contextmanager
def _stop_signals() -> Iterator[None]:
    """Turn terminal/process stop signals into a finally-cleaned interruption."""
    previous = {
        selected: signal.getsignal(selected) for selected in (signal.SIGINT, signal.SIGTERM)
    }

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    for selected in previous:
        signal.signal(selected, request_stop)
    try:
        yield
    finally:
        for selected, handler in previous.items():
            signal.signal(selected, handler)


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consume a signed root control activation")
    parser.add_argument("--trust-file", required=True, type=Path)
    parser.add_argument("--policy-file", required=True, type=Path)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--close-timeout", type=float, default=5.0)
    parsed = parser.parse_args(argv)
    if (
        isinstance(parsed.close_timeout, bool)
        or not math.isfinite(parsed.close_timeout)
        or not 0 <= parsed.close_timeout <= _MAX_CLOSE_TIMEOUT_S
        or not parsed.scope.strip()
    ):
        parser.error("scope and bounded close timeout are required")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Run the opt-in process protocol; environment does not choose trust/policy."""
    # open_root_control_endpoint consumes its private FD. Remember selection
    # first so a malformed descriptor can never downgrade to stdout/stdin.
    private_requested = ROOT_CONTROL_FD_ENV in os.environ
    try:
        endpoint = open_root_control_endpoint()
    except Exception:
        if private_requested:
            return 2
        raise
    if private_requested and endpoint is None:
        return 2
    if endpoint is not None:
        status = 2
        try:
            status = _run_private(argv, endpoint)
        finally:
            try:
                endpoint.close()
            except Exception:
                status = 2
        return status

    parsed = _arguments(argv)
    return _run_stdio(parsed)


def _run_stdio(parsed: argparse.Namespace) -> int:
    """Preserve the historical opt-out stdio contract exactly."""
    try:
        trusted_keys = _read_trusted_keys(parsed.trust_file)
        bootstrap = scoped_process_bootstrap(parsed.scope)
        subject = current_root_sql_subject(bootstrap)
        _write_line(sys.stdout, {"type": "subject", "value": asdict(subject)})
        packet = _read_activation_line(sys.stdin.buffer)
        policy_loader = AdmissionPolicyLoader(
            source=lambda: read_private_operator_policy(parsed.policy_file)
        )
        actor = asyncio.run(
            prepare_original_control_owner(
                packet=packet,
                trusted_keys=trusted_keys,
                bootstrap=bootstrap,
                policy_loader=policy_loader,
                scope_id=parsed.scope,
            )
        )
    except (
        OSError,
        ValueError,
        ConfigurationError,
        RootControlActivationUnavailable,
        SqlStartupPermissionUnavailable,
    ):
        _write_line(sys.stdout, {"type": "error", "code": "root_control_unavailable"})
        return 2

    status = 0
    try:
        with _stop_signals():
            actor.start()
            asyncio.run(actor.wait_ready())
            _write_line(sys.stdout, {"type": "ready"})
            while True:
                try:
                    line = sys.stdin.buffer.readline(_MAX_COMMAND_BYTES + 1)
                except KeyboardInterrupt:
                    break
                if not line:
                    break
                if not line.endswith(b"\n") or len(line) > _MAX_COMMAND_BYTES:
                    _write_line(sys.stdout, {"type": "error", "code": "invalid_command"})
                    status = 2
                    break
                try:
                    command = _parse_command_line(line[:-1])
                except ValueError:
                    _write_line(sys.stdout, {"type": "error", "code": "invalid_command"})
                    status = 2
                    break
                if command == "stop":
                    break
                result = asyncio.run(actor.refresh())
                _write_line(sys.stdout, {"type": "refresh", "reason": result.reason})
            if status == 0:
                _write_line(sys.stdout, {"type": "stopping"})
    except (KeyboardInterrupt, RootControlActivationUnavailable, OriginalControlUnavailable):
        _write_line(sys.stdout, {"type": "stopping"})
        status = 2
    except Exception:
        _write_line(sys.stdout, {"type": "error", "code": "root_control_unavailable"})
        status = 2
    finally:
        try:
            asyncio.run(actor.aclose(timeout=parsed.close_timeout))
        except (Exception, asyncio.CancelledError):
            # The actor retains its exact handle and startup charge. The local
            # variable remains reachable until process exit; no retry authority
            # or alternate close path is manufactured here.
            status = 2
    return status


def _run_private(parsed_argv: Sequence[str] | None, endpoint: RootControlChildEndpoint) -> int:
    """Run Root over its private endpoint without emitting protocol bytes to stdio."""
    actor = None
    activation_received = False
    ready = False
    status = 2
    try:
        parsed = _arguments(parsed_argv)
        trusted_keys = _read_trusted_keys(parsed.trust_file)
        bootstrap = scoped_process_bootstrap(parsed.scope)
        subject = current_root_sql_subject(bootstrap)
        if not publish_resource_binding(subject.binding, first_sql_grant=False):
            return 2

        while True:
            packet = endpoint.receive_activation(timeout_s=1.0)
            if packet is not None:
                activation_received = True
                if type(packet) is not bytes or not packet:
                    raise RootControlTransportUnavailable()
                break

        actor = asyncio.run(
            prepare_original_control_owner(
                packet=packet,
                trusted_keys=trusted_keys,
                bootstrap=bootstrap,
                policy_loader=AdmissionPolicyLoader(
                    source=lambda: read_private_operator_policy(parsed.policy_file)
                ),
                scope_id=parsed.scope,
            )
        )

        with _stop_signals():
            actor.start()
            asyncio.run(actor.wait_ready())
            endpoint.send_ready()
            ready = True
            status = 0
            should_ack_stop = False
            while True:
                try:
                    command = endpoint.receive_command(timeout_s=1.0)
                except RootControlChannelEOF:
                    # EOF is a stop request; the endpoint is already unusable.
                    break
                if command is None:
                    continue
                if command == "stop":
                    should_ack_stop = True
                    break
                if command != "refresh":
                    raise RootControlTransportUnavailable()
                result = asyncio.run(actor.refresh())
                endpoint.send_refresh(result.reason)
            if should_ack_stop:
                endpoint.send_stopping()
    except RootControlChannelEOF:
        # EOF before ready is an incomplete startup; after ready it is a normal
        # parent-requested stop. In either case no reply is sent on the dead fd.
        status = 0 if ready else 2
    except KeyboardInterrupt:
        if activation_received:
            _best_effort_private_error(endpoint)
        status = 2
    except (
        OSError,
        ValueError,
        ConfigurationError,
        RootControlActivationUnavailable,
        SqlStartupPermissionUnavailable,
        OriginalControlUnavailable,
        RootControlTransportUnavailable,
    ):
        if activation_received:
            _best_effort_private_error(endpoint)
        status = 2
    except Exception:
        if activation_received:
            _best_effort_private_error(endpoint)
        status = 2
    finally:
        if actor is not None:
            try:
                asyncio.run(actor.aclose(timeout=parsed.close_timeout))
            except (Exception, asyncio.CancelledError):
                # Keep the actor and its exact started reservation until process
                # exit; neither a failed close nor this private ACK proves join.
                status = 2
    return status


def _best_effort_private_error(endpoint: RootControlChildEndpoint) -> None:
    try:
        endpoint.send_error()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
