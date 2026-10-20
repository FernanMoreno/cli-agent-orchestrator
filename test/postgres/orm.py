"""Native ORM engines bound to the current test's private PostgreSQL target."""

from test.postgres.fixtures import make_orm_engine

from cli_agent_orchestrator.clients.postgres import DatabaseTarget


def configured_orm_engine():
    return make_orm_engine(DatabaseTarget.from_settings())
