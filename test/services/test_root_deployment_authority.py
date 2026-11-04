"""No authentication shortcut or duplicated ownership at the Root operator port."""

import asyncio
import copy
import pickle
import threading
from contextlib import nullcontext
from dataclasses import replace
from test.services.test_root_deployment_runtime import _connection, _fake_deployment, _fixture
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import root_deployment_runtime as module
from cli_agent_orchestrator.services.delivery_admission import CommandReservation

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["none", "nonadmin", "local", "lookalike"])
async def test_unverified_authority_cannot_sample_reserve_connect_or_launch(monkeypatch, kind):
    config, _, _, owner = _fixture()
    before = owner.snapshot(now=1.0).committed

    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized operation reached a resource boundary")

    config = replace(config, clock=forbidden, key_supplier=forbidden)
    runtime = module.RootDeploymentRuntime(config)
    monkeypatch.setattr(module, "connect", forbidden)
    if kind == "none":
        principal = None
    elif kind == "lookalike":
        principal = object.__new__(auth.Principal)
    else:
        principal = auth._verified_principal(
            "urn:caos:operator-port-test",
            "operator",
            [auth.SCOPE_READ] if kind == "nonadmin" else [auth.SCOPE_ADMIN],
            "jwt" if kind == "nonadmin" else "local_operator",
        )
    with pytest.raises(module.RootDeploymentOperatorMismatch):
        await runtime.launch(principal=principal)
    with pytest.raises(module.RootDeploymentOperatorMismatch):
        runtime.status(principal=principal)
    assert owner.snapshot(now=1.0).committed == before
    assert runtime.installable


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_runtime_ownership_cannot_be_copied_or_serialized(operation):
    config, _, _, _ = _fixture()
    runtime = module.RootDeploymentRuntime(config)
    with pytest.raises(TypeError):
        operation(runtime)
    assert runtime.installable


def test_claim_installation_is_one_shot_without_starting_resources(monkeypatch):
    config, _, _, owner = _fixture()

    def forbidden(*args, **kwargs):
        raise AssertionError("installation claim must not start resources")

    monkeypatch.setattr(module, "connect", forbidden)
    runtime = module.RootDeploymentRuntime(replace(config, clock=forbidden))
    before = owner.snapshot(now=1.0).committed
    runtime.claim_installation()
    assert not runtime.installable
    with pytest.raises(module.RootDeploymentRuntimeUnavailable):
        runtime.claim_installation()
    assert owner.snapshot(now=1.0).committed == before


@pytest.mark.asyncio
async def test_positive_cleanup_witness_survives_later_metadata_failure():
    config, principal, subject, owner = _fixture()
    runtime = module.RootDeploymentRuntime(config)
    connection = _connection(config)
    base = _fake_deployment(subject)

    class MetadataFault(base):
        inject_fault = True
        metadata_failed = False

        @property
        def record(self):
            if type(self).metadata_failed:
                raise RuntimeError("controlled metadata getter failure after cleanup")
            return None

        @record.setter
        def record(self, value):
            pass

        def cleanup(self, timeout_s=None):
            type(self).metadata_failed = type(self).inject_fault
            return True

    MetadataFault.reset()
    MetadataFault.join_result = False
    with (
        patch.object(module, "connect", return_value=connection),
        patch.object(module, "RootControlDeployment", MetadataFault),
    ):
        try:
            await runtime.launch(principal=principal)
            charged = owner.snapshot(now=1.0).command_started
            with pytest.raises(module.RootDeploymentRuntimeUnavailable):
                await runtime.aclose(timeout=1)
            assert runtime._cleanup_proven
            assert runtime._child_closed
            assert owner.snapshot(now=1.0).command_started == charged
        finally:
            # Remove only the injected getter fault. No accounting credit or
            # positive process join is manufactured to finish this test.
            MetadataFault.inject_fault = False
            MetadataFault.metadata_failed = False
            with pytest.raises(module.RootDeploymentRuntimeUnavailable):
                await runtime.aclose(timeout=1)
            await runtime._await_worker_return(
                runtime._worker, asyncio.get_running_loop().time() + 2
            )
        assert connection.raw.closed
        assert not runtime._worker_retired
        assert owner.snapshot(now=1.0).command_started == charged


@pytest.mark.asyncio
@pytest.mark.parametrize("attempted", [False, True, None])
@pytest.mark.parametrize("phase", ["unavailable", "awaiting_authority"])
async def test_shutdown_join_budget_uses_activation_attempt_not_cached_phase(attempted, phase):
    config, principal, subject, _ = _fixture()
    runtime = module.RootDeploymentRuntime(config)
    connection = _connection(config)
    base = _fake_deployment(subject)
    budgets = []

    class ActivationState(base):
        _activation_attempted = attempted

        def join(self, timeout_s=None):
            budgets.append(timeout_s)
            return super().join(timeout_s)

    ActivationState.reset()
    with (
        patch.object(module, "connect", return_value=connection),
        patch.object(module, "RootControlDeployment", ActivationState),
    ):
        try:
            await runtime.launch(principal=principal)
            # A failed launch/getter or lost activation ACK may leave stale
            # status. Shutdown must rely on monotonic owner-side evidence.
            runtime._phase = phase
        finally:
            await runtime.aclose(timeout=1)
    assert budgets == [0.0 if attempted is False else 1.0]
    assert runtime._worker_retired
    assert connection.raw.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "command_error", "worker_rejection"])
async def test_retired_command_and_private_reply_are_observed_atomically(mode):
    config, principal, subject, owner = _fixture()
    runtime = module.RootDeploymentRuntime(config)
    connection = _connection(config)
    fake = _fake_deployment(subject)
    fake.reset()
    original_command = module._Command
    original_finish = CommandReservation.finish
    commands = []
    observers = []
    observed_before_publish = []
    observed_ready = []

    def capture(*args):
        command = original_command(*args)
        commands.append(command)
        return command

    def finish(reservation):
        result = original_finish(reservation)
        if commands and reservation is commands[0].reservation:
            observed = threading.Event()

            def observe():
                owner.snapshot(now=1.0)
                observed_ready.append(commands[0].future.done())
                observed.set()

            observer = threading.Thread(target=observe)
            observers.append(observer)
            observer.start()
            observed_before_publish.append(observed.wait(0.1))
        return result

    rejection = (
        patch.object(
            module.RootDeploymentRuntime,
            "_run_command",
            module.RootDeploymentRuntime._reject_command_on_worker,
        )
        if mode == "worker_rejection"
        else nullcontext()
    )
    with (
        patch.object(
            module,
            "connect",
            return_value=connection,
            side_effect=(
                RuntimeError("controlled connect failure") if mode == "command_error" else None
            ),
        ),
        patch.object(module, "RootControlDeployment", fake),
    ):
        try:
            with (
                patch.object(module, "_Command", side_effect=capture),
                patch.object(CommandReservation, "finish", finish),
                rejection,
            ):
                if mode == "success":
                    await runtime.launch(principal=principal)
                else:
                    with pytest.raises(module.RootDeploymentRuntimeUnavailable):
                        await runtime.launch(principal=principal)
            for observer in observers:
                observer.join(timeout=2)
                assert not observer.is_alive()
        finally:
            await runtime.aclose(timeout=2)
    assert observed_before_publish == [False]
    assert observed_ready == [True]
    assert runtime._worker_retired
