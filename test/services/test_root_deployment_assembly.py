"""Trusted deployment profiles describe configuration without creating authority."""

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from test.services.test_root_deployment_runtime import _fixture
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.models.sql_startup_permission import SQL_ROOT_GENESIS_ENV
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.root_deployment_runtime import RootDeploymentRuntimeUnavailable

pytestmark = pytest.mark.offline_database


def _api():
    return (
        importlib.import_module("cli_agent_orchestrator.models.root_deployment_profile"),
        importlib.import_module("cli_agent_orchestrator.services.root_deployment_assembly"),
    )


def _document(tmp_path):
    footprint = dict(memory_bytes=1024 * 1024, cpu_millicores=10, storage_slots=0)
    return dict(
        version=1,
        signing_key_file=str(tmp_path / "external-signer.json"),
        trust_file=str(tmp_path / "root-trust.json"),
        policy_file=str(tmp_path / "root-policy.json"),
        scope="root/operator-test",
        worker_footprint=footprint.copy(),
        child_footprint=footprint.copy(),
        command_footprint=footprint.copy(),
        startup_footprint=footprint.copy(),
        refresh_footprint=footprint.copy(),
    )


def test_profile_api_is_available():
    assert (
        importlib.util.find_spec("cli_agent_orchestrator.models.root_deployment_profile")
        is not None
    )
    assert (
        importlib.util.find_spec("cli_agent_orchestrator.services.root_deployment_assembly")
        is not None
    )


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"argv": ["untrusted"]},
        {"env": {}},
        {"budget": {}},
        {"private_key_base64": "inline-secret"},
        {"signing_key_file": "relative"},
        {"trust_file": "relative"},
        {"policy_file": "relative"},
        {"scope": " root/test"},
        {"scope": "root/\n"},
        {"timeout_s": True},
        {"timeout_s": 30.1},
        {"close_timeout_s": -1.0},
        {"worker_footprint": {"memory_bytes": 1, "cpu_millicores": 1, "storage_slots": 1}},
        {"command_footprint": {"memory_bytes": True, "cpu_millicores": 1, "storage_slots": 0}},
    ],
)
def test_profile_rejects_ambiguous_or_authority_fields(tmp_path, change):
    models, _ = _api()
    with pytest.raises(ValidationError):
        models.RootDeploymentProfile.model_validate({**_document(tmp_path), **change})


def test_assembler_is_inert_and_snapshots_only_trusted_configuration(tmp_path):
    models, module = _api()
    config, _, _, owner = _fixture()
    profile = models.RootDeploymentProfile.model_validate(_document(tmp_path))
    environment = {"CAO_DEPLOY_TEST": "trusted"}
    before = owner.snapshot(now=1.0)
    with (
        patch("os.open", side_effect=AssertionError("inert assembly read a file")),
        patch(
            "threading.Thread.start", side_effect=AssertionError("inert assembly started a thread")
        ),
        patch(
            "cli_agent_orchestrator.services.root_deployment_runtime.connect",
            side_effect=AssertionError("inert assembly opened SQL"),
        ),
    ):
        runtime = module.assemble_root_deployment(
            profile,
            bootstrap=config.bootstrap,
            owner=owner,
            control=config.control,
            target=config.target,
            env=environment,
            clock=config.clock,
        )
    environment["CAO_DEPLOY_TEST"] = "changed"
    assert runtime.installable
    assert runtime._worker is None
    assert runtime._config.env == {"CAO_DEPLOY_TEST": "trusted", SQL_ROOT_GENESIS_ENV: "1"}
    assert environment == {"CAO_DEPLOY_TEST": "changed"}
    assert runtime._config.argv[:3] == (
        sys.executable,
        "-m",
        "cli_agent_orchestrator.runtime_channel.root_control",
    )
    assert runtime._config.argv[3:7] == (
        "--trust-file",
        profile.trust_file,
        "--policy-file",
        profile.policy_file,
    )
    assert runtime._config.credential_preflight is not None
    assert owner.snapshot(now=1.0) == before
    with pytest.raises(ValidationError):
        profile.scope = "changed"


def test_explicit_profile_file_reader_is_private_bounded_and_redacted(tmp_path):
    _, module = _api()
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_document(tmp_path)))
    path.chmod(0o600)
    assert module.load_root_deployment_profile(path).scope == "root/operator-test"
    path.write_text('{"private_key_base64":"should-not-echo"}')
    with pytest.raises(module.RootDeploymentAssemblyUnavailable) as caught:
        module.load_root_deployment_profile(path)
    assert str(path) not in str(caught.value)
    assert "should-not-echo" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_assembler_cannot_turn_an_unfunded_owner_into_a_launch(tmp_path):
    models, module = _api()
    config, principal, _, _ = _fixture()
    owner, control = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    profile = models.RootDeploymentProfile.model_validate(_document(tmp_path))
    runtime = module.assemble_root_deployment(
        profile,
        bootstrap=config.bootstrap,
        owner=owner,
        control=control,
        target=config.target,
        env={},
        clock=config.clock,
    )
    with (
        patch("os.open", side_effect=AssertionError("denied launch read signer")),
        patch("threading.Thread.start", side_effect=AssertionError("denied launch started worker")),
        patch(
            "cli_agent_orchestrator.services.root_deployment_runtime.connect",
            side_effect=AssertionError("denied launch opened SQL"),
        ),
    ):
        with pytest.raises(RootDeploymentRuntimeUnavailable):
            await runtime.launch(principal=principal)
    assert runtime._worker is None
    assert owner.snapshot(now=1.0).command_started == 0


@pytest.mark.parametrize(
    "name",
    [
        "CAO_INTERNAL_RESOURCE_BINDING_FD",
        "CAO_INTERNAL_ROOT_CONTROL_FD",
        "CAO_INTERNAL_SQL_STARTUP_PERMISSION",
        "CAO_INTERNAL_SQL_BROKER_FD",
        "CAO_INTERNAL_ROOT_SQL_GENESIS",
    ],
)
def test_assembler_refuses_inherited_capabilities_and_conflicting_mode(tmp_path, name):
    models, module = _api()
    config, _, _, owner = _fixture()
    profile = models.RootDeploymentProfile.model_validate(_document(tmp_path))
    with pytest.raises(module.RootDeploymentAssemblyUnavailable):
        module.assemble_root_deployment(
            profile,
            bootstrap=config.bootstrap,
            owner=owner,
            control=config.control,
            target=config.target,
            env={name: "0"},
            clock=config.clock,
        )
