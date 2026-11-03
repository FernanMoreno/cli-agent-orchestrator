"""Signed root/PG composition for synchronous authority reads and monitor ports."""

import inspect
from dataclasses import asdict
from test.postgres import test_root_control_ports as root_ports
from test.postgres.test_admission_reservations import shared

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.admission_bootstrap_schema import (
    RESOURCE_ENVELOPE_MIGRATIONS,
    RESOURCE_ENVELOPE_TABLE,
)
from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryAdmission,
    StorageControlPermit,
)

pytestmark = pytest.mark.postgres


def require_api():
    for method in (
        ResourceEnvelopeRepository.read_latest,
        ResourceBootstrap.load_current,
        ResourceBootstrap.shared_repository,
        AdmissionMonitor,
    ):
        assert "control_session" in inspect.signature(method).parameters
    assert callable(getattr(StorageControlPermit, "check_current_operation", None))
    assert callable(getattr(DeliveryAdmission, "try_reserve_command", None))


def test_root_monitor_original_worker_ports_exist():
    require_api()


# Add the new composition after the existing H1 ledger effects, preserving its
# actual genesis/issuer/credit producer and durable-debt assertions verbatim.
BOUNDARY = "        assert channel.raw.closed is False\n    assert channel.raw.closed is False"
assert root_ports.CHILD.count(BOUNDARY) == 1
MONITOR = r"""
        import asyncio,threading
        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace
        from cli_agent_orchestrator.clients import admission_bootstrap_repository as envelope_module
        from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims
        from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor,AuthorityChangeUnavailable
        from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
        from cli_agent_orchestrator.services.admission_resources import ResourceSample
        from cli_agent_orchestrator.services.admission_policy import CapacityObservation
        from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain
        monitor_mode=os.environ['ROOT_MONITOR_MODE']
        envelope_module.connect=no_connect
        authority_binding=[binding]
        resource_clock=[20.0]
        envelope=ResourceEnvelopeRepository(target_supplier=lambda:target,connect_factory=no_connect)
        authority=ResourceBootstrap(repository=envelope,binding_supplier=lambda _:authority_binding[0],instance_supplier=lambda:binding.instance_id,clock=lambda:resource_clock[0])
        fresh_repo=authority.shared_repository(control_session=session)
        assert fresh_repo.current_binding()==binding
        assert fresh_repo.audit_usage()==()
        wrong_target=replace(target,schema=target.schema+'_other')
        try:envelope.read_latest(wrong_target,binding.instance_id,control_session=session)
        except StorageAdmissionUnavailable:pass
        else:raise AssertionError('envelope accepted another target')
        envelope._target_supplier=lambda:wrong_target
        try:authority.load_current(control_session=session)
        except StorageAdmissionUnavailable:pass
        else:raise AssertionError('bootstrap ignored fresh target change')
        finally:envelope._target_supplier=lambda:target
        calls=[]
        class Resources:
            def reset_history(self):pass
            def sample(self,*,storage):
                calls.append('resource')
                if monitor_mode=='binding':
                    authority_binding[0]=replace(binding,resource_view_sha256='f'*64)
                return ResourceSample(CapacityObservation(resource_clock[0],4096*1024*1024,4096*1024*1024,16000,16000,storage.available_slots,'known'),())
        pressure=PostgresPressureSampler(target_supplier=lambda:target,connect_factory=no_connect,clock=lambda:resource_clock[0])
        kwargs=dict(policy_loader=AdmissionPolicyLoader(source=lambda:{'version':1,'policy':{}}),bootstrap=authority,postgres=pressure,scope_id='root/control',clock=lambda:resource_clock[0],resource_factory=lambda _:Resources(),control_session=session)
        if monitor_mode=='wrong_owner':
            other,other_control=DeliveryAdmission.with_storage_control(AdmissionPolicy())
            try:AdmissionMonitor(other,storage_control=other_control,**kwargs)
            except StorageAdmissionUnavailable:pass
            else:raise AssertionError('another owner accepted original session')
        monitor=AdmissionMonitor(owner,storage_control=control,**kwargs)
        before=monitor.diagnostic(now=20.0)
        try:monitor.begin_authority_change()
        except AuthorityChangeUnavailable:pass
        else:raise AssertionError('bound monitor started an owner writer')
        assert not monitor._authority_tokens and not monitor._authority_state.uncertain
        try:asyncio.run(monitor.start())
        except StorageAdmissionUnavailable:pass
        else:raise AssertionError('bound monitor dispatched a transferable async worker')
        assert monitor._runner is None and monitor._lifecycle==0
        if monitor_mode=='foreign':
            # The original owner holds its mutex: a foreign constructor must
            # fail on session affinity before even inspecting owner properties.
            with ThreadPoolExecutor(max_workers=1) as pool:
                with owner._guard:
                    future=pool.submit(lambda:AdmissionMonitor(owner,storage_control=control,**kwargs))
                    try:future.result(timeout=2)
                    except StorageAdmissionUnavailable:pass
                    else:raise AssertionError('foreign constructor accepted session')
            with ThreadPoolExecutor(max_workers=1) as pool:
                def authority_read():
                    with monitor.authority_read():raise AssertionError('foreign authority read entered')
                with monitor._refresh_guard:
                    try:pool.submit(authority_read).result(timeout=2)
                    except StorageAdmissionUnavailable:pass
                    else:raise AssertionError('foreign authority read accepted')
            with ThreadPoolExecutor(max_workers=1) as pool:
                try:pool.submit(monitor.refresh).result(timeout=5)
                except StorageAdmissionUnavailable:pass
                else:raise AssertionError('foreign worker refreshed session')
            async def wrong_task():
                try:monitor.refresh()
                except StorageAdmissionUnavailable:pass
                else:raise AssertionError('foreign task refreshed session')
            asyncio.run(wrong_task())
            assert monitor._lifecycle==0 and not monitor._refresh_guard.locked()
            assert monitor._last_cycle_result is None and not calls
        if monitor_mode in ('rollback','rollback_unknown'):
            original_verify=envelope._verify_reader
            original_rollback_check=session._check_rolled_back
            def failing_reader(reader):
                original_verify(reader)
                reader.execute('SELECT 1/0')
            def unavailable_rollback():raise StorageAdmissionUnavailable()
            envelope._verify_reader=failing_reader
            if monitor_mode=='rollback_unknown':session._check_rolled_back=unavailable_rollback
            try:
                if monitor_mode=='rollback_unknown':
                    try:monitor.refresh()
                    except StorageAdmissionUnavailable:pass
                    else:raise AssertionError('unverified rollback became ordinary authority failure')
                    assert monitor._last_cycle_result.result.reason=='monitor_failed'
                    assert owner.snapshot(now=resource_clock[0]).limit==0
                else:
                    failed=monitor.refresh()
                    assert failed.reason=='authority_unavailable' and not failed.published
                    session.check_target(target)
            finally:
                envelope._verify_reader=original_verify
                session._check_rolled_back=original_rollback_check
            if monitor_mode=='rollback':
                assert authority.load_current(control_session=session).attestation is not None
        if monitor_mode=='busy':
            with session.transaction():
                try:monitor.refresh()
                except StorageAdmissionUnavailable:pass
                else:raise AssertionError('busy session entered monitor')
            assert not monitor._refresh_guard.locked() and not calls
        if monitor_mode in ('postcheck','postcheck_pressure'):
            saved_idle=session._check_idle
            committed=[0]
            generation_before=owner.generation
            saved_tx=channel.funded_transaction
            @contextmanager
            def postcommit():
                with saved_tx() as connection:yield connection
                committed[0]+=1
            def failed_observation():
                saved_idle()
                if committed[0]>=(2 if monitor_mode=='postcheck_pressure' else 1):raise StorageAdmissionUnavailable()
            channel.funded_transaction=postcommit
            session._check_idle=failed_observation
            try:monitor.refresh()
            except FundedSqlCommitUncertain:pass
            else:raise AssertionError('postcommit uncertainty became ordinary capacity')
            finally:
                channel.funded_transaction=saved_tx
                session._check_idle=saved_idle
            try:monitor.refresh()
            except StorageAdmissionUnavailable:pass
            else:raise AssertionError('poisoned session refreshed')
            assert not calls and not monitor._refresh_guard.locked()
            assert monitor._last_cycle_result.result.reason=='monitor_failed'
            assert owner.generation==generation_before+(2 if monitor_mode=='postcheck_pressure' else 1)
            assert owner.snapshot(now=resource_clock[0]).limit==0
        elif monitor_mode=='rollback_unknown':
            try:monitor.refresh()
            except StorageAdmissionUnavailable:pass
            else:raise AssertionError('poisoned rollback session refreshed')
            assert not calls
        else:
            outcomes=[]
            for iteration in range(3):
                resource_clock[0]+=1
                result=monitor.refresh()
                outcomes.append(result)
                backend_pids.append(channel.execute('SELECT pg_backend_pid()').fetchone()[0])
            if monitor_mode in ('binding','revoked'):
                assert all(r.reason=='authority_unavailable' and not r.published for r in outcomes)
                assert owner.snapshot(now=resource_clock[0]).limit==0
                if monitor_mode=='revoked':assert not calls
                else:assert len(calls)==1
            else:
                assert all(r.published for r in outcomes)
                assert outcomes[-1].reason=='capacity_available'
                assert owner.snapshot(now=resource_clock[0]).limit>4
                assert len(calls)==3
            assert pressure.retire_owned()
            if monitor_mode=='commands':
                from cli_agent_orchestrator.services.admission_policy import ResourceAmounts
                from cli_agent_orchestrator.services.admission_diagnostics import diagnostics_response,DiagnosticsVersionRequired
                footprint=ResourceAmounts(1024*1024,10,0)
                reservations=[]
                control_credits=owner.snapshot(now=resource_clock[0]).storage_completion_credits
                for index in range(25):
                    admitted=owner.try_reserve_command(footprint=footprint,now=resource_clock[0])
                    assert admitted.reservation is not None and admitted.reason=='capacity_available'
                    assert owner.owns_normal_storage(admitted.reservation) is False
                    reservations.append(admitted.reservation)
                for reservation in reservations:assert reservation.start(now=resource_clock[0])
                snapshot=owner.snapshot(now=resource_clock[0])
                assert snapshot.command_inflight==snapshot.command_started==25
                assert snapshot.delivery_inflight==snapshot.delivery_started==0
                assert snapshot.storage_connection_loans==snapshot.control_connection_loans==1
                assert snapshot.storage_completion_credits==control_credits
                assert snapshot.committed==ResourceAmounts(25*1024*1024,250,0)
                resource_clock[0]+=1
                assert monitor.refresh().published
                diagnostic=monitor.diagnostic(now=resource_clock[0])
                view=diagnostics_response(diagnostic,now=resource_clock[0],version=2)
                assert view.version==2 and view.operations.commands.inflight==25 and view.operations.commands.started==25
                assert view.operations.inflight_operations==26 and view.operations.started_operations==26
                try:diagnostics_response(diagnostic,now=resource_clock[0])
                except DiagnosticsVersionRequired:pass
                else:raise AssertionError('v1 diagnostics silently hid commands')
                owner.stop_accepting()
                diagnostic=monitor.diagnostic(now=resource_clock[0])
                closing=diagnostics_response(diagnostic,now=resource_clock[0],version=2)
                assert not closing.accepting and closing.operations.commands.inflight==25
                assert closing.debt.memory_bytes==25*1024*1024 and closing.debt.cpu_millicores==250
                for reservation in reservations:
                    assert reservation.cancel_before_start() is False
                    assert reservation.finish() is True
                retired=owner.snapshot(now=resource_clock[0])
                assert retired.command_inflight==retired.command_started==0
                assert retired.committed==ResourceAmounts() and retired.inflight==1
                assert channel.execute('SELECT pg_backend_pid()').fetchone()[0]==backend
                assert owner._shared_coordinator is None
        assert channel.raw.closed is False
    assert channel.raw.closed is False"""
