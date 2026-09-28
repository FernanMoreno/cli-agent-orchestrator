"""Local-default and remote-client tests for distributed CAO memory."""

from unittest.mock import Mock, patch

import pytest
import requests

from cli_agent_orchestrator.models.memory import Memory
from cli_agent_orchestrator.services import memory_gateway
from cli_agent_orchestrator.services.memory_service import MemoryPartialWriteError


def _memory() -> Memory:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return Memory(
        id="m1",
        key="shared-fact",
        memory_type="project",
        scope="project",
        scope_id="project-1",
        file_path="/memory/shared-fact.md",
        created_at=now,
        updated_at=now,
        content="Shared across workers.",
    )


def _revision_payload(**overrides):
    payload = {
        "schema_version": 1,
        "record_id": "record",
        "revision": 1,
        "record_version": 1,
        "scope": "project",
        "scope_id": "project-1",
        "producer_principal_id": "principal-1",
        "work_item_id": None,
        "attempt_id": None,
        "source_artifact_id": None,
        "source_hash": "a" * 64,
        "delivered_hash": "b" * 64,
        "evidence_refs": ["evidence-1"],
        "confidence": 0.8,
        "fresh_until": 1_800_000_000.0,
        "decision": "proposed",
        "supersedes": None,
        "tombstone": False,
        "legacy": False,
        "content": "safe knowledge",
        "redacted": False,
        "truncated": False,
        "created_at": 1_700_000_000.0,
    }
    payload.update(overrides)
    return payload


def _cursor() -> str:
    return "kcr1_" + "c" * 43


def _recovery_page(**overrides):
    payload = {
        "schema_version": 1,
        "revisions": [_revision_payload()],
        "checkpoint": "a" * 64,
        "next_cursor": _cursor(),
        "expires_at": 1_800_000_100.0,
        "retention_policy": "immutable_history_tombstones_retain_metadata_cursor_ttl_only",
    }
    payload.update(overrides)
    return payload


def test_remote_memory_is_opt_in(monkeypatch):
    monkeypatch.delenv("CAO_MEMORY_API_URL", raising=False)
    assert memory_gateway.remote_memory_url() is None


def test_remote_memory_url_normalized(monkeypatch):
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://cao-supervisor:9889/")
    assert memory_gateway.remote_memory_url() == "http://cao-supervisor:9889"


@pytest.mark.asyncio
async def test_remote_store_serializes_context(monkeypatch):
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")

    async def run_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(memory_gateway.asyncio, "to_thread", run_inline)
    stored = _memory()
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "memory": stored.model_dump(mode="json"),
        "action": "created",
    }
    with patch.object(memory_gateway.requests, "post", return_value=response) as post:
        result = await memory_gateway.store_memory(
            content=stored.content,
            scope="project",
            memory_type="project",
            key=stored.key,
            tags="shared",
            terminal_context={"terminal_id": "abc12345", "cwd": "/workspace/repo"},
        )
    assert result.key == "shared-fact"
    assert result.action == "created"
    assert post.call_args.args[0] == "http://memory-owner:9889/internal/memory/store"
    assert post.call_args.kwargs["json"]["terminal_context"]["terminal_id"] == "abc12345"


@pytest.mark.asyncio
async def test_remote_store_reconstructs_partial_write_error(monkeypatch):
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")

    async def run_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(memory_gateway.asyncio, "to_thread", run_inline)
    response = Mock(status_code=500)
    response.json.return_value = {
        "error_kind": "memory_metadata_partial_write",
        "error": "metadata failed after durable writes",
        "partial_write": {
            "key": "shared-fact",
            "scope": "project",
            "scope_id": "project-1",
            "file_path": "/memory/project-1/wiki/project/shared-fact.md",
            "completed_phases": ["wiki", "index"],
            "repair_command": "cao memory repair --apply",
        },
    }

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(MemoryPartialWriteError) as caught,
    ):
        await memory_gateway.store_memory(
            content="already durable",
            scope="project",
            memory_type="project",
            key="shared-fact",
            tags="",
            terminal_context={"cwd": "/workspace/repo"},
        )

    assert caught.value.key == "shared-fact"
    assert caught.value.scope_id == "project-1"
    assert caught.value.completed_phases == ["wiki", "index"]
    response.raise_for_status.assert_not_called()


