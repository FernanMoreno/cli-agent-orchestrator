"""Descriptive root SQL identity; these values grant no authority."""

from dataclasses import dataclass

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding


@dataclass(frozen=True)
class RootSqlSubject:
    """Descriptive request, never permission to open SQL or issue a certificate."""

    binding: ResourceBinding
    sql_application_name: str
    boot_id: str
    start_time_ticks: int
    pid_namespace: tuple[int, int]
