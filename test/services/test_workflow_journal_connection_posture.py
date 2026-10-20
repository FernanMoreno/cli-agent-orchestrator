"""Connection posture for the native PostgreSQL workflow journal."""

import pytest

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, OperationalError
from cli_agent_orchestrator.services import workflow_journal, workflow_spec_service
from cli_agent_orchestrator.services.workflow_journal import _connect

pytestmark = pytest.mark.postgres


def test_connect_uses_exact_target_and_opens_transaction(postgres_targets):
    target = postgres_targets["main"]
    connection = _connect(target)
    try:
        assert isinstance(connection, Connection)
        assert connection.target.identity == target.identity
        assert connection.in_transaction
        assert connection.execute("SHOW transaction_isolation").fetchone()[0] == "read committed"
        assert connection.execute("SHOW lock_timeout").fetchone()[0] == "5s"
        assert connection.execute("SHOW statement_timeout").fetchone()[0] == "30s"
        connection.commit()
        assert connection.execute("SHOW lock_timeout").fetchone()[0] == "10s"
    finally:
        connection.close()


def test_connect_uses_current_configured_target(monkeypatch, postgres_targets):
    target = postgres_targets["main"]
    monkeypatch.setattr(
        DatabaseTarget,
        "from_settings",
        classmethod(lambda cls, scope="main", home=None: target),
    )

    connection = _connect()
    try:
        assert connection.target.identity == target.identity
        assert connection.in_transaction
    finally:
        connection.close()


def test_connect_uses_current_journal_lock_timeout_constant(monkeypatch, postgres_targets):
    target = postgres_targets["main"]
    monkeypatch.setattr(workflow_journal, "WORKFLOW_JOURNAL_BUSY_TIMEOUT_MS", 1234, raising=False)

    connection = _connect(target)
    try:
        assert connection.execute("SHOW lock_timeout").fetchone()[0] == "1234ms"
        assert connection.execute("SHOW statement_timeout").fetchone()[0] == "30s"
    finally:
        connection.close()


def test_each_connection_has_an_independent_transaction(postgres_targets):
    target = postgres_targets["main"]
    first = _connect(target)
    second = _connect(target)
    try:
        assert first.raw is not second.raw
        assert first.in_transaction and second.in_transaction
        first.commit()
        assert not first.in_transaction
        assert second.in_transaction
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    ("module", "opener"),
    (
        (workflow_journal, workflow_journal._connect),
        (workflow_spec_service, workflow_spec_service._connect),
    ),
)
def test_open_closes_connection_when_begin_fails(module, opener, monkeypatch, postgres_targets):
    target = postgres_targets["main"]
    allocated = []
    real_connect = module.connect

    def track_connect(configured_target):
        connection = real_connect(configured_target)
        allocated.append(connection)
        return connection

    def fail_begin(self, *_args, **_kwargs):
        raise OperationalError("injected transaction-start failure")

    monkeypatch.setattr(module, "connect", track_connect)
    monkeypatch.setattr(Connection, "begin", fail_begin)

    with pytest.raises(OperationalError, match="transaction-start failure"):
        opener(target)

    assert len(allocated) == 1
    assert allocated[0].raw.closed


def test_open_closes_connection_when_journal_timeout_setup_fails(monkeypatch, postgres_targets):
    target = postgres_targets["main"]
    allocated = []
    real_connect = workflow_journal.connect
    real_execute = Connection.execute

    def track_connect(configured_target):
        connection = real_connect(configured_target)
        allocated.append(connection)
        return connection

    def fail_timeout_setup(self, statement, params=()):
        if "set_config('lock_timeout'" in statement:
            raise OperationalError("injected journal lock-timeout setup failure")
        return real_execute(self, statement, params)

    monkeypatch.setattr(workflow_journal, "connect", track_connect)
    monkeypatch.setattr(Connection, "execute", fail_timeout_setup)

    with pytest.raises(OperationalError, match="lock-timeout setup failure"):
        _connect(target)

    assert len(allocated) == 1
    assert allocated[0].raw.closed