CHILD = root_ports.CHILD.replace(BOUNDARY, MONITOR)


@pytest.mark.parametrize(
    "monitor_mode",
    [
        "healthy",
        "binding",
        "revoked",
        "wrong_owner",
        "foreign",
        "rollback",
        "busy",
        "postcheck",
        "rollback_unknown",
        "postcheck_pressure",
        "commands",
    ],
)
def test_signed_root_monitor_reuses_original_backend(shared, monkeypatch, monitor_mode):
    require_api()
    original_migrate = root_ports.migrate_component
    original_issue = root_ports.issue_root_sql_certificate

    def migrate(target, migrations):
        original_migrate(target, migrations)
        original_migrate(target, RESOURCE_ENVELOPE_MIGRATIONS)

    def issue(bootstrap, channel, **kwargs):
        target = channel.target
        bound = kwargs["subject"].binding
        values = {
            **asdict(bound),
            **asdict(EnvelopeClaims(4096 * 1024 * 1024, 16000, 64, True, False)),
            "revision": 1,
            "protocol_version": 1,
            "active": monitor_mode != "revoked",
            "actor_principal_id": root_ports.ADMIN,
        }
        with channel.funded_transaction():
            channel.execute(
                sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(target.schema, RESOURCE_ENVELOPE_TABLE),
                    sql.SQL(",").join(map(sql.Identifier, values)),
                    sql.SQL(",").join(sql.Placeholder() for _ in values),
                ),
                tuple(values.values()),
            )
        return original_issue(bootstrap, channel, **kwargs)

    # Nested scope restores the inherited producer before any other tests run.
    with monkeypatch.context() as patch:
        patch.setattr(root_ports, "CHILD", CHILD)
        patch.setattr(root_ports, "migrate_component", migrate)
        patch.setattr(root_ports, "issue_root_sql_certificate", issue)
        patch.setenv("ROOT_MONITOR_MODE", monitor_mode)
        root_ports.test_root_runtime_ports_reuse_one_actual_backend(shared, patch, "normal")
