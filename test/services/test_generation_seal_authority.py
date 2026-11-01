"""Opaque negative authority is rooted in the retained original generation."""

import copy
import pickle
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from test.services.test_admission_bootstrap import binding
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.models.resource_generation import (
    TerminalGenerationAuthority,
    is_terminal_generation_authority,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap

pytestmark = pytest.mark.offline_database


def bootstrap(original, *, terminal=True):
    def forbidden(*args):
        pytest.fail("terminal authority must retain its admitted binding, not recapture")

    return ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=forbidden),
        binding_supplier=forbidden,
        instance_supplier=lambda: original.instance_id,
        terminal_generation=terminal,
    )


def test_original_view_is_retained_and_same_generation_has_a_stable_seal_id():
    original = binding()
    first, second = bootstrap(original), bootstrap(original)
    authority = first.issue_terminal_generation_authority(original)
    assert is_terminal_generation_authority(authority)
    assert authority.binding is original
    assert first.issue_terminal_generation_authority(original) is authority
    other = second.issue_terminal_generation_authority(original)
    assert other is not authority and other.seal_id == authority.seal_id
    with pytest.raises(ValueError):
        first.issue_terminal_generation_authority(replace(original, resource_view_sha256="c" * 64))
    assert first.issue_terminal_generation_authority(original) is authority


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_authority_is_not_copyable_or_serializable(operation):
    original = binding()
    authority = bootstrap(original).issue_terminal_generation_authority(original)
    with pytest.raises(TypeError):
        operation(authority)


@pytest.mark.parametrize("fake", [None, {}, object(), TerminalGenerationAuthority()])
def test_unissued_values_are_not_authority(fake):
    assert not is_terminal_generation_authority(fake)


def test_reusable_participant_cannot_issue_a_terminal_authority():
    original = binding()
    with pytest.raises(PermissionError):
        bootstrap(original, terminal=False).issue_terminal_generation_authority(original)


def test_foreign_process_is_rejected_before_a_held_authority_lock():
    original = binding()
    value = bootstrap(original)
    value._terminal_authority_pid += 1
    with value._terminal_authority_guard:
        with pytest.raises(PermissionError):
            value.issue_terminal_generation_authority(original)


@pytest.mark.parametrize(
    "changes", [{"instance_id": "01692d44-f091-412a-8b3c-1281bb1f1c26"}, {"process_pid": 1}]
)
def test_other_original_identity_cannot_be_sealed(changes):
    original = binding()
    if "process_pid" in changes:
        changes = {"process_pid": original.process_pid + 1}
    with pytest.raises(ValueError):
        bootstrap(original).issue_terminal_generation_authority(replace(original, **changes))


def test_simultaneous_issuance_keeps_one_exact_authority():
    original = binding()
    value = bootstrap(original)
    with ThreadPoolExecutor(max_workers=4) as pool:
        authorities = list(pool.map(value.issue_terminal_generation_authority, [original] * 20))
    assert all(authority is authorities[0] for authority in authorities)
