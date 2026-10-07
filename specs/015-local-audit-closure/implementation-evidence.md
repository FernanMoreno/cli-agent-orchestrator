# Evidencia de implementación — spec 015

Inicio: 2026-10-07. Rama: `015-local-audit-closure`.

Estado actual: **implementación y convergencia local completadas, T001–T068**. Selección completa de CI: **16470 passed, 110 skipped, cero fallos, exit 0** en Python 3.12.13. Ratchet **PASS**: Python **87.09%** y frontend **90.64%**, sin reducir los mínimos. Mypy completo, formato, arquitectura, navegador y Rust pasan los controles indicados. La evidencia final y las omisiones están en [types-root-evidence.md](types-root-evidence.md); los registros que siguen se conservan como historial del primer cierre. No se hizo commit, push ni publicación.

## Estado inicial y alcance

El usuario autorizó empezar el plan y aplicar las precisiones recomendadas. Se revisó `git status` antes de editar: había cambios previos extensos en fuentes, pruebas, automatizaciones y Graphify, además de archivos no rastreados. Se conservan; no se hizo commit, push, publicación ni merge remoto. Se creó una rama local para la implementación sobre ese mismo árbol.

`SPECIFY_FEATURE=015-local-audit-closure .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` resolvió el directorio correcto. La checklist de requisitos tiene 16 criterios comprobados y ninguno pendiente. No existe `.specify/extensions.yml`; no hay hooks de implementación registrados.

Se revisaron los artefactos existentes en orden spec → plan → tasks, la constitución, el modelo de datos, los contratos y la guía. Las precisiones de ejecución se añadieron a plan/research/quickstart; T001 incorpora indisponibilidad del par y T047 amplía la cobertura previa.

## Diagnóstico y método

Clasificación: bug significativo; varias fronteras de persistencia, autorización, eventos, recuperación y distribución. Se usa TDD para cambios de comportamiento: reproducción en la capa propietaria, fallo esperado y verificación después de corregir. Los informes por historia conservan comandos, causas y resultados.

Graphify se consultó antes de implementación (`graphify query 'local_peer_service cancel_assignment WorkReservationRegistry assignment terminal receipt' --budget 1800`). Encontró las relaciones entre servicio, registro, API, terminal y pruebas; las referencias generadas se contrastaron contra las fuentes actuales. El grafo anterior no se considera prueba de comportamiento ni de actualidad.

El gate inicial `project-composition-check "$(cat .ai/project-name)"` terminó con código 0: 398 archivos, 1663 dependencias, cinco contratos de Import Linter conservados. Esta ejecución sirve de diagnóstico inicial; será necesario repetirla después de los cambios para acreditar el cierre.

## Informes por historia

- [Aceptación posterior con proveedores reales](real-provider-evidence.md): **9/9 combinaciones aprobadas** entre Codex, Claude y OpenCode. Tras renovar el login de Claude, las dos celdas pendientes pasaron de nuevo (**2 passed, exit 0**); se conservan las siete aprobaciones anteriores y los dos fallos iniciales como historial. Los demás adaptadores no se ejecutaron.
- [US1 y autorización local](us1-evidence.md).
- [US2 backend](us2-backend-evidence.md).
- [US2 clientes](us2-client-evidence.md).
- [US3 workflows](us3-evidence.md).
- [US4 publicación y proveedores](us4-evidence.md).
- [US5 confianza de plugins](us5-evidence.md).

Los informes conservan las reproducciones y las ejecuciones finales por historia; las cifras de suites solapadas no se suman como pruebas distintas.

## Aceptación y cierre iniciales — historial anterior a la convergencia

Aceptación ampliada con dos procesos CAO y trabajadores `mock_cli`: **1 passed en 52.33 s** después de corregir la confirmación de parada. La prueba comprueba resultados de éxito/error/cancelación, lease retenido mientras el trabajador puede escribir, rechazo de otro escritor, liberación tras parada, idempotencia, sesiones vacías/disponibles/no disponibles, revocación y emparejamiento explícito, envío y consulta con par apagado, resultado retenido y hashes del proyecto compartido. Comando: `TMPDIR=/home/felni/tmp-cao015-us1 .venv/bin/python -m pytest --no-cov -q -m integration test/integration/test_local_peer_two_process.py`.

US1: suite ampliada final **154 passed, 43 deselected**. US3: suite amplia **191 passed** y recuperación final **6 passed**. US4: controles de publicación/aislamiento/manifiesto/documentación **193 passed, 3 skipped** (zsh opcional ausente), vecinos **124 passed**; `uv lock --check --offline` aprobado. Clientes: Web **469 passed**, MCP Apps **92 passed**, visor **6 passed**, Rust **261 pruebas distintas** incluyendo contratos contra API local aislada; builds y typechecks de clientes aprobados. Las regresiones finales de cursor y plugins se registran en sus informes.

Compatibilidad adicional del ejemplo shell AG-UI: `uv run --no-sync pytest --no-cov test/test_agui_showcase_transport.py` reprodujo un bearer en URL (**1 failed**); tras enviar el bearer por header pasó (**1 passed**). `bash -n examples/ag-ui/ag-ui-dashboard/showcase.sh` pasó. El README de ambos ejemplos y `docs/agui.md` explican tickets nuevos por reconexión, cursores y expiración explícita.

