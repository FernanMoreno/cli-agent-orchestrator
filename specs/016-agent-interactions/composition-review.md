# Composition Review Report — Feature 016

## Estado vigente — 2026-10-12

**IN PROGRESS; sin aprobación global.** Spec021 sustituyó la persistencia
runtime por PostgreSQL nativo. El informe SQLite siguiente es histórico y su
veredicto no se aplica al árbol actual. La revisión posterior de PostgreSQL,
AG-UI y workflows se registra en las actualizaciones de este documento y en
[validation.md de Spec021](../021-postgresql-persistence/validation.md).

La revisión actual encontró dos deadlocks reales en órdenes de locks Work,
reproducidos en PostgreSQL con procesos concurrentes. Ledger y Design están
corrigiendo fronteras distintas; los checks estáticos anteriores son del árbol
previo a esos arreglos y no validan el árbol nuevo. La aceptación queda
bloqueada hasta repetir los checks pertinentes con ambas fuentes congeladas.

La revisión T049 confirmó que directorio de artefactos y namespace/store
PostgreSQL no son la misma identidad; la caché de grafo incluye ahora la
identidad completa del target. Regresión RED/GREEN y módulo nativo: **25 PASS**.
T079 quedó verificado en la corrida PostgreSQL del 2026-10-12: 20/20 registros
confirmados y 100/100 preguntas visibles; el p95 desde el commit de registro
hasta el retorno de metadata fue **789,4 ms**. El tiempo solicitud-a-visible,
que incluye espera anterior a la confirmación, queda visible como diagnóstico:
**3312,8 ms p95**. El gate SC-001 usa el inicio confirmado que define la
especificación, no el tiempo previo a admitir la pregunta. La comprobación de
capacidad global, replay/idempotencia y validación Work permanecen en sus
fronteras PostgreSQL reales. La aceptación global de la feature sigue abierta.

La consolidación en `main` está contrastada por hashes y equivalencias de
fuente; no concede aprobación a la suite r9 interrumpida ni a la partición
Linux de **5 PASS/3 FAIL**. Codex y Claude ya completaron recorridos explícitos
reales sobre PostgreSQL; OpenCode sigue sin recorrido con modelo configurado.
La proyección AG-UI, la imagen final, la cobertura y los gates de host requieren
sus comprobaciones finales después del nuevo freeze.

> **Revisión de la versión anterior después de su suite completa:** PASS con límites de aceptación abiertos. La continuación AG-UI/Docker/host está en ejecución en [agui-host-validation.md](agui-host-validation.md) y necesita una nueva revisión final. Evidencia previa en [full-validation.md](full-validation.md). No equivale a aceptación íntegra de todos los proveedores ni de runners omitidos.


## Changed behavior

Implementación de preguntas explícitas mediante MCP, bandeja web, respuestas tipadas y recibos durables. El ledger distingue aceptación, retorno al agente y confirmación. La delegación al supervisor requiere revisión del propietario de la pregunta y una asignación vigente. Los diálogos nativos carecen de adaptador de respuesta verificado: se presentan como avisos manuales sin copiar transcripciones ni inventar opciones.

## Directly changed subsystems

- Persistencia: namespace `interaction_*`, modelos, capacidades privadas, política y servicio de interacciones.
- Transporte: router FastAPI, startup, herramientas MCP y helper HTTP loopback.
- Ejecución: creación de terminales, entrega, generaciones, sesión/asignación, monitor y presupuesto de `agent_step`; lanzamiento Work V1.
- Proveedores: configuración del servidor MCP incluido y observación acotada del terminal.
- Web: API, pestaña Questions, formulario, estados de entrega, delegación y navegación manual.

## Neighboring systems and source verification

Se inspeccionaron WorkRepository, WorkAuthority, WorkContracts, WorkAdmission, WorkBackendView, permisos efectivos de perfiles, InboxService, el bridge y los planos AG-UI, autenticación de navegador/MCP y sesiones/incarnaciones. Graphify se usa como mapa de impacto y sus relaciones se contrastan con estas fuentes; el detalle de extracción aislada está en [graphify-impact.md](graphify-impact.md).

## Boundaries and invariants

