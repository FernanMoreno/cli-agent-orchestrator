"""Offline HTTP and model contract for negotiated admission diagnostics."""

import asyncio
import json
import os
import unittest
from pathlib import Path
from typing import Literal, overload
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cli_agent_orchestrator.api import admission_routes
from cli_agent_orchestrator.models import admission_control
from cli_agent_orchestrator.models.admission_control import (
    AdmissionDiagnostics,
    AdmissionDiagnosticsV2,
)
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    Principal,
    _verified_principal,
    get_current_principal,
)
from cli_agent_orchestrator.services import admission_diagnostics, delivery_admission
from cli_agent_orchestrator.services.admission_monitor import (
    AdmissionMonitor,
    AdmissionMonitorDiagnostic,
)
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    ResourceAmounts,
)
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime

# Captured from the pre-v2 diagnostics projection with no command reservations.
# Keep it in the test so the compatibility contract has no private artifact path.
PRE_V2_V1_NO_COMMAND_JSON = {
    "accepting": True,
    "applied_budget": {"cpu_millicores": 40, "memory_bytes": 40, "storage_slots": 10},
    "capacity": {"cpu_millicores": 40, "memory_bytes": 40, "storage_slots": 10},
    "committed": {"cpu_millicores": 0, "memory_bytes": 0, "storage_slots": 0},
    "debt": {"cpu_millicores": 0, "memory_bytes": 0, "storage_slots": 0},
    "distributed_observed": False,
    "generation": 0,
    "monitor": {
        "authority_changing": False,
        "authority_uncertain": False,
        "generation_at_last_cycle_completion": None,
        "last_cycle_age_s": None,
        "last_cycle_reason": None,
        "sampling": False,
        "stopping": False,
    },
    "operations": {
        "control_connection_loans": 0,
        "control_inflight": 0,
        "control_storage_slots": 0,
        "inflight_deliveries": 0,
        "inflight_operations": 0,
        "inflight_storage_only": 0,
        "reserved_operations": 0,
        "started_deliveries": 0,
        "started_operations": 0,
        "started_storage_only": 0,
        "storage_completion_credits": 0,
        "storage_connection_loans": 0,
        "storage_retirement_pending": 0,
    },
    "policy": {
        "cpu_per_delivery_millicores": 250,
        "cpu_reserve_percent": 0,
        "max_sample_age_s": 3.0,
        "memory_per_delivery_bytes": 67108864,
        "memory_reserve_percent": 0,
    },
    "postgres": None,
    "reason": "resource_exhausted",
    "recovery_ceiling": None,
    "remaining": {"cpu_millicores": 40, "memory_bytes": 40, "storage_slots": 10},
    "resource_probe": None,
    "sample": {
        "age_s": 0.0,
        "coverage": "known",
        "cpu_available_millicores": 40,
        "cpu_limit_millicores": 40,
        "memory_available_bytes": 40,
        "memory_limit_bytes": 40,
        "storage_available_slots": 10,
        "within_max_sample_age": True,
    },
    "sampling_policy": None,
    "scope": "process_local",
    "version": 1,
}


def _owner(*, command_state: str | None = None) -> delivery_admission.DeliveryAdmission:
    owner = delivery_admission.DeliveryAdmission(
        AdmissionPolicy(memory_reserve_percent=0, cpu_reserve_percent=0),
        budget=ResourceAmounts(40, 40, 10),
    )
    observation = CapacityObservation(1.0, 40, 40, 40, 40, 10)
    if not owner.observe(observation, now=1.0, generation=owner.generation):
        raise AssertionError("fixture observation was not accepted")
    if command_state is not None:
        attempt = owner.try_reserve_command(footprint=ResourceAmounts(1, 1, 0), now=1.0)
        if attempt.reservation is None:
            raise AssertionError("fixture command was not admitted")
        if command_state == "started" and not attempt.reservation.start(now=1.0):
            raise AssertionError("fixture command did not start")
    return owner


def _monitor_diagnostic(
    owner: delivery_admission.DeliveryAdmission,
) -> AdmissionMonitorDiagnostic:
    return AdmissionMonitorDiagnostic(
        ledger=owner.diagnostic(now=1.0),
        configuration=None,
        postgres=None,
        resources=None,
        last_cycle=None,
        stopping=False,
        sampling=False,
        authority_changing=False,
        authority_uncertain=False,
    )


class _DiagnosticMonitor(AdmissionMonitor):
    def __init__(self, value: AdmissionMonitorDiagnostic) -> None:
        self.value = value

    def diagnostic(self, *, now: float) -> AdmissionMonitorDiagnostic:
        return self.value


