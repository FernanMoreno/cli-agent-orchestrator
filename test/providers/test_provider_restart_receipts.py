"""Every registered adapter retains the receipt input fence on reconstruction."""

from unittest.mock import patch

import pytest

from cli_agent_orchestrator.providers import manager as pm
from cli_agent_orchestrator.providers.catalog import registered_provider_descriptors
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider


@pytest.mark.parametrize("descriptor", registered_provider_descriptors(), ids=lambda d: d.name)
def test_registered_provider_restores_receipt_without_launch_or_task_replay(descriptor):
    manager = pm.ProviderManager()
    metadata = dict(
        provider=descriptor.name,
        tmux_session="audit",
        tmux_window="audit",
        agent_profile="reviewer",
        engine="v2",
        provider_variant=(
            "code"
            if descriptor.name == "kimi_cli"
            else "opencode-v2" if descriptor.name == "opencode_cli" else None
        ),
    )
    with (
        patch.object(pm, "registered_provider_descriptor", return_value=None),
        patch.object(pm, "get_terminal_metadata", return_value=metadata),
    ):
        original = manager.create_provider(
            descriptor.name,
            "audit",
            "audit",
            "audit",
            "reviewer",
            engine="v2",
            provider_variant=metadata["provider_variant"],
        )
        if original.requires_turn_receipt:
            original.prepare_input("audit task")
            receipt = dict(original.pending_turn_receipt_state(), phase="sent")
        else:
            receipt = None
        manager._providers.clear()
        with (
            patch.object(pm, "get_terminal_turn_receipt", return_value=receipt),
            patch.object(pm, "get_terminal_turn_recovery", return_value=None),
        ):
            restored = manager.get_provider("audit")
    assert isinstance(restored, descriptor.adapter_class)
    if receipt:
        assert restored.pending_turn_receipt_state() == {
            k: receipt[k] for k in ["generation", "receipt_sha256"]
        }
        assert restored.blocks_new_task_input_for_reconciliation
    if descriptor.name == "opencode_cli":
        assert restored.paste_submit_delay == 2.0


def test_open_code_persisted_dialect_restores_without_executable_probe():
    p = OpenCodeCliProvider("audit", "audit", "audit")
    p.restore_runtime_variant("opencode-v2")
    assert p.paste_submit_delay == 2.0
    with pytest.raises(ValueError):
        p.restore_runtime_variant("opencode-v9")


@pytest.mark.parametrize("variant", ["code", "opencode-v2"])
def test_persisted_runtime_variant_survives_actual_metadata_read(isolated_memory_db, variant):
    from cli_agent_orchestrator.clients import database as db

    db.create_terminal(
        "variant", "audit", "worker", "kimi_cli" if variant == "code" else "opencode_cli"
    )
    db.update_terminal_provider_variant("variant", variant)
    assert db.get_terminal_metadata("variant").get("provider_variant") == variant
