"""Pure record, issuer-capability and Ed25519 checks for root SQL genesis."""

from __future__ import annotations

import copy
import json
import os
import pickle
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_root_genesis import (
    ConfirmedRootSqlOpening,
    RootSqlOpeningIntent,
    SignedRootSqlOpening,
    _issue_confirmed_root_sql_opening,
    sql_startup_binding_sha256,
)
from cli_agent_orchestrator.services import sql_root_genesis as root_genesis_service
from cli_agent_orchestrator.services.sql_root_genesis import (
    RootGenesisUnavailable,
    sign_root_opening,
    verify_root_opening,
)

pytestmark = pytest.mark.offline_database


def _binding(**changes: object) -> ResourceBinding:
    values: dict[str, object] = {
        "instance_id": str(uuid4()),
        "process_generation": str(uuid4()),
        "process_pid": 23456,
        "process_started_at": 123.5,
        "store_id": str(uuid4()),
        "target_identity_sha256": "a" * 64,
        "mount_ns_device": 4,
        "mount_ns_inode": 1001,
        "cgroup_ns_device": 4,
        "cgroup_ns_inode": 1002,
        "resource_view_sha256": "b" * 64,
    }
    values.update(changes)
    return ResourceBinding(**values)


def _record(**changes: object) -> RootSqlOpeningIntent:
    credits = tuple(sorted((str(uuid4()), str(uuid4()), str(uuid4()))))
    binding = _binding()
    values: dict[str, object] = {
        "intent_id": str(uuid4()),
        "journal_id": str(uuid4()),
        "ordinal": 1,
        "credit_id": credits[0],
        "credit_ids": credits,
        "membership_sha256": "c" * 64,
        "session_id": str(uuid4()),
        "subject": binding,
        "subject_binding_sha256": sql_startup_binding_sha256(binding),
        "subject_sql_application_name": "caos-sql-v1-" + uuid4().hex,
        "boot_id": str(uuid4()),
        "start_time_ticks": 123456,
        "pid_namespace": (4026531836, 4026531837),
        "path": "native",
        "destination_sha256": "d" * 64,
        "destination_scope": "main",
        "issuer_principal_id": "principal-" + "e" * 64,
        "slots": 1,
    }
    values.update(changes)
    return RootSqlOpeningIntent(**values)


def _issued_cap(record: RootSqlOpeningIntent | None = None, *, live=None):
    active = [True] if live is None else live

    def validate() -> None:
        if not active[0]:
            raise ValueError("issuer is no longer funded and live")

    return _issue_confirmed_root_sql_opening(record or _record(), validate=validate), active


def _signed(record: RootSqlOpeningIntent | None = None, *, key_id: str = "root-key-1"):
    private_key = Ed25519PrivateKey.generate()
    cap, _ = _issued_cap(record)
    certificate = sign_root_opening(cap, key_id=key_id, private_key=private_key)
    return certificate, private_key


def _canonical_json(packet: dict[str, object]) -> bytes:
    return json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def test_root_opening_round_trips_exact_versioned_data_and_permission_is_descriptive():
    record = _record()
    packet = record.to_packet()
    assert set(packet) == {
        "version",
        "kind",
        "intent_id",
        "journal_id",
        "ordinal",
        "credit_id",
        "credit_ids",
        "membership_sha256",
        "session_id",
        "subject",
        "subject_binding_sha256",
        "subject_sql_application_name",
        "boot_id",
        "start_time_ticks",
        "pid_namespace",
        "path",
        "destination_sha256",
        "destination_scope",
        "issuer_principal_id",
        "slots",
    }
    assert packet["version"] == 1
    assert packet["kind"] == "root_sql_opening"
    assert RootSqlOpeningIntent.from_packet(packet) == record

    permission = record.startup_permission()
    assert permission.session_id == record.session_id
    assert permission.subject_binding_sha256 == record.subject_binding_sha256
    assert permission.path == "native"
    assert permission.destination_scope == "main"


@pytest.mark.parametrize(
    "change",
    [
        {"intent_id": "not-a-uuid"},
        {"journal_id": str(uuid4()).upper()},
        {"credit_id": str(uuid4()).upper()},
        {"session_id": "{}".format(uuid4()).replace("-", "")},
        {"boot_id": "{}".format(uuid4()).upper()},
        {"credit_ids": ()},
        {"credit_ids": (str(uuid4()),)},
        {"credit_ids": [str(uuid4())]},
        {"credit_ids": ("not-a-uuid",)},
        {"ordinal": True},
        {"ordinal": 0},
        {"ordinal": 2},
        {"slots": True},
        {"slots": 0},
        {"slots": 2},
        {"membership_sha256": "A" * 64},
        {"subject_binding_sha256": "f" * 64},
        {"subject_sql_application_name": "caos"},
        {"start_time_ticks": True},
        {"start_time_ticks": 0},
        {"start_time_ticks": 2**63},
        {"pid_namespace": [4026531836, 4026531837]},
        {"pid_namespace": (True, 4026531837)},
        {"pid_namespace": (0, 4026531837)},
        {"pid_namespace": (4026531836, 2**63)},
        {"path": "orm"},
        {"destination_sha256": "D" * 64},
        {"destination_scope": "other"},
        {"issuer_principal_id": "principal-" + "F" * 64},
    ],
)
def test_root_opening_rejects_noncanonical_or_non_genesis_values(change):
    with pytest.raises((TypeError, ValueError)):
        _record(**change)