| Frontera | Garantía implementada | Evidencia ejecutable |
| --- | --- | --- |
| SQLite / Work | Historial/checksum propio; Work mantiene sus migraciones; validación Work y mutación comparten transacción | `test_interaction_repository.py`, `test_interaction_work_authority.py` |
| Terminal / capacidad | Terminal, incarnación y generación vigentes; token solo en archivo privado y hash en SQLite | Tests de providers, creación y seguridad |
| Perfil / proveedor | El overlay solo modifica el servidor CAO incluido; no incorpora permisos nuevos ni env a servidores ajenos | `test/providers/test_interactions.py`, tests Codex |
| MCP / HTTP | Solo loopback, sin proxy de entorno ni redirects; identidad no procede de argumentos del agente | Tests de utils, herramientas y transporte |
| Humano / agente | Principal sellado para humanos; contenido restringido al dueño o delegación exacta; metadata general redacted | Tests API, seguridad y delegación |
| Versión / respuesta | If-Match fuerte, clave de intención y CAS; una respuesta efectiva por versión | Tests HTTP, concurrencia y recuperación |
| Aceptación / observación | Recibo aceptado, returned_to_mcp y agent_acknowledged separados | Flujo explícito y stdio real |
| Espera / ejecución | Las requeridas bloquean aun omitiendo wait; opcionales no bloquean; plazo humano separado del presupuesto activo | Tests wait composition y agent_step |
| Espera / Inbox | Guardia común impide contestar vía input genérico o entrega eager; hermanos independientes conservan elegibilidad | Composición con InboxService real |
| Delegación / ciclo | Relación de asignación derivada por servidor y revisión exacta; revocación/TTL generan versión nueva; SCC durable | Tests delegation y cycles |
| Efecto / reinicio | Claim sin confirmación se recupera incierto; no reintento automático | `test_interaction_uncertain_delivery.py` |
| Nativo / resultado | Aviso sin contenido privado; cierre exacto no crea respuesta ni reemplaza un resultado verificado | Tests del monitor y recuperación manual |
| Work / cliente ordinario | Contrato/attempt/grant exactos; V2 y remoto sin fallback local; WorkDecisions conserva autoridad de aprobación | Tests Work y fences HTTP/WS |
| UI / contexto | Snapshot durable y polling; limpieza por nodo/epoch; formulario solo con permiso actual | Tests web y navegación exacta |

## Architecture, contract and integration checks

Comprobaciones del árbol congelado, todas finalizadas con exit 0:

- Suite Python completa aplicable, integraciones incluidas: **16949 passed, 119 skipped**, cero fallos/errores; 2621,15 s. Los skips de host/dependencias/proveedores tienen inventario explícito.
- Black/isort completos: 1231 archivos; mypy: 405 módulos, sin errores.
- Web: **504 tests**, build y **20 Playwright**. Apps: **92 tests**, typecheck, cuatro builds, JIT, presupuestos de tamaño y **7 Playwright**.
- Cobertura: Python **87,0429%**, Apps **90,01%**, ambos floors conservados y ratchet ejecutado con informes verificados.
- Composición ejecutada después de tests: **408 archivos / 1713 dependencias / 5 contratos conservados / 0 rotos**.
- Contratos HTTP/MCP/stdio/Work, idempotencia/concurrencia, incertidumbre, reinicio y ACL incluidos en la suite total. Las pruebas frontend y API se mantienen en la misma versión; no se añade Pact a una frontera monolítica sólo por su nombre.
- Los nuevos contratos de guard nativo (12 tests), identidad/TTL async (4) y recuperación de monitor con SQLite real forman parte de la ejecución total.
- Graphify: extracción aislada final 12426 nodos / 33224 aristas, hashes contrastados; se decide no refrescar de nuevo porque la estructura no cambia después.


## Real dependencies

SQLite real para restricciones, transacciones, concurrencia, Work y recuperación. TestClient atraviesa rutas de producción. La prueba stdio ejecuta un proceso MCP real contra uvicorn en un socket TCP loopback. Los backend/provider de algunos escenarios son fixtures: ese resultado no certifica una CLI real ni una respuesta nativa. Los recorridos browser/provider se distinguen en la evaluación.

## Revisión de composición T079 — 2026-10-12

El registro y su grupo se insertan en una transacción PostgreSQL que conserva
el bloqueo global de capacidad, la validación de la fuente y el control de
capacidad. El perfil mide el instante de confirmación inmediatamente después
de que termina correctamente el contexto de transacción, y mide el extremo de
visibilidad cuando `list_metadata` devuelve el snapshot. Además conserva el
tiempo desde el inicio de la solicitud, la espera/retención del lock y las
latencias de creación y lectura; no elimina la demora anterior a confirmar.
En la corrida actual el p95 de espera del lock fue **1857,2 ms** y el p95 de
retención **173,5 ms**. Las 20 creaciones terminaron correctamente y el
snapshot mostró las 100 preguntas. El tiempo total más alto es diagnóstico y
no se oculta; SC-001 empieza en el registro confirmado.

