"""Fast, credential-free contracts for the real-provider matrix harness."""

from __future__ import annotations

import json
from pathlib import Path
from test.e2e import test_real_provider_matrix as matrix
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _provider_names(count: int = 1) -> list[str]:
    """Use registered provider ids without turning the contract into a fixed list."""

    assert len(matrix.PROVIDERS) >= count
    return list(matrix.PROVIDERS[:count])


def _provider_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "model": "matrix-test-model",
        "binary": "matrix-test-cli",
        "auth_env": ["CAO_MATRIX_TEST_AUTH"],
        "auth_files": [".matrix/auth.json"],
        "capabilities": ["native_children"],
    }
    config.update(overrides)
    return config


def _set_manifest(
    monkeypatch: pytest.MonkeyPatch,
    providers: dict[str, dict[str, object]],
) -> None:
    monkeypatch.setenv(matrix._PROVIDERS_ENV, json.dumps(providers))


def _skip_reason(parameter: pytest.ParameterSet) -> str:
    assert parameter.values
    cell = parameter.values[0]
    assert isinstance(cell, matrix.MatrixCell)
    assert cell.skip_reason is not None
    return cell.skip_reason


def test_manifest_loads_reviewed_registered_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config()})

    loaded = matrix._load_matrix_providers()

    assert loaded[provider] == matrix.MatrixProvider(
        name=provider,
        model="matrix-test-model",
        binary="matrix-test-cli",
        auth_env=("CAO_MATRIX_TEST_AUTH",),
        auth_files=(Path(".matrix/auth.json"),),
        capabilities=frozenset({"native_children"}),
    )


def test_quota_wait_matrix_cell_is_verified_then_reported_as_skip() -> None:
    """A real-provider account quota is an environmental skip, never success.

    The harness must still prove CAO preserved the live child and durable
    reconciliation before it skips the cell. This keeps a provider reset from
    hiding a regression in the no-duplicate-delivery contract.
    """
    detail = {
        "kind": "quota_wait",
        "terminal_id": "quota123",
        "action": "wait_for_quota",
        "delivery_may_have_occurred": True,
        "retryable": False,
        "provider_may_resume": True,
    }
    responses = [
        SimpleNamespace(status_code=409, json=lambda: {"detail": detail}, text="quota"),
        SimpleNamespace(status_code=200, json=lambda: {"status": "waiting_quota"}, text="child"),
    ]
    server = SimpleNamespace(url="http://matrix.test")
    with (
        patch.object(matrix, "_request", side_effect=responses) as request,
        patch.object(
            matrix,
            "_receipt_for_terminal",
            return_value={"state": "reconcile", "error_kind": "quota_wait"},
        ) as receipt,
    ):
        with pytest.raises(pytest.skip.Exception, match="quota pause verified safely"):
            matrix._run_cross_provider_step(
                server,
                parent_id="parent123",
                session_name="session123",
                provider="claude_code",
                profile="matrix-profile",
            )

    assert request.call_count == 2
    receipt.assert_called_once_with(server, "parent123", "quota123")


@pytest.mark.parametrize(
    "manifest",
    (
        "",
        "[]",
        "{",
        "{}",
    ),
)
def test_manifest_requires_a_nonempty_json_object(
    monkeypatch: pytest.MonkeyPatch,
    manifest: str,
) -> None:
    monkeypatch.setenv(matrix._PROVIDERS_ENV, manifest)

    with pytest.raises(pytest.fail.Exception, match="Invalid .* manifest"):
        matrix._load_matrix_providers()


def test_manifest_rejects_unknown_enabled_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_manifest(
        monkeypatch,
        {"unregistered_matrix_provider": _provider_config()},
    )

    with pytest.raises(pytest.fail.Exception, match="has no adapter in this CAO checkout"):
        matrix._load_matrix_providers()


def test_manifest_keeps_unknown_excluded_provider_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_manifest(
        monkeypatch,
        {"gemini_cli": {"exclude_reason": "adapter not installed on this runner"}},
    )

    loaded = matrix._load_matrix_providers()

    assert loaded["gemini_cli"].exclude_reason == "adapter not installed on this runner"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model", "\nnot-a-model"),
        ("binary", ""),
        ("auth_env", "CAO_MATRIX_TEST_AUTH"),
        ("auth_files", ["../outside.json"]),
        ("capabilities", "native_children"),
    ),
)
def test_manifest_validates_endpoint_fields(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config(**{field: value})})

    with pytest.raises(pytest.fail.Exception, match="Invalid .* manifest"):
        matrix._load_matrix_providers()


def test_missing_endpoint_metadata_yields_visible_skip_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    _set_manifest(monkeypatch, {provider: {}})

    reason = _skip_reason(matrix._matrix_cells()[0])

    assert "no reviewed model configured" in reason
    assert "no reviewed CLI binary configured" in reason
    assert "no authentication probe configured" in reason
    assert "missing required capabilities: native_children" in reason


