"""A configured renewable source never falls back to a stale environment token."""

import os
from pathlib import Path

import pytest

from cli_agent_orchestrator.security import auth


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://idp.example/jwks")
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN", "old-environment-token")
    path = tmp_path / "bearer.jwt"
    path.write_text("fresh-token")
    path.chmod(0o600)
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN_FILE", str(path))
    return path


def test_each_request_reads_atomic_replacement(source):
    assert auth.get_local_bearer() == "fresh-token"
    next_file = source.with_suffix(".next")
    next_file.write_text("new-token")
    next_file.chmod(0o600)
    next_file.replace(source)
    assert auth.get_local_bearer() == "new-token"


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "symlink",
        "public",
        "parent_public",
        "directory",
        "fifo",
        "large",
        "nonascii",
        "newline",
        "empty",
        "hardlink",
    ],
)
def test_invalid_source_has_no_environment_fallback(source, fault):
    if fault == "missing":
        source.unlink()
    elif fault == "symlink":
        target = source.with_suffix(".target")
        source.rename(target)
        source.symlink_to(target)
    elif fault == "public":
        source.chmod(0o644)
    elif fault == "parent_public":
        source.parent.chmod(0o755)
    elif fault == "directory":
        source.unlink()
        source.mkdir()
    elif fault == "fifo":
        source.unlink()
        os.mkfifo(source, 0o600)
    elif fault == "large":
        source.write_text("x" * 20000)
    elif fault == "nonascii":
        source.write_bytes(b"\xff")
    elif fault == "newline":
        source.write_text("one\ntwo")
    elif fault == "empty":
        source.write_text("")
    elif fault == "hardlink":
        os.link(source, source.with_suffix(".link"))
    assert auth.get_local_bearer() is None
    assert auth.local_auth_misconfig_error() is not None


def test_relative_source_rejected(source, monkeypatch):
    monkeypatch.chdir(source.parent)
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN_FILE", source.name)
    assert auth.get_local_bearer() is None


def test_disabled_auth_does_not_read_source(source, monkeypatch):
    monkeypatch.delenv("CAO_AUTH_JWKS_URI")
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    assert auth.get_local_bearer() is None


def test_file_failure_recovers_on_next_request_without_cached_bearer(source):
    source.unlink()
    assert auth.get_local_bearer() is None
    source.write_text("recovered-token")
    source.chmod(0o600)
    assert auth.get_local_bearer() == "recovered-token"


def test_wrong_owner_is_rejected(source, monkeypatch):
    from cli_agent_orchestrator.security import local_token

    original = local_token.os.fstat

    def foreign(fd):
        fields = list(original(fd))
        fields[4] += 1
        return os.stat_result(fields)

    monkeypatch.setattr(local_token.os, "fstat", foreign)
    assert auth.get_local_bearer() is None


def test_local_scope_gate_uses_current_file(source, monkeypatch):
    seen = []
    monkeypatch.setattr(
        auth, "extract_scopes_from_token", lambda token: seen.append(token) or ["cao:read"]
    )
    assert auth.get_scopes_for_local_token() == ["cao:read"]
    assert seen == ["fresh-token"]


def test_http_mutation_has_one_attempt_after_file_failure(source, monkeypatch):
    from types import SimpleNamespace

    import requests

    from cli_agent_orchestrator.mcp_server import utils

    source.unlink()
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(requests.HTTPError("401"))
        )

    monkeypatch.setattr(utils.requests, "post", post)
    with pytest.raises(requests.HTTPError):
        utils.post_body_json("/terminals/owned/input", {"message": "one task"})
    assert len(calls) == 1
    assert calls[0]["headers"] is None
