"""Expected native peers come from owned fixture records, never discovery itself."""

import json
from test.live_provider.native_read_bridge import native_operation_result

import pytest

pytestmark = pytest.mark.offline_database
CALL = "call_scoped"
CALLER = "1111aaaa"
PEER = "2222bbbb"
DECOY_GROUP = "3333cccc"
DECOY_SESSION = "4444dddd"
GROUP = ["t229", CALLER]
ROW = {
    "id": PEER,
    "group": GROUP + ["worker"],
    "metadata": {"fixture": "matching"},
    "status": "idle",
}


def test_scoped_adapter_is_preflight_only_and_preserves_legacy_default():
    from test.live_provider.native_cancel_adapters import CLAUDE, claude_adapter_for_manifest

    adapter = claude_adapter_for_manifest(
        {"profile_case": "mcp_discovery_scoped", "case_kind": "preflight"}
    )
    assert adapter.scoped_discovery and adapter.native_operation == "authorized"
    assert adapter.expected_mcp_tools == ("mcp__cao-mcp-server__list_siblings",)
    assert adapter.allowed_tools == ("@cao-mcp-server", "discovery")
    with pytest.raises(AssertionError):
        claude_adapter_for_manifest(
            {"profile_case": "mcp_discovery_scoped", "case_kind": "concurrent"}
        )
    assert claude_adapter_for_manifest({"case_kind": "preflight"}) is CLAUDE


def expectation():
    return {
        "schema": "caos.native-discovery-fixture.v1",
        "caller_id": CALLER,
        "expected": [{k: v for k, v in ROW.items() if k != "status"}],
        "excluded_ids": [CALLER, DECOY_GROUP, DECOY_SESSION],
    }


def packet(rows):
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": CALL,
                        "content": json.dumps({"success": True, "siblings": rows}),
                    }
                ],
            }
        ]
    }


def test_nonempty_native_result_requires_exact_independently_seeded_peer():
    assert native_operation_result(packet([ROW]), CALL, "authorized", expectation=expectation())[
        "siblings"
    ] == [ROW]
    with pytest.raises(ValueError):
        native_operation_result(packet([ROW]), CALL, "authorized")


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [ROW, ROW],
        [{**ROW, "id": CALLER}],
        [{**ROW, "id": DECOY_GROUP}],
        [{**ROW, "id": DECOY_SESSION}],
        [{**ROW, "group": ["foreign"]}],
        [{**ROW, "metadata": {"fixture": "other"}}],
        [{**ROW, "status": 1}],
    ],
)
def test_empty_self_duplicate_decoy_and_wrong_payload_cannot_pass(rows):
    with pytest.raises(ValueError):
        native_operation_result(packet(rows), CALL, "authorized", expectation=expectation())


@pytest.mark.parametrize(
    "tamper", ["caller", "schema", "empty", "duplicate_excluded", "peer_excluded", "peer_shape"]
)
def test_expectation_rejects_unbound_or_unusable_fixture(tamper):
    from test.live_provider.native_read_bridge import validate_discovery_expectation

    value = expectation()
    if tamper == "caller":
        value["caller_id"] = "ffffffff"
    elif tamper == "schema":
        value["schema"] = "unknown"
    elif tamper == "empty":
        value["expected"] = []
    elif tamper == "duplicate_excluded":
        value["excluded_ids"][1] = CALLER
    elif tamper == "peer_excluded":
        value["expected"][0]["id"] = DECOY_GROUP
    else:
        value["expected"][0]["extra"] = True
    with pytest.raises(ValueError):
        validate_discovery_expectation(value, CALLER)


def test_expectation_reader_binds_actual_bytes_owner_and_caller(tmp_path):
    import hashlib
    from test.live_provider.native_read_bridge import read_discovery_expectation

    p = tmp_path / "expectation.json"
    raw = json.dumps(expectation()).encode()
    p.write_bytes(raw)
    p.chmod(0o600)
    assert read_discovery_expectation(p, CALLER) == (expectation(), hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):
        read_discovery_expectation(p, "ffffffff")
    p.chmod(0o644)
    with pytest.raises(ValueError):
        read_discovery_expectation(p, CALLER)
    p.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(OSError):
        read_discovery_expectation(link, CALLER)
    p.write_bytes(b"x" * 4097)
    with pytest.raises(ValueError):
        read_discovery_expectation(p, CALLER)


def test_expectation_writer_creates_private_file_and_never_clobbers_temporary(tmp_path):
    from test.live_provider.native_discovery_fixture import write_expectation
    from test.live_provider.native_read_bridge import read_discovery_expectation

    path = tmp_path / "expectation.json"
    write_expectation(path, expectation())
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_discovery_expectation(path, CALLER)[0] == expectation()
    temporary = path.with_suffix(".tmp")
    temporary.write_text("owned earlier")
    with pytest.raises(FileExistsError):
        write_expectation(path, expectation())
    assert temporary.read_text() == "owned earlier"