def test_strict_mode_fails_before_fixtures_for_selected_unavailable_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    _set_manifest(monkeypatch, {provider: {}})

    with pytest.raises(
        pytest.fail.Exception,
        match="CAO_REAL_PROVIDER_E2E_STRICT=1 refuses to start real providers",
    ) as error:
        matrix._matrix_cells()

    assert "no reviewed model configured" in str(error.value)
    assert "no reviewed CLI binary configured" in str(error.value)
    assert "no authentication probe configured" in str(error.value)
    assert "missing required capabilities: native_children" in str(error.value)


def test_strict_mode_only_checks_enabled_providers_in_selected_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_provider, unavailable_provider = _provider_names(2)
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, f"{ready_provider}->{ready_provider}")
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    monkeypatch.setenv("CAO_MATRIX_TEST_AUTH", "test-only")
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")
    _set_manifest(
        monkeypatch,
        {
            ready_provider: _provider_config(),
            unavailable_provider: {},
        },
    )

    cells = matrix._matrix_cells()

    assert len(cells) == 1
    assert cells[0].values[0].skip_reason is None


def test_strict_mode_requires_an_executable_cell_beyond_intentional_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, f"{provider}->gemini_cli")
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    monkeypatch.setenv("CAO_MATRIX_TEST_AUTH", "test-only")
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")
    _set_manifest(
        monkeypatch,
        {
            provider: _provider_config(),
            "gemini_cli": {"exclude_reason": "adapter has not shipped"},
        },
    )

    with pytest.raises(
        pytest.fail.Exception,
        match="requires at least one selected fully executable parent->child cell",
    ) as error:
        matrix._matrix_cells()

    assert "child 'gemini_cli'" in str(error.value)
    assert "adapter exclusion: adapter has not shipped" in str(error.value)


def test_strict_mode_keeps_intentional_exclusions_as_skips_when_another_cell_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(
        matrix._PAIRS_ENV,
        f"{provider}->{provider},{provider}->gemini_cli",
    )
    monkeypatch.setenv(matrix._STRICT_ENV, "1")
    monkeypatch.setenv("CAO_MATRIX_TEST_AUTH", "test-only")
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")
    _set_manifest(
        monkeypatch,
        {
            provider: _provider_config(),
            "gemini_cli": {"exclude_reason": "adapter has not shipped"},
        },
    )

    cells = matrix._matrix_cells()

    assert len(cells) == 2
    assert cells[0].values[0].skip_reason is None
    assert "adapter exclusion: adapter has not shipped" in _skip_reason(cells[1])


@pytest.mark.parametrize("value", ("true", "yes", "2"))
def test_strict_mode_rejects_ambiguous_enablement_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(matrix._STRICT_ENV, value)

    with pytest.raises(pytest.fail.Exception, match="must be '1'"):
        matrix._strict_mode_enabled()


def test_manifest_rejects_payload_larger_than_64_kib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(matrix._PROVIDERS_ENV, " " * (64 * 1024 + 1))

    with pytest.raises(pytest.fail.Exception, match="must not exceed 65536 UTF-8 bytes"):
        matrix._load_matrix_providers()


def test_pair_selection_all_generates_cartesian_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")

    pairs = matrix._pair_selection(matrix._load_matrix_providers())

    assert pairs == [
        (parent, parent),
        (parent, child),
        (child, parent),
        (child, child),
    ]


def test_pair_selection_accepts_a_focused_explicit_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.setenv(matrix._PAIRS_ENV, f"{parent}->{child},{child}->{parent}")

    assert matrix._pair_selection(matrix._load_matrix_providers()) == [
        (parent, child),
        (child, parent),
    ]


