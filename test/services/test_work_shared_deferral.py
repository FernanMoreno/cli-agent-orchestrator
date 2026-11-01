"""Shared preparation denial defers work before selection SQL or intent."""

from contextlib import contextmanager
from test.services.test_delivery_admission import create, observe

import pytest

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services import work_admission as module

pytestmark = pytest.mark.offline_database


def test_shared_storage_denial_keeps_dispatch_eligible_before_preview(monkeypatch):
    admission = object.__new__(module.WorkAdmission)
    owner = create()
    assert observe(owner, 4)
    admission._resource_admission = owner
    admission._resource_clock = lambda: 10
    admission._preview = lambda **_: pytest.fail("denied preparation reached selection SQL")
    admission._preflight = lambda *_: pytest.fail("denied selection reached external preflight")

    @contextmanager
    def denied(*args, **kwargs):
        raise StorageAdmissionUnavailable()
        yield

    with monkeypatch.context() as patch:
        patch.setattr(module, "physical_storage_scope", denied)
        assert admission._prepare_dispatch(registered_only=True) is None
    assert module.physical_storage_scope is not denied
    assert owner.snapshot(now=10).inflight == 0


def test_storage_error_inside_selection_body_is_never_a_capacity_deferral():
    admission = object.__new__(module.WorkAdmission)
    owner = create()
    assert observe(owner, 4)
    admission._resource_admission = owner
    admission._resource_clock = lambda: 10
    with pytest.raises(StorageAdmissionUnavailable):
        with admission._dispatch_storage_control():
            raise StorageAdmissionUnavailable()
    assert owner.snapshot(now=10).inflight == 0


def test_storage_error_after_delivery_preparation_is_never_a_capacity_deferral():
    admission = object.__new__(module.WorkAdmission)
    owner = create()
    assert observe(owner, 4)
    admission._resource_clock = lambda: 10
    lease = owner.try_reserve("receiver", now=10, storage_slots=2).reservation
    assert lease is not None and lease.start()
    try:
        with pytest.raises(StorageAdmissionUnavailable):
            with admission._physical_dispatch_scope(lease):
                raise StorageAdmissionUnavailable()
    finally:
        lease.finish_after_storage()
    assert owner.snapshot(now=10).inflight == 0
