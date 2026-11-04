"""Credential checks run on the already-funded original worker before effects."""

import threading
from dataclasses import replace
from test.services.test_root_deployment_runtime import _connection, _fake_deployment, _fixture
from unittest.mock import patch
from uuid import uuid4

import pytest

from cli_agent_orchestrator.services import root_deployment_runtime as module

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
async def test_failed_credential_preflight_opens_no_sql_or_child():
    config, principal, _, owner = _fixture()
    checked = []

    def preflight():
        checked.append(threading.current_thread())
        assert not owner._guard._is_owned()
        assert owner.snapshot(now=1.0).command_started >= 1
        raise ValueError("controlled private credential failure")

    runtime = module.RootDeploymentRuntime(replace(config, credential_preflight=preflight))
    with (
        patch.object(module, "connect", side_effect=AssertionError("bad credentials reached SQL")),
        patch.object(
            module.RootControlDeployment,
            "launch",
            side_effect=AssertionError("bad credentials started child"),
        ),
    ):
        try:
            with pytest.raises(module.RootDeploymentRuntimeUnavailable):
                await runtime.launch(principal=principal)
        finally:
            await runtime.aclose(timeout=2)
    assert len(checked) == 1 and checked[0] is not threading.current_thread()
    assert runtime._worker_retired
    assert owner.snapshot(now=1.0).command_started == 0


@pytest.mark.asyncio
async def test_credential_checks_precede_connection_and_activation_issuer():
    config, principal, subject, owner = _fixture()
    original_key_supplier = config.key_supplier
    events = []

    def preflight():
        assert not owner._guard._is_owned()
        events.append("credentials")

    def keys():
        events.append("keys")
        return original_key_supplier()

    connection = _connection(config)

    def connect(target):
        events.append("connect")
        return connection

    runtime = module.RootDeploymentRuntime(
        replace(config, credential_preflight=preflight, key_supplier=keys)
    )
    fake = _fake_deployment(subject)
    fake.reset()
    fake.call_hook = lambda: events.append("activate")
    with (
        patch.object(module, "connect", side_effect=connect),
        patch.object(module, "RootControlDeployment", fake),
    ):
        try:
            await runtime.launch(principal=principal)
            assert events == ["credentials", "connect"]
            await runtime.activate(
                principal=principal,
                journal_id=str(uuid4()),
                intent_id=str(uuid4()),
                session_id=str(uuid4()),
            )
            assert events == ["credentials", "connect", "credentials", "keys", "activate"]
        finally:
            await runtime.aclose(timeout=2)
    assert connection.raw.closed and runtime._worker_retired


@pytest.mark.asyncio
async def test_changed_credentials_refuse_issuer_without_losing_owned_child():
    config, principal, subject, _ = _fixture()
    checks = []

    def preflight():
        checks.append(True)
        if len(checks) > 1:
            raise ValueError("controlled credential rotation")

    runtime = module.RootDeploymentRuntime(replace(config, credential_preflight=preflight))
    connection = _connection(config)
    fake = _fake_deployment(subject)
    fake.reset()
    with (
        patch.object(module, "connect", return_value=connection),
        patch.object(module, "RootControlDeployment", fake),
    ):
        try:
            await runtime.launch(principal=principal)
            with pytest.raises(module.RootDeploymentRuntimeUnavailable):
                await runtime.activate(
                    principal=principal,
                    journal_id=str(uuid4()),
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                )
            assert [e[0] for e in fake.events] == ["launch"]
            assert runtime._deployment is not None
        finally:
            await runtime.aclose(timeout=2)
    assert connection.raw.closed and runtime._worker_retired
