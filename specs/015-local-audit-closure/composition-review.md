# Revisión de composición — spec 015

Estado actual: **composición de producción aprobada; fixtures T088/T089 revisadas, aceptación global posterior en verificación**. Las cinco selecciones completas actuales pasan sin fallos; el ratchet obligatorio de Python 3.12/MCP Apps pasa y el chequeo suplementario de cobertura de 3.14 falla, con sus resultados separados. La revisión focalizada final C-001–C-007 no tiene P1/P2 abierto. Las secciones históricas siguientes conservan sus fechas y alcance; los 16470 éxitos anteriores no representan la fuente final.

## Superficie e invariantes

| Frontera | Propiedad y contrato que se revisa | Escenarios de evidencia |
|---|---|---|
| Tarea local → SQLite → API/MCP | Un write condicional elige el resultado terminal; recibo y estado se conservan juntos; una respuesta atrasada no reemplaza al ganador | Cancelación, finalización concurrente, reconciliación atrasada, consulta posterior |
| Trabajador → reserva compartida | Una solicitud de cancelación no libera la reserva mientras el trabajador puede escribir; actualización antigua no reactiva una reserva liberada | Parada fallida, parada confirmada, reserva posterior y cleanup obsoleto |
| Principal → tarea del origen → par firmado | Principal verificado persistido; proyecto del terminal coincide con petición; identidad, proyecto, acción y propietario del par permanecen vinculados | Dos principales con mismo terminal, grant revocado, proyecto distinto, tarea retenida |
| Snapshot de sesiones → backend local | Capacidad `session:read` explícita; vacío, desaparición e indisponibilidad tienen resultados distintos | Lista vacía, backend caído y desaparición durante enumeración |
| Historial → API de eventos → MCP App | Historial y live usan IDs del mismo productor, replay después de cursor y deduplicación; cursor ausente o expirado se comunica | Evento en ventana de suscripción, desconexión, replay, cursor expirado |
| Autenticación → ticket → SSE/WebSocket | Uso único, recurso, principal, vencimiento y permisos actuales; renovación con ticket nuevo; no bearer reutilizable en query/log | Ticket consumido, vencido o de recurso distinto; revocación durante conexión; cookie no relacionada |
| Ventana host → bridge MCP | Origen esperado deriva de contexto confiable antes del primer mensaje; `event.source` coincide con host | Primera respuesta maliciosa, ventana distinta, contexto no verificable |
| Recuperación → inventario → task handles | Paginación avanza; cooldown conserva equidad; cleanup por identidad de handle no borra su reemplazo | Inventario mayor que lote, fallos sostenidos, generaciones nuevas |
| Configuración → shim HTTP de workflow | Identidad y entorno administrados prevalecen; campos desconocidos se rechazan antes de ejecutar | Opciones falsificadas, extras en distintas entidades YAML |
| Commit validado → gates → artefacto | Manual y programado exigen controles del mismo SHA; versión preparada antes de CI; manifiesto liga hashes e inventario | Control ausente/omitido/fallido, SHA distinto, metadatos o locks sucios |
| Perfil aislado → proveedor → teardown | Falta de autenticación aislada bloquea; copia de credenciales temporal y cleanup, sin fallback al hogar general | Arranque fallido, salida normal, cancelación, origen inválido |
| Plugin → aprobación → proyección/catálogo/MCP | Aprobación de bytes, origen, revisión y permisos exactos; ausencia de procedencia visible; revisar/instalar no concede permisos | Instalación deshabilitada, contenido alterado, incompatibilidad, catálogo tras cambio |

## Arquitectura y dependencias reales

El gate configurado usa Import Linter. La ejecución final del padre terminó con exit 0: 400 archivos, 1675 dependencias, cinco contratos conservados y ninguno roto.

Las pruebas de persistencia usan SQLite real en directorios desechables. La aceptación local ejecutada usa servidores HTTP en procesos separados y el proveedor determinista `mock_cli`, sin cuenta externa: 1 passed en 52.33 s, incluidos hashes y fence de cancelación. No se necesita Testcontainers para SQLite ni un broker remoto: esas dependencias no forman parte de este producto local. Las pruebas de rutas y payloads MCP son los contratos ejecutables pertinentes; no se añade Pact solo por nombre.

