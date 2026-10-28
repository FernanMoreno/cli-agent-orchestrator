"""Container evidence survives deletion without collecting process credentials."""

import copy
import json
from test.live_provider import message_api_restart_live as live
from test.live_provider import native_container_inventory as inventory
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def row(value="a"):
    return {
        "id": value * 64,
        "name": "synthetic-" + value,
        "image": "postgres:16-alpine",
        "session_id": "synthetic-session-" + value,
    }


@pytest.fixture
def query(monkeypatch):
    calls = []
    response = SimpleNamespace(rows=[row()])

    def read(args, **kwargs):
        calls.append((args, kwargs))
        return "\n".join(json.dumps(value) for value in response.rows)

    monkeypatch.setattr(inventory, "subprocess", SimpleNamespace(check_output=read))
    return response, calls


def capture(tmp_path, phase="before", *, writer=live._write_private_json):
    tmp_path.chmod(0o700)
    return inventory.capture_container_inventory(tmp_path, phase, write_json=writer)


def test_retains_missing_container_identity_in_two_private_witnesses(tmp_path, query):
    response, calls = query
    response.rows = [row("b"), row()]
    before = capture(tmp_path)
    response.rows = [row()]
    after = capture(tmp_path, "after")
    assert before - after == {"b" * 64}
    payload = json.loads((tmp_path / "containers-before.json").read_text())
    assert payload == {
        "schema": "caos.container-inventory.v2",
        "phase": "before",
        "containers": [row(), row("b")],
    }
    assert payload["containers"][1]["session_id"] == "synthetic-session-b"
    for phase in ("before", "after"):
        assert (tmp_path / ("containers-" + phase + ".json")).stat().st_mode & 0o777 == 0o600
    assert len(calls) == 2
    args, kwargs = calls[0]
    assert args[:5] == ["docker", "ps", "-a", "--no-trunc", "--format"]
    assert len(args) == 6 and kwargs == {"text": True, "timeout": 5}
    assert all(
        field in args[5] for field in (".ID", ".Names", ".Image", "org.testcontainers.session-id")
    )
    assert not any(field in args[5] for field in (".Env", ".Command", ".Labels"))


@pytest.mark.parametrize("phase", ["foreign", "../after", None, []])
def test_invalid_phase_refused_before_query(tmp_path, query, phase):
    with pytest.raises(RuntimeError, match="inventory_phase_invalid"):
        capture(tmp_path, phase)
    assert query[1] == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "change",
    [
        {"id": "bad"},
        {"id": "A" * 64},
        {"id": 1},
        {"name": None},
        {"session_id": "x" * 129},
        {"environment": {"token": "synthetic-only"}},
    ],
)
def test_invalid_metadata_refused_before_write(tmp_path, query, change):
    query[0].rows = [{**row(), **change}]
    with pytest.raises(RuntimeError, match="inventory_.*invalid"):
        capture(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_duplicate_ids_refused_without_dropping_a_row(tmp_path, query):
    query[0].rows = [row(), {**row(), "name": "duplicate"}]
    with pytest.raises(RuntimeError, match="inventory_duplicate_id"):
        capture(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_existing_witness_is_never_overwritten(tmp_path, query):
    capture(tmp_path)
    path = tmp_path / "containers-before.json"
    original = path.read_bytes()
    query[0].rows = [row("b")]
    with pytest.raises(live.AcceptanceFailure):
        capture(tmp_path)
    assert path.read_bytes() == original


def test_roundtrip_rejects_wrong_permissions(tmp_path, query):
    def writer(path, payload):
        live._write_private_json(path, payload)
        path.chmod(0o644)

    with pytest.raises(RuntimeError, match="inventory_roundtrip_invalid"):
        capture(tmp_path, writer=writer)


def test_empty_inventory_is_retained(tmp_path, query):
    query[0].rows = []
    assert capture(tmp_path) == set()
    assert json.loads((tmp_path / "containers-before.json").read_text())["containers"] == []


def test_roundtrip_reads_actual_content(tmp_path, query):
    def writer(path, payload):
        changed = copy.deepcopy(payload)
        changed["phase"] = "after"
        live._write_private_json(path, changed)

    with pytest.raises(RuntimeError, match="inventory_roundtrip_invalid"):
        capture(tmp_path, writer=writer)