def test_credit_set_must_be_sorted_unique_and_include_primary_credit():
    record = _record()
    first, second, third = record.credit_ids
    for credit_ids in ((second, first, third), (first, first), (second, third)):
        with pytest.raises(ValueError):
            replace(record, credit_ids=credit_ids)


def test_binding_must_be_exact_typed_and_have_its_computed_digest():
    record = _record()
    with pytest.raises(ValueError):
        replace(record, subject={})
    with pytest.raises(ValueError):
        replace(record, subject_binding_sha256="f" * 64)


@pytest.mark.parametrize(
    "tamper", ["extra", "version_bool", "wrong_kind", "binding_extra", "binding_uuid_case"]
)
def test_root_opening_packet_rejects_extra_fields_and_noncanonical_nested_binding(tamper):
    packet = _record().to_packet()
    if tamper == "extra":
        packet["extra"] = "rejected"
    elif tamper == "version_bool":
        packet["version"] = True
    elif tamper == "wrong_kind":
        packet["kind"] = "sql_opening"
    elif tamper == "binding_extra":
        packet["subject"]["unknown"] = "rejected"
    else:
        packet["subject"]["instance_id"] = packet["subject"]["instance_id"].upper()
    with pytest.raises(ValueError):
        RootSqlOpeningIntent.from_packet(packet)


def test_confirmed_cap_is_opaque_single_use_and_bound_to_the_original_live_worker():
    record = _record()
    cap, active = _issued_cap(record)
    assert cap.record is record
    cap.check()
    with pytest.raises(TypeError):
        ConfirmedRootSqlOpening()
    for copy_fn in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            copy_fn(cap)

    worker_result: list[object] = []

    def wrong_worker() -> None:
        try:
            cap.check()
        except BaseException as exc:
            worker_result.append(exc)

    thread = threading.Thread(target=wrong_worker)
    thread.start()
    thread.join()
    assert len(worker_result) == 1
    assert isinstance(worker_result[0], ValueError)

    active[0] = False
    with pytest.raises(ValueError):
        cap.check()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork semantics are POSIX-specific")
