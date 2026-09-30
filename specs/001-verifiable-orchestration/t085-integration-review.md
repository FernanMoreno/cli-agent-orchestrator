# T085: integración upstream aislada

Estado: T085 completada; candidato independiente preparado y verificado.
Aceptación vigente: 13.491 casos aprobados por inventario, incluidas las 119
incidencias corregidas; [estado y límites](acceptance-status.md). Los gates y
contadores iniciales del informe se conservan como evidencia histórica.

## Alcance autorizado

La instrucción «hazlo todo», tras presentar T084/T085/O03, autoriza preparar y
comprobar el candidato aislado. No se incorpora este merge a main.

- Checkout original: `/mnt/c/users/ferna/onedrive/escritorio/caos`.
- Candidato independiente: `/home/felni/.cache/caos/t085-20260930`.
- Base del fork: `0d1b4ff9cf9caa88d28f16a1c9f54c22678813f4`.
- Snapshot local de los cambios pendientes: `f93efdbfe950ab264ef9007d4695e5f0b592b36d`.
- Upstream local fijado: `2fcc3efa6c6e70039e5b9a7308ee67cd1f024885`.
- Merge preparado sin commit final; 12 conflictos textuales resueltos; cero entradas unmerged.
- No se hizo fetch ni publicación. El snapshot pertenece sólo al clon aislado.

## Revisión de composición

Subsistemas: API/MCP, receipts de hijos y handoff, Work/SQLite, memoria/vault,
relaciones, graph/cache, configuración, Agent Plugins, TUI/Web, fleet-panel.
Graphify se consultó y se contrastaron sus vecinos con el código fuente.

Contratos conservados: autoridad verificada antes del efecto; resultado durable
antes de teardown; orden del gate managed; schema Work39 sin renumeraciones;
single owner de DB/cache; bloqueo de proyección; recibos y tombstones;
ningún fallo remoto activa fallback local. Los clients retienen campos previos.

Fallos encontrados y correcciones:

1. Catálogo TUI incompleto: Work y WorkEvents ausentes. Incorporados como
   comandos ocultos hasta revisar su interfaz; no se inventa una ruta de TUI.
2. Audit intent independiente competía con la transacción de proyección vault.
   Se persiste antes de abrirla; las mutaciones anidadas comprueban actor y
   archivo SQLite exactos. Una base ajena se rechaza antes de modificar filas.
3. Nuevos helpers de relaciones podían mutar sin audit intent. Se cubren
   clear_source/supersede_ids/restore_statuses y la migración/rollback de recibos.
4. Lecturas de vault usaban una DB global ajena al MemoryService. Se propaga
   el session factory del propietario a la lectura y al cálculo de identidad.
5. Una exclusión aún sin reconcile dejaba visible la proyección antigua.
   La selección verifica el tombstone durable, además del estado indexed.
6. Recovery perfil30 rechazaba siete tablas upstream y source_kind. El nuevo
   perfil31 conserva catálogos históricos28/29/30 en verify-only y captura
   sólo el catálogo31; referencias canónicas externas siguen declaradas
   requires_future_profile. No se atribuye backup de archivos vault.
7. Gemini CLI estaba ausente de la matriz de entrega Agent Plugins. Se
   declara explícitamente sin transporte/CWD soportado, con rechazo observable.
8. El generador de estados producía Rust no canónico. Se corrige el generador
   y se comprueban artefactos + cargo fmt sin excepciones de formato.
9. Codex0.159.2 cambió el diálogo de confianza. Se reconoce el menú completo
   y seleccionado, anclado al viewport; texto parcial/scrollback no se acepta.
10. Claude2.1.285 puede perder la primera navegación del diálogo al arrancar.
    Se espera una selección explícita; Down sólo se reintenta si No sigue
    seleccionado, y Enter sólo se envía tras observar Yes. Sin cambiar modelo.
11. OpenCode podía aceptar el IDLE nativo del shell antes de dibujar su composer.
    La primera entrega exige ahora un viewport aprobado por OpenCode; el
    cruce Codex→OpenCode en Herdr reprodujo el fallo y pasó tras corregirlo.
12. Un reinicio externo de tmux podía hacer fallar la configuración exit-empty
    previa a new_session. Se reaplica tras crear la sesión cuando el bootstrap
    falló, sin abortar launches legacy. Regresión determinista y tmux real.
13. Fixtures históricos carecían de selección visible de trust, watermark de
    curator, starttime y namespace PID. Se actualizan los datos de prueba;
    siguen vigentes las comprobaciones de autoridad, rechazo y no redelivery.

