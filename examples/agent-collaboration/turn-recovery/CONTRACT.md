# Contrato HTTP

Servidor Python estándar en `0.0.0.0:8092`. Los datos persisten en `notes.json` del directorio actual.

- `GET /api/health` responde `200` con `{"ok":true}`.
- `GET /api/notes` responde `200` con una lista de notas.
- `GET /api/stats` responde `200` con `{"total":N,"pending":N,"completed":N}`. Los tres valores son enteros no negativos; `total = pending + completed`. Cuenta como completadas las notas con `done=true`; una lista vacía devuelve los tres contadores a cero. Lee los datos persistidos bajo el mismo lock que las demás operaciones.
- `POST /api/notes` acepta `{"text":"..."}` y responde `201` con `{id,text,done:false}`. `text` debe ser una cadena; se recortan los espacios de los extremos antes de validar y guardar. Su longitud resultante debe estar entre 1 y 2000 caracteres; valores vacíos, no string o excesivos reciben `400`.
- `PATCH /api/notes/{id}` acepta `{"done":true|false}`, `{"text":"..."}` o ambos campos y responde `200` con la nota actualizada `{id,text,done}`. `done` debe ser booleano; `text` debe ser una cadena que se recorta en los extremos y cuya longitud tras el recorte sea de 1 a 2000 caracteres. Conserva `id` y los campos omitidos (editar solo texto conserva `done`). Los campos desconocidos se ignoran si existe algún campo conocido. Un cuerpo vacío `{}`, sin campos conocidos, JSON inválido, cuerpo que no sea objeto o cualquier campo conocido inválido recibe `400` sin modificar datos: se valida todo antes de mutar, incluso si se envían ambos campos. Con un cuerpo válido, una nota inexistente recibe `404` sin modificar datos.
- `DELETE /api/notes/completed` no requiere cuerpo y responde siempre `200` con `{"deleted_count":N}` (entero no negativo). Elimina únicamente notas con `done=true`, conserva todas las pendientes y devuelve `{"deleted_count":0}` si no hay completadas, incluso con una lista vacía. No valida JSON ni requiere un ID; la ausencia de completadas no produce `404`. Esta ruta se resuelve antes del borrado individual; `completed` es un segmento reservado para esta operación, no un ID de nota.
- `DELETE /api/notes/{id}` responde `200` con `{"id":"...","deleted":true}`; si la nota no existe, responde `404`.
- Las operaciones de lectura-modificación-escritura se protegen con un lock dentro del proceso del servidor. El JSON se publica mediante reemplazo atómico de un archivo temporal en el mismo directorio, evitando pérdidas de actualizaciones entre solicitudes concurrentes.
- JSON inválido o campos no válidos reciben `400`; rutas/notas desconocidas reciben `404`.
- `GET /` entrega `frontend/index.html`; recursos con rutas relativas se sirven desde `frontend/` sin permitir escapar de ese directorio.

## Validación aislada

Ejecutar `PYTHONDONTWRITEBYTECODE=1 python -m unittest -v test_api`. Las pruebas usan un servidor HTTP real en un puerto libre y un archivo dentro de un directorio temporal; no modifican el `notes.json` del proyecto. Cubren notas mezcladas, ninguna completada, lista vacía, borrado individual y la prioridad del segmento reservado `completed`, además de edición y concurrencia.