La revisión independiente encontró tres defectos adicionales: parada sin confirmación, expulsión del cursor entre validación y replay y escaneo nativo de plugins alterados. Se añadieron regresiones antes de corregirlos. También se mueve la reconciliación de proyecciones fuera del event loop y se prueba la resincronización del cliente ante `cursor_expired` seguido de cierre.

Proveedores reales sin perfil aislado o disponibilidad se registran como no disponibles; pruebas deseleccionadas no cuentan como aprobadas. No se ejecutaron publicación, escáneres remotos ni la matriz completa Python 3.10–3.14 en esta PC.

Gate final del padre: `TMPDIR=/home/felni/tmp-cao015-root project-composition-check "$(cat .ai/project-name)"`, **exit 0: 400 archivos, 1674 dependencias, 5 contratos conservados, 0 rotos**. Log: `/home/felni/tmp-cao015-root/composition-final.log`. `git diff --check` de todo el árbol terminó con **exit 0**.

Se repitió composición después de las correcciones finales de revisión (`composition-after-review.log`): **exit 0, 400 archivos, 1675 dependencias, cinco contratos conservados**. Revisión final del diff: cambios propios de credenciales, ejemplos, documentación y artefactos contrastados con fuentes; runtime/receipts/eventos y workflows/publicación/plugins revisados independientemente en [review-runtime.md](review-runtime.md) y [review-release-plugins.md](review-release-plugins.md). Ambos informes terminan **PASS WITH RISKS**, sin hallazgos P1/P2 pendientes en el alcance revisado. Se preservan los cambios ajenos del árbol inicial.

Verificación final de anotaciones US1: servicio/registro/rutas locales **mypy PASS, tres archivos**; autenticación de pares **mypy PASS, un archivo**; normalización posterior **19 passed**. Backend final de API plugins/tickets/WebSocket **63 passed**, incluido plugin ilegible que no oculta entradas sanas. `event_stream_ticket.py` **mypy PASS**. Estos resultados focalizados no sustituyen el gate global.

Control obligatorio `TMPDIR=/home/felni/tmp-cao015-root uv run --no-sync mypy src/`: **exit 1: 573 errores en 85 archivos, 398 fuentes comprobadas**. Log: `/home/felni/tmp-cao015-root/mypy-final.log`. Incluye deuda extensa de SQLAlchemy/modelos, clientes y servicios; no se desactiva el gate ni se añaden ignores para obtener un resultado verde. Parte del diagnóstico local tomó la fuente antes de las últimas anotaciones; las verificaciones focalizadas posteriores se registran en los informes. Este resultado bloquea afirmar aprobación completa del repositorio o preparación para publicar.

Decisión de conocimiento durable: los contratos y decisiones permanecen en Spec Kit. No se duplican fuentes, hechos generados de Graphify ni logs temporales en Obsidian; se reconsiderará persistencia si aparece una conclusión durable que no esté ya representada por estos artefactos.

Ejecución completa de ese cierre inicial, después de las correcciones de revisión: `TMPDIR=/home/felni/tmp-cao015-root uv run --no-sync mypy src/` → **exit 1, 531 errores en 81 archivos (398 fuentes comprobadas)**. Log: `/home/felni/tmp-cao015-root/mypy-after-review.log`. Ambos resultados fallidos son diagnósticos históricos; las ejecuciones limpias de convergencia están documentadas en `types-root-evidence.md`.

US5: verificación final afectada **193 passed en 120.47 s**, incluidos los dos casos corregidos de la suite ampliada que antes había terminado con 679 passed y 2 failed. Las cuatro regresiones nativas incluyen revocación fallida y event loop bajo contención. No se suma esta suite con ejecuciones solapadas.

Graphify actualizado sin sobrescribir el grafo previo: [snapshot estructural](../../graphify-out/2026-10-07-spec015-structure/GRAPH_REPORT.md), **26 fuentes, 1652 nodos, 3711 relaciones, cero fuentes fallidas, 0/0 tokens**. SHA-256 idénticos antes/después de la extracción final y comprobados de nuevo al terminar; manifiesto por fuente incluido. Es cobertura estructural focalizada, no una nueva auditoría semántica completa del repositorio.

T001–T056 ejecutadas y documentadas. Resultado de aquel cierre inicial: implementación local entregada y arquitectura aprobada, con bloqueos globales de tipos y navegador que motivaron T057–T064. Esos bloqueos históricos se corrigieron en la convergencia. No se hizo commit, push, merge remoto ni publicación; las operaciones remotas no ejecutadas siguen sin acreditarse.

## Ampliación de aceptación e integración del fork (Phase 10)

Los resultados anteriores son checkpoints históricos de las fuentes de cada
ejecución. El usuario autorizó después completar las validaciones restantes,
crear commits y hacer push no forzado a `main` del fork. La aceptación adicional
incorpora proveedores reales utilizables, Docker/Bubblewrap, Rust, seguridad y
la matriz Python completa. Los fallos iniciales de perfiles/fixtures, logout,
conexión SQLite y claves públicas débiles se reprodujeron antes de corregirse.
El estado y los resultados actuales se consolidan en
[final-integration-evidence.md](final-integration-evidence.md); la matriz nueva
aún debe terminar antes de aprobar los commits y el push. Las cuentas ausentes
y la pausa real por cuota no observada siguen sin acreditarse.
