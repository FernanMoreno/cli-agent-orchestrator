"""Finite authoring scope must be parsed from source without executing it."""

import importlib

import pytest


def scope_module():
    # Keep collection usable while the integration module is not installed yet.
    return importlib.import_module("cli_agent_orchestrator.services.execution_scope")


def source_with(targets):
    return "SCOPE = " + repr({"version": 1, "targets": targets}) + "\n"


def test_scope_parse_uses_source_not_executed_python_namespace():
    module = scope_module()
    source = source_with({"repo": {"agents": ["developer"], "memory": "off"}})
    declaration = module.parse_scope_declaration(
        source + "raise RuntimeError('must not execute')\n"
    )
    assert declaration.version == 1
    assert isinstance(declaration.targets, tuple)
    assert declaration.target("repo").key == "repo"


@pytest.mark.parametrize(
    "source",
    [
        "SCOPE = choose_scope()\n",
        "SCOPE = {}\nSCOPE = {}\n",
        "SCOPE = {'version': 1, 'version': 1, 'targets': {}}\n",
        "SCOPE = {'version': True, 'targets': {}}\n",
        "if True:\n    SCOPE = {}\n",
    ],
)
def test_scope_rejects_dynamic_duplicate_or_nonexact_declarations(source):
    module = scope_module()
    with pytest.raises(module.ScopeDeclarationError):
        module.parse_scope_declaration(source)


def test_scope_target_inventory_is_bounded_before_git_or_profile_resolution():
    module = scope_module()
    targets = {f"repo_{index}": {"agents": ["developer"], "memory": "off"} for index in range(65)}
    with pytest.raises(module.ScopeDeclarationError):
        module.parse_scope_declaration(source_with(targets))
