"""Persistent policy reads share the one actual funded Root backend."""

from test.postgres import test_root_control_monitor as root_monitor
from test.postgres import test_root_control_ports as root_ports
from test.postgres.test_admission_reservations import shared

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.admission_policy_schema import (
    ADMISSION_POLICY_MIGRATIONS,
    ADMISSION_POLICY_TABLE,
)
from cli_agent_orchestrator.models.admission_policy_control import AdmissionPolicyValues

pytestmark = pytest.mark.postgres


def test_persistent_monitor_uses_exact_original_postgres_session(shared, monkeypatch):
    original_migrate = root_ports.migrate_component
    original_issue = root_ports.issue_root_sql_certificate

    def migrate(target, migrations):
        original_migrate(target, migrations)
        original_migrate(target, ADMISSION_POLICY_MIGRATIONS)

    def issue(bootstrap, channel, **kwargs):
        values = {
            "profile_id": kwargs["subject"].binding.instance_id,
            "revision": 1,
            "active": True,
            **AdmissionPolicyValues().model_dump(),
            "actor_principal_id": root_ports.ADMIN,
        }
        with channel.funded_transaction():
            channel.execute(
                sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(channel.target.schema, ADMISSION_POLICY_TABLE),
                    sql.SQL(",").join(map(sql.Identifier, values)),
                    sql.SQL(",").join(sql.Placeholder() for _ in values),
                ),
                tuple(values.values()),
            )
        return original_issue(bootstrap, channel, **kwargs)

    marker = "policy_loader=AdmissionPolicyLoader(source=lambda:{'version':1,'policy':{}})"
    assert root_monitor.CHILD.count(marker) == 1
    child = root_monitor.CHILD.replace(
        marker,
        "policy_loader=AdmissionPolicyLoader(repository=__import__("
        "'cli_agent_orchestrator.clients.admission_policy_repository',"
        "fromlist=['AdmissionPolicyRepository']).AdmissionPolicyRepository("
        "profile_id=binding.instance_id,target_supplier=lambda:target,connect_factory=no_connect))",
    )
    assert child.count("monitor=AdmissionMonitor(owner,storage_control=control,**kwargs)") == 1
    child = child.replace(
        "assert channel.raw.closed is False\n    assert channel.raw.closed is False",
        "assert monitor.applied_policy_revision==1\n"
        "        assert channel.raw.closed is False\n    assert channel.raw.closed is False",
    )
    compile(child, "persistent-root-policy-child", "exec")
    with monkeypatch.context() as patch:
        patch.setattr(root_ports, "migrate_component", migrate)
        patch.setattr(root_ports, "issue_root_sql_certificate", issue)
        patch.setattr(root_monitor, "CHILD", child)
        root_monitor.test_signed_root_monitor_reuses_original_backend(shared, patch, "healthy")