def test_confirmed_cap_rejects_fork_child_without_poisoning_parent():
    cap, _ = _issued_cap()
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            cap.check()
        except ValueError:
            os.write(write_fd, b"rejected")
            os.close(write_fd)
            os._exit(0)
        os.write(write_fd, b"accepted")
        os.close(write_fd)
        os._exit(1)

    os.close(write_fd)
    child_message = os.read(read_fd, 16)
    os.close(read_fd)
    _, status = os.waitpid(child_pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert child_message == b"rejected"
    cap.check()


def test_signing_rejects_descriptive_data_and_consumes_exactly_one_capability():
    private_key = Ed25519PrivateKey.generate()
    record = _record()
    with pytest.raises(RootGenesisUnavailable):
        sign_root_opening(record, key_id="root-key", private_key=private_key)

    cap, _ = _issued_cap(record)
    certificate = sign_root_opening(cap, key_id="root.key_1", private_key=private_key)
    assert certificate.record is record
    assert certificate.key_id == "root.key_1"
    with pytest.raises(RootGenesisUnavailable):
        sign_root_opening(cap, key_id="root.key_1", private_key=private_key)


@pytest.mark.parametrize("key_id", ["", "bad key", "é", "x" * 65, "root/key"])
def test_signing_rejects_invalid_key_ids_without_emitting_a_certificate(key_id):
    cap, _ = _issued_cap()
    with pytest.raises(RootGenesisUnavailable):
        sign_root_opening(cap, key_id=key_id, private_key=Ed25519PrivateKey.generate())


def test_failed_sign_attempt_does_not_rearm_the_capability(monkeypatch):
    cap, _ = _issued_cap()
    private_key = Ed25519PrivateKey.generate()

    def fail_packet(self):
        raise RuntimeError("controlled serialization failure")

    monkeypatch.setattr(RootSqlOpeningIntent, "to_packet", fail_packet)
    with pytest.raises(RootGenesisUnavailable):
        sign_root_opening(cap, key_id="root-key", private_key=private_key)
    with pytest.raises(RootGenesisUnavailable):
        sign_root_opening(cap, key_id="root-key", private_key=private_key)


def test_scope_invalidation_during_signing_discards_certificate_without_rearming(monkeypatch):
    cap, active = _issued_cap()
    private_key = Ed25519PrivateKey.generate()
    original_opening_bytes = root_genesis_service._opening_bytes

    def invalidate_during_sign(record):
        payload = original_opening_bytes(record)
        active[0] = False
        return payload

    monkeypatch.setattr(root_genesis_service, "_opening_bytes", invalidate_during_sign)
    with pytest.raises(RootGenesisUnavailable):
        root_genesis_service.sign_root_opening(cap, key_id="root-key", private_key=private_key)

    # Reactivate the callback to prove the failed attempt itself consumed the cap.
    active[0] = True
    with pytest.raises(RootGenesisUnavailable):
        root_genesis_service.sign_root_opening(cap, key_id="root-key", private_key=private_key)


def test_certificate_is_canonical_bounded_and_contains_no_public_key():
    certificate, _ = _signed()
    encoded = certificate.to_bytes()
    assert encoded == _canonical_json(certificate.to_packet())
    assert SignedRootSqlOpening.from_bytes(encoded) == certificate
    assert set(certificate.to_packet()) == {"version", "kind", "record", "key_id", "signature"}
    assert "public_key" not in certificate.to_packet()
    assert b"public_key" not in encoded

    with pytest.raises(ValueError):
        SignedRootSqlOpening.from_bytes(encoded + b" ")
    with pytest.raises(ValueError):
        SignedRootSqlOpening.from_bytes(b" " * (16 * 1024 + 1))
    with pytest.raises(ValueError):
        SignedRootSqlOpening.from_bytes(bytearray(encoded))


@pytest.mark.parametrize(
    "change", ["key_id", "signature_short", "signature_padding", "record_type"]
)
def test_certificate_constructor_rejects_invalid_key_signature_or_record(change):
    certificate, _ = _signed()
    if change == "key_id":
        values = {"key_id": "key with whitespace"}
    elif change == "signature_short":
        values = {"signature": "A" * 85}
    elif change == "signature_padding":
        values = {"signature": certificate.signature + "="}
    else:
        values = {"record": {}}
    with pytest.raises(ValueError):
        replace(certificate, **values)


@pytest.mark.parametrize(
    "tamper", ["duplicate_top", "duplicate_nested", "extra", "noncanonical_order"]
)
def test_certificate_parser_rejects_duplicates_extra_schema_and_noncanonical_json(tamper):
    certificate, _ = _signed()
    packet = certificate.to_packet()
    if tamper == "duplicate_top":
        encoded = certificate.to_bytes().replace(
            b'"kind":"root_sql_certificate"',
            b'"kind":"other","kind":"root_sql_certificate"',
            1,
        )
    elif tamper == "duplicate_nested":
        encoded = certificate.to_bytes().replace(b'"version":1', b'"version":1,"version":1', 1)
    elif tamper == "extra":
        packet["extra"] = "rejected"
        encoded = _canonical_json(packet)
    else:
        encoded = json.dumps(packet, separators=(",", ":")).encode("ascii")
    with pytest.raises(ValueError):
        SignedRootSqlOpening.from_bytes(encoded)


def test_certificate_serialization_enforces_the_size_bound():
    record = _record()
    credit_ids = tuple(sorted(str(uuid4()) for _ in range(700)))
    oversized_record = replace(record, credit_id=credit_ids[0], credit_ids=credit_ids)
    certificate = SignedRootSqlOpening(
        record=oversized_record,
        key_id="root-key",
        signature="A" * 86,
    )
    with pytest.raises(ValueError, match="size bound"):
        certificate.to_bytes()

    cap, _ = _issued_cap(oversized_record)
    with pytest.raises(RootGenesisUnavailable):
        sign_root_opening(cap, key_id="root-key", private_key=Ed25519PrivateKey.generate())
    with pytest.raises(RootGenesisUnavailable):
        sign_root_opening(cap, key_id="root-key", private_key=Ed25519PrivateKey.generate())


def test_trusted_key_verification_returns_data_and_rejects_unknown_or_wrong_keys():
    certificate, private_key = _signed()
    public_key = private_key.public_key()
    assert (
        verify_root_opening(certificate, trusted_keys={certificate.key_id: public_key})
        == certificate.record
    )
    with pytest.raises(RootGenesisUnavailable, match="^root SQL genesis unavailable$"):
        verify_root_opening(certificate, trusted_keys={})

    wrong_key = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(RootGenesisUnavailable):
        verify_root_opening(certificate, trusted_keys={certificate.key_id: wrong_key})

    first = "A" if certificate.signature[0] != "A" else "B"
    tampered_signature = replace(
        certificate,
        signature=first + certificate.signature[1:],
    )
    with pytest.raises(RootGenesisUnavailable):
        verify_root_opening(tampered_signature, trusted_keys={certificate.key_id: public_key})

    changed_record = replace(certificate.record, destination_sha256="f" * 64)
    changed_certificate = replace(certificate, record=changed_record)
    with pytest.raises(RootGenesisUnavailable):
        verify_root_opening(changed_certificate, trusted_keys={certificate.key_id: public_key})


def test_verifier_rejects_wrong_certificate_and_untrusted_key_map_types():
    certificate, _ = _signed()
    with pytest.raises(RootGenesisUnavailable):
        verify_root_opening(certificate.record, trusted_keys={})
    with pytest.raises(RootGenesisUnavailable):
        verify_root_opening(certificate, trusted_keys=[])  # type: ignore[arg-type]
