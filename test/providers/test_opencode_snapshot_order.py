"""Freezing a native profile must preserve last-match permission semantics."""

import json
from test.live_provider import opencode_interaction_journey as journey
from test.providers.test_opencode_profile_initialization import launch

import frontmatter
import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration
from cli_agent_orchestrator.utils.opencode_v2 import _effective_permission, write_v2_configuration

pytestmark = pytest.mark.offline_database


def test_snapshot_keeps_the_real_journey_exact_permission_contract(launch):
    instance, _, _ = launch
    path = opencode_cli.OPENCODE_AGENTS_DIR / "reviewer.md"
    path.write_text(journey._agent_source("reviewer"))
    instance._validate_native_profile()
    frozen = frontmatter.loads(instance._native_agent_source[1])
    from cli_agent_orchestrator.utils.opencode_v2 import _rules

    assert journey._exact_interaction_permissions(_rules(frozen.metadata["permission"])) is True


def test_snapshot_does_not_turn_a_final_wildcard_deny_into_an_allow(launch, tmp_path):
    instance, _, _ = launch
    path = opencode_cli.OPENCODE_AGENTS_DIR / "reviewer.md"
    path.write_text("---\npermission:\n  cao*: allow\n  '*': deny\n---\nReview.")
    instance._validate_native_profile()
    destination = write_v2_configuration(
        tmp_path / "native-v2",
        source_agents=opencode_cli.OPENCODE_AGENTS_DIR,
        source_config={},
        session_id="test1234",
        selected_agent_source=instance._native_agent_source,
    )
    rules = json.loads((destination / "opencode.json").read_text())["agents"]["reviewer"][
        "permissions"
    ]
    assert _effective_permission(rules, "cao_sensitive_tool") == "deny"


def test_snapshot_preserves_nested_native_resource_rule_order(launch, tmp_path):
    instance, _, _ = launch
    path = opencode_cli.OPENCODE_AGENTS_DIR / "reviewer.md"
    path.write_text("---\npermission:\n  bash:\n    'git *': allow\n    '*': deny\n---\nReview.")
    instance._validate_native_profile()
    destination = write_v1_configuration(
        tmp_path / "native-v1",
        source_config={},
        session_id="test1234",
        selected_agent_source=instance._native_agent_source,
    )
    rules = json.loads((destination / "opencode.json").read_text())["agent"]["reviewer"][
        "permission"
    ]["bash"]
    assert list(rules) == ["git *", "*"]
