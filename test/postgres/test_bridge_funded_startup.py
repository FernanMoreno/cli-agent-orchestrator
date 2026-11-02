"""Real Bridge module through SQL initialization/restore, before admission startup.

Root funding and model/native-provider/network acceptance are separate gates.
The private sitecustomize only records drivers, supplies an isolated profile,
removes native backend creation, and stops at the admission composition boundary.
"""

import json
import os
import time
from test.postgres.test_sql_broker import parent
from test.postgres.test_sql_intent_journal import api, marked, prepare, shared
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.runtime_channel_schema import runtime_channel_migrations
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_STARTUP_PERMISSION_ENV,
    SqlBrokerIdle,
)
from cli_agent_orchestrator.runtime_channel.bridge_supervisor import funded_bridge
from cli_agent_orchestrator.services.resource_process_supervisor import BINDING_FD_ENV

pytestmark = pytest.mark.postgres

INSTRUMENTATION = r"""
import atexit,json,os
from pathlib import Path
import psycopg
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.runtime_channel import store
from cli_agent_orchestrator.backends import registry
from cli_agent_orchestrator.services import admission_bootstrap,inbox_admission_runtime
from cli_agent_orchestrator.utils import process_sql_origin as origin
from cli_agent_orchestrator.services import resource_process_supervisor as supervisor
identities={}; identity_events=[]; actual_identity=supervisor._identity
def observed_identity(pid):
    try:
        value=actual_identity(pid)
    except BaseException as failure:
        identity_events.append(dict(pid=pid,error=type(failure).__name__,message=str(failure)))
        raise
    old=identities.get(pid)
    if old is not None and old!=value:
        identity_events.append(dict(pid=pid,changed=[k for k in vars(value) if getattr(old,k)!=getattr(value,k)]))
    identities[pid]=value
    return value
supervisor._identity=observed_identity
marker=Path(os.environ['FUNDED_BRIDGE_MARKER'])
report=dict(calls=0,initialized=False,restored=False,admission=False)
original=psycopg.connect
def driver(**kwargs):
    report['calls']+=1
    return original(**kwargs)
psycopg.connect=driver
initial=database.init_runtime_db
def initialize():
    initial();report['initialized']=True
database.init_runtime_db=initialize
restore=store.RemoteOperationStore.get_runtime
def restored(self,*args):
    result=restore(self,*args);report['restored']=True;return result
store.RemoteOperationStore.get_runtime=restored
registry.get_backend=lambda:object()
admission_bootstrap.get_persistent_instance_id=lambda:os.environ['FUNDED_BRIDGE_PROFILE']
def boundary(*args,**kwargs):
    bootstrap=kwargs['bootstrap_factory']()
    assert bootstrap.terminal_generation
    report['admission']=True
    raise SystemExit(23)
inbox_admission_runtime.runtime_from_environment=boundary
def record():
    report['startup_spent']=origin._STATE.startup_spent
    marker.with_suffix('.kernel.json').write_text(json.dumps(identity_events))
    marker.write_text(json.dumps(report))
atexit.register(record)
"""
compile(INSTRUMENTATION, "bridge-startup-instrumentation", "exec")


def environment(tmp_path):
    instrumentation = tmp_path / "instrumentation"
    instrumentation.mkdir()
    (instrumentation / "sitecustomize.py").write_text(INSTRUMENTATION)
    values = dict(os.environ)
    for name in (BINDING_FD_ENV, SQL_STARTUP_PERMISSION_ENV, SQL_BROKER_FD_ENV):
        values.pop(name, None)
    values.update(
        PYTHONPATH=str(instrumentation) + os.pathsep + os.environ["PYTHONPATH"],
        CAO_ADMISSION_MODE="shared",
        CAO_ADMISSION_POLICY_FILE=str(tmp_path / "not-read-before-admission.json"),
        CAO_BRIDGE_RUNTIME_ID="funded-startup-" + uuid4().hex,
        CAO_BRIDGE_SERVER_URL="http://127.0.0.1:1",
        CAO_RUNTIME_TOKEN="synthetic-fixture-only",
        FUNDED_BRIDGE_PROFILE=str(uuid4()),
        FUNDED_BRIDGE_MARKER=str(tmp_path / "observed.json"),
    )
    values.pop("CAO_RUNTIME_TOKEN_FILE", None)
    return values


