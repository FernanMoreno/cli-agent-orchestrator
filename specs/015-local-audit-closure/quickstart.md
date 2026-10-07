# Quickstart: Validar cierre de brechas locales

**Fecha**: 2026-10-07\
**Spec**: [spec.md](spec.md)

Esta guía es para la etapa de implementación. Las ejecuciones actuales se registran en `implementation-evidence.md` y en los informes por historia; los comandos siguientes no acreditan por sí solos un resultado. Las pruebas con proveedores reales necesitan perfiles aislados y disponibilidad de cuenta/modelo.

## Prerrequisitos

- Repositorio CAO y varias instancias en la misma PC y entorno de ejecución.
- Herramientas ya usadas por el proyecto: uv, Node/npm y Rust/Cargo para el TUI.
- Copia desechable de un proyecto para pruebas que puedan escribir.
- Perfil de autenticación aislado para cada prueba real de proveedor.
- No imprimir ni copiar secretos a logs o a los artefactos de evidencia.

## Límites de aceptación

- Autenticación y lectura de respuesta Web: presupuesto total de 10 segundos y cancelación al cambiar la generación de sesión.
- Ticket de flujo/terminal: 30 segundos y un solo uso; cada reconexión obtiene uno nuevo y mantiene el último cursor confirmado.
- Observación: backoff 1/2/4/8/16/30 segundos; después, reintentos cada 60 segundos hasta recuperar la observación, conservando el cursor de paginación.
- Plugins no verificados: revisión o instalación deshabilitada permitida; habilitación solo tras aprobación explícita del contenido exacto, sin concesión automática de permisos.
- Compatibilidad anunciada: Python 3.10, 3.11, 3.12, 3.13 y 3.14.

La configuración predeterminada de pytest excluye `integration` y `e2e`. Para aceptación entre procesos se debe seleccionar expresamente su prueba con `-m integration`; una prueba deseleccionada no cuenta como ejecutada. Las pruebas de cuentas reales se ejecutan por separado y no se habilitan al ejecutar controles locales de aislamiento.

## Validación focalizada por historia

Desde la raíz del repositorio:

    uv run --no-sync pytest test/services/test_local_peer_registry.py test/services/test_local_peer_auth.py test/services/test_local_peer_recovery.py test/services/test_work_reservations.py test/mcp_server/test_local_peer_transport.py test/api/test_local_coordination_routes.py

Para autorización y transporte de eventos, credenciales y logs:

    uv run --no-sync pytest test/api/test_events_endpoints.py test/api/test_agui_auth_hardening.py test/api/test_agui_stream_reconnect.py test/api/test_ws_auth.py test/api/test_workflow_events_sse.py test/utils/test_logging.py test/utils/test_orchestration.py

Compatibilidad del ejemplo AG-UI con autorización por header:

    uv run --no-sync pytest --no-cov test/test_agui_showcase_transport.py

Aceptación con dos servidores reales y el proveedor determinista sin cuenta externa:

    uv run --no-sync pytest --no-cov -m integration test/integration/test_local_peer_two_process.py

Desde web:

    npm test -- --run src/test/browser-auth.test.ts src/test/api.test.ts src/test/workflow-sse.test.ts src/test/workflow-api.test.ts

Desde cao_mcp_apps:

    npm test -- --run src/test/integration.test.tsx src/test/lifecycle.test.tsx

Para workflows y configuración, desde la raíz:

    uv run --no-sync pytest test/services/test_continuation_responsiveness.py test/services/test_terminal_observation_recovery.py test/services/test_integration_008_continuation.py test/models/test_workflow.py

Para release, CI, manifiesto y plugins:

    uv run --no-sync pytest test/test_integration_008_release_policy.py test/test_integration_008_ci_policy.py test/e2e/test_real_provider_matrix.py test/agent_plugins/test_resolver.py test/agent_plugins/test_provider_provenance.py test/agent_plugins/test_no_auto_grant.py test/agent_plugins/test_cli.py test/scripts/test_build_release_manifest.py

Para TUI, desde la raíz:

    cargo test --manifest-path tui/Cargo.toml

Al terminar cambios significativos, ejecutar también desde la raíz:

    project-composition-check "$(cat .ai/project-name)"

    uv run --no-sync mypy src/

Los archivos de prueba nuevos ya forman parte de la implementación. Ejecutar también las suites completas aplicables; los resultados históricos de la auditoría no sustituyen esas ejecuciones. Usar `TMPDIR` en un volumen con espacio disponible; para sockets tmux, elegir una ruta corta y un `--basetemp` hijo como se indica abajo. La evidencia distingue los controles locales comprobados de las pruebas remotas no ejecutadas; el bloqueo histórico del gate de tipos ya está corregido.

## Aceptación local de dos instancias

1. Crear una copia temporal del proyecto y dos o más perfiles CAO separados.
2. Iniciar las instancias en el mismo entorno local y vincularlas explícitamente al mismo proyecto con permisos mínimos.
3. Enviar una tarea de solo lectura; observar aceptación y resultado en ambas instancias.
4. Repetirla con la misma clave de idempotencia y confirmar que no aparece una tarea duplicada.
5. Cancelar otra tarea durante su ejecución; revisar estado, recibo y reserva antes y después de que cese toda escritura.
6. Desconectar y reconectar cada cliente de eventos mientras se generan eventos; comparar identificadores desde el último cursor confirmado.
7. Revocar un par e intentar una operación sin autorización; comprobar el rechazo y la continuidad de la instancia local.
8. Detener un par durante la consulta de sesiones y distinguir el proceso no disponible de una lista vacía.
9. Comparar hashes del proyecto temporal con el original y verificar que no hubo cambios fuera del alcance de la prueba.