## Límites que deben quedar visibles

- El historial general de flota usa el ring existente de 500 eventos/24 horas en memoria. Un cursor fuera de retención o después de reiniciar el productor requiere resincronización explícita; no equivale a un journal durable. El journal de workflows mantiene su contrato anterior.
- Aprobar contenido de plugin no verifica la identidad del productor ni añade aislamiento del sistema operativo. Código ya consumido por un proveedor activo puede requerir reinicio para retirar sus capacidades.
- Una matriz CI declarada no acredita que todas sus versiones se hayan ejecutado en esta PC. Escáneres y publicación remota no se ejecutan sin petición expresa.
- `/tmp` se llenó por artefactos ajenos durante pruebas. Las ejecuciones afectadas deben repetirse con `TMPDIR` aislado en un volumen disponible antes de computarlas como aprobadas.

## Veredicto inicial — historial anterior a la convergencia

Las revisiones independientes están en [review-runtime.md](review-runtime.md) y [review-release-plugins.md](review-release-plugins.md). Los hallazgos dieron lugar a regresiones: parada ordinaria fallida conserva recursos/fence; replay valida y selecciona desde un snapshot único tras suscribirse; proyección nativa se reconcilia antes del launch y falla cerrado si no puede retirarse, con trabajo bloqueante fuera del event loop; los clientes manejan `cursor_expired` de forma explícita y acotan la reconexión.

Verificación adicional del padre después de los cambios: **84 passed, 5 warnings en 54.87 s**, con `TMPDIR=/home/felni/tmp-cao015-root uv run --no-sync pytest --no-cov -q test/test_agui_showcase_transport.py test/api/test_workflow_events_sse.py test/models/test_workflow.py test/services/test_continuation_responsiveness.py test/services/test_terminal_observation_recovery.py`.

El E2E MCP Apps no alcanzó las aserciones de aplicación: Chromium no arranca por falta de `libasound.so.2`; es un bloqueo ambiental. Proveedores reales, publicación y matriz completa de Python no se ejecutaron. Arquitectura: **PASS, cinco contratos**. Revisiones independientes: **PASS WITH RISKS**, sin P1/P2 pendiente en las fronteras revisadas. Control completo `mypy src/`: **FAIL, última ejecución 531 errores en 81 archivos**; se corrigen diagnósticos propios con verificaciones posteriores, pero la deuda global impide declarar cierre completo de gates o preparación para publicar.


## Convergencia posterior autorizada

El bloqueo histórico de tipos y el arranque del navegador quedan resueltos. Mypy completo después de las últimas correcciones: **398 fuentes sin errores, exit 0**. Black: **1177 archivos sin cambios, exit 0**; isort: **exit 0**. Arquitectura repetida después de recuperación: **PASS, cinco contratos**. E2E MCP Apps completo: **7 passed, exit 0**. Web y MCP Apps unitarias repetidas: **469 y 92 passed**.

La revisión de tipos encontró desapariciones concurrentes de proyecciones y escalares remotos sin validar. Se corrigieron con RED/GREEN y se revisaron sus consumidores en [types-composition-review.md](types-composition-review.md). Persistencia mantiene las 33 tablas y 284 columnas del metadata original. Tmux/proveedores/MCP se revisan en [types-root-review.md](types-root-review.md).

La revisión de recuperación amplía el inventario cerrado actual y rechaza autoridad no portable y objetos SQLite ejecutables no declarados antes de restaurar. Véase [recovery-composition-review.md](recovery-composition-review.md). Suite ampliada final: **1010 passed, 2 skipped**, incluidos 17 casos de pares/triggers/vistas. Los catálogos históricos y el schema de Work no se alteran.

El snapshot estructural actualizado declara 110 fuentes y extracción AST por archivo sin resolución entre archivos. No sustituye el contraste de vecinos realizado contra fuentes ni las pruebas de composición. Los hashes se verifican antes/después de extracción.

Veredicto actual de fronteras revisadas: sin P1/P2 abierto. **Cierre global pendiente** de la selección completa final de CI con cobertura; los resultados y sus límites se consolidan en types-root-evidence.md. Operaciones de publicación y cuentas externas no se ejecutan.

