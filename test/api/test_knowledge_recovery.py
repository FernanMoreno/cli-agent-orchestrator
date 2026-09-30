"""HTTP recovery pages use the durable knowledge authority, never a local feed."""

import hashlib
import time
from test.api.test_knowledge_authority import authority, client  # noqa: F401

import pytest


@pytest.mark.asyncio
async def test_recovery_pages_all_revisions_in_binary_keyset_order_with_opaque_cursor(authority):
    """Would fail if recovery used OFFSET, record heads, or a client-made cursor."""
    _, repository, actor, _, _, selectors, body, _ = authority
    async with client(authority) as http:
        for record_id, expected_version, content in (
            ("a", 0, "first a revision"),
            ("a", 1, "second a revision"),
            ("B", 0, "B revision"),
        ):
            created = await http.post(
                f"/v1/knowledge/records/{record_id}/revisions",
                params=selectors,
                json={**body, "expected_version": expected_version, "content": content},
            )
            assert created.status_code == 201, created.text

        first = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 2,
            },
        )
        assert first.status_code == 200, first.text
        page = first.json()
        assert page["schema_version"] == 1
        assert (
            page["retention_policy"]
            == "immutable_history_tombstones_retain_metadata_cursor_ttl_only"
        )
        assert [(row["record_id"], row["revision"]) for row in page["revisions"]] == [
            ("B", 1),
            ("a", 1),
        ]
        assert len(page["checkpoint"]) == 64
        assert page["expires_at"] > time.time()
        cursor = page["next_cursor"]
        assert isinstance(cursor, str) and cursor.startswith("kcr1_")

        with repository.connection() as connection:
            persisted = connection.execute(
                "SELECT token_hash,principal_id,scope,scope_id,last_record_id,last_revision "
                "FROM work_knowledge_cursors"
            ).fetchone()
        assert tuple(persisted[1:]) == (actor.id, "project", "project", "a", 1)
        assert persisted[0] == hashlib.sha256(cursor.encode()).hexdigest()
        assert cursor not in tuple(persisted)

        second = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 2,
                "cursor": cursor,
            },
        )
    assert second.status_code == 200, second.text
    assert [(row["record_id"], row["revision"]) for row in second.json()["revisions"]] == [("a", 2)]
    assert second.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_recovery_cursor_rejects_a_changed_page_limit(authority):
    """Would fail if a caller could reinterpret persisted keyset cursor state."""
    _, _, _, _, _, selectors, body, _ = authority
    async with client(authority) as http:
        for record_id in ("first", "second"):
            created = await http.post(
                f"/v1/knowledge/records/{record_id}/revisions",
                params=selectors,
                json={**body, "content": record_id},
            )
            assert created.status_code == 201, created.text
        initial = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
            },
        )
        assert initial.status_code == 200, initial.text
        cursor = initial.json()["next_cursor"]
        denied = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 2,
                "cursor": cursor,
            },
        )
    assert denied.status_code == 403
    assert cursor not in denied.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["proposal", "review", "tombstone"])
async def test_recovery_cursor_expires_after_any_selected_scope_mutation(authority, change):
    """Would fail if a later page mixed a current decision with an old scope cut."""
    _, _, _, _, _, selectors, body, evidence = authority
    async with client(authority) as http:
        for record_id in ("first", "second"):
            created = await http.post(
                f"/v1/knowledge/records/{record_id}/revisions",
                params=selectors,
                json={**body, "content": record_id},
            )
            assert created.status_code == 201, created.text
        initial = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
            },
        )
        cursor = initial.json()["next_cursor"]
        if change == "proposal":
            mutation = await http.post(
                "/v1/knowledge/records/first/revisions",
                params=selectors,
                json={**body, "expected_version": 1, "content": "later"},
            )
        elif change == "review":
            mutation = await http.post(
                "/v1/knowledge/records/first/revisions/1/review",
                params=selectors,
                json={
                    "schema_version": 1,
                    "expected_version": 1,
                    "decision": "approved",
                    "examined_refs": [evidence["id"]],
                },
            )
        else:
            mutation = await http.post(
                "/v1/knowledge/records/first/revisions/1/tombstone",
                params=selectors,
                json={"schema_version": 1, "expected_version": 1},
            )
        assert mutation.status_code == (201 if change == "proposal" else 200), mutation.text
        expired = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
                "cursor": cursor,
            },
        )
    assert expired.status_code == 410
    assert expired.json()["detail"] == {
        "code": "knowledge_cursor_expired",
        "message": "Knowledge recovery cursor expired.",
        "retryable": False,
        "required_action": "restart_authorized_snapshot",
    }
    assert cursor not in expired.text


@pytest.mark.asyncio
async def test_recovery_keeps_tombstone_metadata_and_ignores_other_scope_changes(authority):
    """Would fail if tombstones vanished or a project cursor watched all scopes."""
    _, _, _, _, _, selectors, body, _ = authority
    async with client(authority) as http:
        for record_id in ("retired", "remaining"):
            created = await http.post(
                f"/v1/knowledge/records/{record_id}/revisions",
                params=selectors,
                json={**body, "content": record_id},
            )
            assert created.status_code == 201, created.text
        retired = await http.post(
            "/v1/knowledge/records/retired/revisions/1/tombstone",
            params=selectors,
            json={"schema_version": 1, "expected_version": 1},
        )
        assert retired.status_code == 200, retired.text
        initial = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
            },
        )
        page = initial.json()
        assert page["revisions"][0]["record_id"] == "remaining"
        cursor = page["next_cursor"]
        other_scope = await http.post(
            "/v1/knowledge/records/job-only/revisions",
            params=selectors,
            json={
                **body,
                "scope": "job",
                "scope_id": selectors["job_id"],
                "content": "separate scope",
            },
        )
        assert other_scope.status_code == 201, other_scope.text
        continued = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
                "cursor": cursor,
            },
        )
    assert continued.status_code == 200, continued.text
    tombstone = continued.json()["revisions"]
    assert [
        (row["record_id"], row["revision"], row["tombstone"], row["content"]) for row in tombstone
    ] == [("retired", 1, True, None)]


@pytest.mark.asyncio
async def test_recovery_revalidates_revocation_and_sanitizes_cursor_failures(authority):
    """Would fail if a revoked or altered cursor could disclose another page."""
    _, _, actor, control, grant, selectors, body, _ = authority
    async with client(authority) as http:
        for record_id in ("first", "second"):
            created = await http.post(
                f"/v1/knowledge/records/{record_id}/revisions",
                params=selectors,
                json={**body, "content": record_id},
            )
            assert created.status_code == 201, created.text
        initial = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
            },
        )
        cursor = initial.json()["next_cursor"]
        malformed = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
                "cursor": "AKIAIOSFODNN7EXAMPLE",
            },
        )
        assert malformed.status_code == 422 and "AKIAIOSFODNN7EXAMPLE" not in malformed.text
        altered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
        lost = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
                "cursor": altered,
            },
        )
        assert lost.status_code == 410 and altered not in lost.text
        control.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="revoked")
        revoked = await http.get(
            "/v1/knowledge/recovery",
            params={
                **selectors,
                "schema_version": 1,
                "scope": "project",
                "scope_id": "project",
                "limit": 1,
                "cursor": cursor,
            },
        )
    assert revoked.status_code == 403
    assert cursor not in revoked.text