## Publicación y proveedores

1. Probar el candidato de release mediante los disparadores manual y programado.
2. Repetir con control fallido, ausente, omitido y asociado a contenido distinto; cada caso debe bloquear publicación.
3. Iniciar una prueba real solo con el hogar aislado configurado.
4. Repetir sin hogar aislado; debe detenerse antes de leer credenciales generales.
5. Finalizar, fallar y cancelar pruebas y comprobar la limpieza de credenciales y archivos temporales.
6. Si el proveedor bloquea por región, cuenta o modelo, anotar el caso como no disponible; no contar como aprobación ni como fallo del producto.

## Plugins y artefactos

1. Revisar un plugin con procedencia declarada, uno con contenido alterado y uno sin evidencia verificable.
2. Confirmar que la referencia fijada, la identidad del publicador y los permisos aparecen como evidencias distintas.
3. Aplicar la política local antes de habilitar el plugin y confirmar que revisar no concede permisos.
4. Verificar que el manifiesto del artefacto coincide con la versión, revisión, hashes e inventario producido.

## Resultado esperado

- Estado y recibo iguales para cada resultado terminal.
- Eventos recuperados sin huecos visibles ni duplicados después de una reconexión.
- Ningún bearer reutilizable en URLs o registros de acceso del flujo cubierto.
- Publicación manual y programada bloqueadas ante evidencia insuficiente.
- Trabajo elegible progresa y errores persistentes tienen reintentos limitados.
- Sesiones vacías e instancias no disponibles se muestran como resultados distintos.
- Plugin y artefacto presentan evidencia verificable o declaran qué dato no se pudo comprobar.

## Convergencia de controles locales

`uv run --no-sync mypy src/` usa `mypy.ini`, con precedencia sobre `pyproject.toml`. Para replicar los controles actuales no añada flags que supriman errores ni interprete el resultado como comprobación de todos los cuerpos sin anotaciones.

La selección ampliada de CI incluye `test/ examples/workflow/tests/`, `-m 'not e2e'` y excluye `test/providers/test_kiro_cli_integration.py` y `test/e2e`. Para ejecutarla en paralelo se comprobó `-n 4 --dist loadfile`. Use un `--basetemp` corto **dentro de TMPDIR** en un volumen con espacio: los workers añaden subdirectorios y las rutas largas pueden exceder el límite AF_UNIX antes de ejercitar la aplicación. Mantenga cobertura/logs en archivos exclusivos por ejecución. En WSL, si el editable conserva otra combinación de mayúsculas del workspace, `PYTHONPATH="$PWD/src"` permite medir la fuente que se está comprobando.

El E2E MCP Apps pasó usando Chromium local y una biblioteca `libasound.so.2` extraída de un paquete oficial en un directorio temporal. La selección explícita del ejecutable y `LD_LIBRARY_PATH` se aplicaron solamente al proceso de prueba. Para un entorno nuevo, el script `npm run test:e2e:install` instala Chromium y sus dependencias según el flujo habitual de Playwright; no sustituya una ausencia de dependencias por omitir las aserciones.

El catálogo actual de recuperación es 39; el schema Work mantiene su identidad existente. Las tablas de autoridad local de pares deben estar vacías para captura/restauración portable. Los catálogos históricos siguen disponibles para verificación y no conceden compatibilidad de restauración con el perfil actual. Los triggers/vistas nativos no declarados se rechazan antes de las escrituras de restauración.

## Regresiones del cierre final

Antes del barrido completo, comprobar propiedad de conexiones y firma de
pares con SQLite real y tráfico entre procesos:

```bash
uv run pytest --no-cov -q \
  test/clients/test_workflow_index_connection_lifecycle.py \
  test/clients/test_startup_connection_ownership.py \
  test/clients/test_sqlite_setup_connection_ownership.py \
  test/services/test_workflow_journal_connection_ownership.py \
  test/services/test_store_connection_ownership.py \
  test/clients/test_workflow_index_migration.py \
  test/services/test_local_peer_weak_keys.py \
  test/services/test_local_peer_auth.py \
  test/api/test_scope_coverage.py \
  test/fixtures/test_cao_server_environment.py \
  test/runtime_channel/test_cancellation_completion_race.py \
  test/runtime_channel/test_remote_integration.py \
  test/scripts/test_collaboration_project_policy.py \
  test/scripts/test_docker_install.py
uv run pytest --no-cov -m 'not e2e' -q \
  test/integration/test_local_peer_two_process.py
```

El contexto transaccional SQLite no cierra su conexión por sí solo. La
regresión exige commit/rollback antes del cierre. La autorización de pares
rechaza codificaciones públicas no canónicas y puntos de orden pequeño,
incluidos grants antiguos; la comprobación criptográfica conserva los bytes
originales y las claves generadas por CAO. Para pruebas paralelas o varias
versiones de Python, establecer HOME/CAO_HOME_DIR y sockets tmux independientes
**antes de importar** la aplicación. No quitar el control de un único proceso
por perfil para hacer pasar el runner.

Las cuentas de proveedores ausentes no pueden validarse en esta fase. No se
considera probada una pausa por cuota si no ocurre realmente. Consultar la
[evidencia final](final-integration-evidence.md) para distinguir aceptación,
historial de diagnósticos y límites externos.

## Regresiones finales de compatibilidad y canal privado

```bash
uv run pytest test/services/test_script_lint.py test/services/test_integration_008_continuation_review.py test/services/test_workflow_step_projection_journal.py
```

La aceptación completa conserva las cinco versiones Python de CI y los mínimos de cobertura; los retries focalizados no reemplazan ese gate.
