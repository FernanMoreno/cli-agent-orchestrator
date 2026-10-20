"""Servidor HTTP pequeño para la aplicación de notas."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import uuid
import tempfile
from threading import RLock


ROOT = Path.cwd()
NOTES_FILE = ROOT / "notes.json"
FRONTEND = ROOT / "frontend"
NOTES_LOCK = RLock()


def load_notes():
    if not NOTES_FILE.exists():
        return []
    try:
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_notes(notes):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=NOTES_FILE.parent,
            prefix=".notes-", suffix=".json", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(notes, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, NOTES_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                return None
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return None

    def do_GET(self):
        if self.path == "/api/health":
            return self.send_json(200, {"ok": True})
        if self.path == "/api/notes":
            with NOTES_LOCK:
                notes = load_notes()
            return self.send_json(200, notes)
        if self.path == "/api/stats":
            with NOTES_LOCK:
                notes = load_notes()
                completed = sum(note["done"] is True for note in notes)
                stats = {"total": len(notes), "pending": len(notes) - completed, "completed": completed}
            return self.send_json(200, stats)
        if self.path == "/":
            path = FRONTEND / "index.html"
        else:
            # Rutas relativas (styles.css, app.js) sin permitir salir de frontend/.
            relative = self.path.lstrip("/")
            if not relative or ".." in Path(relative).parts:
                return self.send_json(404, {"error": "No encontrado"})
            path = FRONTEND / relative
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(FRONTEND.resolve())
            if not resolved.is_file():
                raise FileNotFoundError
            body = resolved.read_bytes()
        except (OSError, ValueError):
            return self.send_json(404, {"error": "No encontrado"})
        content_type = "text/html; charset=utf-8" if resolved.suffix == ".html" else (
            "text/css; charset=utf-8" if resolved.suffix == ".css" else
            "application/javascript; charset=utf-8" if resolved.suffix == ".js" else
            "application/octet-stream"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/notes":
            return self.send_json(404, {"error": "No encontrado"})
        data = self.read_json()
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            return self.send_json(400, {"error": "text debe ser una cadena de 1 a 2000 caracteres"})
        text = data["text"].strip()
        if not 1 <= len(text) <= 2000:
            return self.send_json(400, {"error": "text debe tener entre 1 y 2000 caracteres"})
        note = {"id": uuid.uuid4().hex, "text": text, "done": False}
        with NOTES_LOCK:
            notes = load_notes()
            notes.append(note)
            save_notes(notes)
        self.send_json(201, note)

    def do_PATCH(self):
        match = re.fullmatch(r"/api/notes/([^/]+)", self.path)
        if not match:
            return self.send_json(404, {"error": "No encontrado"})
        data = self.read_json()
        if not isinstance(data, dict) or not {"done", "text"}.intersection(data):
            return self.send_json(400, {"error": "Se requiere done y/o text"})
        updates = {}
        if "done" in data:
            if not isinstance(data["done"], bool):
                return self.send_json(400, {"error": "done debe ser booleano"})
            updates["done"] = data["done"]
        if "text" in data:
            if not isinstance(data["text"], str):
                return self.send_json(400, {"error": "text debe ser una cadena de 1 a 2000 caracteres"})
            text = data["text"].strip()
            if not 1 <= len(text) <= 2000:
                return self.send_json(400, {"error": "text debe tener entre 1 y 2000 caracteres"})
            updates["text"] = text
        with NOTES_LOCK:
            notes = load_notes()
            for note in notes:
                if note.get("id") == match.group(1):
                    note.update(updates)
                    save_notes(notes)
                    return self.send_json(200, note)
        self.send_json(404, {"error": "Nota no encontrada"})

    def do_DELETE(self):
        # Resolver el segmento reservado antes de buscar un ID individual.
        if self.path == "/api/notes/completed":
            with NOTES_LOCK:
                notes = load_notes()
                pending = [note for note in notes if note.get("done") is not True]
                deleted_count = len(notes) - len(pending)
                save_notes(pending)
            return self.send_json(200, {"deleted_count": deleted_count})
        match = re.fullmatch(r"/api/notes/([^/]+)", self.path)
        if not match:
            return self.send_json(404, {"error": "No encontrado"})
        note_id = match.group(1)
        with NOTES_LOCK:
            notes = load_notes()
            for index, note in enumerate(notes):
                if note.get("id") == note_id:
                    del notes[index]
                    save_notes(notes)
                    return self.send_json(200, {"id": note_id, "deleted": True})
        self.send_json(404, {"error": "Nota no encontrada"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8092), Handler).serve_forever()
