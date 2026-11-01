"""Refused preparation and failures inside a delivery have distinct authority."""

from test.services.test_delivery_admission import create, observe

import pytest

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.inbox_service import InboxService

pytestmark = pytest.mark.offline_database


def test_storage_error_inside_inbox_body_remains_visible(monkeypatch):
    owner = create()
    assert observe(owner, 4)
    service = InboxService(admission=owner, clock=lambda: 10)
    lease = owner.try_reserve("receiver", now=10, storage_slots=2).reservation
    assert lease is not None

    def body(*args):
        raise StorageAdmissionUnavailable()

    with monkeypatch.context() as patch:
        patch.setattr(service, "_deliver_pending_locked", body)
        with pytest.raises(StorageAdmissionUnavailable):
            service._execute_reserved(lease, "receiver", 1, None)
    assert owner.snapshot(now=10).inflight == 0
