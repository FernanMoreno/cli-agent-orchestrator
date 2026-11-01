"""Shared-required configuration closes data until trusted runtime binding."""

from test.services.test_delivery_admission import observe, reserve

import pytest

from cli_agent_orchestrator.clients.storage_admission import acquire_storage_connection
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


def test_shared_required_owner_denies_data_without_denial_of_control():
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    owner.require_shared_admission()
    assert observe(owner, 8)
    assert owner.try_reserve("new", now=10).reservation is None
    assert owner.try_reserve_storage(now=10).reservation is None
    assert owner.shared_required and not owner.shared_ready
    with permit.operation(clock=lambda: 10):
        loan = acquire_storage_connection()
        assert loan is not None and loan.finish()


def test_cannot_change_to_shared_mode_while_normal_work_is_alive():
    owner, _ = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert observe(owner, 8)
    lease = reserve(owner, "active")
    assert lease.start()
    with pytest.raises(RuntimeError):
        owner.require_shared_admission()
    assert not owner.shared_required
    assert lease.finish()
    owner.require_shared_admission()
    assert owner.shared_required