La creación agrupa los eventos `question_created` en
`InteractionRepository.append_events` dentro de la misma transacción del grupo.
El método exige una transacción de escritura propiedad del caller, valida toda
la metadata antes del primer DML, verifica que `executemany` insertó el lote
completo y no confirma ni cierra la conexión. Después se inserta el evento
`group_created`, de modo que el orden durable sigue siendo preguntas primero y
grupo después. Las pruebas PostgreSQL verifican IDs/secuencia, orden, rechazo
de metadata antes de escribir, rollback ante una fila duplicada y rechazo de
conexiones sin transacción o de solo lectura.

La lectura del snapshot precarga terminales, incarnaciones, intentos Work y
fuentes dentro de la misma transacción repeatable-read. El fast path evita
validaciones repetidas para una fuente sin vínculo Work únicamente si no hay
intento histórico y la fuente no declara un modo administrado; fuentes ligadas,
con historial o en modo administrado conservan validación Work completa. La
verificación del esquema Work sigue ocurriendo en cada operación de lectura,
y una lectura nueva observa revocaciones/cambios de fuente. La corrida
PostgreSQL enfocada más reciente pasó diez casos sobre ciclos, expiración,
reemplazo de fuente, drift del allowlist, límite mixto y estos caminos Work.

Evidencia dirigida (ejecuciones separadas; sus conteos no se suman):
`audit/capacity-confirmed-registration-profile.log` (2 passed),
`audit/capacity-related-regressions.log` (10 passed),
`audit/t079-native-refresh-8.log` (10 parametrized cases passed; 2 existing
deprecation warnings), y `audit/append-events-shortbatch-regressions.log`
(4 passed, incluyendo la regresión de lote incompleto y tres pruebas PostgreSQL
del repositorio). Mypy completo sobre el snapshot de 497 archivos fuente pasó:
`audit/t079-mypy-full-r2.log` (424 source files, no issues); el manifiesto está
en `audit/t079-static-r2/src-manifest.sha256`. Black e isort dirigidos a los
cuatro archivos Python de T079 pasaron. Graphify code-only aislado, sobre la
copia ext4 cuyo manifiesto de fuente coincide con el árbol compartido, produjo
**13.699 nodos / 38.600 aristas**; `graph.json` SHA-256
`ea223c6528d60d5351fc42e66ff46dd595da2c764bf37a6aaec9518705fcbc58`, manifest
SHA-256 `e639aeb8a1179bb22a86d3785bb573027434e9ac748678c16d114230b256e427`.
Graphify contiene los nodos `InteractionService.create_group` y
`InteractionRepository.append_events`; el extractor no resolvió la llamada a
través del receptor dinámico `self.repository`. La fuente confirma esa llamada
en `service.py:2730` y la definición en `interaction_repository.py:450`; esa
comprobación manual es la autoridad para el enlace. El manifiesto de los cuatro
archivos T079 está en `audit/t079-owned-files-r2.sha256`. El veredicto permanece
**IN PROGRESS** hasta terminar los gates globales de backend, Docker/QEMU y
SC-007 descritos arriba.

## Bloqueador actual: orden de locks Work — 2026-10-12

Una regresión PostgreSQL determinista con dos procesos reproduce
`psycopg.errors.DeadlockDetected` sobre `work_attempts`. La carrera cruza la
liberación de una concesión por el servicio con la transición de intento y su
evento durable; el test fuerza esa intercalación mediante una barrera y falla
con el árbol previo al arreglo. Evidencia RED:
`/home/felni/.cache/caos-spec021-evidence-20261012/ledger-deadlock/red-regression-r2.log`
(1 failed, 9.15 s). Ledger está implementando el orden de locks acordado y
Design está cubriendo por separado la inversión del agregado del scheduler.

La segunda reproducción independiente combina `WorkAdmission.preview` con
`WorkService.retry` → `Scheduler.release`; los procesos invierten el lock
advisory del agregado y el lock FK del job y PostgreSQL detecta otro deadlock.
Evidencia RED: `/home/felni/.cache/caos-spec021-evidence-20261012/design-scheduler-lock/scheduler-lock-red-2.log`
(1 failed, 10.43 s). Design está aplicando un orden común que toma primero el
job y vuelve a comprobar las condiciones después de esperar el lock. Ninguna
reproducción se considera resuelta hasta que ambas regresiones PostgreSQL
pasen con el árbol congelado y se repitan mypy, Black, isort y composición;
los resultados T079 anteriores se conservan como evidencia del árbol previo,
no como aceptación de los arreglos.

