"""Offline contract tests for the portable MCP receipt/cleanup boundary."""

from __future__ import annotations

from test.live_provider import mcp_stdio_cleanup as operator
from test.live_provider.mcp_stdio_cleanup import (
    NativeReceipt,
    OperatorFailure,
    cleanup_diagnostic_properties,
    cleanup_owned,
    receipt_rows,
)
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database

_PARENT_ID = "1234abcd"
_RECEIPT_ID = "7c16ef2a-0130-4f71-8c14-3bf9de85d200"
_CHILD_TERMINAL_ID = "a1b2c3d4"


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


def _public_receipt(**updates):
    row = {
        "id": _RECEIPT_ID,
        "parent_terminal_id": _PARENT_ID,
        "terminal_id": _CHILD_TERMINAL_ID,
        "state": "succeeded",
        "cleanup_completed_at": "2026-10-15T00:00:00Z",
        "provider": "opencode_cli",
        "agent_profile": "worker",
        "error_kind": None,
        "error_summary": None,
    }
    row.update(updates)
    return row


def test_receipt_rows_preserve_uuid_and_parent_from_public_shape():
    rows = receipt_rows(
        {
            "success": True,
            "parent_terminal_id": _PARENT_ID,
            "children": [_public_receipt(output="PRIVATE_OUTPUT_SENTINEL")],
        },
        _PARENT_ID,
    )
    assert rows == (NativeReceipt(_RECEIPT_ID, _PARENT_ID, _CHILD_TERMINAL_ID, "succeeded", True),)
    assert "PRIVATE_OUTPUT_SENTINEL" not in repr(rows)


@pytest.mark.parametrize(
    "updates",
    [
        {"id": "a1b2c3d4"},
        {"parent_terminal_id": "8765abcd"},
        {"cleanup_completed_at": "__missing__"},
    ],
)
def test_receipt_rows_reject_incomplete_or_unowned_public_rows(updates):
    row = _public_receipt(**updates)
    if updates.get("cleanup_completed_at") == "__missing__":
        row.pop("cleanup_completed_at")
    with pytest.raises(OperatorFailure):
        receipt_rows(
            {
                "success": True,
                "parent_terminal_id": _PARENT_ID,
                "children": [row],
            },
            _PARENT_ID,
        )


def test_cleanup_deletes_only_parent_scoped_public_receipts(monkeypatch):
    import requests

    calls = []

    class Response:
        def __init__(self, status, payload=None):
            self.status_code, self.payload = status, payload

        def json(self):
            return self.payload

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        assert kwargs["allow_redirects"] is False
        assert max(kwargs["timeout"]) <= 3
        if method == "GET" and url.endswith(f"/terminals/{_PARENT_ID}/children"):
            return Response(200, [_public_receipt(state="running", cleanup_completed_at=None)])
        if method == "GET" and "/sessions/" in url:
            return Response(404)
        return Response(204)

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    assert cleanup_owned(
        "http://127.0.0.1:12345",
        _PARENT_ID,
        "mcp-stdio-0123456789ab",
        diagnostic=diagnostic,
    )
    assert diagnostic == []
    assert cleanup_diagnostic_properties(diagnostic) == ("none", "none", "none")
    assert [url for method, url in calls if method == "DELETE"] == [
        "http://127.0.0.1:12345/terminals/a1b2c3d4",
        f"http://127.0.0.1:12345/terminals/{_PARENT_ID}",
        "http://127.0.0.1:12345/sessions/mcp-stdio-0123456789ab",
    ]


def test_cleanup_skips_completed_receipt_but_deletes_active_child(monkeypatch):
    import requests

    calls = []

    class Response:
        def __init__(self, status, payload=None):
            self.status_code, self.payload = status, payload

        def json(self):
            return self.payload

    completed = _public_receipt(state="succeeded")
    active = _public_receipt(
        id="8c16ef2a-0130-4f71-8c14-3bf9de85d201",
        terminal_id="b1b2c3d4",
        state="running",
        cleanup_completed_at=None,
    )

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        assert kwargs["allow_redirects"] is False
        assert max(kwargs["timeout"]) <= 3
        if method == "GET" and url.endswith(f"/terminals/{_PARENT_ID}/children"):
            return Response(200, [completed, active])
        if method == "DELETE" and url.endswith(f"/terminals/{_CHILD_TERMINAL_ID}"):
            return Response(409)
        if method == "GET" and "/sessions/" in url:
            return Response(404)
        return Response(204)

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    assert cleanup_owned(
        "http://127.0.0.1:12345",
        _PARENT_ID,
        "mcp-stdio-0123456789ab",
        diagnostic=diagnostic,
    )
    assert diagnostic == []
    assert [url for method, url in calls if method == "DELETE"] == [
        "http://127.0.0.1:12345/terminals/b1b2c3d4",
        f"http://127.0.0.1:12345/terminals/{_PARENT_ID}",
        "http://127.0.0.1:12345/sessions/mcp-stdio-0123456789ab",
    ]


