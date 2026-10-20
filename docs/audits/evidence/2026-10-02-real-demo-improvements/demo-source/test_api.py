"""Pruebas HTTP aisladas: nunca usan el notes.json del proyecto."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from api import server


class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass


class APIContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.notes_file = Path(self.temporary.name) / 'notes.json'
        self.storage = patch.object(server, 'NOTES_FILE', self.notes_file)
        self.storage.start()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.storage.stop()
        self.temporary.cleanup()

    def request(self, method, path='/api/notes', data=None, raw=None):
        connection = HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=10)
        try:
            body = raw if raw is not None else (json.dumps(data) if data is not None else None)
            connection.request(method, path, body, {'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.assertEqual(response.getheader('Content-Type'), 'application/json; charset=utf-8')
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_crud_and_persistence(self):
        self.assertEqual(self.request('GET', '/api/health'), (200, {'ok': True}))
        self.assertEqual(self.request('GET'), (200, []))
        status, note = self.request('POST', data={'text': '  Hola \n'})
        self.assertEqual(status, 201)
        self.assertEqual(set(note), {'id', 'text', 'done'})
        self.assertEqual(note['text'], 'Hola')
        self.assertIs(note['done'], False)
        path = '/api/notes/' + note['id']
        updated = dict(note, done=True)
        self.assertEqual(self.request('PATCH', path, {'done': True}), (200, updated))
        self.assertEqual(json.loads(self.notes_file.read_text()), [updated])
        # Una nueva instancia de servidor debe recuperar el mismo archivo.
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()
        self.assertEqual(self.request('GET'), (200, [updated]))
        self.assertEqual(self.request('DELETE', path), (200, {'id': note['id'], 'deleted': True}))
        self.assertEqual(self.request('DELETE', path)[0], 404)
        self.assertEqual(self.request('PATCH', path, {'done': False})[0], 404)
        self.assertEqual(self.request('DELETE', '/api/notes')[0], 404)
        self.assertEqual(self.request('GET'), (200, []))
        self.assertEqual(json.loads(self.notes_file.read_text()), [])

    def test_text_validation(self):
        invalid = [{}, {'text': ''}, {'text': ' \n\t '}, {'text': None},
                   {'text': 7}, {'text': True}, {'text': []}, {'text': {}},
                   {'text': 'x' * 2001}, [], None]
        for data in invalid:
            with self.subTest(data=data):
                self.assertEqual(self.request('POST', data=data)[0], 400)
        self.assertEqual(self.request('POST', raw='{')[0], 400)
        self.assertEqual(self.request('GET'), (200, []))
        for text in [' x ', ' ' + 'é' * 2000 + '\n']:
            status, note = self.request('POST', data={'text': text})
            self.assertEqual(status, 201)
            self.assertEqual(note['text'], text.strip())
        for data in [{}, {'done': 1}, {'done': 'true'}]:
            self.assertEqual(self.request('PATCH', '/api/notes/missing', data)[0], 400)
        self.assertEqual(len(json.loads(self.notes_file.read_text())), 2)

    def test_delete_only_target_and_invalid_patch_keeps_state(self):
        notes = [self.request('POST', data={'text': f'N{i}'})[1] for i in range(3)]
        target = notes[1]['id']
        # Un PATCH inválido sobre una nota existente no la modifica.
        self.assertEqual(self.request('PATCH', '/api/notes/' + target, {'done': 'true'})[0], 400)
        self.assertEqual(self.request('PATCH', '/api/notes/' + target, raw='{')[0], 400)
        self.assertEqual(self.request('GET'), (200, notes))
        self.assertEqual(self.request('DELETE', '/api/notes/' + target),
                         (200, {'id': target, 'deleted': True}))
        self.assertEqual(self.request('GET'), (200, [notes[0], notes[2]]))
        self.assertEqual(json.loads(self.notes_file.read_text()), [notes[0], notes[2]])

    def test_frontend_served_and_no_traversal(self):
        for path, kind in [('/', 'text/html'), ('/app.js', 'application/javascript'),
                           ('/styles.css', 'text/css')]:
            connection = HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=10)
            connection.request('GET', path)
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 200, path)
            self.assertTrue(response.getheader('Content-Type').startswith(kind), path)
        for path in ['/../notes.json', '/..%2fnotes.json', '/nope.js']:
            self.assertEqual(self.request('GET', path)[0], 404, path)

    def test_concurrent_creation(self):
        count = 24
        barrier = threading.Barrier(8)

        def create(index):
            barrier.wait(timeout=10)
            return self.request('POST', data={'text': f'Nota {index}'})

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(create, range(count)))
        self.assertTrue(all(status == 201 for status, _ in results))
        ids = {note['id'] for _, note in results}
        self.assertEqual(len(ids), count)
        status, notes = self.request('GET')
        self.assertEqual(status, 200)
        self.assertEqual({note['id'] for note in notes}, ids)
        self.assertEqual({note['text'] for note in notes}, {f'Nota {i}' for i in range(count)})
        self.assertEqual(json.loads(self.notes_file.read_text()), notes)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [self.notes_file])


if __name__ == '__main__':
    unittest.main()
