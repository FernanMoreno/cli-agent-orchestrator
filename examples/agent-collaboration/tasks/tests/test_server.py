"""Regression checks for concurrent JSON API writes (standard library only)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "api"))
import server  # noqa: E402


class ConcurrentTaskWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.previous_data_file = server.DATA_FILE
        self.previous_load_tasks = server.load_tasks
        server.DATA_FILE = Path(self.temporary_directory.name) / "tasks.json"
        self.load_state_lock = threading.Lock()
        self.load_state = {"active": 0, "maximum": 0}
        original_load_tasks = server.load_tasks

        def delayed_load_tasks():
            with self.load_state_lock:
                self.load_state["active"] += 1
                self.load_state["maximum"] = max(
                    self.load_state["maximum"], self.load_state["active"]
                )
            try:
                tasks = original_load_tasks()
                time.sleep(0.04)
                return tasks
            finally:
                with self.load_state_lock:
                    self.load_state["active"] -= 1

        server.load_tasks = delayed_load_tasks
        quiet_handler = type(
            "QuietHandler",
            (server.Handler,),
            {"log_message": lambda *_args: None},
        )
        self.http_server = ThreadingHTTPServer(("127.0.0.1", 0), quiet_handler)
        self.server_thread = threading.Thread(
            target=self.http_server.serve_forever, daemon=True
        )
        self.server_thread.start()

    def tearDown(self) -> None:
        self.http_server.shutdown()
        self.http_server.server_close()
        self.server_thread.join(timeout=2)
        server.DATA_FILE = self.previous_data_file
        server.load_tasks = self.previous_load_tasks
        self.temporary_directory.cleanup()

    def test_parallel_creates_keep_every_task_and_return_success(self) -> None:
        count = 12
        ready = threading.Barrier(count)

        def create_task(index: int) -> int:
            ready.wait(timeout=5)
            body = json.dumps({"title": f"Concurrent task {index}"}).encode("utf-8")
            request = Request(
                f"http://127.0.0.1:{self.http_server.server_port}/api/tasks",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urlopen(request, timeout=5) as response:
                    return response.status
            except HTTPError as error:
                status = error.code
                error.close()
                return status

        with ThreadPoolExecutor(max_workers=count) as pool:
            statuses = list(pool.map(create_task, range(count)))

        saved_tasks = json.loads(server.DATA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(statuses, [201] * count)
        self.assertEqual(len(saved_tasks), count)
        self.assertEqual(len({task["id"] for task in saved_tasks}), count)
        self.assertEqual(self.load_state["maximum"], 1)

    def test_invalid_persisted_task_returns_a_json_server_error(self) -> None:
        server.DATA_FILE.write_text(
            json.dumps([{"id": 0, "title": "Invalid ID", "completed": False}]),
            encoding="utf-8",
        )
        cases = (
            ("GET", "/api/tasks", None, "No se pudieron leer las tareas"),
            ("POST", "/api/tasks", {"title": "Nueva"}, "No se pudo guardar la tarea"),
            (
                "PATCH",
                "/api/tasks/1",
                {"completed": True},
                "No se pudo actualizar la tarea",
            ),
        )
        for method, path, payload, expected_error in cases:
            body_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
            request = Request(
                f"http://127.0.0.1:{self.http_server.server_port}{path}",
                data=body_bytes,
                headers={"Content-Type": "application/json"} if body_bytes else {},
                method=method,
            )
            try:
                with urlopen(request, timeout=5) as response:
                    status = response.status
                    response_body = json.loads(response.read())
            except HTTPError as error:
                status = error.code
                response_body = json.loads(error.read())
                error.close()

            self.assertEqual(status, 500, method)
            self.assertEqual(response_body, {"error": expected_error}, method)

    def test_patch_rejects_integer_one_as_completed_boolean(self) -> None:
        server.DATA_FILE.write_text(
            json.dumps([{"id": 1, "title": "Pending", "completed": False}]),
            encoding="utf-8",
        )
        request = Request(
            f"http://127.0.0.1:{self.http_server.server_port}/api/tasks/1",
            data=b'{"completed":1}',
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urlopen(request, timeout=5) as response:
                status = response.status
                response_body = json.loads(response.read())
        except HTTPError as error:
            status = error.code
            response_body = json.loads(error.read())
            error.close()

        self.assertEqual(status, 400)
        self.assertEqual(response_body, {"error": "Se esperaba completed: true"})
        self.assertFalse(json.loads(server.DATA_FILE.read_text(encoding="utf-8"))[0]["completed"])

    def test_oversized_task_id_is_reported_as_missing(self) -> None:
        server.DATA_FILE.write_text(
            json.dumps([{"id": 1, "title": "Existing", "completed": False}]),
            encoding="utf-8",
        )
        request = Request(
            f"http://127.0.0.1:{self.http_server.server_port}/api/tasks/{'9' * 5000}",
            data=b'{"completed":true}',
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urlopen(request, timeout=5) as response:
                status = response.status
                response_body = json.loads(response.read())
        except HTTPError as error:
            status = error.code
            response_body = json.loads(error.read())
            error.close()

        self.assertEqual(status, 404)
        self.assertEqual(response_body, {"error": "Tarea no encontrada"})

    def request_body(self, body):
        request = Request(
            f"http://127.0.0.1:{self.http_server.server_port}/api/tasks",
            data=body, headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_non_utf8_requests_are_rejected_without_writing_data(self):
        for encoding in ("utf-16", "utf-32"):
            with self.subTest(encoding=encoding):
                status, result = self.request_body(json.dumps({"title": "Nueva"}).encode(encoding))
                self.assertEqual(status, 400)
                self.assertIn("error", result)
                self.assertFalse(server.DATA_FILE.exists())

    def test_invalid_utf8_returns_json_error_instead_of_dropping_connection(self):
        status, result = self.request_body(b'{"title":"\xff"}')
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertFalse(server.DATA_FILE.exists())

    def test_unpaired_unicode_surrogate_is_a_client_error_without_file_effects(self):
        status, result = self.request_body(b'{"title":"\\ud800"}')
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertFalse(server.DATA_FILE.exists())
        self.assertFalse(server.DATA_FILE.with_suffix(".json.tmp").exists())

    def test_deeply_nested_request_returns_json_client_error(self):
        body = b'{"title":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'
        status, result = self.request_body(body)
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertFalse(server.DATA_FILE.exists())

    def test_deeply_nested_persisted_data_returns_json_server_errors(self):
        before = b'[' * 20000 + b'0' + b']' * 20000
        server.DATA_FILE.write_bytes(before)
        cases = (("GET", "/api/tasks", None), ("POST", "/api/tasks", b'{"title":"Nueva"}'),
                 ("PATCH", "/api/tasks/1", b'{"completed":true}'))
        for method, path, body in cases:
            with self.subTest(method=method):
                request = Request(f"http://127.0.0.1:{self.http_server.server_port}{path}",
                                  data=body, headers={"Content-Type": "application/json"}, method=method)
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=5)
                error = caught.exception
                try:
                    self.assertEqual(error.code, 500)
                    self.assertIn("error", json.loads(error.read()))
                finally:
                    error.close()
                self.assertEqual(server.DATA_FILE.read_bytes(), before)

    def test_body_limit_counts_utf8_bytes_and_preserves_data_on_rejection(self):
        overhead = len(b'{"title":""}')
        for title in ("x" * (16384 - overhead), "ñ" * ((16384 - overhead) // 2)):
            with self.subTest(multibyte=title.startswith("ñ")):
                body = json.dumps({"title": title}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.assertEqual(len(body), 16384)
                status, result = self.request_body(body)
                self.assertEqual(status, 201)
                self.assertEqual(result["task"]["title"], title)
                before = server.DATA_FILE.read_bytes()
                status, result = self.request_body(body[:-2] + b'x"}')
                self.assertEqual(status, 400)
                self.assertIn("error", result)
                self.assertEqual(server.DATA_FILE.read_bytes(), before)

    def test_server_bind_host_defaults_to_loopback_and_can_be_overridden(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.server_bind_host(), "127.0.0.1")

        with patch.dict(os.environ, {"CAO_DEMO_HOST": "0.0.0.0"}, clear=True):
            self.assertEqual(server.server_bind_host(), "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
