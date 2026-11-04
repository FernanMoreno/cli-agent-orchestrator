"""Policy control uses the same admin, CAS and physical runtime owner as resources."""

from datetime import datetime, timezone
from test.api.test_admission_control import control, headers
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.admission_policy_repository import (
    PolicyNotFound,
    PolicyRevisionConflict,
)
from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyHistory,
    AdmissionPolicyRecord,
    AdmissionPolicyValues,
)

pytestmark = pytest.mark.offline_database


class Repository:
    def __init__(self):
        self.rows = []
        self.profile_id = str(uuid4())
        self.error = None

    def resolve_target(self):
        return self

    def read_latest(self, target, **kwargs):
        return self.rows[-1] if self.rows else None

    def append(self, target, *, policy, expected_revision, principal_id, active=True):
        if self.error:
            raise self.error
        current = self.read_latest(target)
        if expected_revision != (0 if current is None else current.revision):
            raise PolicyRevisionConflict()
        record = AdmissionPolicyRecord(
            profile_id=self.profile_id,
            revision=expected_revision + 1,
            policy=policy,
            active=active,
            actor_principal_id=principal_id,
            recorded_at=datetime.now(timezone.utc),
        )
        self.rows.append(record)
        return record

    def revoke(self, target, *, expected_revision, principal_id):
        current = self.read_latest(target)
        if current is None:
            raise PolicyNotFound()
        return self.append(
            target,
            policy=current.policy,
            expected_revision=expected_revision,
            principal_id=principal_id,
            active=False,
        )

    def history(self, target, *, limit=50, before_revision=None):
        rows = [
            r
            for r in reversed(self.rows)
            if before_revision is None or r.revision < before_revision
        ]
        page = rows[:limit]
        return AdmissionPolicyHistory(
            entries=tuple(page),
            next_before_revision=page[-1].revision if len(rows) > limit else None,
        )


@pytest.fixture
def policy_control(control):
    client, f, key = control
    repository = Repository()
    runtime = client.app.state.inbox_admission_runtime
    runtime._policy_repository = repository
    return client, f, key, repository, runtime


def test_policy_admin_create_replace_history_revoke_and_conflict(policy_control):
    client, f, key, repository, runtime = policy_control
    auth = headers(key)
    result = client.get("/admission/policy", headers=auth)
    assert result.status_code == 200 and result.json() == {
        "persisted": None,
        "applied_revision": None,
    }
    created = client.put(
        "/admission/policy", headers=auth, json={"expected_revision": 0, "policy": {}}
    )
    assert created.status_code == 200 and created.json()["revision"] == 1
    assert created.json()["actor_principal_id"].startswith("principal-")
    assert created.headers["cache-control"] == "no-store"
    conflict = client.put(
        "/admission/policy", headers=auth, json={"expected_revision": 0, "policy": {}}
    )
    assert conflict.status_code == 409 and len(repository.rows) == 1
    updated = client.put(
        "/admission/policy",
        headers=auth,
        json={"expected_revision": 1, "policy": {"cpu_per_delivery_millicores": 500}},
    )
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    revoked = client.post("/admission/policy/revoke", headers=auth, json={"expected_revision": 2})
    assert revoked.status_code == 200 and not revoked.json()["active"]
    assert revoked.json()["policy"]["cpu_per_delivery_millicores"] == 500
    history = client.get("/admission/policy/history?limit=2", headers=auth)
    assert history.status_code == 200
    assert [r["revision"] for r in history.json()["entries"]] == [3, 2]
    assert history.json()["next_before_revision"] == 2
    assert (
        client.get("/admission/policy/history?before_revision=2", headers=auth).json()["entries"][
            0
        ]["revision"]
        == 1
    )


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": True, "policy": {}},
        {"expected_revision": 0, "policy": {"max_workers": 4}},
        {"expected_revision": 0, "policy": {}, "path": "private-canary"},
    ],
)
def test_policy_invalid_values_are_fixed_and_never_mutate(policy_control, body):
    client, f, key, repository, runtime = policy_control
    response = client.put("/admission/policy", headers=headers(key), json=body)
    assert response.status_code == 422 and "private-canary" not in response.text
    assert repository.rows == []


