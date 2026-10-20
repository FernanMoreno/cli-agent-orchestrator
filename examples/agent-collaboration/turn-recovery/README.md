# Notas compartidas (demo de recuperación de turnos)

Aplicación local de notas en español, creada como una demo de colaboración CAO. El ejemplo contiene una API Python estándar y frontend HTML/CSS/JavaScript; no depende del runtime CAO para ejecutarse.

## Ejecutar

Desde `examples/agent-collaboration/turn-recovery/`:

```bash
python3 api/server.py
```

Abre <http://127.0.0.1:8092/> desde la misma máquina. **El código original enlaza el servidor a `0.0.0.0:8092` y no proporciona autenticación**; eso lo hace accesible desde otras interfaces de red. Úsalo solo en un entorno local/de confianza y no publiques el puerto en una red compartida. Esta advertencia documenta el comportamiento del código sin cambiarlo.

## Probar

Desde esta carpeta, con Python 3.10 o posterior y Node.js 18 o posterior:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m unittest -v test_api
node --check frontend/app.js
```

La suite usa un archivo `notes.json` temporal y un servidor HTTP en loopback; no toca el archivo de ejecución de este ejemplo. No hay pruebas automáticas de navegador ni dependencias npm.

## Funciones y contrato

La interfaz permite crear, buscar, filtrar, editar, completar y eliminar notas, borrar las completadas y ver contadores. La API ofrece `GET /api/health`, `/api/notes`, `/api/stats`, además de `POST`, `PATCH`, `DELETE /api/notes/{id}` y `DELETE /api/notes/completed`. Consulta [CONTRACT.md](CONTRACT.md) para validación, respuestas y persistencia.

## Datos locales

`notes.json` es el archivo mutable de ejecución y está ignorado por Git. Si no existe, la aplicación presenta una lista vacía; la primera mutación lo crea. `notes.seed.json` contiene únicamente `[]` como referencia de estado limpio y no se carga automáticamente. El `notes.json` externo no se incorporó.

## Procedencia y auditorías

El código, contrato, pruebas y capturas se conservaron. `COLLABORATION.md` y los documentos de [auditoría](audits/AUDIT_INDEX.md) son registros históricos. En particular, las auditorías iniciales de backend/frontend tienen un alcance anterior a las funciones actuales; no reemplazan la fuente ni las pruebas de este ejemplo.
