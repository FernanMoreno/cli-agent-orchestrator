"""API and Bridge resource history never overwrite one another on a shared store."""

import os
from test.postgres.test_admission_bootstrap import resource_operator, resource_target
from types import SimpleNamespace
from uuid import uuid4

import psutil
import pytest

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims
from cli_agent_orchestrator.services import admission_bootstrap as api

pytestmark = pytest.mark.postgres


def test_api_and_bridge_scopes_keep_independent_history_on_same_profile_store(
    resource_target, monkeypatch
):
    profile = str(uuid4())
    monkeypatch.setattr(api, "get_persistent_instance_id", lambda: profile, raising=False)
    identity = SimpleNamespace(
        instance_id=profile,
        process_generation=str(uuid4()),
        pid=os.getpid(),
        process_started_at=psutil.Process().create_time(),
    )
    repository = ResourceEnvelopeRepository(target_supplier=lambda: resource_target)
    server = api.ResourceBootstrap(
        repository=repository,
        binding_supplier=lambda selected: api.capture_server_binding(
            selected, identity_supplier=lambda: identity
        ),
        instance_supplier=lambda: profile,
    )
    bridge_a = api.scoped_process_bootstrap("bridge-resource/runtime-A", repository=repository)
    bridge_b = api.scoped_process_bootstrap("bridge-resource/runtime-B", repository=repository)
    claims = EnvelopeClaims(1024**3, 1000, 4, False, False)
    actor = resource_operator()
    for bootstrap in (server, bridge_a, bridge_b):
        assert (
            bootstrap.attest_current(principal=actor, claims=claims, expected_revision=0).revision
            == 1
        )
        assert bootstrap.load_current().reason == "hierarchy_unverified"
    assert len({bootstrap._server_instance() for bootstrap in (server, bridge_a, bridge_b)}) == 3
    bridge_a.revoke_current(principal=actor, expected_revision=1)
    assert bridge_a.load_current().reason == "authority_revoked"
    assert server.load_current().reason == bridge_b.load_current().reason == "hierarchy_unverified"
    reopened = api.scoped_process_bootstrap("bridge-resource/runtime-B", repository=repository)
    assert reopened.registered_current(principal=actor).revision == 1
    assert reopened.load_current().reason == "hierarchy_unverified"