T066 alinea nueve comandos nuevos Click/Rust manteniendo todos HIDE/routeless; 4 controles Python y 36 integraciones Rust pasan, además de 223 pruebas de bins y Clippy/fmt. T068 corrige el campo real de sesión en el descubrimiento curator; 1101 pruebas amplias pasan, y la revisión independiente de pares/snapshots/curator pasa 141 casos. Las nuevas pruebas funcionales de cobertura conservan autoridad y límites nativos. Detalles en [catalog-evidence.md](catalog-evidence.md) y [coverage-contracts-review.md](coverage-contracts-review.md). Los gates de arquitectura posteriores conservan los cinco contratos (400 archivos, 1680 dependencias). La selección global nueva sigue pendiente de terminar.

## Veredicto final de convergencia local

**PASS**, sin P1/P2 abierto en las fronteras revisadas. Las correcciones de tipos preservan los contratos y cierran respuestas/proyecciones inválidas; los cambios de estado, credenciales, replay, recuperación, aprobación y catálogo tienen regresiones comprobadas. La selección completa sobre una copia Linux con los mismos **1198 hashes** termina **16470 passed, 110 skipped, cero fallos, exit 0**. El problema WSL ENOMEM se reprodujo en imports y desapareció al mover código/dependencias al filesystem Linux; no se modifica comportamiento ni se omiten los tres casos afectados. Cobertura **87.09% backend / 90.64% frontend**, ratchet **PASS** con baseline idéntico y ambos informes presentes.

Arquitectura: **400 archivos, 1680 dependencias, 5 contratos conservados, 0 rotos**. Graphify: **110 fuentes actuales**, AST por archivo y hashes verificados; las pruebas de contratos/composición aportan la evidencia de comportamiento. Markdown links y diff final pasan después de registrar estos resultados. Comandos, duración, logs, límites y motivos de las 110 omisiones: [types-root-evidence.md](types-root-evidence.md). La validación no acredita cuentas reales, publicación, matriz completa Python ni sandbox Docker/Bubblewrap desplegado.

## Integración final autorizada (Phase 10)

El veredicto de convergencia anterior corresponde a su snapshot, no a las
correcciones posteriores. La revisión independiente de publicación y de
T077/T078/T079 no encuentra bloqueos en las fronteras inspeccionadas. El cierre de
SQLite conserva transacciones y catálogos; la guardia de claves de pares se
comparte entre admisión y grants existentes, preserva los bytes originales y
las firmas legítimas. La prueba real entre dos procesos pasa después de ambas
correcciones. Los cinco contratos de arquitectura siguen conservados.
Véanse [publicación](final-publication-review.md),
[SQLite](final-sqlite-evidence.md),
[arranque](final-startup-ownership-evidence.md), [firmas](final-peer-key-evidence.md) y
[aceptación final](final-integration-evidence.md). La matriz corregida y el
ratchet conjunto permanecen pendientes de su resultado completo.

La revisión posterior T080 amplió el inventario a 55 contextos de operación
propietaria y tres aperturas con fallo antes de devolver el handle. Los
resultados se materializan antes del cierre; se mantienen transacciones,
commits explícitos/parciales y rechazos ante almacenes desconocidos. Las
34 regresiones nuevas pasan en revisión independiente, junto con 248
pruebas de vecinos y 168 de apertura/permisos. El control real del journal
y los archivos corruptos produce cero avisos. La aceptación entre dos
procesos pasa de nuevo después del cambio. Véase
[propiedad de almacenes](final-store-ownership-evidence.md). La matriz
completa de este freeze permanece pendiente; no se reutiliza cobertura
de ejecuciones interrumpidas.

The managed server fixture also binds HOME and CAO_HOME_DIR together while
preserving explicit overrides. Real child-process regression and the original
HTTP refusal case passed; full matrix acceptance remains pending. See
[fixture profile evidence](final-fixture-profile-evidence.md).

