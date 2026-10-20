"""Exact reviewed Bubblewrap build profiles stay statically allowlisted."""

from __future__ import annotations

import os

import pytest

from cli_agent_orchestrator.backends import bubblewrap_backend
from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

_REPRODUCED_PROFILE_SHA256 = "18a513db177974d371d2c1fbf9d6f8f5b2bcb43565e242e5a7b4bdc4886e4c39"


def test_reproduced_profile_is_static_and_unknown_digest_is_rejected_before_probe(
    tmp_path, monkeypatch
):
    assert _REPRODUCED_PROFILE_SHA256 in bubblewrap_backend._BWRAP_SHA256_ALLOWLIST

    candidate = tmp_path / "bwrap"
    candidate.write_bytes(b"unreviewed Bubblewrap profile")
    descriptor = os.open(candidate, os.O_RDONLY | os.O_CLOEXEC)
    monkeypatch.setattr(
        bubblewrap_backend,
        "_verified_bwrap_descriptor",
        lambda _path: descriptor,
    )
    probe_calls = []
    monkeypatch.setattr(
        bubblewrap_backend.subprocess,
        "run",
        lambda *args, **kwargs: probe_calls.append((args, kwargs)),
    )

    with pytest.raises(UnsupportedWorkEnforcement, match="SHA-256 digest is not allowlisted"):
        bubblewrap_backend._probe_bwrap_version(candidate)

    assert probe_calls == []