## Failure analysis

Además de los defectos iniciales de contratos/permisos, la ronda completa reprodujo:

- Guard nativo basado en estado transitorio: el aviso durable bloquea ahora input/key automático antes del efecto, incluso con IDLE, TTL o cancelación sin cierre físico.
- Reinicio del monitor: pin durable con digest de target; dos capturas cambiadas estables; cierre event-only sin liberar un trabajo antiguo.
- Aviso cerrado o supersedido reutilizado: se crea una ocurrencia nueva y sólo se deduplica la observación actual.
- Espera liberada por otra pregunta: question_id y observation_id quedan fijados entre polls y límites de tiempo.
- TTL escapado tras WebSocket: stale nativo también caduca; cierre posterior no revive ni libera la espera.
- Runner: perfil compartido entre workers disparaba correctamente la protección de servidores simultáneos; reproducción roja y prueba independiente verde con plugin externo por worker. No se modifica ese guard ni su test.

Los errores previos de fixtures se reproducen contra los límites reales (engine/SessionLocal, singleton ligado a DB, snapshots y recibos tipados) antes de corregirlos. La suite total final vuelve a ejecutarse completa; las rondas fallidas se conservan como tales.


## Residual risks and release verdict

**Veredicto vigente: IN PROGRESS.** La regresión de deadlock Work descrita
arriba es un defecto reproducible pendiente de corrección y revalidación. La
entrada nativa sigue siendo manual: no hay adaptador automático verificado.

SC-007 pendiente: Claude completa el recorrido externo; Codex externo no genera preguntas/tools; OpenCode no dispone de ruta de modelo y el recorrido loopback tampoco satisface aceptación; Hermes demuestra diálogo nativo mediante CLI/PTy y modelo loopback, pero no overlay explícito seguro. T011/T066 permanecen parciales. T064 no añade proyección de recibos AG-UI.

119 skips corresponden a entorno/host/dependencias/opt-in; Hermes nativo se ejecutó aparte. Docker sin daemon, host Linux aprobado, matriz Python/OS y suites legacy externas interrumpidas no están certificados. No se copian credenciales ni se relajan políticas para cambiar esos resultados.

La revisión final preserva Graphify/auditorías/specs 017–020 preexistentes, comprueba el diff y confirma los hashes de la producción congelada. Contratos y decisiones durables permanecen en spec 016; no se duplican logs ni hechos generados en Obsidian. No hay commit ni publicación.

## Continuación PostgreSQL — 2026-10-11

Los resultados anteriores describen sus fuentes históricas. La aceptación actual
se compone con [spec021](../021-postgresql-persistence/validation.md): browser
completo **20 passed, cero skips**, p95 DOM **1294 ms**; instalación Docker
completa **1 passed**, 225,81 s, incluidos reinicio, actualización, rollback y
recuperación de candidato fallido. La importación Docker de tres ámbitos,
T020/T097 y la suite backend global siguen pendientes.

Codex **0.162.1 / gpt-6-luna** ya completó el canal explícito real sobre
PostgreSQL: dos preguntas (texto y selección), dos respuestas humanas y ambas
entregas `agent_acknowledged`. El probe de servicio confirmó coincidencia de
grupo, preguntas, versiones y recibos sin registrar sus valores. Evidencia:
`/home/felni/.cache/caos-spec021-live-acceptance/.origin/logs/codex-gpt6luna-live-r7-ack-identity.log`.
El harness autoriza sólo las siete herramientas de interacción, manteniendo
la política global `never`. Las rondas previas fallidas se conservan; el éxito
posterior no demuestra la causa del `not_found` anterior.

SC-007 completo sigue pendiente: el resultado Codex no acredita OpenCode ni
el overlay explícito de Hermes. La suite AG-UI actual y la revisión final de
composición requieren la ejecución global PostgreSQL; no se reutiliza como
aceptación actual la suite SQLite histórica.

### Aceptación Docker PostgreSQL r5 — 2026-10-11

La imagen corregida r5 pasa instalación/reinstalación/upgrade/rollback (1 caso, 281,33 s) e importación SQLite de main/auth/peer seguida de instalación normal (1 caso, 90,47 s). Se verifican recibos antes de activar el planificador y se conservan los archivos fuente. Logs: `/home/felni/.cache/c16t/spec021-app-acceptance-r5/install.log` y `/home/felni/.cache/c16t/spec021-app-acceptance-r5/import.log`. La suite global PostgreSQL, T020/T097, grafo final y SC-007 completo siguen pendientes; no es cierre de la feature.
