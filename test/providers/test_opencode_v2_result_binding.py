"""Private selected-agent and database ownership for native-v2 results."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

AGENT = "storage_probe"
SOURCE = "---\ndescription: Storage probe\n---\nReturn exact text.\n"


def _binding_module():
    from cli_agent_orchestrator.utils import opencode_v2_result_binding

    return opencode_v2_result_binding


def _private_v2(tmp_path: Path, terminal_id="terminal-1"):
    owner = PrivateConfigOwnership(tmp_path / "cao", terminal_id)
    root = owner.allocate(2)
    config = {
        "agents": {AGENT: {"system": "Return exact text.", "permissions": {}}},
        "mcp": {"servers": {}},
    }
    config_path = root / "opencode.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    config_path.chmod(0o600)
    data_home = root / "data"
    data_home.mkdir(mode=0o700)
    opencode = data_home / "opencode"
    opencode.mkdir(mode=0o700)
    database = opencode / "opencode.db"
    database.write_bytes(b"synthetic db path placeholder")
    database.chmod(0o600)
    return owner, root, config_path, database


def test_bound_root_claim_and_selected_agent_roundtrip(tmp_path):
    module = _binding_module()
    owner, root, config, database = _private_v2(tmp_path)

    module.write_v2_result_binding(owner, (AGENT, SOURCE), config)
    binding = module.read_v2_result_binding(owner, AGENT)

    assert binding.root == root
    assert binding.database_path == database
    assert binding.agent_id == AGENT
    assert len(binding.source_sha256) == 64
    assert len(binding.agent_config_sha256) == 64

    restored = PrivateConfigOwnership(owner.base, owner.terminal_id)
    restored.bind_restored()
    restored.bind_restored()
    recovered = module.read_v2_result_binding(restored, AGENT)
    assert recovered.root == root


def test_rendered_mode_is_private_and_restorable_without_database(tmp_path):
    module = _binding_module()
    owner, root, config, _database = _private_v2(tmp_path)

    module.write_v2_result_binding(owner, None, config)
    sidecar = root / module.BINDING_FILENAME
    info = sidecar.stat()
    binding = module.read_v2_result_binding(owner)

    assert info.st_uid == os.getuid()
    assert info.st_mode & 0o777 == 0o600
    assert info.st_nlink == 1
    assert binding.mode == "rendered"
    assert binding.agent_id is None
    assert binding.database_path is None
    module.assert_v2_result_binding_unchanged(binding)

    restored = PrivateConfigOwnership(owner.base, owner.terminal_id)
    restored.bind_restored()
    restored_binding = module.read_v2_result_binding(restored)
    assert restored_binding.mode == "rendered"


def test_rendered_mode_rejects_profile_metadata_that_claims_a_selected_agent(tmp_path):
    module = _binding_module()
    owner, _root, config, _database = _private_v2(tmp_path)
    module.write_v2_result_binding(owner, None, config)

    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner, AGENT)


@pytest.mark.parametrize("contents", [None, b"{invalid json\n"])
def test_missing_or_corrupt_private_mode_fails_closed_even_without_agent_metadata(
    tmp_path, contents
):
    module = _binding_module()
    owner, root, config, _database = _private_v2(tmp_path)
    module.write_v2_result_binding(owner, (AGENT, SOURCE), config)
    sidecar = root / module.BINDING_FILENAME
    if contents is None:
        sidecar.unlink()
    else:
        sidecar.write_bytes(contents)
        sidecar.chmod(0o600)

    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner)


def test_read_binding_fails_on_changed_agent_selection_or_config(tmp_path):
    module = _binding_module()
    owner, _root, config_path, _database = _private_v2(tmp_path)
    module.write_v2_result_binding(owner, (AGENT, SOURCE), config_path)

    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner, "different_agent")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["agents"][AGENT]["system"] = "replaced system prompt"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    config_path.chmod(0o600)
    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner, AGENT)


def test_owner_claim_inside_private_binding_must_match_owned_root(tmp_path):
    module = _binding_module()
    owner, root, config_path, _database = _private_v2(tmp_path)
    module.write_v2_result_binding(owner, (AGENT, SOURCE), config_path)
    sidecar = root / module.BINDING_FILENAME
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    document["owner_claim"]["inode"] += 1
    sidecar.write_text(json.dumps(document), encoding="utf-8")
    sidecar.chmod(0o600)

    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner, AGENT)


@pytest.mark.parametrize("bad_file", ["binding", "config", "database"])
@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
def test_symlink_or_hardlink_cannot_substitute_private_result_files(tmp_path, bad_file, link_kind):
    module = _binding_module()
    owner, root, config_path, database = _private_v2(tmp_path)
    module.write_v2_result_binding(owner, (AGENT, SOURCE), config_path)
    if bad_file == "binding":
        target = root / module.BINDING_FILENAME
    elif bad_file == "config":
        target = config_path
    else:
        target = database
    backup = target.with_name(target.name + ".original")
    target.rename(backup)
    if link_kind == "hardlink":
        os.link(backup, target)
    else:
        os.symlink(backup, target)

    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner, AGENT)


def test_wrong_major_retiring_or_unbound_ownership_cannot_resolve_root(tmp_path):
    module = _binding_module()
    owner, root, config, _database = _private_v2(tmp_path)
    module.write_v2_result_binding(owner, (AGENT, SOURCE), config)
    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner, AGENT, expected_major=1)

    claim = owner._read()["claim"]
    owner._publish(claim, phase="retiring")
    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(owner, AGENT)

    unbound = PrivateConfigOwnership(owner.base, "other-terminal")
    with pytest.raises(module.ResultBindingError):
        module.read_v2_result_binding(unbound, AGENT)