14. El journal importaba el serializer de resultados del servicio, contra su
    frontera DAL. La serialización/redacción queda en workflow_service; el
    journal recibe el JSON y conserva los gates de generation/attempt/fingerprint.
15. Fixtures de aprobación daban localhost como socket servidor, y un fixture
    de inbox omitía task_delivery. Se usa loopback literal y se exige el marcador
    real de entrega; no se relaja la autoridad ni el reconocimiento de turnos.

16. Una toma de ownership Work entre la lectura legacy y su escritura permitía
    borrar work_pending. Dos regresiones reprodujeron la pérdida de estado.
    El UPSERT rechaza work_pending en su propio SQL; el callback prepara una
    copia y sólo publica su transición en memoria si el gate no la rechaza.
    El control de atomicidad exige una sola conexión escritora y un solo SQL,
    permitiendo el preflight de lectura. 153 pruebas focalizadas pasan.

## Evidencia fresca ya completada

| Gate | Resultado |
|---|---|
| Recovery final/históricos y replay | 147 passed |
| Recovery/históricos, contratos de entrega y nombres (gate anterior) | 282 passed |
| Vault/forget/reconcile/gateway antes de revisión final | 182 passed |
| Docker real T019/T020 | 14 passed, sin skips |
| Lifecycle local con mock_cli | 12 passed |
| Herdr0.9.1 + mock_cli | 1 passed |
| Web | 362 passed, 22 archivos; TypeScript/Vite build exit0 |
| TUI contra servidor temporal real | 252 passed; fmt/clippy exit0 |
| MCP Apps | 78 passed; typecheck/build de cuatro apps y límites de tamaño exit0 |
| Wheel Linux x86_64 | build e instalación temporal; Web, cuatro apps y ELF64 TUI incluidos |
| Autoridad/exclusiones/graph y regresiones asociadas | 134 passed, incluidas ocho regresiones nuevas |
| Fleet-panel, lock propio | 44 Python + 30 JavaScript passed |
| Agent Plugins schemas/packages | ambos targets make exit0 |
| Generador de estados | seis artefactos up to date |
| Arquitectura candidato | 5 kept, 0 broken; 328 archivos/1316 dependencias |
| Composición checkout original | 5 kept, 0 broken |
| Memoria con autoridad/curator final | 25 passed |
| Proveedores: arranque, contenedor e idle-gap | 110 passed |
| Proof de proceso/starttime/namespace | 4 passed |
| Settlement/ownership Work: archivos afectados | 825 passed |
| Checkout main: regresiones de ownership/replay/script | 153 passed |
| Agent Plugins: pipeline offline real validate/add/install/remove | PASS, seis assertions + pipeline final |
| Python black/isort (958 archivos) | exit0; 155 archivos normalizados en candidato, sin diferencias AST salvo docs/import order |
| Enlaces Markdown y diff check | exit0 |

Los resultados parciales no cierran T085 hasta cubrir toda la suite. Se registra
por particiones, con un inventario único de nodeids: los casos completados
se conservan y cada archivo afectado se vuelve a ejecutar. Un intento fallido
no cuenta como aceptación; se conservan sus JUnit junto a los posteriores.
Los fallos de preparación (extras AG-UI ausentes, TMPDIR inadecuado y espacio
/tmp agotado) se corrigieron; ninguna ejecución interrumpida cuenta como passed.

## Límites

T084 tiene autorización para Codex, Claude y OpenCode, con 0 € adicionales:
OAuth de suscripción para Codex/Claude y modelo gratuito explícito de OpenCode.
Las 18 celdas autorizadas están aceptadas: nueve en tmux y nueve en Herdr0.9.1.
Los cinco casos tmux con OpenCode se repitieron tras su corrección de bootstrap;
OpenCode→OpenCode en Herdr se repitió después del cambio del journal.
Evidencia saneada: [t084-live-matrix.json](t084-live-matrix.json).
OpenCode v1.18.32 se selecciona en PATH aislado, sin sustituir la instalación v2.
O03 mantiene sin certificar proveedores adicionales no probados; Docker
sólo acredita su perfil local.
No hay host de producción previsto. Las fuentes externas de vault necesitan
su propio backup. Gemini CLI no recibe Agent Plugins MCP en este candidato.

Veredicto final: **PASS WITH RISKS** dentro del alcance autorizado. Los límites
de proveedores, referencias externas de vault y producción constan arriba.

Wheel final instalado fuera del checkout: módulos Codex, Claude, OpenCode,
tmux, journal/workflow_service, authority y vault reader coinciden byte a byte
con el candidato. SHA256 del wheel: `665f5a218b6f1871dcf9a618d270a1b9d568302dc70c4355ad70eee42371062d`.
TUI ELF64 incluido: 1.742.512 bytes.