class _ProjectionRuntime(InboxAdmissionRuntime):
    """Route adapter with no monitor threads, repository, database or server."""

    def __init__(self, value: AdmissionMonitorDiagnostic) -> None:
        self.value = value
        self.calls: list[dict[str, object]] = []

    @property
    def available(self) -> bool:
        return True

    @overload
    def diagnostics(self, *, principal: object, version: Literal[2]) -> AdmissionDiagnosticsV2: ...

    @overload
    def diagnostics(
        self, *, principal: object, version: Literal[1] | None = None
    ) -> AdmissionDiagnostics: ...

    @overload
    def diagnostics(
        self, *, principal: object, version: int | None
    ) -> AdmissionDiagnostics | AdmissionDiagnosticsV2: ...

    def diagnostics(
        self, *, principal: object, version: int | None = None
    ) -> AdmissionDiagnostics | AdmissionDiagnosticsV2:
        self.calls.append({} if version is None else {"version": version})
        if version is None:
            return admission_diagnostics.diagnostics_response(self.value, now=1.0)
        return admission_diagnostics.diagnostics_response(self.value, now=1.0, version=version)


def _client(runtime: _ProjectionRuntime) -> TestClient:
    app = FastAPI()
    app.state.inbox_admission_runtime = runtime
    app.include_router(admission_routes.router)
    principal = _verified_principal("test-issuer", "admin-subject", [SCOPE_ADMIN], "jwt")

    async def verified_admin() -> Principal:
        return principal

    app.dependency_overrides[get_current_principal] = verified_admin
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)