T082/T083 composition review: completed send/result cannot swallow caller
cancellation; the bounded child is owned, cancelled and drained, while durable
reconciliation/compensation remains covered by the unchanged original case.
Final 71-case runtime suites passed on Python 3.10 and 3.11; independent
47-case review passed on 3.11. Docker TOML compatibility preserves mount and
MCP initialize policies (44 neighboring cases pass on 3.10 and 3.12). See
[runtime compatibility evidence](final-runtime-compatibility-evidence.md).

After the final helper typing correction, composition checks again kept all
five contracts (400 files, 1,680 dependencies, zero broken). Black accepted
all 1,194 Python source/test/workflow files, isort passed, and mypy reported
no issues in 398 source files. The real two-process local-peer acceptance
also passed (one case, 32.60 seconds) on the same production bytes.

T084–T086 final focused review found no material blockers. The deleted-byte
fixture explicitly selects its adversarial SQLite condition and closes its
observer after the original transaction; actual warnings changed from one to
zero. Pure lint rejects parser-excessive expressions before entering native
AST parsing, preserving ordinary large scripts and existing response/schema
boundaries (98 cases in all five Python versions and 106 caller cases pass).
Retry authorization still runs inside the real transaction; the test observes
rollback before delegated closure and requires closure afterward.

Graphify was queried for lint callers and compared with current source: API
validation/run paths, workflow spec validation, Work plan preparation and the
script runner still call the same lint function and consume its existing
result. Its stored nodes remain historical and do not claim the new token
helper exists in that snapshot. No dependency-boundary refresh is needed for
this standard-library helper within the existing linter module; current
executable composition checks again keep five contracts (400 files, 1,680
dependencies, zero broken). Mypy checks 398 source files successfully. Full
matrix acceptance, final diff/source verification and publication are pending.

### Recuperación de validación y T087

El candidato reconstruido conserva la fuente final T077–T087, con cero diferencias en 1358 archivos entre el workspace y el candidato. La revisión independiente de T087 confirmó que la observación acotada del entorno ocurre dentro de attach, antes del registro final y antes del `os.write` de la capacidad; SQL de allocation_pending, ausencia de bindings y reap siguen reales. Sus seis casos pasan; un caso vecino sin cambios requiere el resultado de la matriz por su fallo intermitente. Los logs temporales históricos ya no están disponibles; los resultados publicados se mantienen como evidencia histórica registrada, no pruebas reejecutadas.

La revisión de cierre reconstruida verificó las fronteras C-001–C-007 contra fuentes actuales sin encontrar nuevos bloqueos: fence de cancelación ante parada no confirmada, snapshot después de suscripción, tickets de un solo uso, cleanup por identidad, aprobación por bytes y gates del SHA exacto. La revisión fue focalizada y no representa inspección exhaustiva de las 809 rutas. El gate de composición vuelve a conservar cinco contratos y cero rotos. El índice/candidato/original coinciden en los 1358 hashes de fuentes. La validación de Markdown del árbol `c483c4f66dd4e529c3fd34fd4e84a5f451642fd7` revisó 482 archivos sin errores.

### Veredicto final después de la matriz

La revisión independiente volvió a comprobar C-001–C-007 y la identidad de fuente final de 1358 archivos sin diferencias. Leídos los cinco posthash y JUnit completos, todos registran pytest exit 0, 16704 casos, cero fallos/errores y 1361 registros posteriores sin cambios. Los vecinos de autorización y observación de T087 también pasan en las selecciones completas, superando el diagnóstico intermitente anterior sin inventar su causa. `project-composition-check caos` e Import Linter ejecutados después de la matriz conservan cinco contratos y cero rotos (400 archivos, 1680 dependencias).

La composición queda apta para commit, condicionada a revisar el índice final, enlaces, secretos, diff y publicación. Esta revisión es focalizada en las fronteras afectadas y no declara inspección exhaustiva línea a línea del diff acumulado. El gráfico histórico conserva su procedencia y se verificó contra fuentes actuales; no hay nueva dependencia entre subsistemas que exija regenerarlo. La evidencia durable se conserva en los artefactos mantenidos del spec; los perfiles y copias temporales se eliminan después de registrar sus resultados.

## Fixtures de CI posteriores — T088/T089