def test_cleanup_does_not_delete_children_without_verified_parent_list(monkeypatch):
    import requests

    calls = []

    class Response:
        def __init__(self, status, payload=None):
            self.status_code, self.payload = status, payload

        def json(self):
            return self.payload

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        if method == "GET" and url.endswith(f"/terminals/{_PARENT_ID}/children"):
            return Response(503)
        if method == "GET" and "/sessions/" in url:
            return Response(404)
        return Response(204)

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    assert not cleanup_owned(
        "http://127.0.0.1:12345",
        _PARENT_ID,
        "mcp-stdio-0123456789ab",
        diagnostic=diagnostic,
    )
    assert diagnostic == [("children_list", "http_status", 503)]
    assert cleanup_diagnostic_properties(diagnostic) == ("children_list", "http_status", "503")
    deleted = [url for method, url in calls if method == "DELETE"]
    assert deleted == [
        f"http://127.0.0.1:12345/terminals/{_PARENT_ID}",
        "http://127.0.0.1:12345/sessions/mcp-stdio-0123456789ab",
    ]


@pytest.mark.parametrize(
    ("failed_stage", "failure_kind", "status_code"),
    [
        ("children_list", "http_status", 503),
        ("children_list", "request_exception", None),
        ("child_delete", "http_status", 409),
        ("child_delete", "http_status", 503),
        ("child_delete", "request_exception", None),
        ("parent_delete", "http_status", 503),
        ("parent_delete", "request_exception", None),
        ("session_delete", "http_status", 503),
        ("session_delete", "request_exception", None),
        ("session_absence_poll", "http_status", 503),
        ("session_absence_poll", "request_exception", None),
    ],
)
def test_cleanup_diagnostic_classifies_first_failed_http_boundary_without_extra_requests(
    monkeypatch,
    failed_stage,
    failure_kind,
    status_code,
):
    import requests

    class Response:
        def __init__(self, status, payload=None):
            self.status_code, self.payload = status, payload

        def json(self):
            return self.payload

    api = "http://127.0.0.1:12345"
    calls = []

    def request_stage(method, url):
        if method == "GET" and url.endswith(f"/terminals/{_PARENT_ID}/children"):
            return "children_list"
        if method == "DELETE" and url.endswith(f"/terminals/{_CHILD_TERMINAL_ID}"):
            return "child_delete"
        if method == "DELETE" and url.endswith(f"/terminals/{_PARENT_ID}"):
            return "parent_delete"
        if method == "DELETE" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            return "session_delete"
        if method == "GET" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            return "session_absence_poll"
        raise AssertionError("unexpected_cleanup_request")

    def fake_request(method, url, **kwargs):
        stage = request_stage(method, url)
        calls.append(stage)
        assert kwargs["allow_redirects"] is False
        assert max(kwargs["timeout"]) <= 3
        if stage == failed_stage:
            if failure_kind == "request_exception":
                raise requests.ConnectionError()
            return Response(status_code)
        if stage == "children_list":
            children = (
                [_public_receipt(state="running", cleanup_completed_at=None)]
                if failed_stage == "child_delete"
                else []
            )
            return Response(200, children)
        if stage == "session_absence_poll":
            return Response(404)
        return Response(204)

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    result = cleanup_owned(api, _PARENT_ID, "mcp-stdio-0123456789ab", diagnostic=diagnostic)

    expected_calls = ["children_list"]
    if failed_stage == "child_delete":
        expected_calls.append("child_delete")
    expected_calls.extend(["parent_delete", "session_delete"])
    expected_calls.append("session_absence_poll")
    assert result is False
    assert calls == expected_calls
    assert diagnostic == [(failed_stage, failure_kind, status_code)]
    assert cleanup_diagnostic_properties(diagnostic) == (
        failed_stage,
        failure_kind,
        str(status_code) if status_code is not None else "none",
    )


def test_cleanup_diagnostic_classifies_receipt_parse_without_deleting_unverified_children(
    monkeypatch,
):
    import requests

    class Response:
        def __init__(self, status, payload=None):
            self.status_code, self.payload = status, payload

        def json(self):
            return self.payload

    calls = []

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith(f"/terminals/{_PARENT_ID}/children"):
            calls.append("children_list")
            return Response(200, [{"not_a_receipt": True}])
        if method == "DELETE" and url.endswith(f"/terminals/{_PARENT_ID}"):
            calls.append("parent_delete")
            return Response(204)
        if method == "DELETE" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            calls.append("session_delete")
            return Response(204)
        if method == "GET" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            calls.append("session_absence_poll")
            return Response(404)
        raise AssertionError("unexpected_cleanup_request")

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    result = cleanup_owned(
        "http://127.0.0.1:12345", _PARENT_ID, "mcp-stdio-0123456789ab", diagnostic=diagnostic
    )
    assert result is False
    assert calls == ["children_list", "parent_delete", "session_delete", "session_absence_poll"]
    assert diagnostic == [("children_list", "receipt_parse", 200)]
    assert cleanup_diagnostic_properties(diagnostic) == ("children_list", "receipt_parse", "200")


