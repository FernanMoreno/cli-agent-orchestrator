"""Exact-output contracts for PostgreSQL catalog normalization and digesting."""

from __future__ import annotations

import hashlib
import json

import pytest

from cli_agent_orchestrator.clients.postgres.migrations import (
    _catalog_manifest_digest,
    _digest,
    _normalize_catalog_manifest,
)


def _routine(name, identity_arguments, definition):
    return {
        "name": name,
        "identity_arguments": identity_arguments,
        "kind": "f",
        "definition": definition,
        "has_argument_defaults": False,
        "has_sql_standard_body": False,
        "has_support_routine": False,
    }


def _trigger(name, identity_arguments):
    return {
        "table_name": "records",
        "name": f"{name}_trigger",
        "function_name": name,
        "function_identity_arguments": identity_arguments,
        "function_kind": "f",
    }


def _normalization_result(routines, triggers):
    return {
        "routines": routines,
        "triggers": triggers,
        "columns": [{"table_name": "records"}],
        "constraints": [],
    }


@pytest.mark.offline_database
def test_catalog_normalization_uses_exact_null_identity_key_and_keeps_overloads_distinct():
    unicode_identity = [["café 東京 🧠", "text\nwith\tcontrols"]]
    result = _normalization_result(
        [
            _routine("no_arguments", None, "CREATE FUNCTION no_arguments()"),
            _routine("overloaded", [["pg_catalog", "text"]], "overload text"),
            _routine("overloaded", unicode_identity, "overload unicode"),
        ],
        [
            _trigger("no_arguments", None),
            _trigger("overloaded", [["pg_catalog", "text"]]),
            _trigger("overloaded", unicode_identity),
        ],
    )

    normalized = _normalize_catalog_manifest(("records",), result)

    assert [trigger["function"] for trigger in normalized["triggers"]] == [
        "CREATE FUNCTION no_arguments()",
        "overload text",
        "overload unicode",
    ]
    assert all("function_identity_arguments" not in trigger for trigger in normalized["triggers"])
    assert all(
        "has_argument_defaults" not in routine
        and "has_sql_standard_body" not in routine
        and "has_support_routine" not in routine
        for routine in normalized["routines"]
    )


@pytest.mark.offline_database
def test_catalog_manifest_digest_matches_default_digest_for_json_dags():
    shared = {
        "café": ["東京", "🧠", 7, None, True],
        "finite_float": 3.141592653589793,
        "negative_zero": -0.0,
        "false": False,
        "controls": "line\nwith\ttab\x00and control",
    }
    manifest = {"first": shared, "second": shared}
    expected = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert _digest(manifest) == expected
    assert _catalog_manifest_digest(manifest) == expected


@pytest.mark.offline_database
def test_general_digest_still_rejects_cyclic_arbitrary_values():
    cyclic = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError, match="Circular reference detected"):
        _digest(cyclic)
