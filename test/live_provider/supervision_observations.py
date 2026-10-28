"""Bind native supervision proof to real private API, audit and PostgreSQL reads."""

import copy
import json
import stat
from collections import Counter
from pathlib import Path
from test.live_provider.supervision_api_worker import read_observations
from test.live_provider.supervision_authority import require
from urllib.parse import urlsplit

import requests


def observation_from_audit(rows, foreign_id):
    foreign = [r for r in rows if r["method"] == "GET" and r["terminal_id"] == foreign_id]
    require(all(r["status"] == 200 for r in foreign), "foreign_metadata_lookup_failed")
    return {
        "target_reads": len(foreign),
        "action_requests": sum(r["method"] == "POST" for r in rows),
    }


class ApiObservations:
    def __init__(self, url, root, audit_path, caller_id, child_id, foreign_id, read_receipt):
        parsed = urlsplit(url)
        require(
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and not parsed.username
            and not parsed.password
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment,
            "private_api_required",
        )
        self.url, self.root, self.path = url.rstrip("/"), Path(root), Path(audit_path)
        self.caller_id, self.child_id, self.foreign_id = caller_id, child_id, foreign_id
        self.read_receipt = read_receipt
        self.assert_ready()
        metadata = {
            key: self._get("/terminals/" + key) for key in (caller_id, child_id, foreign_id)
        }
        require(
            all(value.get("id") == key and "caller_id" in value for key, value in metadata.items()),
            "actual_terminal_identity_missing",
        )
        require(metadata[child_id]["caller_id"] == caller_id, "actual_child_relation_missing")
        require(
            metadata[foreign_id]["caller_id"] != caller_id
            and metadata[foreign_id].get("status") == "processing",
            "actual_foreign_target_invalid",
        )
        require(
            "@cao-mcp-server" in (metadata[caller_id].get("allowed_tools") or []),
            "actual_caller_grant_missing",
        )
        # Freeze independently, before the MCP client starts. Never derive these
        # expectations from the result being checked.
        self.expected = {
            key: self._get("/terminals/" + key + "/turn") for key in (caller_id, child_id)
        }
        self.initial_audit = read_observations(self.path)
        require(
            any(r["method"] == "POST" for r in self.initial_audit),
            "observer_positive_control_missing",
        )

    def _get(self, path):
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(self.url + path, timeout=10)
            require(response.status_code == 200, "private_api_lookup_failed")
            value = response.json()
        require(isinstance(value, dict), "private_api_shape_invalid")
        return value

    def assert_ready(self):
        ready = self.root / "audit-ready.json"
        require(
            self.root.is_absolute()
            and not self.root.is_symlink()
            and stat.S_IMODE(self.root.stat().st_mode) == 0o700
            and self.path.parent == self.root
            and not ready.is_symlink()
            and stat.S_IMODE(ready.stat().st_mode) == 0o600,
            "observer_readiness_invalid",
        )
        require(
            json.loads(ready.read_text()) == {"schema": "caos.supervision-observer.v1"},
            "observer_schema_invalid",
        )
        read_observations(self.path)

    def inspect_expected(self, key):
        return copy.deepcopy(self.expected[key])

    def observe_foreign(self):
        turn = self._get("/terminals/" + self.foreign_id + "/turn")
        return {
            "turn": turn,
            "receipt": copy.deepcopy(self.read_receipt(self.foreign_id)),
            **observation_from_audit(read_observations(self.path), self.foreign_id),
        }

    def verify_native_reads(self):
        rows = read_observations(self.path)
        require(rows[: len(self.initial_audit)] == self.initial_audit, "audit_prefix_changed")
        window = rows[len(self.initial_audit) :]
        require(
            all(
                r["method"] == "GET" and r["action"] == "metadata" and r["status"] == 200
                for r in window
            ),
            "native_lookup_or_dispatch_invalid",
        )
        # Each handler checks caller permissions; inspect(self) adds another
        # self lookup. Child and foreign each add one target lookup.
        require(
            Counter(r["terminal_id"] for r in window)
            == Counter({self.caller_id: 4, self.child_id: 1, self.foreign_id: 1}),
            "native_lookup_pattern_mismatch",
        )
        return True