def test_test_filter_alias_selects_matrix_cells_without_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clearer filter name is an E2E selection alias, not pair admission."""
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.delenv(matrix._PAIRS_ENV, raising=False)
    monkeypatch.setenv(matrix._TEST_FILTER_ENV, f"{parent}->{child}")

    assert matrix._pair_selection(matrix._load_matrix_providers()) == [(parent, child)]


def test_legacy_parent_child_selects_one_manifest_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, child = _provider_names(2)
    _set_manifest(
        monkeypatch,
        {
            parent: _provider_config(),
            child: _provider_config(binary="matrix-test-cli-child"),
        },
    )
    monkeypatch.delenv(matrix._PAIRS_ENV, raising=False)
    monkeypatch.setenv(matrix._LEGACY_PARENT_ENV, parent)
    monkeypatch.setenv(matrix._LEGACY_CHILD_ENV, child)

    assert matrix._pair_selection(matrix._load_matrix_providers()) == [(parent, child)]


def test_one_legacy_endpoint_without_a_pair_selector_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config()})
    monkeypatch.delenv(matrix._PAIRS_ENV, raising=False)
    monkeypatch.setenv(matrix._LEGACY_PARENT_ENV, provider)
    monkeypatch.delenv(matrix._LEGACY_CHILD_ENV, raising=False)

    with pytest.raises(pytest.fail.Exception, match="must be set together"):
        matrix._pair_selection(matrix._load_matrix_providers())


@pytest.mark.parametrize("selection", ("missing->missing", "not-a-pair", ""))
def test_pair_selection_rejects_unknown_or_malformed_cells(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    provider = _provider_names()[0]
    _set_manifest(monkeypatch, {provider: _provider_config()})
    monkeypatch.setenv(matrix._PAIRS_ENV, selection)
    monkeypatch.delenv(matrix._LEGACY_PARENT_ENV, raising=False)
    monkeypatch.delenv(matrix._LEGACY_CHILD_ENV, raising=False)

    with pytest.raises(pytest.fail.Exception):
        matrix._pair_selection(matrix._load_matrix_providers())


def test_disabled_matrix_never_parses_an_ambient_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAO_RUN_LIVE_PROVIDER_TESTS", raising=False)
    monkeypatch.setenv(matrix._PROVIDERS_ENV, "not valid JSON")

    cells = matrix._matrix_cells()

    assert len(cells) == 1
    assert cells[0].id == "live-provider-tests-disabled"


def test_excluded_provider_emits_visible_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    _set_manifest(monkeypatch, {provider: {"exclude_reason": "adapter not installed"}})

    cells = matrix._matrix_cells()

    assert len(cells) == 1
    reason = _skip_reason(cells[0])
    assert f"parent {provider!r}: adapter exclusion: adapter not installed" in reason
    assert f"child {provider!r}: adapter exclusion: adapter not installed" in reason


def test_missing_binary_emits_visible_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    _set_manifest(monkeypatch, {provider: _provider_config(binary="matrix-absent-cli")})
    monkeypatch.setattr(matrix.shutil, "which", lambda _: None)

    cells = matrix._matrix_cells()

    assert "required binary 'matrix-absent-cli' is not on PATH" in _skip_reason(cells[0])


def test_missing_auth_emits_visible_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(tmp_path))
    monkeypatch.delenv("CAO_MATRIX_TEST_AUTH", raising=False)
    _set_manifest(monkeypatch, {provider: _provider_config()})
    monkeypatch.setattr(matrix.shutil, "which", lambda _: "/runner/bin/matrix-test-cli")

    cells = matrix._matrix_cells()

    assert "no reusable auth" in _skip_reason(cells[0])
    assert "CAO_MATRIX_TEST_AUTH" in _skip_reason(cells[0])


def test_missing_native_child_capability_keeps_all_skip_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    provider = _provider_names()[0]
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv(matrix._PAIRS_ENV, "all")
    monkeypatch.setenv(matrix._AUTH_HOME_ENV, str(tmp_path))
    monkeypatch.delenv("CAO_MATRIX_TEST_AUTH", raising=False)
    _set_manifest(
        monkeypatch,
        {
            provider: _provider_config(
                binary="matrix-absent-cli",
                capabilities=["ordinary_terminal"],
            )
        },
    )
    monkeypatch.setattr(matrix.shutil, "which", lambda _: None)

    reason = _skip_reason(matrix._matrix_cells()[0])

    assert "missing required capabilities: native_children" in reason
    assert "required binary 'matrix-absent-cli' is not on PATH" in reason
    assert "no reusable auth" in reason


def test_profile_persists_explicit_model_as_frontmatter(tmp_path) -> None:
    server = SimpleNamespace(home_dir=tmp_path)

    matrix._write_profile(
        server,
        "matrix-child",
        "opencode_cli",
        "opencode/mimo-v2.5-free",
    )

    profile = (
        tmp_path / ".aws" / "cli-agent-orchestrator" / "agent-store" / "matrix-child.md"
    ).read_text(encoding="utf-8")
    assert 'model: "opencode/mimo-v2.5-free"' in profile


def test_real_provider_workflow_uses_manifest_and_dynamic_test_filter() -> None:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "real-provider-e2e.yml").read_text(
        encoding="utf-8"
    )

    assert "providers_json:" in workflow
    assert "CAO_REAL_PROVIDER_E2E_PROVIDERS:" in workflow
    assert "CAO_REAL_PROVIDER_E2E_TEST_FILTER: ${{ inputs.pairs }}" in workflow
    assert 'CAO_REAL_PROVIDER_E2E_STRICT: "1"' in workflow
    assert "inputs.providers_json || vars.CAO_REAL_PROVIDER_E2E_PROVIDERS" in workflow
    assert workflow.index('if len(raw.encode("utf-8")) > 65_536:') < workflow.index(
        "if not raw.strip():"
    )
    assert "parent_provider:" not in workflow
    assert "child_provider:" not in workflow
    assert "parent_model:" not in workflow
    assert "child_model:" not in workflow


def test_real_provider_matrix_documentation_explains_dynamic_contract() -> None:
    documentation = (_REPO_ROOT / "docs" / "real-provider-e2e.md").read_text(encoding="utf-8")

    assert "CAO_REAL_PROVIDER_E2E_PROVIDERS" in documentation
    assert "CAO_REAL_PROVIDER_E2E_TEST_FILTER=all" in documentation
    assert "**not** a\nruntime compatibility policy" in documentation
    assert "deprecated alias" in documentation
    assert "CAO_REAL_PROVIDER_E2E_STRICT=1" in documentation
    assert "at least one\nselected parent -> child cell" in documentation
    assert "exclude_reason" in documentation
    assert "native_children" in documentation
    assert "64 KiB" in documentation