Cuota Codex leída antes y después de la matriz: 53% semanal en ambos casos;
saldo de créditos sin cambios (62500). La segunda lectura de cuota Claude
falló; sólo se certifica la observación previa de extra_usage desactivado.
No se provocó agotamiento de cuota ni se activó pago adicional.

## Fronteras revisadas y dependencia real

| Frontera | Ownership/orden/fallo verificado | Evidencia |
|---|---|---|
| API/MCP → resultados/receipts → cleanup | resultado durable precede teardown; timeout no autoriza redelivery; ACK exige identidad exacta | matriz real de 18 celdas, handoff/turn/replay en suite Python |
| Workflow producer → journal SQLite → Work | producer redacts/bounds; una sola escritura terminal; Work pendiente no admite callback legacy ni siquiera entre lectura y escritura | 825 casos; dos regresiones de carrera con SQLite real |
| Vault/memoria → relaciones/audit → graph | audit intent precede proyección; actor/DB exactos; scope/source_kind y exclusiones durables | ocho regresiones nuevas, DB temporal real y suites vault/graph |
| Recovery → catálogo/migración → restore | schema Work39 no renumerado; perfil31; perfiles28/29/30 verify-only; referencias externas explícitas | 147 casos de recovery/replay/catalog |
| Provider startup → terminal backend → primer prompt | trust sólo con selección visible; viewport aprobado antes de entrega; timeout/cancel no falsean éxito | Codex/Claude/OpenCode reales en tmux/Herdr; lifecycle mock y Docker |
| API/settings/status → Web/TUI/MCP Apps | catálogo de rutas y enum cerrados; proyecciones compatibles; recursos instalados verificados | 362 Web, 252 Rust, 78 Apps; wheel instalado fuera del checkout |
| Agent Plugins → profile/config/provider → retirada | entrega no concede autorización; slots propios; config de usuario conservada; retirada explícita | suite agent_plugins + pipeline offline real con seis assertions |

No se añadió Pact: API y consumers están en el mismo repositorio y sus
contratos se verifican mediante suites de rutas, schemas y servidor temporal
real. SQLite, tmux, Herdr y Docker fueron dependencias reales; mocks quedan en
los fallos deterministas y proveedores sin cuenta autorizada.

Graphify se contrastó con fuente. La decisión de refresh/persistencia conserva
el índice previo; estas conclusiones quedan en los contratos y esta revisión.
No se copia código/logs/secretos al vault. El inventario final acredita cero casos faltantes/fallos vigentes; no se certifica producción ni
proveedores adicionales a las tres cuentas autorizadas.


## Cierre T085 — 2026-09-30

Candidato upstream preparado y verificado en clon independiente. Inventario
Python único: **13.476 casos**; **13.357 passed, 118 skipped, 1 xfailed**;
cero nodeids faltantes y cero fallos vigentes. Aceptación por particiones y
repetición de archivos afectados, no una ejecución monolítica que se atribuya
a los intentos interrumpidos. Los skips conservan sus límites de entorno y el
xfail conocido no se convierte en passed. Los fallos anteriores quedan
registrados junto a sus reruns; la prueba final de telemetría pasa en ambos
checkouts con DB propia y esquema Work real.

Gates finales: arquitectura candidato 5 kept/0 broken; composición del checkout
original PASS; black/isort sobre 958 archivos exit0; enlaces Markdown/diff
check exit0. Tras normalizar formato se repitieron 1.682 casos de runtime
con 3 skipped y 1 xfailed; 825 casos de settlement/Work/replay pasan.
Web, TUI, MCP Apps, Docker y Agent Plugins conservan sus gates documentados
en la revisión. El wheel final instalado coincide byte a byte con nueve módulos
del candidato y contiene sus recursos Web/TUI/MCP Apps/Agent Plugins.

**T085 completada; ambos specs quedan sin tareas abiertas.** Esto acredita
preparación y aceptación local del candidato; su integración en main sigue
siendo una acción separada. O03 no certifica proveedores adicionales a los
autorizados; no existe despliegue/host de producción dentro de este cierre.
Artefactos locales de cobertura y parche en `/home/felni/ct/`; manifiesto
final: `/home/felni/ct/t085-candidate-manifest.json`.


## Corrección posterior de aceptación — 2026-09-30

Las 119 incidencias del resultado anterior están resueltas en el candidato:
**13.491 passed por inventario**, con 15 regresiones nuevas y aceptación real
por entorno. Véase [omitted-acceptance.md](omitted-acceptance.md) y su JSON por nodeid.
Los contadores antiguos arriba describen ejecuciones históricas.
