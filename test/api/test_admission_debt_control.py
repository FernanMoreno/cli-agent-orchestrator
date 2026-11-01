"""An operator can discover recovery targets only through its paid shared control."""

from test.api.test_admission_control import control, headers
from uuid import uuid4

import pytest

from cli_agent_orchestrator.models.admission_domains import (
    SharedDebtCursor,
    SharedDebtEntry,
    SharedDebtPage,
)
from cli_agent_orchestrator.security.auth import is_verified_principal
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime

pytestmark = pytest.mark.offline_database
PATH = "/admission/shared/debt"


def test_admin_gets_typed_descriptive_page_without_private_binding(control, monkeypatch):
    client, _, key = control
    entry_id = str(uuid4())
    entry = SharedDebtEntry(
        "operation", entry_id, str(uuid4()), str(uuid4()), 1, "preparing", False
    )
    page = SharedDebtPage((entry,), SharedDebtCursor("operation", entry_id))
    calls = []

    async def listing(self, *, principal, **kwargs):
        assert is_verified_principal(principal) and principal.kind == "jwt"
        calls.append(kwargs)
        return page

    monkeypatch.setattr(InboxAdmissionRuntime, "outstanding_resources", listing, raising=False)
    response = client.post(PATH, json={"version": 1, "limit": 1}, headers=headers(key))
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 1 and data["next_cursor"] == {
        "kind": "operation",
        "entry_id": entry_id,
    }
    assert len(data["entries"]) == 1 and data["entries"][0]["current_owner"] is False
    assert set(data["entries"][0]) == {
        "kind",
        "entry_id",
        "instance_id",
        "process_generation",
        "revision",
        "state",
        "current_owner",
    }
    assert calls == [{"after": None, "limit": 1}]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "change",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 1001},
        {"after": {"kind": "unknown", "entry_id": str(uuid4())}},
        {"after": {"kind": "operation", "entry_id": "private-input-canary"}},
        {"binding": "private-input-canary"},
    ],
)
def test_bad_catalog_request_is_fixed_before_control(control, monkeypatch, change):
    client, _, key = control

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid debt query reached control")

    monkeypatch.setattr(InboxAdmissionRuntime, "outstanding_resources", forbidden, raising=False)
    response = client.post(PATH, json={"version": 1, **change}, headers=headers(key))
    assert response.status_code == 422 and "private-input-canary" not in response.text


@pytest.mark.parametrize("scopes", ["cao:read", "cao:write"])
def test_nonadmin_cannot_resolve_debt_targets(control, monkeypatch, scopes):
    client, _, key = control

    async def forbidden(*args, **kwargs):
        pytest.fail("nonadmin read resource debt")

    monkeypatch.setattr(InboxAdmissionRuntime, "outstanding_resources", forbidden, raising=False)
    assert client.post(PATH, json={"version": 1}, headers=headers(key, scopes)).status_code == 403


def test_unbound_control_does_not_use_an_unpaid_inventory_connection(control):
    client, _, key = control
    assert client.post(PATH, json={"version": 1}, headers=headers(key)).status_code == 503
    assert client.post(PATH, json={"version": 1}).status_code == 401