class AdmissionCommandDiagnosticsTests(unittest.TestCase):
    def test_all_changed_modules_import_from_this_checkout(self) -> None:
        root = Path(__file__).resolve().parents[2]
        modules = (
            (admission_control, "src/cli_agent_orchestrator/models/admission_control.py"),
            (admission_diagnostics, "src/cli_agent_orchestrator/services/admission_diagnostics.py"),
            (delivery_admission, "src/cli_agent_orchestrator/services/delivery_admission.py"),
            (admission_routes, "src/cli_agent_orchestrator/api/admission_routes.py"),
            (
                __import__(
                    "cli_agent_orchestrator.services.inbox_admission_runtime",
                    fromlist=["InboxAdmissionRuntime"],
                ),
                "src/cli_agent_orchestrator/services/inbox_admission_runtime.py",
            ),
        )
        for module, relative in modules:
            with self.subTest(module=module.__name__):
                module_file = module.__file__
                if module_file is None:
                    self.fail("module has no source origin")
                self.assertEqual(Path(module_file).resolve(), (root / relative).resolve())

    def test_pre_v2_no_command_v1_wire_is_unchanged(self) -> None:
        owner = _owner()
        value = _monitor_diagnostic(owner)
        response = admission_diagnostics.diagnostics_response(value, now=1.0)
        self.assertEqual(response.model_dump(mode="json"), PRE_V2_V1_NO_COMMAND_JSON)

    def test_v1_projection_requires_v2_when_commands_are_active(self) -> None:
        owner = _owner(command_state="started")
        value = _monitor_diagnostic(owner)
        version_error = getattr(admission_diagnostics, "DiagnosticsVersionRequired", None)
        self.assertIsNotNone(version_error)
        if version_error is not None:
            with self.assertRaises(version_error):
                admission_diagnostics.diagnostics_response(value, now=1.0)

    def test_v2_projection_reports_reserved_started_and_stop_retained_commands(self) -> None:
        owner = _owner(command_state="reserved")
        value = _monitor_diagnostic(owner)
        projection = getattr(admission_diagnostics, "diagnostics_response")

        reserved = projection(value, now=1.0, version=2)
        self.assertEqual(reserved.version, 2)
        self.assertEqual(reserved.operations.commands.inflight, 1)
        self.assertEqual(reserved.operations.commands.started, 0)
        self.assertEqual(reserved.operations.inflight_operations, 1)
        self.assertEqual(reserved.operations.inflight_deliveries, 0)
        version_error = getattr(admission_diagnostics, "DiagnosticsVersionRequired", None)
        self.assertIsNotNone(version_error)
        if version_error is not None:
            with self.assertRaises(version_error):
                projection(value, now=1.0)

        command = next(iter(owner._commands.values()))
        self.assertTrue(command.start(now=1.0))
        owner.stop_accepting()
        stopped = projection(_monitor_diagnostic(owner), now=1.0, version=2)
        self.assertFalse(stopped.accepting)
        self.assertEqual(stopped.operations.commands.inflight, 1)
        self.assertEqual(stopped.operations.commands.started, 1)
        if version_error is not None:
            with self.assertRaises(version_error):
                projection(_monitor_diagnostic(owner), now=1.0)
        self.assertTrue(command.finish())
        v1_after_drain = projection(_monitor_diagnostic(owner), now=1.0)
        self.assertFalse(v1_after_drain.accepting)
        self.assertEqual(v1_after_drain.operations.inflight_operations, 0)

    def test_v2_models_validate_conservation_and_reject_extra_fields(self) -> None:
        command_model = getattr(admission_control, "CommandOperationDiagnostics", None)
        operations_model = getattr(admission_control, "AdmissionOperationDiagnosticsV2", None)
        response_model = getattr(admission_control, "AdmissionDiagnosticsV2", None)
        self.assertIsNotNone(command_model)
        self.assertIsNotNone(operations_model)
        self.assertIsNotNone(response_model)
        if command_model is None or operations_model is None or response_model is None:
            return

        commands = command_model(inflight=1, started=1)
        operations = operations_model(
            inflight_operations=3,
            reserved_operations=1,
            started_operations=2,
            inflight_deliveries=1,
            started_deliveries=0,
            inflight_storage_only=1,
            started_storage_only=1,
            commands=commands,
            storage_connection_loans=0,
            storage_completion_credits=0,
            storage_retirement_pending=0,
            control_storage_slots=0,
            control_inflight=0,
            control_connection_loans=0,
        )
        payload = dict(PRE_V2_V1_NO_COMMAND_JSON)
        payload["version"] = 2
        payload["operations"] = operations.model_dump(mode="json")
        response = response_model.model_validate(payload)
        self.assertEqual(response.version, 2)
        self.assertEqual(response.operations.inflight_operations, 3)

        invalid_total = operations.model_dump(mode="python")
        invalid_total["inflight_operations"] = 4
        with self.assertRaises(ValidationError):
            operations_model.model_validate(invalid_total)

        invalid_commands = commands.model_dump(mode="python")
        invalid_commands["started"] = 2
        with self.assertRaises(ValidationError):
            command_model.model_validate(invalid_commands)

        extra = commands.model_dump(mode="python")
        extra["hidden"] = 0
        with self.assertRaises(ValidationError):
            command_model.model_validate(extra)

        strict_version = dict(payload)
        strict_version["version"] = "2"
        with self.assertRaises(ValidationError):
            response_model.model_validate(strict_version)

    def test_typed_version_error_has_fixed_code_and_supported_versions(self) -> None:
        error_model = getattr(admission_control, "AdmissionDiagnosticsVersionRequired", None)
        response_model = getattr(
            admission_control, "AdmissionDiagnosticsVersionRequiredResponse", None
        )
        self.assertIsNotNone(error_model)
        self.assertIsNotNone(response_model)
        if error_model is None or response_model is None:
            return
        detail = {
            "code": "diagnostics_version_required",
            "required_version": 2,
            "supported_versions": [1, 2],
        }
        self.assertEqual(error_model().model_dump(mode="json"), detail)
        response = response_model.model_validate_json(json.dumps({"detail": detail}))
        self.assertEqual(response.detail.model_dump(mode="json"), detail)

    def test_inprocess_http_negotiates_versions_and_coerces_query_strings(self) -> None:
        owner = _owner(command_state="started")
        runtime = _ProjectionRuntime(_monitor_diagnostic(owner))
        with _client(runtime) as client:
            default_v1 = client.get("/admission/diagnostics")
            self.assertEqual(default_v1.status_code, 409)
            self.assertEqual(default_v1.headers["cache-control"], "no-store")
            self.assertEqual(
                default_v1.json()["detail"],
                {
                    "code": "diagnostics_version_required",
                    "required_version": 2,
                    "supported_versions": [1, 2],
                },
            )
            response_model = admission_control.AdmissionDiagnosticsVersionRequiredResponse
            self.assertEqual(
                response_model.model_validate_json(default_v1.content).detail.code,
                "diagnostics_version_required",
            )
            self.assertEqual(runtime.calls, [{}])

            explicit_v1 = client.get("/admission/diagnostics?version=1")
            self.assertEqual(explicit_v1.status_code, 409)
            self.assertEqual(runtime.calls, [{}, {}])

            explicit_v2 = client.get("/admission/diagnostics?version=2")
            self.assertEqual(explicit_v2.status_code, 200)
            body = explicit_v2.json()
            self.assertEqual(body["version"], 2)
            self.assertEqual(body["operations"]["commands"], {"inflight": 1, "started": 1})
            self.assertEqual(body["operations"]["inflight_operations"], 1)
            self.assertEqual(body["operations"]["inflight_deliveries"], 0)
            self.assertEqual(runtime.calls[-1], {"version": 2})

            before_invalid = len(runtime.calls)
            invalid = client.get("/admission/diagnostics?version=3")
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json(), {"detail": {"code": "invalid_diagnostics_query"}})
            self.assertEqual(invalid.headers["cache-control"], "no-store")
            invalid_model = admission_control.AdmissionDiagnosticsQueryErrorResponse
            self.assertEqual(
                invalid_model.model_validate_json(invalid.content).detail.code,
                "invalid_diagnostics_query",
            )
            for unsupported in ("0", "1.5", "bad"):
                response = client.get("/admission/diagnostics?version=" + unsupported)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {"detail": {"code": "invalid_diagnostics_query"}})
            self.assertEqual(len(runtime.calls), before_invalid)

    def test_inprocess_http_v1_wire_fixture_and_default_runtime_call_shape(self) -> None:
        runtime = _ProjectionRuntime(_monitor_diagnostic(_owner()))
        with _client(runtime) as client:
            default = client.get("/admission/diagnostics")
            explicit = client.get("/admission/diagnostics?version=1")
            invalid_envelope = client.post("/admission/resource-envelope", json={})
        self.assertEqual(invalid_envelope.status_code, 422)
        self.assertEqual(invalid_envelope.json(), {"detail": {"code": "invalid_resource_envelope"}})
        self.assertEqual(invalid_envelope.headers["cache-control"], "no-store")
        self.assertEqual(default.status_code, 200)
        self.assertEqual(explicit.status_code, 200)
        self.assertEqual(default.json(), PRE_V2_V1_NO_COMMAND_JSON)
        self.assertEqual(explicit.json(), PRE_V2_V1_NO_COMMAND_JSON)
        self.assertEqual(runtime.calls, [{}, {}])

    def test_runtime_default_preserves_the_existing_projection_factory_signature(self) -> None:
        value = _monitor_diagnostic(_owner())
        calls = []

        def legacy_projection(snapshot, *, now):
            calls.append((snapshot, now))
            return admission_diagnostics.diagnostics_response(snapshot, now=now)

        async def invoke():
            runtime = object.__new__(InboxAdmissionRuntime)
            runtime._pid = os.getpid()
            runtime._loop = asyncio.get_running_loop()
            runtime._started = True
            runtime._closing = False
            runtime._terminal_closing = False
            runtime._retired = False
            runtime._clock = lambda: 1.0
            runtime.monitor = _DiagnosticMonitor(value)
            principal = _verified_principal("test-issuer", "admin-subject", [SCOPE_ADMIN], "jwt")
            with patch(
                "cli_agent_orchestrator.services.inbox_admission_runtime.diagnostics_response",
                legacy_projection,
            ):
                return runtime.diagnostics(principal=principal)

        result = asyncio.run(invoke())
        self.assertEqual(result.version, 1)
        self.assertEqual(calls, [(value, 1.0)])

    def test_runtime_accepts_explicit_v2_and_keeps_default_v1_call_shape(self) -> None:
        owner = _owner()
        value = _monitor_diagnostic(owner)

        async def invoke() -> tuple[
            AdmissionDiagnostics | AdmissionDiagnosticsV2,
            AdmissionDiagnostics | AdmissionDiagnosticsV2,
        ]:
            runtime = object.__new__(InboxAdmissionRuntime)
            runtime._pid = os.getpid()
            runtime._loop = asyncio.get_running_loop()
            runtime._started = True
            runtime._closing = False
            runtime._terminal_closing = False
            runtime._retired = False
            runtime._clock = lambda: 1.0
            runtime.monitor = _DiagnosticMonitor(value)
            principal = _verified_principal("test-issuer", "admin-subject", [SCOPE_ADMIN], "jwt")
            v1 = runtime.diagnostics(principal=principal)
            v2 = runtime.diagnostics(principal=principal, version=2)
            return v1, v2

        v1, v2 = asyncio.run(invoke())
        self.assertEqual(v1.version, 1)
        self.assertEqual(v2.version, 2)


if __name__ == "__main__":
    unittest.main()