Graphify localizó WorkProcessSupervisor y create_terminal; las fuentes actuales confirmaron el preflight Kiro antes de reconciliar proyecciones y el probe exacto user/map-root/net/IPC/PID antes de Work. T088 sólo sustituye el preflight junto al proveedor ya simulado. T089 sólo aplica precondiciones a seis casos nativos y diferencia omisión conocida de errores de bootstrap. No cambia propiedad de estado, transacciones, eventos, APIs, autorización, esquema ni efectos de producción. Los fixtures reales conservan gating/identidad, cleanup, entorno y persistencia; los controles inesperados convierten un SKIP incorrecto en FAIL.

La revisión independiente aprueba ambos fixes después de resolver dos P2. No se requiere extracción estructural nueva para dos archivos de pruebas: los hashes exactos y los contratos ejecutables conservan la identidad; los snapshots Graphify anteriores mantienen su alcance histórico. Resultados y omisiones separados en [evidencia de CI](final-ci-fixture-evidence.md); aceptación global posterior pendiente.

## T091 — preparación nativa de CI

La revisión independiente final declara PASS_WITH_RISKS, sin P1/P2 abierto, para el nuevo helper de CI y sus fronteras de privilegios, probe real, JUnit sin SKIP, drenaje y restauración. La revisión anterior reprodujo un descendiente resistente a SIGTERM vivo tras salida del líder; tres RED reales precedieron el fix y la re-revisión lo declaró ADDRESSED. La validación local final pasa 87 casos, incluidos 38 nativos sin omisiones. La selección y los argumentos de cobertura permanecen exactos. Se conservan producción, dependencias, contratos y QEMU; ci.yml cambia su launcher.

El manifiesto T091 contiene 1360 archivos, SHA-256 `a7214e973b40d28f929ce64d9fc7ebc18e8f9e99fcd2cf19f84546e32de31011`. El comando de composición registrado conserva cinco contratos/cero rotos, y el Import Linter ejecutado directamente en el candidato confirma cinco/cero. Graphify dirigido real produce 139 nodos/324 aristas con fuentes verificadas; la caché propia ya fue eliminada. La revisión no acredita causalidad AppArmor del runner ni mínimos 87/90 del nuevo SHA: esas condiciones requieren CI hospedado posterior. Véase [evidencia de CI](final-ci-fixture-evidence.md).

## T092/T093 — verificación de selección y salida de pruebas

Las revisiones de tarea aprueban el selector exacto del comando y sus dos fixes de casos límite, además del cambio exclusivo de IDs Docker; sin P1/P2 abiertos. El skill canónico y sus dos copias distribuidas describen el launcher real y mantienen paridad. No se añade dependencia entre subsistemas ni cambia estado/eventos/APIs/runtime. Los controles nuevos verifican flags ejecutables, unicidad y literalidad sin ejecutar shell no confiable. El nombre Docker deja de incluir un payload de 8 MiB, conservando los mismos datos y aserciones.

Fuente final: 1363 registros con tres documentos, SHA-256 `b0b5836af7ff40a0ddd18d9bf0d238961cce6827130dc3cad902a313c6a7bcaa`; configuración ampliada 1366. Composición registrada y directa del candidato mantienen cinco contratos/cero rotos. Source runtime/locks/QEMU sin cambios. La revisión final independiente T092/T093 declara PASS_WITH_RISKS, sin P1/P2 nuevos; publicación/fresh CI todavía pendientes; la causa de las cinco cancelaciones del CI anterior no se atribuye al volumen de log sin prueba hospedada.

Controles previos al commit T093: los 1366 registros del índice coinciden sin diferencias; Gitleaks staged exit 0/sin secretos. Trivy real sobre el árbol inmutable `aef1d2bf7c1993cf7ecd37cbbbe3d7d579cfcf4b` pasa exit 0, cero resultados SARIF, 44.909 s; SARIF SHA-256 `97134045079a76cd56c514cc152d9b12e9643c0dda31e182aa35b697387f2e26`. Exportaciones bloqueadas por lock, informe y logs temporales eliminados tras registrar hashes. Las adiciones posteriores a ese árbol son únicamente este recibo documental. Hook completo y aceptación hospedada siguen pendientes.