@pytest.mark.parametrize("scopes", ["cao:read", "cao:write"])
def test_policy_requires_real_admin(policy_control, scopes):
    client, f, key, repository, runtime = policy_control
    assert (
        client.put(
            "/admission/policy",
            headers=headers(key, scopes),
            json={"expected_revision": 0, "policy": {}},
        ).status_code
        == 403
    )
    assert repository.rows == []


def test_policy_missing_revoke_known_conflict_does_not_poison_owner(policy_control):
    client, f, key, repository, runtime = policy_control
    assert (
        client.post(
            "/admission/policy/revoke", headers=headers(key), json={"expected_revision": 0}
        ).status_code
        == 404
    )
    assert not f.monitor._authority_state.uncertain
    assert (
        client.put(
            "/admission/policy", headers=headers(key), json={"expected_revision": 0, "policy": {}}
        ).status_code
        == 200
    )


def test_policy_seed_explicit_private_source_and_only_empty_history(policy_control, tmp_path):
    client, f, key, repository, runtime = policy_control
    path = tmp_path / "private-policy.json"
    path.write_text('{"version":1,"policy":{"cpu_per_delivery_millicores":700}}')
    path.chmod(0o600)
    runtime._policy_file = path
    response = client.post(
        "/admission/policy/seed", headers=headers(key), json={"expected_revision": 0}
    )
    assert (
        response.status_code == 200
        and response.json()["policy"]["cpu_per_delivery_millicores"] == 700
    )
    assert (
        client.post(
            "/admission/policy/seed", headers=headers(key), json={"expected_revision": 0}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/admission/policy/seed", headers=headers(key), json={"expected_revision": 1}
        ).status_code
        == 409
    )
    assert len(repository.rows) == 1 and not f.monitor._authority_state.uncertain


def test_policy_bad_seed_read_does_not_poison_owner(policy_control, tmp_path):
    client, f, key, repository, runtime = policy_control
    path = tmp_path / "bad-policy.json"
    path.write_text('{"version":1,"policy":{"max_workers":4}}')
    path.chmod(0o600)
    runtime._policy_file = path
    response = client.post(
        "/admission/policy/seed", headers=headers(key), json={"expected_revision": 0}
    )
    assert response.status_code == 503 and not f.monitor._authority_state.uncertain
    assert repository.rows == []


def test_policy_uncertain_write_is_redacted_and_never_replayed(policy_control):
    client, f, key, repository, runtime = policy_control
    repository.error = OSError("private-canary")
    response = client.put(
        "/admission/policy", headers=headers(key), json={"expected_revision": 0, "policy": {}}
    )
    assert response.status_code == 503 and "private-canary" not in response.text
    assert f.monitor._authority_state.uncertain
    repository.error = None
    assert (
        client.put(
            "/admission/policy", headers=headers(key), json={"expected_revision": 0, "policy": {}}
        ).status_code
        == 503
    )
    assert repository.rows == []


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "before_revision=0", "before_revision=9223372036854775808"]
)
def test_policy_history_rejects_invalid_cursor_before_control(policy_control, query):
    client, f, key, repository, runtime = policy_control
    response = client.get("/admission/policy/history?" + query, headers=headers(key))
    assert response.status_code == 422
    assert repository.rows == [] and not f.monitor._authority_state.uncertain


def test_proven_prewrite_failure_does_not_poison_later_policy_control(policy_control):
    from cli_agent_orchestrator.clients import admission_policy_repository as policy_module

    error_type = getattr(policy_module, "PolicyWriteNotStarted", None)
    assert isinstance(error_type, type), "pre-write refusal must be distinct from uncertain I/O"
    client, f, key, repository, runtime = policy_control
    repository.error = error_type()
    response = client.put(
        "/admission/policy", headers=headers(key), json={"expected_revision": 0, "policy": {}}
    )
    assert response.status_code == 503
    assert not f.monitor._authority_state.uncertain
    repository.error = None
    assert (
        client.put(
            "/admission/policy", headers=headers(key), json={"expected_revision": 0, "policy": {}}
        ).status_code
        == 200
    )
