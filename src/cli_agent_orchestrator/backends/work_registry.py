"""Explicit server-owned registry for backends that enforce Work contracts."""

from cli_agent_orchestrator.backends.base import TerminalBackend


# Work admission stays unavailable until the server explicitly registers a
# backend whose preflight and protected effect boundary enforce the contract.
WORK_BACKENDS: dict[str, TerminalBackend] = {}
