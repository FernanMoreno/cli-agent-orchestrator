"""Owned transport adapters against local synthetic HTTP and tiny capture children."""

import json
import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import native_phase_readers as readers

SECRET = "private-never-export"


@contextmanager
def api(
    body,
    delay=0,
    drip=False,
    encoded=False,
    state=None,
    status=200,
    extra_headers=(),
    send_length=True,
    content_type="application/json",
):
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            content = body
            if state is not None and "/output" not in self.path:
                content = json.dumps(state() if callable(state) else state).encode()
            self.send_response(status)
            if send_length:
                self.send_header("Content-Length", str(len(content)))
            if content_type is not None:
                self.send_header("Content-Type", content_type)
            if encoded:
                self.send_header("Content-Encoding", "gzip")
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            try:
                if drip:
                    for byte in content:
                        if stopped.wait(0.02):
                            break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                else:
                    stopped.wait(delay)
                    if not stopped.is_set():
                        self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class ReaderTests(unittest.TestCase):
    def make(self, url="http://127.0.0.1:12345", verify=lambda: True):
        return readers.OwnedPhaseReaders(
            url, "/unused/socket", {"abc12345": {"session": "owned", "pane": "pane"}}, verify
        )

    def test_state_binds_terminal_session_and_pane(self):
        good = dict(
            id="abc12345",
            session_name="owned",
            name="pane",
            status="completed",
            turn_sequence=1,
            turn_completed=1,
        )
        for key in (None, "id", "session_name", "name"):
            data = good if key is None else {**good, key: "other"}
            with api(json.dumps(data).encode()) as url:
                if key:
                    with self.assertRaisesRegex(ValueError, "terminal_binding"):
                        self.make(url).read_state("abc12345", time.monotonic() + 2)
                else:
                    self.assertEqual(
                        self.make(url).read_state("abc12345", time.monotonic() + 2)[
                            "turn_completed"
                        ],
                        1,
                    )

    def test_durable_turn_projection_is_owned_bounded_and_does_not_export_extra_fields(self):
        good = dict(
            terminal_id="abc12345",
            generation="a" * 32,
            state="verified",
            attempts=0,
            allowed_actions=[],
            private_extra=SECRET,
        )
        with api(json.dumps(good).encode()) as url:
            value = self.make(url).read_turn("abc12345", time.monotonic() + 2)
            self.assertNotIn(SECRET, json.dumps(value))
            self.assertEqual(value["state"], "verified")
        with api(json.dumps({**good, "terminal_id": "other"}).encode()) as url:
            with self.assertRaisesRegex(ValueError, "terminal_turn_binding"):
                self.make(url).read_turn("abc12345", time.monotonic() + 2)
        with api(b" " * 8193) as url:
            with self.assertRaisesRegex(ValueError, "http_body_limit"):
                self.make(url).read_turn("abc12345", time.monotonic() + 2)

    def frame_reader(self, url, verify=lambda: True):
        reader = self.make(url, verify)
        reader._turns["abc12345"] = ("completed", 1, 1)
        reader.read_state = lambda *args: dict(
            http_status=200, status="completed", turn_sequence=1, turn_completed=1
        )
        return reader

    def test_frame_turn_change_after_output_fails_closed(self):
        good = dict(
            id="abc12345",
            session_name="owned",
            name="pane",
            status="completed",
            turn_sequence=1,
            turn_completed=1,
        )
        states = iter([good, good, {**good, "turn_sequence": 2, "turn_completed": 2}])
        with api(b'{"output":"ACK","mode":"last"}', state=lambda: next(states)) as url:
            reader = self.make(url)
            reader.read_state("abc12345", time.monotonic() + 2)
            with self.assertRaisesRegex(ValueError, "terminal_turn_changed"):
                reader.read_output("abc12345", time.monotonic() + 2, max_bytes=65537)

    def test_output_preserves_exact_ack_whitespace_and_ignores_proxy(self):
        with (
            api(json.dumps({"output": " ACK\n", "mode": "last"}).encode()) as url,
            patch.dict(os.environ, {"HTTP_PROXY": "http://invalid:1"}),
        ):
            self.assertEqual(
                self.frame_reader(url).read_output(
                    "abc12345", time.monotonic() + 2, max_bytes=65537
                ),
                " ACK\n",
            )

    def test_wire_length_is_rejected_before_body_materialization(self):
        with api(b"x" * 1000000) as url:
            with self.assertRaisesRegex(ValueError, "http_body_limit"):
                self.frame_reader(url).read_output("abc12345", time.monotonic() + 2, max_bytes=100)

    def test_trickle_does_not_extend_absolute_deadline(self):
        with api(b"{" * 1000, drip=True) as url:
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "reader_deadline"):
                self.frame_reader(url).read_output("abc12345", started + 0.1, max_bytes=65537)
            self.assertLess(time.monotonic() - started, 0.4)

    def test_compression_is_rejected(self):
        with api(b"{}", encoded=True) as url:
            with self.assertRaisesRegex(ValueError, "http_framing"):
                self.make(url).read_state("abc12345", time.monotonic() + 2)

    def test_http_409_exposes_only_allowlisted_turn_state(self):
        for state in ("verified", "reconcile", "cancelling", "cancelled"):
            body = json.dumps(
                {
                    "detail": {
                        "state": state,
                        "generation": SECRET,
                        "reason": SECRET,
                        "unexpected": {"receipt_nonce": SECRET},
                    }
                }
            ).encode()
            with self.subTest(state=state), api(body, status=409) as url:
                try:
                    readers.http_json_bytes(
                        url,
                        "/terminals/abc/output?mode=last",
                        time.monotonic() + 2,
                        8192,
                    )
                except readers.HTTPStatusError as caught:
                    # unittest.assertRaises clears traceback frames on exit.
                    # Inspect the actual exception before that can hide data.
                    error = caught
                    traceback = error.__traceback__
                    reader_locals = []
                    while traceback is not None:
                        if traceback.tb_frame.f_code.co_name == "http_json_bytes":
                            reader_locals.append(repr(traceback.tb_frame.f_locals))
                        traceback = traceback.tb_next
                else:
                    self.fail("409 did not raise HTTPStatusError")
                self.assertEqual(error.status_code, 409)
                self.assertEqual(error.turn_state, state)
                self.assertEqual(error.args, ("http_status",))
                self.assertEqual(str(error), "http_status")
                self.assertNotIn(SECRET, repr(error) + repr(vars(error)))
                self.assertIsNone(error.__context__)
                self.assertIsNone(error.__cause__)
                self.assertTrue(reader_locals)
                self.assertNotIn(SECRET, "".join(reader_locals))

    def test_http_409_malformed_unknown_and_unframed_details_keep_status(self):
        responses = (
            (b'{"detail":{"state":"unknown"}}', (), True, None, 8192),
            (
                b'{"detail":{"state":"reconcile","generation":"' + SECRET.encode() + b'"',
                (),
                True,
                None,
                8192,
            ),
            (
                b'{"detail":{"state":"verified","state":"reconcile"}}',
                (),
                True,
                None,
                8192,
            ),
            (b"x" * 4097, (), True, None, 8192),
            (
                b'{"detail":{"state":"verified"}}',
                (("X-Receipt", "one"), ("x-receipt", "two")),
                True,
                None,
                8192,
            ),
            (
                b'{"detail":{"state":"verified"}}',
                (("Transfer-Encoding", "chunked"),),
                True,
                None,
                8192,
            ),
            (b'{"detail":{"state":"verified"}}', (), False, None, 8192),
            (
                b'{"detail":{"state":"verified"}}',
                (("Content-Length", "31"),),
                True,
                None,
                8192,
            ),
            (b'{"detail":{"state":"verified"}}', (), True, "text/plain", 8192),
            (b'{"detail":{"state":"verified"}}', (), True, None, 8),
        )
        for body, headers, send_length, content_type, wire_limit in responses:
            with self.subTest(headers=headers, send_length=send_length, wire_limit=wire_limit):
                with api(
                    body,
                    status=409,
                    extra_headers=headers,
                    send_length=send_length,
                    content_type=content_type,
                ) as url:
                    with self.assertRaises(readers.HTTPStatusError) as caught:
                        readers.http_json_bytes(
                            url,
                            "/terminals/abc/output?mode=last",
                            time.monotonic() + 2,
                            wire_limit,
                        )
                self.assertEqual(caught.exception.status_code, 409)
                self.assertIsNone(caught.exception.turn_state)
                self.assertEqual(caught.exception.args, ("http_status",))
                self.assertNotIn(SECRET, repr(caught.exception) + repr(vars(caught.exception)))
                self.assertIsNone(caught.exception.__context__)

    def test_non_409_http_errors_keep_legacy_status_and_no_turn_state(self):
        with api(b'{"detail":{"state":"verified"}}', status=202) as url:
            with self.assertRaises(readers.HTTPStatusError) as caught:
                readers.http_json_bytes(
                    url,
                    "/terminals/abc/output?mode=last",
                    time.monotonic() + 2,
                    8192,
                )
        self.assertEqual(caught.exception.status_code, 202)
        self.assertIsNone(caught.exception.turn_state)
        self.assertEqual(caught.exception.args, ("http_status",))

    def test_409_optional_body_read_keeps_original_deadline(self):
        body = b'{"detail":{"state":"verified"}}'
        with api(body, delay=0.5, status=409) as url:
            started = time.monotonic()
            with self.assertRaises(readers.HTTPStatusError) as caught:
                readers.http_json_bytes(
                    url,
                    "/terminals/abc/output?mode=last",
                    started + 0.15,
                    8192,
                )
            elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIsNone(caught.exception.turn_state)
        self.assertLess(elapsed, 0.4)

    def test_ownership_and_expired_deadline_prevent_io(self):
        for verify, stop in (
            (lambda: False, time.monotonic() + 2),
            (lambda: True, time.monotonic() - 1),
        ):
            with patch.object(readers, "http_json_bytes") as get:
                with self.assertRaises(ValueError):
                    self.make(verify=verify).read_state("abc12345", stop)
                get.assert_not_called()

    def test_post_read_identity_change_is_rejected(self):
        with api(b'{"output":"ACK","mode":"last"}') as url:
            with self.assertRaisesRegex(ValueError, "reader_ownership"):
                self.frame_reader(
                    url, verify=unittest.mock.Mock(side_effect=[True, False])
                ).read_output("abc12345", time.monotonic() + 2, max_bytes=65537)

    def test_unsafe_url_and_unknown_terminal_fail_closed(self):
        for url in (
            "https://remote.test",
            "http://user:secret@127.0.0.1:1",
            "http://127.0.0.1:1/path",
        ):
            with self.assertRaises(ValueError):
                readers.OwnedPhaseReaders(url, "/unused", {}, lambda: True)
        with self.assertRaisesRegex(ValueError, "reader_scope"):
            self.make().read_state(SECRET, time.monotonic() + 2)

    def test_capture_subprocess_is_incrementally_bounded(self):
        with self.assertRaisesRegex(ValueError, "capture_byte_limit"):
            readers.capture_bytes(
                [sys.executable, "-c", 'import os;os.write(1,b"x"*1000000)'],
                time.monotonic() + 2,
                100,
            )

    def test_capture_timeout_reaps_owned_child(self):
        made = []
        original = readers.subprocess.Popen

        def create(*a, **kw):
            child = original(*a, **kw)
            made.append(child)
            return child

        with patch.object(readers.subprocess, "Popen", side_effect=create):
            with self.assertRaisesRegex(ValueError, "capture_deadline"):
                readers.capture_bytes(
                    [sys.executable, "-c", "import time;time.sleep(5)"], time.monotonic() + 0.1, 100
                )
        self.assertIsNotNone(made[0].returncode)
        self.assertTrue(made[0].stdout.closed)

    def test_selector_setup_failure_still_reaps_owned_child(self):
        made = []
        original = readers.subprocess.Popen

        def create(*a, **kw):
            child = original(*a, **kw)
            made.append(child)
            return child

        with (
            patch.object(readers.subprocess, "Popen", side_effect=create),
            patch.object(readers.selectors, "DefaultSelector", side_effect=OSError("synthetic")),
        ):
            with self.assertRaises(OSError):
                readers.capture_bytes(
                    [sys.executable, "-c", "import time;time.sleep(5)"], time.monotonic() + 2, 100
                )
        self.assertIsNotNone(made[0].returncode)
        self.assertTrue(made[0].stdout.closed)

    def test_capture_preserves_exact_bytes_and_checks_exit(self):
        self.assertEqual(
            readers.capture_bytes(
                [sys.executable, "-c", 'print("ACK")'], time.monotonic() + 2, 100
            ),
            b"ACK\n",
        )
        with self.assertRaisesRegex(ValueError, "capture_exit"):
            readers.capture_bytes(
                [sys.executable, "-c", "raise SystemExit(1)"], time.monotonic() + 2, 100
            )


if __name__ == "__main__":
    unittest.main()
