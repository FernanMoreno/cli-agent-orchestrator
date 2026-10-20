# Validation guide

Prerequisites: Docker local; no recursos del operador reutilizados; imagen PG oficial fijada por digest, test credentials privados, namespace por test/worker. Configurar targets con archivos privados; véase [configuración PostgreSQL](../../docs/postgresql.md).

## Instalación y recuperación PostgreSQL

Instalación nueva administrada, desde el checkout:

```bash
./install.sh
```

La instalación nueva usa PostgreSQL gestionado o targets externos configurados
explícitamente. CAOS no importa ni convierte archivos SQLite. Las opciones y
comandos retirados, incluidos `--import-sqlite` e `import-sqlite`, se rechazan
antes de acceder a rutas, crear directorios, adquirir locks o conectarse a la
base de datos. Este error explícito no añade avisos ni permisos a una instalación
nueva normal. Si encuentra estado existente desconocido, falla cerrado y deja
intactos los archivos.

Para realizar un backup PostgreSQL personal:

```bash
python scripts/personal_deployment.py --root /ruta/privada/perfil backup /ruta/privada/backup-pg
```

La restauración requiere un destino distinto y una configuración privada de
PostgreSQL. Verifica el backup y los tres scopes antes de activar:

```bash
python scripts/personal_deployment.py --root /ruta/nuevo/perfil restore /ruta/privada/backup-pg --database-config /ruta/privada/postgres-config.json
python scripts/personal_deployment.py --root /ruta/nuevo/perfil verify-database
```

Los recibos PostgreSQL históricos se conservan como procedencia de sólo lectura.
Los bundles OKF modernos en directorio y tar siguen disponibles en formato
SQLite-free; un bundle legacy con `sqlite-dump.json` se rechaza y permanece
intacto.

## Validación

El fixture de pruebas prepara PostgreSQL desechable. Un servicio obligatorio
ausente debe causar un error, no un skip; véase [guía del servicio de pruebas](../../test/postgres/README.md).

```bash
uv run pytest -o addopts= -m '' --no-cov test/clients/test_postgres.py
uv run pytest -o addopts= -m '' test/
npm --prefix web run test
npm --prefix web run test:e2e -- e2e/questions-inbox.spec.ts --workers=1
project-composition-check "$(cat .ai/project-name)"
```

Estos son comandos de validación, no resultados de aceptación. Los resultados
reales y los requisitos de host se registran en [validation.md](validation.md).

1. Inicializar target vacío, repetir bootstrap y verificar fingerprints; modificar esquema en test privado y observar rechazo.
2. Crear sesión/agente/workflow/pregunta, contestar y comprobar accepted/returned/ack por API/MCP/AG-UI; reiniciar y verificar estado.
3. Dos conexiones/procesos compiten por respuesta/claim; sólo una intención efectiva, historial exacto.
4. Capturar los scopes PostgreSQL, restaurar en destino aislado, comparar registros, secuencias y recibos históricos; comprobar que el backup original permanece intacto.
5. Instalar managed real y external-config mode, verificar credenciales redacted, backup/restore aislado, rechazo de estado desconocido sin mutación y rollback compatible.
6. Ejecutar suites completas, web/apps/browser, Docker/host aplicables, coverage ratchet con ambos reportes, project-composition-check, composition review, diff review, Graphify isolated y verification.

Registrar cada comando/exit/log/hash y skips sin declararlos verdes. La aceptación previa SQLite spec016 permanece histórica.
