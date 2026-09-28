"""Controlled real-provider matrix for CAO's native child lifecycle.

This module intentionally does not use a fake provider.  Each selected case
launches a real parent and real child, then proves the child lifecycle from its
durable receipt rather than from a terminal viewport:

* a cross-provider ``run-step`` returns output and settles ``succeeded``;
* sibling inbox transport records a delivered message;
* deletion of unfinished siblings records ``cancelled`` plus cleanup; and
* a deliberately short post-send deadline records ``reconcile`` rather than a
  guessed success.

It is expensive and requires logged-in local CLIs, so it is disabled unless
``CAO_RUN_LIVE_PROVIDER_TESTS=1``.  When enabled, a reviewed JSON manifest
names the provider CLIs and models that the runner is allowed to charge; the
harness derives its parent × child cells from that manifest.  This keeps the
test extensible (for example, when Gemini CLI is enabled on a reviewed runner)
without silently discovering and invoking arbitrary local CLIs.  See
``docs/real-provider-e2e.md``.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from test.fixtures.cao_server import (
    CaoServer,
    _fixture_health_timeout,
    _pick_free_port,
    _start_cao_server,
)
from typing import Callable, Final, Iterator, NoReturn

import pytest
import requests

from cli_agent_orchestrator.constants import PROVIDERS

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.live_provider,
    pytest.mark.skipif(
        os.environ.get("CAO_RUN_LIVE_PROVIDER_TESTS") != "1",
        reason="Real provider matrix disabled; set CAO_RUN_LIVE_PROVIDER_TESTS=1.",
    ),
]


_PROVIDERS_ENV: Final = "CAO_REAL_PROVIDER_E2E_PROVIDERS"
# The canonical name describes its limited purpose: it selects test cells in
# this E2E harness.  It is never read by CAO runtime authorization or task
# scheduling.  Keep _PAIRS_ENV as a compatibility alias for existing runners.
_TEST_FILTER_ENV: Final = "CAO_REAL_PROVIDER_E2E_TEST_FILTER"
_PAIRS_ENV: Final = "CAO_REAL_PROVIDER_E2E_PAIRS"
_STRICT_ENV: Final = "CAO_REAL_PROVIDER_E2E_STRICT"
_AUTH_HOME_ENV: Final = "CAO_REAL_PROVIDER_E2E_AUTH_HOME"
_QUOTA_OBSERVATION_SECONDS_ENV: Final = "CAO_REAL_PROVIDER_E2E_QUOTA_OBSERVATION_SECONDS"
_LEGACY_PARENT_ENV: Final = "CAO_REAL_PROVIDER_E2E_PARENT"
_LEGACY_CHILD_ENV: Final = "CAO_REAL_PROVIDER_E2E_CHILD"
_MAX_MANIFEST_BYTES: Final = 64 * 1024
_REQUIRED_CAPABILITIES: Final = frozenset({"native_children"})
_READY_STATES: Final = {"idle", "completed"}
_RECEIPT_WAIT_SECONDS: Final = 30.0
_STEP_TIMEOUT_SECONDS: Final = 180.0
_DECIMAL_SECONDS_PATTERN: Final = re.compile(
    r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
_MATRIX_CELL_SCENARIOS: Final = (
    "successful_provider_turn",
    "sibling_message_delivery",
    "sibling_cancellation_cleanup",
    "timeout_reconciliation",
    "quota_wait_reconciliation",
    "quota_continuation",
)
_MATRIX_EVIDENCE_SCHEMA_VERSION: Final = 1
_MATRIX_EVIDENCE_STATUSES: Final = frozenset({"validated", "skipped", "failed", "not_observed"})
_MATRIX_EVIDENCE_REASONS: Final = frozenset(
    {
        "provider_quota",
        "auto_resume_unsupported",
        "delivery_not_confirmed",
        "observation_limit",
        "verification_unavailable",
        "observation_window_unconfigured",
        "not_applicable",
        "continuation_not_observed",
        "assertion_failed",
        "cleanup_failed",
        "setup_not_started",
    }
)


@dataclass(frozen=True)
class MatrixProvider:
    """One explicitly authorised real-provider endpoint for the matrix.

    Authentication paths are deliberately supplied by the operator rather
    than inferred from a provider name.  A new provider can therefore join
    this matrix as soon as CAO supports it, without adding a second hard-coded
    allowlist to this test module.
    """

    name: str
    model: str
    binary: str
    auth_env: tuple[str, ...]
    auth_files: tuple[Path, ...]
    capabilities: frozenset[str]
    exclude_reason: str | None = None


@dataclass(frozen=True)
class MatrixCell:
    """A parent → child real-provider cell plus a non-invasive skip reason."""

    parent: MatrixProvider
    child: MatrixProvider
    skip_reason: str | None = None

    @property
    def id(self) -> str:
        return f"{self.parent.name}->{self.child.name}"


def _configured_auth_home() -> Path:
    """Return the operator home which owns the reusable CLI logins."""
    configured_home = os.environ.get(_AUTH_HOME_ENV, "").strip()
    return Path(configured_home).expanduser() if configured_home else Path.home()


def _manifest_failure(message: str) -> NoReturn:
    pytest.fail(f"Invalid {_PROVIDERS_ENV} manifest: {message}")


def _nonempty_string(value: object, *, field: str, provider: str) -> str:
    if not isinstance(value, str):
        _manifest_failure(f"provider {provider!r} field {field!r} must be a string")
    if "\x00" in value or "\r" in value or "\n" in value:
        _manifest_failure(f"provider {provider!r} field {field!r} must be one line without NUL")
    result = value.strip()
    if not result:
        _manifest_failure(f"provider {provider!r} field {field!r} must not be empty")
    return result


def _string_list(value: object, *, field: str, provider: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _manifest_failure(f"provider {provider!r} field {field!r} must be a JSON array")
    values = tuple(_nonempty_string(item, field=field, provider=provider) for item in value)
    if len(values) != len(set(values)):
        _manifest_failure(f"provider {provider!r} field {field!r} must not contain duplicates")
    return values


def _auth_files(value: object, *, provider: str) -> tuple[Path, ...]:
    paths = _string_list(value, field="auth_files", provider=provider)
    result: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts or "\\" in raw_path:
            _manifest_failure(
                f"provider {provider!r} auth_files entries must be safe relative POSIX paths"
            )
        result.append(path)
    return tuple(result)


def _load_matrix_providers() -> dict[str, MatrixProvider]:
    """Load the reviewed, explicit provider manifest for a real matrix run.

    Presence in this manifest is the enablement decision.  We intentionally do
    not enumerate every provider CAO happens to support or every binary on
    PATH: doing either could turn a normal authenticated workstation into an
    unreviewed, billable test run.
    """

    raw_manifest = os.environ.get(_PROVIDERS_ENV, "")
    if len(raw_manifest.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        _manifest_failure(f"must not exceed {_MAX_MANIFEST_BYTES} UTF-8 bytes")
    raw_manifest = raw_manifest.strip()
    if not raw_manifest:
        _manifest_failure(
            "set it to a JSON object mapping CAO provider ids to reviewed endpoint settings"
        )
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError as exc:
        _manifest_failure(f"must contain valid JSON ({exc.msg})")
    if not isinstance(manifest, dict) or not manifest:
        _manifest_failure("must be a non-empty JSON object")

    providers: dict[str, MatrixProvider] = {}
    allowed_fields = {
        "model",
        "binary",
        "auth_env",
        "auth_files",
        "capabilities",
        "exclude_reason",
    }
    for name, raw_config in manifest.items():
        if not isinstance(name, str) or not name:
            _manifest_failure("provider ids must be non-empty strings")
        if not isinstance(raw_config, dict):
            _manifest_failure(f"provider {name!r} must map to a JSON object")
        unexpected = sorted(set(raw_config) - allowed_fields)
        if unexpected:
            _manifest_failure(
                f"provider {name!r} contains unsupported fields: {', '.join(unexpected)}"
            )

        raw_exclude_reason = raw_config.get("exclude_reason")
        exclude_reason: str | None = None
        if raw_exclude_reason is not None:
            exclude_reason = _nonempty_string(
                raw_exclude_reason, field="exclude_reason", provider=name
            )

        if name not in PROVIDERS and exclude_reason is None:
            _manifest_failure(
                f"provider {name!r} has no adapter in this CAO checkout; add the adapter first "
                "or retain it with exclude_reason so its skipped cells stay visible"
            )

        # An excluded provider is intentionally represented in the resulting
        # Cartesian product so pytest reports every skipped pair and its reason.
        # It does not need a working binary, model, or credential configuration.
        if exclude_reason is not None:
            providers[name] = MatrixProvider(
                name=name,
                model="",
                binary="",
                auth_env=(),
                auth_files=(),
                capabilities=frozenset(),
                exclude_reason=exclude_reason,
            )
            continue

        # A manifest can name every provider that ought to participate on a
        # runner even while one is not installed, authenticated, or assigned a
        # reviewed model yet.  Missing endpoint settings are availability
        # diagnoses, so affected cells are reported as skips instead of
        # suppressing the rest of the N×N matrix.  A supplied value, however,
        # must have a safe, well-defined shape.
        model = (
            _nonempty_string(raw_config["model"], field="model", provider=name)
            if "model" in raw_config
            else ""
        )
        binary = (
            _nonempty_string(raw_config["binary"], field="binary", provider=name)
            if "binary" in raw_config
            else ""
        )
        auth_env = (
            _string_list(raw_config["auth_env"], field="auth_env", provider=name)
            if "auth_env" in raw_config
            else ()
        )
        auth_files = (
            _auth_files(raw_config["auth_files"], provider=name)
            if "auth_files" in raw_config
            else ()
        )
        capabilities = (
            frozenset(_string_list(raw_config["capabilities"], field="capabilities", provider=name))
            if "capabilities" in raw_config
            else frozenset()
        )
        providers[name] = MatrixProvider(
            name=name,
            model=model,
            binary=binary,
            auth_env=auth_env,
            auth_files=auth_files,
            capabilities=capabilities,
        )
    return providers


def _pair_selection(providers: dict[str, MatrixProvider]) -> list[tuple[str, str]]:
    """Return test cells, or the full Cartesian product for ``all``.

    This function is deliberately kept under its historical private name for
    test compatibility.  The selector is an E2E *test filter*, not a runtime
    provider-pair policy: a CAO job still admits providers exclusively through
    its own authorization allowlist.
    """

    raw_filter = os.environ.get(_TEST_FILTER_ENV, "").strip()
    raw_legacy_pairs = os.environ.get(_PAIRS_ENV, "").strip()
    if raw_filter and raw_legacy_pairs and raw_filter != raw_legacy_pairs:
        pytest.fail(
            f"{_TEST_FILTER_ENV} and deprecated {_PAIRS_ENV} disagree; set only "
            f"{_TEST_FILTER_ENV}"
        )
    raw_selection = raw_filter or raw_legacy_pairs
    selection_env = _TEST_FILTER_ENV if raw_filter else _PAIRS_ENV
    if not raw_selection:
        # Preserve a cheap local repro for the former one-cell interface while
        # requiring the new reviewed manifest for binary/model/auth data.  We
        # deliberately do not reconstruct provider settings from the old
        # environment variables: that would reintroduce a fixed provider
        # allowlist and could launch an unreviewed model.
        legacy_parent = os.environ.get(_LEGACY_PARENT_ENV, "").strip()
        legacy_child = os.environ.get(_LEGACY_CHILD_ENV, "").strip()
        if legacy_parent and legacy_child:
            raw_selection = f"{legacy_parent}->{legacy_child}"
        elif legacy_parent or legacy_child:
            pytest.fail(
                f"{_LEGACY_PARENT_ENV} and {_LEGACY_CHILD_ENV} must be set together, or use "
                f"{_TEST_FILTER_ENV}"
            )
        else:
            pytest.fail(
                f"{_TEST_FILTER_ENV} is required with live provider tests; set it to 'all' or "
                "a comma-separated list such as 'codex->gemini'"
            )
    if raw_selection == "all":
        return [(parent, child) for parent in providers for child in providers]

    pairs: list[tuple[str, str]] = []
    for raw_pair in raw_selection.split(","):
        parent, separator, child = raw_pair.partition("->")
        parent = parent.strip()
        child = child.strip()
        if separator != "->" or not parent or not child:
            pytest.fail(f"{selection_env} entries must use parent->child syntax; got {raw_pair!r}")
        if parent not in providers or child not in providers:
            pytest.fail(
                f"{selection_env} selects {parent!r}->{child!r}, but both must appear in "
                f"{_PROVIDERS_ENV}"
            )
        pair = (parent, child)
        if pair in pairs:
            pytest.fail(f"{selection_env} contains duplicate cell {parent!r}->{child!r}")
        pairs.append(pair)
    return pairs


def _provider_unavailability_reasons(provider: MatrixProvider) -> tuple[str, ...]:
    """Return every preflight reason a selected provider must not be launched."""

    if provider.exclude_reason is not None:
        return (f"adapter exclusion: {provider.exclude_reason}",)

    reasons: list[str] = []
    if not provider.model:
        reasons.append("no reviewed model configured")
    missing_capabilities = sorted(_REQUIRED_CAPABILITIES - provider.capabilities)
    if missing_capabilities:
        reasons.append("missing required capabilities: " + ", ".join(missing_capabilities))
    if not provider.binary:
        reasons.append("no reviewed CLI binary configured")
    elif shutil.which(provider.binary) is None:
        reasons.append(f"required binary {provider.binary!r} is not on PATH")
    if not provider.auth_env and not provider.auth_files:
        reasons.append("no authentication probe configured")
        return tuple(reasons)
    if any(os.environ.get(variable) for variable in provider.auth_env):
        return tuple(reasons)

    auth_home = _configured_auth_home()
    if any((auth_home / relative_path).is_file() for relative_path in provider.auth_files):
        return tuple(reasons)

    variables = " or ".join(provider.auth_env)
    paths = ", ".join(str(auth_home / path) for path in provider.auth_files)
    if variables and paths:
        reasons.append(f"no reusable auth (set {variables} or add a login record at {paths})")
    elif variables:
        reasons.append(f"no reusable auth (set {variables})")
    else:
        reasons.append(f"no reusable auth (add a login record at {paths})")
    return tuple(reasons)


def _provider_unavailable_reason(provider: MatrixProvider) -> str | None:
    """Return a compact backwards-compatible unavailable-provider diagnosis."""

    reasons = _provider_unavailability_reasons(provider)
    return "; ".join(reasons) or None


def _strict_mode_enabled() -> bool:
    """Return whether this run must fail closed on unavailable enabled providers."""

    raw_value = os.environ.get(_STRICT_ENV, "").strip()
    if raw_value in ("", "0"):
        return False
    if raw_value == "1":
        return True
    pytest.fail(f"{_STRICT_ENV} must be '1' to enable strict mode or unset/'0' to disable it")


def _strict_preflight(providers: dict[str, MatrixProvider], pairs: list[tuple[str, str]]) -> None:
    """Fail before fixtures when a selected enabled provider is not runnable.

    An ``exclude_reason`` is an explicit operator decision rather than a
    readiness defect.  It remains a visible skipped cell in strict mode.  All
    other availability failures are aggregated so a CI operator can fix the
    complete runner configuration without paying for a partial live matrix.
    """

    if not _strict_mode_enabled():
        return

    selected_names = dict.fromkeys(name for pair in pairs for name in pair)
    preflight_reasons = {
        name: _provider_unavailability_reasons(providers[name]) for name in selected_names
    }
    unavailable: list[str] = []
    for name in selected_names:
        provider = providers[name]
        if provider.exclude_reason is not None:
            continue
        reasons = preflight_reasons[name]
        if reasons:
            unavailable.append(f"{name!r}: " + "; ".join(reasons))
    if unavailable:
        pytest.fail(
            f"{_STRICT_ENV}=1 refuses to start real providers with incomplete preflight:\n- "
            + "\n- ".join(unavailable)
        )

    if any(
        not preflight_reasons[parent_name] and not preflight_reasons[child_name]
        for parent_name, child_name in pairs
    ):
        return

    blocked_cells: list[str] = []
    for parent_name, child_name in pairs:
        reasons: list[str] = []
        for role, name in (("parent", parent_name), ("child", child_name)):
            for reason in preflight_reasons[name]:
                reasons.append(f"{role} {name!r}: {reason}")
        blocked_cells.append(f"{parent_name}->{child_name}: " + "; ".join(reasons))
    pytest.fail(
        f"{_STRICT_ENV}=1 requires at least one selected fully executable parent->child "
        "cell; all selected cells are intentionally excluded:\n- " + "\n- ".join(blocked_cells)
    )


def _matrix_cells() -> list[pytest.ParameterSet]:
    """Materialize the selected cells without touching unauthorised CLIs.

    The disabled path deliberately avoids parsing an ambient manifest.  This
    lets normal unit-test runs coexist with a runner environment that has
    provider credentials or a matrix configuration exported globally.
    """

    if os.environ.get("CAO_RUN_LIVE_PROVIDER_TESTS") != "1":
        return [
            pytest.param(
                None,
                id="live-provider-tests-disabled",
                marks=pytest.mark.skip(reason="Real provider matrix disabled."),
            )
        ]

    providers = _load_matrix_providers()
    pairs = _pair_selection(providers)
    _strict_preflight(providers, pairs)
    result: list[pytest.ParameterSet] = []
    for parent_name, child_name in pairs:
        parent = providers[parent_name]
        child = providers[child_name]
        reasons: list[str] = []
        for role, provider in (("parent", parent), ("child", child)):
            for reason in _provider_unavailability_reasons(provider):
                description = f"{role} {provider.name!r}: {reason}"
                if description not in reasons:
                    reasons.append(description)
        cell = MatrixCell(parent=parent, child=child, skip_reason="; ".join(reasons) or None)
        marks = pytest.mark.skip(reason=cell.skip_reason) if cell.skip_reason else ()
        result.append(pytest.param(cell, id=cell.id, marks=marks))
    return result


@pytest.fixture
def live_provider_cao_server(tmp_path: Path) -> Iterator[CaoServer]:
    """Start one fresh, isolated CAO server for the selected matrix cell.

    Provider login files are linked after startup by ``_link_auth_material``.
    In particular, do not convert the access token inside a renewable OAuth
    receipt into ``CLAUDE_CODE_OAUTH_TOKEN``: those are distinct auth inputs.
    """
    server = _start_cao_server(
        tmp_path / "live_provider_cao_home",
        _pick_free_port(),
        deadline=_fixture_health_timeout(),
    )
    try:
        yield server
    finally:
        server.stop()


def _link_auth_material(cao_server: CaoServer, provider: MatrixProvider) -> None:
    """Expose only the selected provider's auth file to CAO's isolated HOME.

    The managed server deliberately redirects HOME to keep its database,
    profiles and logs isolated.  Copying a complete provider directory would
    both leak unrelated state into artifacts and let a test mutate it.  A
    Symlink every documented auth record for the selected provider, while
    keeping all unrelated provider state out of the test HOME.  Some CLIs
    split renewable credentials and account/session metadata across more than
    one file; linking only the first one can make a logged-in CLI reopen an
    interactive browser flow.  API-key based setups need no filesystem link.
    """

    if any(os.environ.get(key) for key in provider.auth_env):
        return

    auth_home = _configured_auth_home()
    linked_any = False
    for relative_path in provider.auth_files:
        source = auth_home / relative_path
        if not source.is_file():
            continue
        target = cao_server.home_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if target.resolve() != source.resolve():
                pytest.fail(f"Refusing to replace existing isolated auth path {target}")
            linked_any = True
            continue
        target.symlink_to(source)
        linked_any = True

    if linked_any:
        return

    searched = ", ".join(str(auth_home / path) for path in provider.auth_files)
    variables = " or ".join(provider.auth_env)
    pytest.fail(
        f"Selected provider {provider.name!r} has no reusable auth. Set {variables} "
        f"or place its login record under {_AUTH_HOME_ENV} (searched: {searched})."
    )


def _write_profile(cao_server: CaoServer, name: str, provider: str, model: str) -> None:
    store = cao_server.home_dir / ".aws" / "cli-agent-orchestrator" / "agent-store"
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Dedicated real-provider matrix profile for {provider}\n"
        f"provider: {provider}\n"
        f"model: {json.dumps(model)}\n"
        "role: developer\n"
        "---\n\n"
        "Follow the user's request exactly. Do not edit files, invoke tools, "
        "delegate work, or start background work unless the request explicitly requires it.\n",
        encoding="utf-8",
    )


def _request(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", _STEP_TIMEOUT_SECONDS + 60.0)
    response = requests.request(method, url, **kwargs)
    return response


def _wait_for_terminal_ready(base_url: str, terminal_id: str) -> None:
    deadline = time.monotonic() + _STEP_TIMEOUT_SECONDS
    last_status = "unknown"
    while time.monotonic() < deadline:
        response = _request("GET", f"{base_url}/terminals/{terminal_id}")
        if response.status_code == 200:
            last_status = response.json().get("status", "unknown")
            if last_status in _READY_STATES:
                return
            if last_status == "error":
                break
        time.sleep(1.0)
    pytest.fail(f"Terminal {terminal_id} did not become ready; last status={last_status!r}")


def _create_parent(cao_server: CaoServer, provider: str, profile: str) -> tuple[str, str]:
    session_name = f"real-e2e-{provider[:5]}-{uuid.uuid4().hex[:10]}"
    response = _request(
        "POST",
        f"{cao_server.url}/sessions",
        params={
            "provider": provider,
            "agent_profile": profile,
            "session_name": session_name,
        },
    )
    assert response.status_code in (
        200,
        201,
    ), f"parent session creation returned HTTP {response.status_code}"
    data = response.json()
    terminal_id = data["id"]
    _wait_for_terminal_ready(cao_server.url, terminal_id)
    return terminal_id, data["session_name"]


def _create_native_child(
    cao_server: CaoServer,
    *,
    session_name: str,
    parent_id: str,
    provider: str,
    profile: str,
) -> str:
    response = _request(
        "POST",
        f"{cao_server.url}/sessions/{session_name}/terminals",
        params={
            "provider": provider,
            "agent_profile": profile,
            "caller_id": parent_id,
        },
    )
    assert (
        response.status_code == 201
    ), f"native child creation returned HTTP {response.status_code}"
    terminal_id = response.json()["id"]
    _wait_for_terminal_ready(cao_server.url, terminal_id)
    return terminal_id


def _children(cao_server: CaoServer, parent_id: str) -> list[dict]:
    response = _request("GET", f"{cao_server.url}/terminals/{parent_id}/children")
    assert response.status_code == 200, f"native child listing returned HTTP {response.status_code}"
    return response.json()


def _receipt_for_terminal(cao_server: CaoServer, parent_id: str, terminal_id: str) -> dict:
    deadline = time.monotonic() + _RECEIPT_WAIT_SECONDS
    while time.monotonic() < deadline:
        for receipt in _children(cao_server, parent_id):
            if receipt["terminal_id"] == terminal_id:
                return receipt
        time.sleep(0.25)
    pytest.fail(f"Native receipt for terminal {terminal_id} was never persisted")


def _delete_terminal(cao_server: CaoServer, terminal_id: str) -> None:
    response = _request("DELETE", f"{cao_server.url}/terminals/{terminal_id}")
    assert response.status_code == 200, f"terminal deletion returned HTTP {response.status_code}"


def _delete_session(cao_server: CaoServer, session_name: str) -> None:
    response = _request("DELETE", f"{cao_server.url}/sessions/{session_name}")
    assert response.status_code in (
        200,
        404,
    ), f"session deletion returned HTTP {response.status_code}"


def _new_matrix_evidence(
    *,
    cell_id: str,
    parent_provider: str,
    parent_model: str,
    child_provider: str,
    child_model: str,
) -> dict[str, object]:
    """Create the allowlisted evidence envelope before cell setup begins."""

    return {
        "schema_version": _MATRIX_EVIDENCE_SCHEMA_VERSION,
        "cell": cell_id,
        "parent_provider": parent_provider,
        "parent_model": parent_model,
        "child_provider": child_provider,
        "child_model": child_model,
        "status": "not_observed",
        "scenarios": {name: {"status": "not_observed"} for name in _MATRIX_CELL_SCENARIOS},
        "cleanup": {
            "session": {"status": "not_observed"},
            "quota_terminal": {"status": "skipped", "reason": "not_applicable"},
        },
        "quota_observation": {
            "limit_seconds": None,
            "started_at_utc": None,
            "elapsed_seconds": None,
        },
        "_active_scenario": None,
        "_requested_status": None,
        "_requested_reason": None,
    }


def _quota_observation_seconds_from_env() -> float | None:
    """Return only an explicitly configured, finite positive decimal window."""

    raw_value = os.environ.get(_QUOTA_OBSERVATION_SECONDS_ENV)
    if raw_value is None or _DECIMAL_SECONDS_PATTERN.fullmatch(raw_value) is None:
        return None
    try:
        seconds = float(raw_value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds


def _activate_matrix_scenario(evidence: dict[str, object], scenario: str) -> None:
    if scenario not in _MATRIX_CELL_SCENARIOS:
        raise AssertionError("unknown matrix evidence scenario")
    evidence["_active_scenario"] = scenario


def _set_matrix_scenario(
    evidence: dict[str, object],
    scenario: str,
    status: str,
    reason: str | None = None,
) -> None:
    if scenario not in _MATRIX_CELL_SCENARIOS or status not in _MATRIX_EVIDENCE_STATUSES:
        raise AssertionError("invalid matrix evidence scenario state")
    if reason is not None and reason not in _MATRIX_EVIDENCE_REASONS:
        raise AssertionError("invalid matrix evidence reason code")
    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, dict)
    value: dict[str, str] = {"status": status}
    if reason is not None:
        value["reason"] = reason
    scenarios[scenario] = value
    if evidence.get("_active_scenario") == scenario:
        evidence["_active_scenario"] = None


def _finish_matrix_evidence(
    evidence: dict[str, object],
    *,
    status: str | None = None,
    reason: str | None = None,
) -> None:
    if status is not None and status not in _MATRIX_EVIDENCE_STATUSES:
        raise AssertionError("invalid matrix evidence status")
    if reason is not None and reason not in _MATRIX_EVIDENCE_REASONS:
        raise AssertionError("invalid matrix evidence reason code")

    scenarios = evidence["scenarios"]
    assert isinstance(scenarios, dict)
    states = [value["status"] for value in scenarios.values()]
    cleanup = evidence["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup_states = [
        cleanup_result["status"]
        for cleanup_result in cleanup.values()
        if isinstance(cleanup_result, dict)
    ]
    if status is None:
        if "failed" in states or "failed" in cleanup_states:
            status = "failed"
        elif "not_observed" in states:
            status = "skipped"
        else:
            status = "validated"
    evidence["status"] = status
    if reason is not None:
        evidence["reason"] = reason


def _record_matrix_evidence(
    record_property: Callable[[str, str], None],
    evidence: dict[str, object],
) -> str:
    """Serialize only the fixed allowlisted result and attach one JUnit property."""

    public_evidence = {key: value for key, value in evidence.items() if not key.startswith("_")}
    serialized = json.dumps(public_evidence, sort_keys=True, separators=(",", ":"))
    record_property("matrix_evidence", serialized)
    return serialized


def _wait_for_message_delivery(cao_server: CaoServer, receiver_id: str, message_id: str) -> dict:
    deadline = time.monotonic() + _RECEIPT_WAIT_SECONDS
    while time.monotonic() < deadline:
        response = _request(
            "GET",
            f"{cao_server.url}/terminals/{receiver_id}/inbox/messages",
            params={"limit": 100},
        )
        assert (
            response.status_code == 200
        ), f"inbox message listing returned HTTP {response.status_code}"
        for message in response.json():
            if message["id"] == message_id:
                if message["status"] == "delivered":
                    return message
                if message["status"] == "failed":
                    pytest.fail(f"Sibling message {message_id} failed delivery")
        time.sleep(0.25)
    pytest.fail(f"Sibling message {message_id} was never delivered")


def _run_cross_provider_step(
    cao_server: CaoServer,
    *,
    cell_id: str,
    parent_id: str,
    session_name: str,
    provider: str,
    profile: str,
    record_property: Callable[[str, str], None],
    evidence: dict[str, object] | None = None,
) -> dict:
    marker = f"CAO_REAL_PROVIDER_MATRIX_{uuid.uuid4().hex}"
    owns_evidence = evidence is None
    if evidence is None:
        evidence = _new_matrix_evidence(
            cell_id=cell_id,
            parent_provider=provider,
            parent_model="",
            child_provider=provider,
            child_model="",
        )
    _activate_matrix_scenario(evidence, "successful_provider_turn")

    def record_if_owned(status: str, reason: str | None = None) -> None:
        if owns_evidence:
            _finish_matrix_evidence(evidence, status=status, reason=reason)
            _record_matrix_evidence(record_property, evidence)

    try:
        response = _request(
            "POST",
            f"{cao_server.url}/terminals/run-step",
            json={
                "provider": provider,
                "agent": profile,
                "prompt": (
                    f"Return this exact marker on its own line: {marker}. Then follow the "
                    "CAO completion-receipt instruction supplied with this task. Do not call "
                    "tools, edit files, or delegate. Apart from the marker and the required "
                    "receipt, add no other text."
                ),
                "session_name": session_name,
                "caller_id": parent_id,
                "teardown": True,
                "timeout": _STEP_TIMEOUT_SECONDS,
                "prompt_redelivery": False,
            },
        )

        if response.status_code == 409:
            detail = response.json().get("detail", {})
            if isinstance(detail, dict) and detail.get("kind") == "quota_wait":
                _activate_matrix_scenario(evidence, "quota_wait_reconciliation")
                terminal_id = detail.get("terminal_id")
                native_child_id = detail.get("native_child_id")
                delivery_may_have_occurred = detail.get("delivery_may_have_occurred")
                provider_may_resume = detail.get("provider_may_resume")
                assert (
                    isinstance(terminal_id, str) and terminal_id
                ), "quota response has no terminal id"
                assert (
                    isinstance(native_child_id, str) and native_child_id
                ), "quota response has no native child receipt id"
                assert detail.get("action") == "wait_for_quota", "quota action is inconsistent"
                assert (
                    type(delivery_may_have_occurred) is bool
                ), "quota delivery capability is not a boolean"
                assert detail.get("retryable") is False, "quota response must not be retryable"
                assert (
                    type(provider_may_resume) is bool
                ), "provider resume capability is not a boolean"

                terminal_response = _request("GET", f"{cao_server.url}/terminals/{terminal_id}")
                assert (
                    terminal_response.status_code == 200
                ), f"quota terminal lookup returned HTTP {terminal_response.status_code}"
                terminal_data = terminal_response.json()
                assert terminal_data.get("id") == terminal_id, "quota terminal identity changed"
                assert (
                    terminal_data.get("status") == "waiting_quota"
                ), "quota terminal did not start in waiting_quota"
                receipt = _read_native_child(cao_server, native_child_id)
                _assert_quota_receipt_identity(
                    receipt,
                    native_child_id=native_child_id,
                    terminal_id=terminal_id,
                    parent_id=parent_id,
                )
                assert (
                    receipt.get("state") == "reconcile"
                ), "original quota receipt is not in reconcile"
                assert (
                    receipt.get("error_kind") == "quota_wait"
                ), "original receipt does not record quota_wait"
                _set_matrix_scenario(evidence, "quota_wait_reconciliation", "validated")
                cleanup = evidence["cleanup"]
                assert isinstance(cleanup, dict)
                cleanup["quota_terminal"] = {"status": "not_observed"}

                if not delivery_may_have_occurred:
                    _set_matrix_scenario(
                        evidence,
                        "successful_provider_turn",
                        "not_observed",
                        "provider_quota",
                    )
                    _set_matrix_scenario(
                        evidence,
                        "quota_continuation",
                        "not_observed",
                        "delivery_not_confirmed",
                    )
                    evidence["_requested_status"] = "skipped"
                    evidence["_requested_reason"] = "provider_quota"
                    cleanup["quota_terminal"] = {
                        "status": "skipped",
                        "reason": "delivery_not_confirmed",
                    }
                    record_if_owned("skipped", "provider_quota")
                    pytest.skip("provider quota; delivery was not confirmed")
                if not provider_may_resume:
                    _set_matrix_scenario(
                        evidence,
                        "successful_provider_turn",
                        "not_observed",
                        "provider_quota",
                    )
                    _set_matrix_scenario(
                        evidence,
                        "quota_continuation",
                        "not_observed",
                        "auto_resume_unsupported",
                    )
                    evidence["_requested_status"] = "skipped"
                    evidence["_requested_reason"] = "provider_quota"
                    cleanup["quota_terminal"] = {
                        "status": "skipped",
                        "reason": "auto_resume_unsupported",
                    }
                    record_if_owned("skipped", "provider_quota")
                    pytest.skip("provider quota; automatic resume is unsupported")

                observation_seconds = _quota_observation_seconds_from_env()
                if observation_seconds is None:
                    _set_matrix_scenario(
                        evidence,
                        "successful_provider_turn",
                        "not_observed",
                        "provider_quota",
                    )
                    _set_matrix_scenario(
                        evidence,
                        "quota_continuation",
                        "not_observed",
                        "observation_window_unconfigured",
                    )
                    evidence["_requested_status"] = "skipped"
                    evidence["_requested_reason"] = "provider_quota"
                    cleanup["quota_terminal"] = {
                        "status": "skipped",
                        "reason": "continuation_not_observed",
                    }
                    record_if_owned("skipped", "provider_quota")
                    pytest.skip("provider quota; finite observation window is not configured")
                quota_observation = evidence["quota_observation"]
                assert isinstance(quota_observation, dict)
                quota_observation["limit_seconds"] = observation_seconds

                _activate_matrix_scenario(evidence, "quota_continuation")
                receipt, observation_reason = _observe_quota_continuation(
                    cao_server,
                    evidence=evidence,
                    native_child_id=native_child_id,
                    terminal_id=terminal_id,
                    parent_id=parent_id,
                    marker=marker,
                    timeout_seconds=observation_seconds,
                )
                if receipt is None:
                    _set_matrix_scenario(
                        evidence,
                        "successful_provider_turn",
                        "not_observed",
                        "provider_quota",
                    )
                    _set_matrix_scenario(
                        evidence,
                        "quota_continuation",
                        "not_observed",
                        observation_reason,
                    )
                    evidence["_requested_status"] = "skipped"
                    evidence["_requested_reason"] = "provider_quota"
                    cleanup["quota_terminal"] = {
                        "status": "skipped",
                        "reason": "continuation_not_observed",
                    }
                    record_if_owned("skipped", "provider_quota")
                    pytest.skip("provider quota; continuation was not observed before the limit")
                _set_matrix_scenario(evidence, "quota_continuation", "validated")

                try:
                    _delete_terminal(cao_server, terminal_id)
                    cleaned_receipt = _read_native_child(cao_server, native_child_id)
                    _assert_quota_receipt_identity(
                        cleaned_receipt,
                        native_child_id=native_child_id,
                        terminal_id=terminal_id,
                        parent_id=parent_id,
                    )
                    assert (
                        cleaned_receipt.get("state") == "succeeded"
                    ), "quota terminal cleanup changed the succeeded receipt"
                    assert (
                        cleaned_receipt.get("settled_at") is not None
                    ), "cleaned quota receipt lost settlement time"
                    assert (
                        cleaned_receipt.get("cleanup_completed_at") is not None
                    ), "quota terminal cleanup was not recorded"
                    cleanup["quota_terminal"] = {"status": "validated"}
                    receipt = cleaned_receipt
                except BaseException:
                    cleanup["quota_terminal"] = {
                        "status": "failed",
                        "reason": "cleanup_failed",
                    }
                    raise
                _set_matrix_scenario(evidence, "successful_provider_turn", "validated")
                record_if_owned("validated")
                return receipt

        assert response.status_code == 200, f"run-step returned HTTP {response.status_code}"
        data = response.json()
        assert marker in data.get(
            "last_message", ""
        ), "successful run-step response omitted its unique turn marker"
        receipt = _receipt_for_terminal(cao_server, parent_id, data["terminal_id"])
        assert receipt.get("provider") == provider, "successful receipt has the wrong provider"
        assert receipt.get("state") == "succeeded", "successful receipt did not settle succeeded"
        assert receipt.get("settled_at") is not None, "successful receipt has no settlement time"
        assert (
            receipt.get("cleanup_completed_at") is not None
        ), "successful receipt has no cleanup time"

        joined = _request("POST", f"{cao_server.url}/native-children/{receipt['id']}/join")
        assert joined.status_code == 200, f"native child join returned HTTP {joined.status_code}"
        joined_data = joined.json()
        assert joined_data.get("settled") is True, "successful receipt did not settle during join"
        assert (
            joined_data.get("child", {}).get("state") == "succeeded"
        ), "joined child did not preserve the succeeded receipt"
        _set_matrix_scenario(evidence, "successful_provider_turn", "validated")
        record_if_owned("validated")
        return receipt
    except pytest.skip.Exception:
        if owns_evidence and evidence.get("_requested_status") is None:
            record_if_owned("skipped", "provider_quota")
        raise
    except BaseException:
        active_scenario = evidence.get("_active_scenario")
        if isinstance(active_scenario, str):
            _set_matrix_scenario(evidence, active_scenario, "failed", "assertion_failed")
        cleanup = evidence["cleanup"]
        assert isinstance(cleanup, dict)
        quota_cleanup = cleanup["quota_terminal"]
        assert isinstance(quota_cleanup, dict)
        reason = "cleanup_failed" if quota_cleanup.get("status") == "failed" else "assertion_failed"
        evidence["_requested_status"] = "failed"
        evidence["_requested_reason"] = reason
        record_if_owned("failed", reason)
        raise


def _read_native_child(cao_server: CaoServer, native_child_id: str) -> dict:
    response = _request("GET", f"{cao_server.url}/native-children/{native_child_id}")
    assert (
        response.status_code == 200
    ), f"native child receipt lookup returned HTTP {response.status_code}"
    return response.json()


def _assert_quota_receipt_identity(
    receipt: dict,
    *,
    native_child_id: str,
    terminal_id: str,
    parent_id: str,
) -> None:
    assert receipt.get("id") == native_child_id, "quota receipt identity changed"
    assert receipt.get("terminal_id") == terminal_id, "quota receipt terminal changed"
    assert receipt.get("parent_terminal_id") == parent_id, "quota receipt parent changed"


def _observe_quota_continuation(
    cao_server: CaoServer,
    *,
    evidence: dict[str, object],
    native_child_id: str,
    terminal_id: str,
    parent_id: str,
    marker: str,
    timeout_seconds: float,
) -> tuple[dict | None, str | None]:
    """Observe the original receipt and terminal using reads only until a finite deadline."""

    started = time.monotonic()
    observation = evidence["quota_observation"]
    assert isinstance(observation, dict)
    observation["started_at_utc"] = datetime.now(timezone.utc).isoformat()
    deadline = started + timeout_seconds
    marker_observed = False

    while time.monotonic() < deadline:
        terminal_response = _request("GET", f"{cao_server.url}/terminals/{terminal_id}")
        assert (
            terminal_response.status_code == 200
        ), f"quota terminal observation returned HTTP {terminal_response.status_code}"
        terminal_data = terminal_response.json()
        assert terminal_data.get("id") == terminal_id, "quota terminal identity changed"

        output_response = _request(
            "GET",
            f"{cao_server.url}/terminals/{terminal_id}/output",
            params={"mode": "last"},
        )
        if output_response.status_code == 404:
            observation["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return None, "verification_unavailable"
        if output_response.status_code == 200:
            output_data = output_response.json()
            marker_observed = marker_observed or marker in output_data.get("output", "")
        elif output_response.status_code < 500:
            assert False, f"quota output observation returned HTTP {output_response.status_code}"

        receipt_response = _request("GET", f"{cao_server.url}/native-children/{native_child_id}")
        assert (
            receipt_response.status_code == 200
        ), f"original quota receipt observation returned HTTP {receipt_response.status_code}"
        receipt = receipt_response.json()
        _assert_quota_receipt_identity(
            receipt,
            native_child_id=native_child_id,
            terminal_id=terminal_id,
            parent_id=parent_id,
        )
        if receipt.get("state") in {"failed", "cancelled"}:
            assert False, "original quota receipt ended without success"
        if receipt.get("state") == "succeeded" and receipt.get("settled_at") and marker_observed:
            observation["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return receipt, None

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))

    observation["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return None, "observation_limit"


@pytest.mark.parametrize("cell", _matrix_cells())
def test_real_provider_native_child_matrix(
    live_provider_cao_server: CaoServer,
    cell: MatrixCell | None,
    record_property: Callable[[str, str], None],
) -> None:
    """Exercise each explicitly selected parent × child provider cell.

    ``CAO_REAL_PROVIDER_E2E_TEST_FILTER=all`` creates one pytest case for every
    ordered pair in the reviewed manifest.  A skipped provider remains visible
    as skipped cells with its exact availability reason; it is never silently
    removed from the report.  A comma-separated test filter keeps local repros
    cheap without changing the manifest, runtime authorization, or scheduler.
    """

    if cell is None:
        pytest.skip("real-provider matrix is disabled")

    cao_server = live_provider_cao_server
    parent_provider = cell.parent
    child_provider = cell.child
    evidence = _new_matrix_evidence(
        cell_id=cell.id,
        parent_provider=parent_provider.name,
        parent_model=parent_provider.model,
        child_provider=child_provider.name,
        child_model=child_provider.model,
    )
    parent_id: str | None = None
    session_name: str | None = None
    outcome: BaseException | None = None

    try:
        _link_auth_material(cao_server, parent_provider)
        if child_provider.name != parent_provider.name:
            _link_auth_material(cao_server, child_provider)

        suffix = uuid.uuid4().hex[:10]
        parent_profile = f"real_matrix_parent_{suffix}"
        child_profile = f"real_matrix_child_{suffix}"
        _write_profile(cao_server, parent_profile, parent_provider.name, parent_provider.model)
        _write_profile(cao_server, child_profile, child_provider.name, child_provider.model)
        parent_id, session_name = _create_parent(cao_server, parent_provider.name, parent_profile)

        _activate_matrix_scenario(evidence, "successful_provider_turn")
        _run_cross_provider_step(
            cao_server,
            cell_id=cell.id,
            parent_id=parent_id,
            session_name=session_name,
            provider=child_provider.name,
            profile=child_profile,
            record_property=record_property,
            evidence=evidence,
        )
        _set_matrix_scenario(evidence, "successful_provider_turn", "validated")

        _activate_matrix_scenario(evidence, "sibling_message_delivery")
        left_id = _create_native_child(
            cao_server,
            session_name=session_name,
            parent_id=parent_id,
            provider=child_provider.name,
            profile=child_profile,
        )
        right_id = _create_native_child(
            cao_server,
            session_name=session_name,
            parent_id=parent_id,
            provider=child_provider.name,
            profile=child_profile,
        )
        sibling_marker = f"CAO_SIBLING_MESSAGE_{uuid.uuid4().hex}"
        sent = _request(
            "POST",
            f"{cao_server.url}/terminals/{right_id}/inbox/messages",
            params={"sender_id": left_id, "message": sibling_marker},
        )
        assert sent.status_code == 200, f"sibling inbox send returned HTTP {sent.status_code}"
        delivered = _wait_for_message_delivery(cao_server, right_id, sent.json()["message_id"])
        assert delivered.get("sender_id") == left_id, "sibling sender identity changed"
        assert delivered.get("receiver_id") == right_id, "sibling receiver identity changed"
        assert delivered.get("message") == sibling_marker, "sibling message body changed"
        _set_matrix_scenario(evidence, "sibling_message_delivery", "validated")

        _activate_matrix_scenario(evidence, "sibling_cancellation_cleanup")
        _delete_terminal(cao_server, left_id)
        _delete_terminal(cao_server, right_id)
        for child_id in (left_id, right_id):
            receipt = _receipt_for_terminal(cao_server, parent_id, child_id)
            assert receipt.get("state") == "cancelled", "unfinished sibling was not cancelled"
            assert (
                receipt.get("cleanup_completed_at") is not None
            ), "sibling cancellation has no cleanup receipt"
        _set_matrix_scenario(evidence, "sibling_cancellation_cleanup", "validated")

        _activate_matrix_scenario(evidence, "timeout_reconciliation")
        uncertain = _request(
            "POST",
            f"{cao_server.url}/terminals/run-step",
            json={
                "provider": child_provider.name,
                "agent": child_profile,
                "prompt": "Do not produce a final answer; remain active until cancelled.",
                "session_name": session_name,
                "caller_id": parent_id,
                "teardown": True,
                "timeout": 0.1,
                "prompt_redelivery": False,
            },
        )
        assert (
            uncertain.status_code == 504
        ), f"uncertain run-step returned HTTP {uncertain.status_code}"
        detail = uncertain.json().get("detail", {})
        assert (
            isinstance(detail, dict) and detail.get("kind") == "timeout"
        ), "uncertain run-step did not report a timeout"
        timeout_terminal_id = detail.get("terminal_id")
        timeout_child_id = detail.get("native_child_id")
        assert (
            isinstance(timeout_terminal_id, str) and timeout_terminal_id
        ), "timeout response has no terminal id"
        assert (
            isinstance(timeout_child_id, str) and timeout_child_id
        ), "timeout response has no native child receipt id"
        receipt_response = _request("GET", f"{cao_server.url}/native-children/{timeout_child_id}")
        assert (
            receipt_response.status_code == 200
        ), f"timeout receipt lookup returned HTTP {receipt_response.status_code}"
        receipt = receipt_response.json()
        assert receipt.get("id") == timeout_child_id, "timeout receipt identity changed"
        assert (
            receipt.get("terminal_id") == timeout_terminal_id
        ), "timeout receipt terminal identity changed"
        assert receipt.get("state") == "reconcile", "timeout receipt was promoted without proof"
        _delete_terminal(cao_server, timeout_terminal_id)
        after_cleanup = _request("GET", f"{cao_server.url}/native-children/{timeout_child_id}")
        assert (
            after_cleanup.status_code == 200
        ), f"cleaned timeout receipt lookup returned HTTP {after_cleanup.status_code}"
        cleaned_receipt = after_cleanup.json()
        assert cleaned_receipt.get("id") == timeout_child_id, "cleaned receipt identity changed"
        assert (
            cleaned_receipt.get("terminal_id") == timeout_terminal_id
        ), "cleaned receipt terminal identity changed"
        assert (
            cleaned_receipt.get("state") == "reconcile"
        ), "cleanup promoted an uncertain timeout receipt"
        assert (
            cleaned_receipt.get("cleanup_completed_at") is not None
        ), "timeout terminal cleanup was not recorded"
        _set_matrix_scenario(evidence, "timeout_reconciliation", "validated")

        _set_matrix_scenario(evidence, "quota_wait_reconciliation", "skipped", "not_applicable")
        _set_matrix_scenario(evidence, "quota_continuation", "skipped", "not_applicable")
    except pytest.skip.Exception as exc:
        outcome = exc
        active_scenario = evidence.get("_active_scenario")
        if isinstance(active_scenario, str):
            _set_matrix_scenario(evidence, active_scenario, "not_observed", "provider_quota")
        cleanup = evidence["cleanup"]
        assert isinstance(cleanup, dict)
        quota_cleanup = cleanup["quota_terminal"]
        assert isinstance(quota_cleanup, dict)
        if quota_cleanup.get("status") == "skipped":
            if quota_cleanup.get("reason") == "not_applicable":
                quota_cleanup["reason"] = "continuation_not_observed"
        elif quota_cleanup.get("status") == "not_observed":
            quota_cleanup.update({"status": "skipped", "reason": "continuation_not_observed"})
        evidence["_requested_status"] = "skipped"
        evidence["_requested_reason"] = "provider_quota"
    except BaseException as exc:
        outcome = exc
        active_scenario = evidence.get("_active_scenario")
        if isinstance(active_scenario, str):
            _set_matrix_scenario(evidence, active_scenario, "failed", "assertion_failed")
        evidence["_requested_status"] = "failed"
        evidence["_requested_reason"] = "assertion_failed"
    finally:
        cleanup = evidence["cleanup"]
        assert isinstance(cleanup, dict)
        session_cleanup = cleanup["session"]
        quota_cleanup = cleanup["quota_terminal"]
        assert isinstance(session_cleanup, dict)
        assert isinstance(quota_cleanup, dict)
        try:
            if session_name is None:
                session_cleanup.update({"status": "skipped", "reason": "setup_not_started"})
            else:
                _delete_session(cao_server, session_name)
                session_cleanup.update({"status": "validated"})
        except BaseException as cleanup_error:
            session_cleanup.update({"status": "failed", "reason": "cleanup_failed"})
            evidence["_requested_status"] = "failed"
            evidence["_requested_reason"] = "cleanup_failed"
            if outcome is None or isinstance(outcome, pytest.skip.Exception):
                outcome = cleanup_error

        requested_status = evidence.get("_requested_status")
        requested_reason = evidence.get("_requested_reason")
        _finish_matrix_evidence(
            evidence,
            status=requested_status if isinstance(requested_status, str) else None,
            reason=requested_reason if isinstance(requested_reason, str) else None,
        )
        _record_matrix_evidence(record_property, evidence)

    if outcome is not None:
        raise outcome