@pytest.mark.parametrize("exhaust", [False, True])
def test_actual_bridge_startup_uses_paid_initial_and_followup_openings(
    shared, postgres_service, tmp_path, monkeypatch, exhaust
):
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS
    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable

    target = shared[0]
    migrate_component(target, CORE_MIGRATIONS)
    migrate_component(target, runtime_channel_migrations())
    with marked(monkeypatch):
        repository, journal = prepare(shared, api(), capacity=10)
        units = 1 if exhaust else 5
        owner = None
        receipts = []
        with parent(target, units) as (budget, reservation, channel):
            with postgres_service.connect_admin(target.database) as observer:
                original = repository.record_child_followup

                def committed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    row = observer.execute(
                        sql.SQL("SELECT session_id FROM {} WHERE intent_id=%s").format(
                            sql.Identifier(target.schema, "_cao_sql_child_session_fundings")
                        ),
                        (result.intent_id,),
                    ).fetchone()
                    assert row and str(row[0]) == kwargs["session_charge"].session_id
                    receipts.append(result)
                    return result

                monkeypatch.setattr(repository, "record_child_followup", committed)
                with funded_bridge(
                    channel=channel,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    session_reservation=reservation,
                    env=environment(tmp_path),
                ) as owner:
                    deadline = time.monotonic() + 60
                    denied = False
                    try:
                        while time.monotonic() < deadline:
                            result = owner.serve_child_sql_request(
                                channel=channel,
                                repository=repository,
                                destination=target,
                                session_reservation=reservation,
                                timeout_s=0.1,
                            )
                            if result is None:
                                break
                            assert result is SqlBrokerIdle.NO_REQUEST or result is receipts[-1]
                        else:
                            pytest.fail("Bridge did not close its owned broker before the deadline")
                    except StorageAdmissionUnavailable as failure:
                        assert exhaust
                        assert failure.sql_broker_request.sequence == 1
                        denied = True
                    # Observe natural exit and the driver's report BEFORE context cleanup.
                    assert denied is exhaust
                    assert owner.wait(timeout_s=30).returncode == (1 if exhaust else 23)
                assert owner is not None and owner._exited and owner._closed
                charges = owner.child_sql_session_charges
                assert len(charges) == (1 if exhaust else 3)
                assert budget.snapshot(now=10.0).storage_connection_loans == len(charges) + 1
                assert channel.execute("SELECT 42").fetchone()[0] == 42
                assert reservation.finish() is False
                observed = json.loads((tmp_path / "observed.json").read_text())
                assert observed == dict(
                    calls=1 if exhaust else 3,
                    initialized=True,
                    restored=not exhaust,
                    admission=not exhaust,
                    startup_spent=True,
                )
                assert [r.sequence for r in receipts] == ([] if exhaust else [1, 2])
                assert len({c.session_id for c in charges}) == len(charges)
        assert budget.snapshot(now=10.0).storage_connection_loans == len(charges)


def test_closed_original_control_scope_cannot_launch_a_bridge(shared, monkeypatch):
    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
    from cli_agent_orchestrator.runtime_channel import bridge_supervisor

    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, api())
        with parent(target, 1) as (_, reservation, channel):
            pass
        monkeypatch.setattr(
            bridge_supervisor,
            "launch_bridge",
            lambda **kw: pytest.fail("launched without original funding scope"),
        )
        with pytest.raises(StorageAdmissionUnavailable):
            with funded_bridge(
                channel=channel,
                repository=repository,
                journal_id=journal,
                intent_id=str(uuid4()),
                session_reservation=reservation,
            ):
                pytest.fail("entered without funding")