def test_remote_non_typed_error_still_raises_http_error(monkeypatch):
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=503)
    response.json.return_value = {"detail": "unavailable"}
    response.raise_for_status.side_effect = requests.HTTPError("503")

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(requests.HTTPError, match="503"),
    ):
        memory_gateway._post("/internal/memory/store", {})


def test_remote_memory_uses_elastic_gateway_credentials(monkeypatch):
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://cao-worker-broker:9890")
    monkeypatch.setenv("CAO_ELASTIC_WORKER_ID", "deadbeef")
    monkeypatch.setenv("CAO_ELASTIC_RELEASE_TOKEN", "release-token")
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"context": "shared"}

    with patch.object(memory_gateway.requests, "post", return_value=response) as post:
        result = memory_gateway._post("/internal/memory/context", {})

    assert result == {"context": "shared"}
    assert post.call_args.kwargs["headers"] == {
        "X-CAO-Worker-ID": "deadbeef",
        "X-CAO-Release-Token": "release-token",
    }


def test_propose_revision_uses_the_versioned_cas_authority(monkeypatch):
    """Changing this route, CAS body or redirect policy must break the client."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    monkeypatch.setattr(memory_gateway, "get_local_bearer", lambda: "verified-bearer")
    response = Mock(status_code=201)
    response.raise_for_status.return_value = None
    response.json.return_value = _revision_payload()

    with patch.object(memory_gateway.requests, "post", return_value=response) as post:
        result = memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=["evidence-1"],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    assert result == _revision_payload()
    assert post.call_args.args[0] == "http://memory-owner:9889/v1/knowledge/records/record/revisions"
    assert post.call_args.kwargs["params"] == {
        "job_id": "job-1",
        "grant_id": "grant-1",
        "grant_revision": 1,
    }
    assert post.call_args.kwargs["json"] == {
        "schema_version": 1,
        "expected_version": 0,
        "scope": "project",
        "scope_id": "project-1",
        "content": "safe knowledge",
        "evidence_refs": ["evidence-1"],
        "confidence": 0.8,
        "fresh_until": 1_800_000_000.0,
    }
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer verified-bearer"
    assert post.call_args.kwargs["allow_redirects"] is False


def test_propose_revision_types_only_the_known_conflict_envelope(monkeypatch):
    """Changing a known CAS conflict into a generic HTTP error must break this client."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=409)
    response.json.return_value = {
        "detail": {
            "code": "knowledge_revision_conflict",
            "message": "Knowledge revision changed.",
            "retryable": False,
            "required_action": "read_current_revision",
        }
    }
    response.raise_for_status.side_effect = requests.HTTPError("409")

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(memory_gateway.KnowledgeRevisionConflict) as caught,
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    assert caught.value.code == "knowledge_revision_conflict"
    assert caught.value.required_action == "read_current_revision"
    assert "Knowledge revision changed." not in str(caught.value)


