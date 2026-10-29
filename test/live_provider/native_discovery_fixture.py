"""Owned live mock peers seeded through API independently of native discovery."""

import json
import os
from pathlib import Path
from test.e2e import test_real_provider_matrix as matrix
from test.live_provider.native_cancel_support import sha
from test.live_provider.native_read_bridge import (
    DISCOVERY_EXPECTATION,
    validate_discovery_expectation,
)

PROFILE = "t229_mock"


def write_expectation(path, value):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_mock_profile(home):
    store = Path(home) / ".aws/cli-agent-orchestrator/agent-store"
    store.mkdir(parents=True, exist_ok=True)
    # mock_cli is a supported test runtime, intentionally absent from the
    # user-installable provider catalogue. Load a plain owned store profile.
    (store / (PROFILE + ".md")).write_text(
        "---\nname: t229_mock\ndescription: Owned discovery fixture\n"
        "provider: mock_cli\nrole: developer\nallowedTools: []\n---\nFixture only.\n"
    )


class OwnedDiscoveryFixture:
    def __init__(self, server, home, session, caller, group, sample, *, variant=""):
        self.server = server
        self.home = Path(home)
        self.session = session
        self.caller = caller
        self.group = group
        self.sample = sample
        self.extra_sessions = []
        self.records = []
        self.expectation = None
        self.digest = None
        self.variant = variant

    def request(self, method, tid, suffix="", **kwargs):
        response = matrix._request(method, self.server.url + "/terminals/" + tid + suffix, **kwargs)
        assert response.status_code == 200
        return response.json()

    def configure(self, tid, group, label, expected_session):
        updated = self.request("PATCH", tid, "/group", json={"group": group})
        assert updated["group"] == group and updated["session_name"] == expected_session
        metadata = {"fixture": label}
        updated = self.request("PATCH", tid, "/metadata", json={"metadata": metadata})
        assert updated["metadata"] == metadata
        record = self.request("GET", tid)
        assert record["group"] == group and record["session_name"] == expected_session
        assert record["metadata"] == metadata and record["status"] in {"idle", "completed"}
        self.records.append(
            {"id": tid, "session": expected_session, "group": group, "metadata": metadata}
        )
        return {"id": tid, "group": group, "metadata": metadata}

    def setup(self):
        write_mock_profile(self.home)
        matching = matrix._create_native_child(
            self.server,
            session_name=self.session,
            parent_id=self.caller,
            provider="mock_cli",
            profile=PROFILE,
        )
        self.sample()
        row = self.configure(matching, self.group + ["worker"], "matching", self.session)
        wrong_group = matrix._create_native_child(
            self.server,
            session_name=self.session,
            parent_id=self.caller,
            provider="mock_cli",
            profile=PROFILE,
        )
        self.sample()
        self.configure(
            wrong_group, [self.group[0], self.group[1] + "0"], "wrong-group", self.session
        )
        other_session, session = matrix._create_parent(self.server, "mock_cli", PROFILE)
        self.extra_sessions.append(session)
        self.sample()
        assert session != self.session
        self.configure(other_session, self.group, "other-session", session)
        value = {
            "schema": "caos.native-discovery-fixture.v1",
            "caller_id": self.caller,
            "expected": [row],
            "excluded_ids": [self.caller, wrong_group, other_session],
        }
        if self.variant:
            from test.live_provider.native_discovery_cases import build_discovery_expectation

            value = build_discovery_expectation(self.caller, self.records, self.variant)
        self.expectation = validate_discovery_expectation(value, self.caller)
        path = self.home / DISCOVERY_EXPECTATION
        write_expectation(path, self.expectation)
        self.digest = sha(path)
        return {
            "expected_count": len(self.expectation["expected"]),
            "decoy_count": len(self.expectation["excluded_ids"]) - 1,
            "expectation_sha256": self.digest,
            "mock_records": self.records,
        }

    def assert_live(self):
        self.sample()
        for record in self.records:
            current = self.request("GET", record["id"])
            assert current["status"] in {"idle", "completed"}
            assert current["session_name"] == record["session"]
            assert current["group"] == record["group"] and current["metadata"] == record["metadata"]

    def cleanup(self):
        for session in reversed(self.extra_sessions):
            matrix._delete_session(self.server, session)
        self.extra_sessions.clear()
