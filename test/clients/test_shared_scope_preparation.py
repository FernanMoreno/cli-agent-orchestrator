"""The physical-worker boundary prepares normal shared work before its body."""

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    physical_storage_scope,
)

pytestmark = pytest.mark.offline_database


class Preparer:
    storage_process_current = True

    def __init__(self, events, *, fail=False, result=None):
        self.events, self.fail, self.result = events, fail, result

    def try_acquire_storage_connection(self, *, now):
        pytest.fail("this boundary test opens no connection")

    def prepare_storage_scope(self):
        self.events.append("prepare")
        if self.fail:
            raise RuntimeError("private diagnostic must not reach caller")
        return self.result


def test_shared_preparation_precedes_body_and_nested_scope_is_idempotent():
    events = []
    owner = Preparer(events)
    with physical_storage_scope(owner, clock=lambda: 10):
        events.append("body")
        with physical_storage_scope(owner, clock=lambda: 10):
            events.append("nested")
    assert events == ["prepare", "body", "nested"]


@pytest.mark.parametrize("options", [{"fail": True}, {"result": True}])
def test_failed_or_nonprotocol_preparation_never_enters_body(options):
    events = []
    with pytest.raises(StorageAdmissionUnavailable) as error:
        with physical_storage_scope(Preparer(events, **options), clock=lambda: 10):
            events.append("body")
    assert events == ["prepare"]
    assert "private diagnostic" not in str(error.value)


def test_foreign_process_is_rejected_before_prepare():
    owner = Preparer([])
    owner.storage_process_current = False
    with pytest.raises(StorageAdmissionUnavailable):
        with physical_storage_scope(owner, clock=lambda: 10):
            pytest.fail("foreign process entered")
    assert owner.events == []