def test_propose_revision_rejects_an_invalid_record_path_before_transport(monkeypatch):
    """A record identifier must never turn the fixed CAS route into a new path."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    post = Mock()

    with (
        patch.object(memory_gateway.requests, "post", post),
        pytest.raises(ValueError, match="invalid record_id"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record/other",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    post.assert_not_called()


@pytest.mark.parametrize("delimiter", ["?", "#"])
def test_propose_revision_rejects_an_empty_authority_query_or_fragment_before_transport(
    monkeypatch, delimiter
):
    """An empty URL delimiter still moves the fixed CAS path out of the authority path."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", f"http://memory-owner:9889{delimiter}")
    post = Mock(side_effect=AssertionError("transport called"))

    with (
        patch.object(memory_gateway.requests, "post", post),
        pytest.raises(ValueError, match="invalid knowledge authority URL"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    post.assert_not_called()


@pytest.mark.parametrize("transport", ["post", "get"])
def test_knowledge_clients_reject_an_authority_without_hostname_before_transport(
    monkeypatch, transport
):
    """Both CAS and recovery need a host before selecting a remote authority."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://:80")
    request = Mock(side_effect=AssertionError("transport called"))

    with (
        patch.object(memory_gateway.requests, transport, request),
        pytest.raises(ValueError, match="invalid knowledge authority URL"),
    ):
        if transport == "post":
            memory_gateway.knowledge_propose_revision(
                "record",
                job_id="job-1",
                grant_id="grant-1",
                grant_revision=1,
                expected_version=0,
                scope="project",
                scope_id="project-1",
                content="safe knowledge",
                evidence_refs=[],
                confidence=0.8,
                fresh_until=1_800_000_000.0,
            )
        else:
            memory_gateway.knowledge_recovery_page(
                job_id="job-1",
                grant_id="grant-1",
                grant_revision=1,
                scope="project",
                scope_id="project-1",
                limit=1,
            )

    request.assert_not_called()


@pytest.mark.parametrize(
    "authority",
    [
        "http://:80",
        "http:///missing",
        "ftp://authority",
        "http://authority:not-a-port",
        "http://authority:-1",
        "http://authority:65536",
        "http://[::1",
        "http://[not-an-ipv6]",
        "http://user:pass@authority",
        "http://@authority",
        r"http://authority\\path",
        "http://authority?",
        "http://authority#",
        "http://authority?path=/other",
        "http://authority#path=/other",
        "http://au thority",
        "http://authority\x7f",
        "\thttp://authority",
        "http://authority\t",
        "\rhttp://authority",
        "http://authority\n",
    ],
)
def test_knowledge_clients_reject_malformed_authorities_before_transport(monkeypatch, authority):
    """CAS and recovery fail closed for the URL forms requests must never receive."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", authority)

    for transport in ("post", "get"):
        request = Mock(side_effect=AssertionError("transport called"))
        with (
            patch.object(memory_gateway.requests, transport, request),
            pytest.raises(ValueError, match="invalid knowledge authority URL"),
        ):
            if transport == "post":
                memory_gateway.knowledge_propose_revision(
                    "record",
                    job_id="job-1",
                    grant_id="grant-1",
                    grant_revision=1,
                    expected_version=0,
                    scope="project",
                    scope_id="project-1",
                    content="safe knowledge",
                    evidence_refs=[],
                    confidence=0.8,
                    fresh_until=1_800_000_000.0,
                )
            else:
                memory_gateway.knowledge_recovery_page(
                    job_id="job-1",
                    grant_id="grant-1",
                    grant_revision=1,
                    scope="project",
                    scope_id="project-1",
                    limit=1,
                )
        request.assert_not_called()


def test_knowledge_clients_reject_a_nul_authority_before_transport(monkeypatch):
    """NUL cannot enter os.environ, but a selected authority containing it still fails closed."""
    monkeypatch.delenv("CAO_MEMORY_API_URL", raising=False)
    monkeypatch.setattr(memory_gateway, "remote_memory_url", lambda: "http://authority\x00")

    for transport in ("post", "get"):
        request = Mock(side_effect=AssertionError("transport called"))
        with (
            patch.object(memory_gateway.requests, transport, request),
            pytest.raises(ValueError, match="invalid knowledge authority URL"),
        ):
            if transport == "post":
                memory_gateway.knowledge_propose_revision(
                    "record",
                    job_id="job-1",
                    grant_id="grant-1",
                    grant_revision=1,
                    expected_version=0,
                    scope="project",
                    scope_id="project-1",
                    content="safe knowledge",
                    evidence_refs=[],
                    confidence=0.8,
                    fresh_until=1_800_000_000.0,
                )
            else:
                memory_gateway.knowledge_recovery_page(
                    job_id="job-1",
                    grant_id="grant-1",
                    grant_revision=1,
                    scope="project",
                    scope_id="project-1",
                    limit=1,
                )
        request.assert_not_called()


@pytest.mark.parametrize("record_id", [".", ".."])
def test_propose_revision_rejects_dot_record_segments_before_transport(monkeypatch, record_id):
    """Dot segments can be normalized by an intermediary and must not alter the fixed path."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://authority")
    post = Mock(side_effect=AssertionError("transport called"))

    with (
        patch.object(memory_gateway.requests, "post", post),
        pytest.raises(ValueError, match="invalid record_id"),
    ):
        memory_gateway.knowledge_propose_revision(
            record_id,
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    post.assert_not_called()


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("http://localhost", "http://localhost"),
        ("https://127.0.0.1:443", "https://127.0.0.1:443"),
        ("http://[::1]", "http://[::1]"),
        ("http://[::1]:0", "http://[::1]:0"),
        ("http://internal.example/prefix/", "http://internal.example/prefix"),
        ("  http://localhost/prefix/  ", "http://localhost/prefix"),
        ("http://authority:", "http://authority:"),
    ],
)
def test_propose_revision_preserves_valid_authority_urls(monkeypatch, authority, expected):
    """Authority syntax remains compatible with local, IPv6 and prefixed deployments."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", authority)
    response = Mock(status_code=201)
    response.json.return_value = _revision_payload()

    with patch.object(memory_gateway.requests, "post", return_value=response) as post:
        memory_gateway.knowledge_propose_revision(
            "release.notes",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    assert post.call_args.args[0] == f"{expected}/v1/knowledge/records/release.notes/revisions"
    assert post.call_args.kwargs["allow_redirects"] is False


def test_propose_revision_rejects_a_malformed_success_body(monkeypatch):
    """The authority response is not a success until the complete v1 DTO validates."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=201)
    response.raise_for_status.return_value = None
    response.json.return_value = _revision_payload(unexpected="not part of v1")

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(ValueError, match="malformed knowledge revision response"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )


@pytest.mark.parametrize("digest_field", ["source_hash", "delivered_hash"])
def test_propose_revision_rejects_a_201_with_a_non_sha256_digest(monkeypatch, digest_field):
    """A successful response is incompatible unless both server digests are canonical SHA-256."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=201)
    response.raise_for_status.return_value = None
    response.json.return_value = _revision_payload(**{digest_field: "not-a-sha256-digest"})

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(ValueError, match="malformed knowledge revision response"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )


def test_propose_revision_rejects_the_server_freshness_upper_bound_before_transport(monkeypatch):
    """The strict server bound excludes exactly 1e15, before an indeterminate POST exists."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    post = Mock(side_effect=AssertionError("transport called"))

    with (
        patch.object(memory_gateway.requests, "post", post),
        pytest.raises(ValueError, match="invalid fresh_until"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1e15,
        )

    post.assert_not_called()


def test_propose_revision_rejects_a_201_with_the_server_freshness_upper_bound(monkeypatch):
    """A complete response is still invalid when its freshness violates the server DTO bound."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=201)
    response.raise_for_status.return_value = None
    response.json.return_value = _revision_payload(fresh_until=1e15)

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(ValueError, match="malformed knowledge revision response"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )


def test_propose_revision_does_not_type_a_near_miss_conflict(monkeypatch):
    """Only the server's exact, non-sensitive conflict envelope is semantic."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=409)
    response.json.return_value = {
        "detail": {
            "code": "knowledge_revision_conflict",
            "message": "private authority detail",
            "retryable": False,
            "required_action": "read_current_revision",
        }
    }
    response.raise_for_status.side_effect = requests.HTTPError("409")

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(requests.HTTPError, match="409"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )


def test_propose_revision_rejects_a_redirect_before_parsing_json(monkeypatch):
    """A redirect could cross an authority boundary, so it is never followed or decoded."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=307)

    with (
        patch.object(memory_gateway.requests, "post", return_value=response),
        pytest.raises(ValueError, match="redirects are not accepted"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    response.json.assert_not_called()


def test_propose_revision_propagates_a_transport_failure_without_local_write(monkeypatch):
    """An indeterminate POST stays indeterminate; it is never retried or made local."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    local_store = Mock(side_effect=AssertionError("local fallback"))
    post = Mock(side_effect=requests.ConnectionError("authority unavailable"))
    monkeypatch.setattr(memory_gateway.MemoryService, "store", local_store)

    with (
        patch.object(memory_gateway.requests, "post", post),
        pytest.raises(requests.ConnectionError, match="authority unavailable"),
    ):
        memory_gateway.knowledge_propose_revision(
            "record",
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            expected_version=0,
            scope="project",
            scope_id="project-1",
            content="safe knowledge",
            evidence_refs=[],
            confidence=0.8,
            fresh_until=1_800_000_000.0,
        )

    assert post.call_count == 1
    local_store.assert_not_called()


def test_recovery_rejects_an_unknown_schema_before_returning_a_page(monkeypatch):
    """A future page version cannot be treated as a compatible recovery snapshot."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = _recovery_page(schema_version=2)

    with (
        patch.object(memory_gateway.requests, "get", return_value=response),
        pytest.raises(ValueError, match="malformed knowledge recovery response"),
    ):
        memory_gateway.knowledge_recovery_page(
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            scope="project",
            scope_id="project-1",
            limit=1,
        )


def test_recovery_page_uses_the_authorized_versioned_endpoint(monkeypatch):
    """Recovery fixes one remote authority and never reinterprets the opaque cursor."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    monkeypatch.setattr(memory_gateway, "get_local_bearer", lambda: "verified-bearer")
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = _recovery_page()

    with patch.object(memory_gateway.requests, "get", return_value=response) as get:
        result = memory_gateway.knowledge_recovery_page(
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            scope="project",
            scope_id="project-1",
            limit=1,
            cursor=_cursor(),
        )

    assert result == _recovery_page()
    assert get.call_args.args[0] == "http://memory-owner:9889/v1/knowledge/recovery"
    assert get.call_args.kwargs["params"] == {
        "schema_version": 1,
        "job_id": "job-1",
        "grant_id": "grant-1",
        "grant_revision": 1,
        "scope": "project",
        "scope_id": "project-1",
        "limit": 1,
        "cursor": _cursor(),
    }
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer verified-bearer"
    assert get.call_args.kwargs["allow_redirects"] is False


def test_recovery_rejects_an_invalid_cursor_before_transport(monkeypatch):
    """An untrusted cursor must not be sent or transformed into a local query."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    get = Mock()

    with (
        patch.object(memory_gateway.requests, "get", get),
        pytest.raises(ValueError, match="invalid cursor"),
    ):
        memory_gateway.knowledge_recovery_page(
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            scope="project",
            scope_id="project-1",
            limit=1,
            cursor="cursor/other",
        )

    get.assert_not_called()


def test_recovery_types_only_the_exact_expired_cursor_envelope(monkeypatch):
    """The one typed recovery failure is safe to expose without copying authority data."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=410)
    response.json.return_value = {
        "detail": {
            "code": "knowledge_cursor_expired",
            "message": "Knowledge recovery cursor expired.",
            "retryable": False,
            "required_action": "restart_authorized_snapshot",
        }
    }
    response.raise_for_status.side_effect = requests.HTTPError("410")

    with (
        patch.object(memory_gateway.requests, "get", return_value=response),
        pytest.raises(memory_gateway.KnowledgeRecoveryCursorExpired) as caught,
    ):
        memory_gateway.knowledge_recovery_page(
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            scope="project",
            scope_id="project-1",
            limit=1,
        )

    assert caught.value.code == "knowledge_cursor_expired"
    assert caught.value.required_action == "restart_authorized_snapshot"
    assert "Knowledge recovery cursor expired." not in str(caught.value)


def test_recovery_does_not_type_a_near_miss_expired_cursor(monkeypatch):
    """Unknown 410 payloads retain their ordinary HTTP failure semantics."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=410)
    response.json.return_value = {
        "detail": {
            "code": "knowledge_cursor_expired",
            "message": "private authority detail",
            "retryable": False,
            "required_action": "restart_authorized_snapshot",
        }
    }
    response.raise_for_status.side_effect = requests.HTTPError("410")

    with (
        patch.object(memory_gateway.requests, "get", return_value=response),
        pytest.raises(requests.HTTPError, match="410"),
    ):
        memory_gateway.knowledge_recovery_page(
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            scope="project",
            scope_id="project-1",
            limit=1,
        )


def test_recovery_rejects_a_redirect_before_parsing_json(monkeypatch):
    """A recovery page must remain on its selected authority."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    response = Mock(status_code=302)

    with (
        patch.object(memory_gateway.requests, "get", return_value=response),
        pytest.raises(ValueError, match="redirects are not accepted"),
    ):
        memory_gateway.knowledge_recovery_page(
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            scope="project",
            scope_id="project-1",
            limit=1,
        )

    response.json.assert_not_called()


def test_recovery_rejects_an_invalid_authority_url_before_transport(monkeypatch):
    """A query or fragment cannot turn configured authority into a second request target."""
    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889?redirect=elsewhere")
    get = Mock()

    with (
        patch.object(memory_gateway.requests, "get", get),
        pytest.raises(ValueError, match="invalid knowledge authority URL"),
    ):
        memory_gateway.knowledge_recovery_page(
            job_id="job-1",
            grant_id="grant-1",
            grant_revision=1,
            scope="project",
            scope_id="project-1",
            limit=1,
        )

    get.assert_not_called()


@pytest.mark.asyncio
async def test_local_mcp_memory_also_crosses_authenticated_http(monkeypatch):
    from cli_agent_orchestrator.mcp_server import server
    from cli_agent_orchestrator.constants import API_BASE_URL

    monkeypatch.delenv("CAO_MEMORY_API_URL", raising=False)
    monkeypatch.setattr(server, "_get_terminal_context_from_env", lambda: None)
    monkeypatch.setattr(memory_gateway, "get_local_bearer", lambda: "verified-bearer")
    monkeypatch.setattr(
        memory_gateway.MemoryService, "recall", Mock(side_effect=AssertionError("local bypass"))
    )
    response = Mock(status_code=200)
    response.json.return_value = {"memories": []}
    post = Mock(return_value=response)
    monkeypatch.setattr(memory_gateway.requests, "post", post)
    result = await server.memory_recall(
        query="test",
        scope="project",
        memory_type=None,
        limit=10,
        search_mode="hybrid",
        sort_by="recency",
        include_related=False,
    )
    assert result["success"] is True
    assert post.call_args.args[0] == API_BASE_URL + "/internal/memory/recall"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer verified-bearer"


@pytest.mark.asyncio
async def test_reviewed_mcp_uses_configured_memory_authority_without_local_fallback(monkeypatch):
    from cli_agent_orchestrator.mcp_server import knowledge_tools, utils

    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    monkeypatch.setattr(memory_gateway, "get_local_bearer", lambda: "verified-bearer")
    monkeypatch.setattr(utils, "get_json", Mock(side_effect=AssertionError("wrong authority")))
    get = Mock(side_effect=requests.ConnectionError("secret-diagnostic"))
    monkeypatch.setattr(memory_gateway.requests, "get", get)
    result = await knowledge_tools.knowledge_read("record", "job", "grant", 1)
    assert result["ok"] is False
    assert result["code"] == "knowledge_authority_unavailable"
    assert "secret-diagnostic" not in str(result)
    assert get.call_args.args[0] == "http://memory-owner:9889/v1/knowledge/records/record"


@pytest.mark.asyncio
async def test_store_lesson_uses_the_configured_memory_authority(monkeypatch):
    from cli_agent_orchestrator.mcp_server import server

    monkeypatch.setenv("CAO_MEMORY_API_URL", "http://memory-owner:9889")
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.is_learning_enabled", lambda: True
    )
    monkeypatch.setattr(
        server, "_get_terminal_context_from_env", lambda: {"agent_profile": "developer"}
    )
    monkeypatch.setattr(
        memory_gateway.MemoryService,
        "store",
        Mock(side_effect=AssertionError("local write bypass")),
    )
    response = Mock(status_code=200)
    response.json.return_value = {"memory": _memory().model_dump(mode="json"), "action": "created"}
    post = Mock(return_value=response)
    monkeypatch.setattr(memory_gateway.requests, "post", post)
    result = await server.store_lesson(
        content="lesson", target_agent_profile="developer", key="lesson", tags=""
    )
    assert result["success"] is True
    assert post.call_args.args[0] == "http://memory-owner:9889/internal/memory/store"
    assert post.call_args.kwargs["json"]["scope"] == "agent"
