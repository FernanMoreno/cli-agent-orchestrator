# Lista de tareas (demo de colaboración)

Aplicación mínima en español: API Python estándar, frontend HTML/CSS/JavaScript y almacenamiento JSON local. Es un ejemplo independiente del runtime de CAO.

## Ejecutar

Desde `examples/agent-collaboration/tasks/`:

```bash
python3 api/server.py
```

Abre <http://127.0.0.1:8091/>. El servidor escucha solo en loopback por defecto. Para una ejecución dentro de un contenedor, `CAO_DEMO_HOST=0.0.0.0` permite escuchar dentro del contenedor; publica el puerto del host únicamente en loopback.

## Probar

Desde esta carpeta, con Python 3.10 o posterior y Node.js 18 o posterior:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/test_frontend.js
```

Las pruebas Python usan un archivo temporal para las mutaciones. La prueba Node usa solo módulos estándar y no necesita instalar paquetes.

## API

- `GET /api/health` → estado del servicio.
- `GET /api/tasks` → lista de tareas.
- `POST /api/tasks` con `{"title":"..."}` crea una tarea.
- `PATCH /api/tasks/{id}` con `{"completed":true}` la completa.

El contrato completo, incluidos límites UTF-8 y errores, está en [CONTRACT.md](CONTRACT.md). La interfaz limita el título a 200 unidades UTF-16; la API limita el tamaño completo del cuerpo a 16.384 bytes. La API rechaza títulos vacíos y no ofrece una transición de completada a pendiente.

## Datos locales

`data/tasks.json` es el archivo mutable de ejecución y está ignorado por Git. Si falta, el servidor inicia con una lista vacía; al crear o completar una tarea, escribe allí mediante reemplazo atómico. `data/tasks.seed.json` contiene solo `[]` como referencia de estado limpio y no se carga automáticamente. El `tasks.json` de la carpeta original no se incorporó.

## Procedencia

El código, los contratos y las pruebas se importaron de la demo creada el 2026-10-01. [COLLABORATION.md](COLLABORATION.md) es un registro histórico acotado, no una afirmación de autonomía completa. [SOURCE_AGENTS.md](SOURCE_AGENTS.md) conserva las instrucciones originales como procedencia; no actúa como AGENTS.md de este repositorio. La captura `demo-preview.png` muestra datos de prueba sintéticos.
