"""Fast, credential-free contracts for the real-provider matrix harness."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from test.e2e import test_real_provider_matrix as matrix


def test_selected_model_requires_a_single_line_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_CHILD_MODEL", "opencode/mimo-v2.5-free")

    assert (
        matrix._selected_model("CAO_REAL_PROVIDER_E2E_CHILD_MODEL", "opencode_cli")
        == "opencode/mimo-v2.5-free"
    )


@pytest.mark.parametrize("value", ("", "\nmodel", "model\r"))
def test_selected_model_refuses_implicit_or_multiline_value(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_CHILD_MODEL", value)

    with pytest.raises(pytest.fail.Exception):
        matrix._selected_model("CAO_REAL_PROVIDER_E2E_CHILD_MODEL", "opencode_cli")


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
