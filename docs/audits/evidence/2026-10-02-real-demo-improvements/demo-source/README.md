# Notas compartidas CAO

Aplicación local de notas implementada mediante colaboración entre tres proveedores en CAO (CLI Agent Orchestrator).

## Características

- **Agregar notas**: Escribe una nota y presiona "Añadir"
- **Marcar como completada**: Usa el checkbox para marcar una nota como hecha
- **Persistencia**: Las notas se guardan en `notes.json` y se restauran al recargar la página
- **Eliminar notas**: botón × por nota (`DELETE /api/notes/{id}`)
- **Búsqueda, filtro y contadores**: búsqueda por texto, filtro Todas/Pendientes/Completadas y contadores de total y pendientes
- **Validación**: texto de 1 a 2000 caracteres tras recortar espacios (el input tiene `maxlength="2000"`); los errores se muestran en una región `role="alert"`
- **Interfaz en español**: Toda la aplicación está en español

## Estructura

```
.
├── api/server.py          # Servidor HTTP en Python (stdlib)
├── frontend/
│   ├── index.html         # HTML principal (español)
│   ├── styles.css         # Estilos CSS
│   └── app.js             # Lógica JavaScript (fetch, rendering)
├── CONTRACT.md            # Contrato HTTP entre proveedores
├── notes.json             # Persistencia de notas (creado al arrancar)
└── README.md              # Este archivo
```

## Arrancar la aplicación

```bash
python3 api/server.py
```

El servidor escucha en `0.0.0.0:8092`. Abre [http://localhost:8092](http://localhost:8092) en el navegador.

## API

### `GET /api/health`
Verifica que el servidor está activo.
```json
{"ok": true}
```

### `GET /api/notes`
Devuelve la lista de notas.
```json
[
  {"id": "abc...", "text": "Nota ejemplo", "done": false},
  {"id": "def...", "text": "Completada", "done": true}
]
```

### `POST /api/notes`
Crea una nueva nota.
```
Body: {"text": "Mi nota"}
Response (201): {"id": "xyz...", "text": "Mi nota", "done": false}
```

### `PATCH /api/notes/{id}`
Actualiza el estado de una nota.
```
Body: {"done": true}
Response (200): {"id": "xyz...", "text": "Mi nota", "done": true}
```

### `DELETE /api/notes/{id}`
Elimina una nota.
```
Response (200): {"id": "xyz...", "deleted": true}   # 404 si no existe
```

## Pruebas

```bash
python3 -m unittest -v test_api
```

Las pruebas usan un `notes.json` temporal (nunca el del proyecto). Cubren CRUD, persistencia, validación de texto (1–2000), DELETE/PATCH inválidos, concurrencia y servicio estático sin traversal. No hay pruebas automáticas de navegador; la verificación en Chromium es manual/externa.

## Implementación

- **Backend**: Python 3 con `http.server` (stdlib)
- **Frontend**: HTML5, CSS3, JavaScript vanilla (sin dependencias)
- **Persistencia**: JSON en archivo local
- **Seguridad**: Protección contra directory traversal en rutas relativas