def test_cleanup_diagnostic_classifies_deadline_before_request_without_extra_http(monkeypatch):
    import requests

    assert operator.CLEANUP_SECONDS == 25.0

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return []

    clock = _FakeClock()
    monkeypatch.setattr(
        operator,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep),
    )
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, "children_list"))
        clock.advance(operator.CLEANUP_SECONDS + 0.001)
        return Response()

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    result = cleanup_owned(
        "http://127.0.0.1:12345", _PARENT_ID, "mcp-stdio-0123456789ab", diagnostic=diagnostic
    )
    assert result is False
    assert calls == [("GET", "children_list")]
    assert diagnostic == [("parent_delete", "deadline", None)]
    assert cleanup_diagnostic_properties(diagnostic) == ("parent_delete", "deadline", "none")


def test_cleanup_diagnostic_classifies_absence_poll_deadline_with_last_status(monkeypatch):
    import requests

    class Response:
        def __init__(self, status, payload=None):
            self.status_code, self.payload = status, payload

        def json(self):
            return self.payload

    clock = _FakeClock()
    monkeypatch.setattr(
        operator,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep),
    )
    calls = []

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith(f"/terminals/{_PARENT_ID}/children"):
            calls.append("children_list")
            return Response(200, [])
        if method == "DELETE" and url.endswith(f"/terminals/{_PARENT_ID}"):
            calls.append("parent_delete")
            return Response(204)
        if method == "DELETE" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            calls.append("session_delete")
            return Response(204)
        if method == "GET" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            calls.append("session_absence_poll")
            clock.advance(operator.CLEANUP_SECONDS + 0.001)
            return Response(200)
        raise AssertionError("unexpected_cleanup_request")

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    result = cleanup_owned(
        "http://127.0.0.1:12345", _PARENT_ID, "mcp-stdio-0123456789ab", diagnostic=diagnostic
    )
    assert result is False
    assert calls == ["children_list", "parent_delete", "session_delete", "session_absence_poll"]
    assert diagnostic == [("session_absence_poll", "deadline", 200)]
    assert cleanup_diagnostic_properties(diagnostic) == (
        "session_absence_poll",
        "deadline",
        "200",
    )


@pytest.mark.parametrize(
    "api,parent_id,session_name",
    [
        ("http://localhost:12345", _PARENT_ID, "mcp-stdio-0123456789ab"),
        ("http://127.0.0.1:12345", "not-a-parent", "mcp-stdio-0123456789ab"),
        ("http://127.0.0.1:12345", _PARENT_ID, "not-a-session"),
    ],
)
def test_cleanup_diagnostic_classifies_invalid_inputs_without_http(
    monkeypatch, api, parent_id, session_name
):
    import requests

    def unexpected_request(*args, **kwargs):
        raise AssertionError("unexpected_cleanup_request")

    monkeypatch.setattr(requests, "request", unexpected_request)
    diagnostic = []
    result = cleanup_owned(api, parent_id, session_name, diagnostic=diagnostic)
    assert result is False
    assert diagnostic == [("input_validation", "invalid_input", None)]
    assert cleanup_diagnostic_properties(diagnostic) == (
        "input_validation",
        "invalid_input",
        "none",
    )


def test_cleanup_diagnostic_keeps_first_failed_boundary_without_extra_requests(monkeypatch):
    import requests

    calls = []

    class Response:
        status_code = 503

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith(f"/terminals/{_PARENT_ID}/children"):
            stage = "children_list"
        elif method == "DELETE" and url.endswith(f"/terminals/{_PARENT_ID}"):
            stage = "parent_delete"
        elif method == "DELETE" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            stage = "session_delete"
        elif method == "GET" and url.endswith("/sessions/mcp-stdio-0123456789ab"):
            stage = "session_absence_poll"
        else:
            raise AssertionError("unexpected_cleanup_request")
        calls.append(stage)
        return Response()

    monkeypatch.setattr(requests, "request", fake_request)
    diagnostic = []
    result = cleanup_owned(
        "http://127.0.0.1:12345",
        _PARENT_ID,
        "mcp-stdio-0123456789ab",
        diagnostic=diagnostic,
    )
    assert result is False
    assert calls == [
        "children_list",
        "parent_delete",
        "session_delete",
        "session_absence_poll",
    ]
    assert diagnostic == [("children_list", "http_status", 503)]
    assert cleanup_diagnostic_properties(diagnostic) == ("children_list", "http_status", "503")
