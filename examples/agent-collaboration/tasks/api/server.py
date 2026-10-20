"""Small standard-library JSON API for the tasks demo."""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "tasks.json"
TASK_ROUTE = re.compile(r"^/api/tasks/(\d+)$")
FRONTEND_DIR = ROOT / "frontend"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
TASKS_LOCK = threading.Lock()
MAX_REQUEST_BYTES = 16_384


def server_bind_host() -> str:
    """Keep direct local runs private; containers can opt into wildcard binding."""
    return os.environ.get("CAO_DEMO_HOST", "127.0.0.1").strip() or "127.0.0.1"


def load_tasks() -> list[dict]:
    try:
        value = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except RecursionError as exc:
        raise ValueError("El archivo de tareas contiene JSON demasiado anidado") from exc
    if not isinstance(value, list):
        raise ValueError("El archivo de tareas debe contener una lista")
    seen_ids: set[int] = set()
    for task in value:
        if not isinstance(task, dict) or set(task) != {"id", "title", "completed"}:
            raise ValueError("Cada tarea debe tener id, title y completed")
        task_id = task["id"]
        title = task["title"]
        if (
            isinstance(task_id, bool)
            or not isinstance(task_id, int)
            or task_id <= 0
            or task_id in seen_ids
            or not isinstance(title, str)
            or not title.strip()
            or title != title.strip()
            or not isinstance(task["completed"], bool)
        ):
            raise ValueError("El archivo contiene una tarea inválida")
        seen_ids.add(task_id)
    return value


def save_tasks(tasks: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DATA_FILE)


class Handler(BaseHTTPRequestHandler):
    server_version = "DemoTasks/1.0"

    @staticmethod
    def known_route(path: str) -> bool:
        return path in {"/api/health", "/api/tasks"} or TASK_ROUTE.fullmatch(path) is not None

    def method_not_allowed(self) -> None:
        self.send_json(405, {"error": "Método no permitido"})

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Se esperaba un objeto JSON válido"})
            return None
        if not isinstance(value, dict):
            self.send_json(400, {"error": "Se esperaba un objeto JSON"})
            return None
        return value

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            file_path = (FRONTEND_DIR / filename).resolve()
            if file_path.parent != FRONTEND_DIR.resolve():
                self.send_json(404, {"error": "Ruta no encontrada"})
                return
            try:
                with open(file_path, "rb") as static_file:
                    body = static_file.read()
            except OSError:
                self.send_json(404, {"error": "Ruta no encontrada"})
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/health":
            self.send_json(200, {"status": "ok"})
            return
        if path == "/api/tasks":
            try:
                self.send_json(200, {"tasks": load_tasks()})
            except (OSError, ValueError, json.JSONDecodeError):
                self.send_json(500, {"error": "No se pudieron leer las tareas"})
            return
        if self.known_route(path):
            self.method_not_allowed()
            return
        self.send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/api/tasks":
            if self.known_route(path):
                self.method_not_allowed()
                return
            self.send_json(404, {"error": "Ruta no encontrada"})
            return
        data = self.read_json()
        if data is None:
            return
        title = data.get("title")
        if not isinstance(title, str) or not title.strip() or set(data) != {"title"}:
            self.send_json(400, {"error": "El campo title es obligatorio"})
            return
        try:
            title.encode("utf-8")
        except UnicodeEncodeError:
            self.send_json(400, {"error": "El campo title debe contener texto Unicode válido"})
            return
        try:
            with TASKS_LOCK:
                tasks = load_tasks()
                task = {"id": max((item["id"] for item in tasks), default=0) + 1,
                        "title": title.strip(), "completed": False}
                tasks.append(task)
                save_tasks(tasks)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            self.send_json(500, {"error": "No se pudo guardar la tarea"})
            return
        self.send_json(201, {"task": task})

    def do_PATCH(self) -> None:
        path = urlsplit(self.path).path
        match = TASK_ROUTE.fullmatch(path)
        if not match:
            if self.known_route(path):
                self.method_not_allowed()
                return
            self.send_json(404, {"error": "Ruta no encontrada"})
            return
        data = self.read_json()
        if data is None:
            return
        if set(data) != {"completed"} or data["completed"] is not True:
            self.send_json(400, {"error": "Se esperaba completed: true"})
            return
        try:
            task_id = int(match.group(1))
        except ValueError:
            self.send_json(404, {"error": "Tarea no encontrada"})
            return
        try:
            with TASKS_LOCK:
                tasks = load_tasks()
                task = next((item for item in tasks if item["id"] == task_id), None)
                if task is not None:
                    task["completed"] = True
                    save_tasks(tasks)
            if task is None:
                self.send_json(404, {"error": "Tarea no encontrada"})
                return
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            self.send_json(500, {"error": "No se pudo actualizar la tarea"})
            return
        self.send_json(200, {"task": task})

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # BaseHTTPRequestHandler otherwise emits an HTML 501 for unknown verbs.
        if code == 501:
            if self.known_route(urlsplit(self.path).path):
                self.method_not_allowed()
            else:
                self.send_json(404, {"error": "Ruta no encontrada"})
            return
        self.send_json(code, {"error": message or "Error interno"})

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))


if __name__ == "__main__":
    ThreadingHTTPServer((server_bind_host(), 8091), Handler).serve_forever()
