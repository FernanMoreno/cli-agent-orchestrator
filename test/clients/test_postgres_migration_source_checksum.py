"""Expected-source memoization must never make observed database proof stale."""

import hashlib
import json
from dataclasses import asdict, fields
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.clients.postgres import migrations

pytestmark = pytest.mark.offline_database


def descriptor(**overrides):
    values = dict(
        component="source-cache",
        scope="main",
        version=1,
        statements=("CREATE TABLE records(id text)",),
        tables=("records",),
    )
    values.update(overrides)
    return migrations.ComponentMigration(**values)


def reference(value):
    return hashlib.sha256(
        json.dumps(
            [value.component, value.scope, value.version, value.statements, value.tables],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@pytest.fixture(autouse=True)
def isolated_source_checksum_cache():
    cache = getattr(migrations, "_cached_component_migration_checksum", None)
    if cache is not None:
        cache.cache_clear()
    yield
    if cache is not None:
        cache.cache_clear()


def test_immutable_source_is_serialized_once_across_equal_descriptors():
    first, second = descriptor(), descriptor()
    expected = reference(first)
    with patch.object(migrations.json, "dumps", wraps=json.dumps) as encode:
        actual = [first.checksum, first.checksum, second.checksum]
    assert actual == [expected] * 3
    assert encode.call_count == 1


@pytest.mark.parametrize(
    "replacement",
    [
        {"component": "changed"},
        {"scope": "auth"},
        {"version": 2},
        {"statements": ("CREATE TABLE changed(id text)",)},
        {"tables": ("changed",)},
    ],
)
def test_bypassing_frozen_assignment_cannot_reuse_old_source_checksum(replacement):
    value = descriptor()
    before = value.checksum
    for name, changed in replacement.items():
        object.__setattr__(value, name, changed)
    assert value.checksum == reference(value)
    assert value.checksum != before


def test_mutable_source_keeps_fresh_serialization_after_mutation():
    statements = ["CREATE TABLE records(id text)"]
    value = descriptor(statements=statements)
    before = value.checksum
    statements.append("ALTER TABLE records ADD COLUMN payload text")
    assert value.checksum == reference(value)
    assert value.checksum != before


@pytest.mark.parametrize("name", ["component", "scope", "version", "statements", "tables"])
def test_json_null_retains_historical_fallback_without_cache_retention(name):
    value = descriptor(**{name: None})
    expected = reference(value)
    with patch.object(migrations.json, "dumps", wraps=json.dumps) as encode:
        actual = [value.checksum, value.checksum]
    assert actual == [expected] * 2
    assert encode.call_count == 2
    assert migrations._cached_component_migration_checksum.cache_info().currsize == 0


@pytest.mark.parametrize("version", [True, 1.0])
def test_noncanonical_numeric_version_does_not_collide_with_integer(version):
    canonical = descriptor()
    assert canonical.checksum == reference(canonical)
    other = descriptor(version=version)
    assert other.checksum == reference(other)
    assert other.checksum != canonical.checksum


def test_equal_hash_string_subclasses_keep_original_json_semantics():
    class Pretend(str):
        def __hash__(self):
            return 1

        def __eq__(self, other):
            return True

    first = descriptor(statements=(Pretend("SELECT 'a'"),))
    second = descriptor(statements=(Pretend("SELECT 'b'"),))
    assert first.checksum == reference(first)
    assert second.checksum == reference(second)
    assert first.checksum != second.checksum


def test_subclass_constructor_hooks_and_serializer_behavior_are_unchanged():
    called = []

    class Child(migrations.ComponentMigration):
        def __post_init__(self):
            called.append(True)

    value = Child("child", "main", 1, ("SELECT 1",), ("records",))
    assert not called
    with patch.object(migrations.json, "dumps", wraps=json.dumps) as encode:
        first, second = value.checksum, value.checksum
    assert first == second == reference(value)
    assert encode.call_count == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"component": "x" * 262145},
        {"scope": "x" * 262145},
        {"statements": ("x" * 262145,)},
        {"tables": ("x" * 262145,)},
        {"statements": ("SELECT 1",) * 1025},
        {"tables": ("records",) * 257},
        {"version": 2**40},
    ],
)
def test_oversized_source_uses_original_serializer_without_cache_retention(overrides):
    value = descriptor(**overrides)
    expected = reference(value)
    with patch.object(migrations.json, "dumps", wraps=json.dumps) as encode:
        actual = [value.checksum, value.checksum]
    assert actual == [expected] * 2
    assert encode.call_count == 2
    cache = getattr(migrations, "_cached_component_migration_checksum", None)
    if cache is not None:
        assert cache.cache_info().currsize == 0


def test_cache_retention_is_bounded_and_eviction_recomputes_expected_source():
    cache = getattr(migrations, "_cached_component_migration_checksum", None)
    assert cache is not None
    values = [descriptor(component=f"source-{index}") for index in range(40)]
    for value in values:
        assert value.checksum == reference(value)
    assert cache.cache_info().maxsize == 32
    assert cache.cache_info().currsize == 32
    with patch.object(migrations.json, "dumps", wraps=json.dumps) as encode:
        first = values[0].checksum
    assert first == reference(values[0])
    assert encode.call_count == 1


def test_unicode_null_escapes_and_dataclass_contract_keep_exact_historical_bytes():
    value = descriptor(
        component="equipo-ñ", statements=("SELECT '🧭\n\u0000'",), tables=("tabla_ñ", "表")
    )
    expected_repr, expected_dict, expected_hash = repr(value), asdict(value), hash(value)
    assert value.checksum == reference(value)
    assert value.checksum == reference(value)
    assert repr(value) == expected_repr
    assert asdict(value) == expected_dict
    assert hash(value) == expected_hash
    assert [f.name for f in fields(value)] == [
        "component",
        "scope",
        "version",
        "statements",
        "tables",
    ]
