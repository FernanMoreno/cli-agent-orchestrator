# Evidencia de implementación de spec007

Fecha: 2026-10-02. Código implementado y validado con el snapshot final; recursos propios cerrados. No se hicieron commits ni se modificó la instalación personal.

**Nota de vigencia**: el resultado real del 2026-10-02 es histórico y corresponde al demo de recuperación señalado en real-validation.md. La revalidación posterior del 2026-10-05 sobre el proyecto pedido por el usuario no pudo cargar `cao-mcp-server` por falta de memoria del entorno; T020/SC-006 no se consideran completos para esta aceptación. Ver la evidencia actual al final de real-validation.md.

## Diagnóstico y secuencia

Se inspeccionó git status antes de editar. README, documentación de despliegue, instalador Docker y artefactos de specs005/006 ya tenían trabajo previo; se preservó. El instalador existente se modificó únicamente para alinear su comprobación de OpenCode con el lanzamiento.

Graphify se consultó antes de implementar: vocabulario receipt/terminal/opencode/inbox/status/settle/provider, 662 nodos alcanzados y 44 mostrados. Sus ubicaciones se contrastaron con la fuente; el grafo era orientación, no prueba de comportamiento. Después se extrajo una copia aislada del código final con `graphify update /tmp/spec007-graph-k0jb3kn_ --no-cluster`: 462 archivos, 11675 nodos y 30899 relaciones, sin LLM. La consulta final localiza `_transition_turn_cancellation`, `receipt_requires_reconciliation` y `_ensure_turn_recovery` en la fuente vigente. No se sobrescribió el grafo global que ya tenía cambios previos. No se duplicaron estos artefactos en el vault: no apareció una conclusión transversal que requiriera otra fuente durable.

Las regresiones se escribieron antes de sus cambios por frontera: launch/perfil, persistencia SQLite, recuperación del supervisor, contratos API y consumidores. En US1, las regresiones iniciales reprodujeron el PATH perdido, la aceptación de un perfil ausente y la sobrescritura de configuración ajena antes de corregirlos. Las revisiones encontraron además una causa real en tmux: `respawn-pane` sin comando vuelve a ejecutar el comando original. Se sustituyó por una shell explícita y una prueba con servidor tmux privado acredita cambio de PID, conservación del pane/entorno y ausencia de repetición de `sleep 300`.

## Trazabilidad

| Requisitos | Implementación y prueba |
|---|---|
| FR-001–004 | `providers/opencode_cli.py`, `scripts/docker_install.py`; pruebas launch-contract, unit/V2 e instalación. Binario absoluto y PATH fijados; perfil nativo validado sin sobrescritura; configuración privada propia limpiada ante fallo. |
| FR-005, FR-015 | `clients/database.py`, settlement en `terminal_service.py`; SQLite real con CAS, carreras verify/cancel, generaciones antiguas, cancelación confirmada e historial del hijo. |
| FR-006–007 | Recuperación durable independiente de NativeChild, tres intentos y deadline de 55 segundos desde detección final; timestamp conservado al reiniciar. Pruebas de supervisor ordinario, captura lenta, diálogo/cuota y trabajo todavía activo. |
| FR-008–009, FR-011 | `turn_recovery_service.py`, API y backend tmux; consulta/verify solo leen evidencia, cancel hace fence y reset de pane antes de liberar entrada; fallo conserva cancelling. Ninguna acción reenvía tarea original. |
| FR-010, FR-014 | Restauración de proveedor y suites inbox/receipt-delivery/deferred-submit; IDs y orden de mensajes se mantienen, desbloqueo publica readiness tras liberar lock. Reinicio restaura observación, no reconstruye prompt ni nonce. |
| FR-012–013 | API LAST 202 pendiente, 409 reconciliación/cancelación, 200 resultado verificado estable, 500 avería real. FULL conserva transcripción. CLI, MCP y web consumen el mismo turno/generación. |
| FR-016 | Diagnóstico público de proveedor/terminal/generación/motivo estable, sin hashes privados ni nonce. Resultado persistido redacta marcadores de recibo; digest conserva evidencia original. |
| FR-017 | Regresiones deterministas abajo y ejecución real documentada en `real-validation.md`. Los snapshots previos no acreditan las correcciones posteriores; el snapshot final se compara byte a byte con la fuente actual y acredita el flujo corregido. |

## Verificación ejecutada

- Python final: **781 passed, 2 deselected**, 5 avisos existentes, 95.65 segundos. Suites: SQLite/DB/tmux, integración tmux aislada, OpenCode launch/unit/V2, ProviderManager, recuperación/recibos/monitor/terminal/inbox/agent-step/deferred-submit, API, CLI, MCP e instalador.
- Web final: **105 passed**, cinco archivos: turn-recovery, turn-output, api, work-state y browser-terminal.
- `npm run build`: exit 0; aviso existente de bundle mayor de 500 kB.
- `project-composition-check "$(cat .ai/project-name)"`: **PASS**, cinco contratos de imports conservados y cero rotos.
- `git diff --check` y `node design-tokens/gen.mjs --check`: exit 0.

Logs consolidados locales: `/tmp/spec007-tests-final.log` y `/tmp/spec007-composition-identity-final.log`. La suite web final se ejecutó con `npm test -- --run src/test/turn-recovery.test.tsx src/test/turn-output.test.tsx src/test/api.test.ts src/test/work-state.test.tsx src/test/browser-terminal.test.tsx`, seguida de `npm run build`. No contienen el HOME privado de proveedores ni se publican credenciales.

## Límites de la evidencia

No se ejecutó toda la batería del repositorio; se ejecutaron las suites afectadas y el gate de composición. La parada de cancelación acredita el proceso del pane propio y el fence del resultado tardío; no se añadió aislamiento para procesos que se desprendan y se reparenten fuera de tmux. La prueba real usa imagen integrada existente con snapshot read-only, no reconstruye ni publica una imagen ni actualiza el despliegue personal. La renovación del login de Claude es un prerrequisito externo registrado, no una corrección automática de autenticación.

## Revisión y fallos adicionales corregidos

La revisión independiente de recuperación no encontró bloqueantes: 62 pruebas de DB/servicio/API/restauración pasan. Una reproducción adicional de desconexión confirma que cancelar al consumidor no libera el flock antes de terminar la recuperación.

La revisión final encontró que CLI/web sobrescribían diálogo/cuota pese a conservarlos el backend. Se corrigió esa primera frontera de proyección; seis combinaciones de espera/recuperación cubren los consumidores. 65 pruebas Python de consumidores y la suite final de 105 pruebas web pasan.

La segunda aceptación real encontró que respawn perdía CAO_TERMINAL_ID del trabajador y heredaba el del supervisor. Fuente y entorno de procesos confirmaron la causa. T022 pasa terminal_id autoritativo por argumento obligatorio hasta backend/cliente; `respawn-pane -e CAO_TERMINAL_ID=...` lo restaura antes de iniciar proveedor. La regresión RED leyó super001 donde debía abcd1234; la corrección devuelve abcd1234 en tmux real. La suite de 31 pruebas de backend/servicio/integración y revisión independiente pasan. El mensaje del snapshot anterior con remitente erróneo no acredita ida/vuelta; la prueba real conserva DB/tmux/IDs y repite ese flujo con código corregido. Los resultados y el límite de intervención del runner están en real-validation.md.

## Aceptación real del código final

El manifiesto final coincide exactamente con la fuente vigente: SHA-256 `aae9579be3435b516a92dd76f7100253089131aa75b12ef9b3721819b3e9ba92`. Mismo supervisor e8133ce6 y trabajadores Codex bdfefff9 / OpenCode a72ecfac conservados tras actualizar únicamente el servidor de prueba. Mensaje 4 OpenCode–Codex y ACK 6 Codex–OpenCode entregados con remitentes correctos. Los tres LAST devuelven 200, incluido el trabajador reutilizado después de cancelación confirmada, conservando el hijo histórico cancelado. Navegador final: POST 201, PATCH 200, reload conserva done:true, cero errores/fallos. Detalles, intervenciones del operador y cleanup en real-validation.md.

El mensaje 7 de confirmación también se entregó al mismo supervisor. Cleanup confirmado: API DELETE 200, cero sesiones, contenedores propios y auxiliares cerrados, puertos de prueba libres; cao-personal sigue running/healthy. Todas las tareas de spec007 quedan verificadas; ver informe real para intervenciones y casos omitidos.

## Convergencia posterior — 2026-10-05

T023–T026 añadieron el enrutamiento CAO explícito para Codex, grants normalizados para OpenCode v2, MCP requerido al inicio de Codex y una barrera de readiness OpenCode v2 con datos privados por terminal. Las suites locales de proveedor pasaron (406/3); el gate de composición también pasó. La prueba real sobre el demo solicitado no creó trabajadores porque el proceso MCP de Claude no pudo importarse bajo la presión de memoria del host. Por eso T020/SC-006 y T027/SC-007 siguen incompletos, aunque los requisitos locales T023–T026 estén probados. Evidencia actual y limitaciones en real-validation.md y composition-review.md.

Graphify se actualizó de forma aislada sobre esos tres módulos (141 nodos, 254 relaciones), se contrastó contra la fuente y se guardó el resultado de la consulta en `graphify-out/memory/`; el grafo global preexistente no se regeneró ni sustituyó.

## Correcciones tras petición de detectar errores — 2026-10-05

Se inspeccionó `git status` antes de editar y se preservó el trabajo previo. Graphify se consultó antes de implementar sobre `ClaudeCodeProvider`, `OpenCodeCliProvider`, startup y readiness; sus vecinos se contrastaron con la fuente. Los artefactos spec/plan/tasks se ampliaron antes de cambiar comportamiento.

1. El registro real de OpenCode 2.0.18 contiene `message="mcp connected" server=cao ...`. La expresión anterior esperaba texto sin las comillas logfmt y rechazaba una conexión válida. La prueba se cambió para usar la línea real; falló antes del parche. También fallaron tres casos de espera de MCP deshabilitado (`enabled: false` v1 y `disabled: true` v2). Se verifica además que un servidor con nombre parecido no satisfaga la espera.
2. La siguiente prueba real reprodujo `CONNECT_TIMEOUT after 30000ms` en Claude. Una importación aislada del MCP tardó 22.06 s y usó 103464 KiB RSS; la variación por presión de memoria se registró como condición externa. La prueba de comando falló por ausencia de `alwaysLoad` antes de cambiar el proveedor. Se configura carga de CAO al inicio y ambos presupuestos de conexión con el timeout del proveedor, preservando overrides del shell. La prueba de shell ejecuta un Claude ficticio que solo devuelve los presupuestos, y verifica que 120000/180000 ms explícitos sobrevivan al comando. La política de carga de servidores ajenos se conserva. La identificación MCP se comparte en el helper de resolución existente para evitar duplicación entre tres proveedores.
3. La pasada posterior sí creó Codex y OpenCode desde Claude. El supervisor recibió una tarea monolítica que le ordenaba terminar la respuesta mientras esperaba callbacks, omitiendo el recibo requerido; alcanzó reconciliación y bloqueó la siguiente entrega. Se repite la aceptación con una tarea inicial explícita de delegación y joins acotados; los callbacks son etapas nuevas con sus propios recibos. Esto no acredita continuaciones de una tarea todavía abierta ni autoriza eludir reconciliación.
4. La prueba por etapas encontró que OpenCode no generaba el archivo XDG esperado. El help instalado documenta `--print-logs`, emisión por stderr y la necesidad de `--standalone` para logs del servidor. Las regresiones fallaron por ausencia de flags y porque la espera leía otra ruta. Se captura stderr en `mcp-startup.log`, creado 0600 dentro del directorio privado 0700, con nivel INFO. El stdout conserva el TUI. Se conserva limpieza de artefactos propios y configuración de servidores ajenos.

La suite Claude/Codex/OpenCode v2/resolución MCP/timeouts pasó: **556 passed, 3 skipped**, dos avisos Pydantic existentes (277.24 s). Este resultado precede únicamente a la última corrección de captura del log OpenCode, cuya suite afectada se repite. Las pruebas del demo solicitado pasaron: 5 casos API con unittest y 1 caso frontend con Node. La primera ejecución del gate falló por `Cannot allocate memory`; la repetición después de limpiar el runtime propio pasó (393 archivos, 1654 dependencias, 5 contratos conservados). Ningún proceso de otros proyectos se cerró. La aceptación real final continúa pendiente de la pasada vigente.

Después de la captura explícita, las **102 pruebas OpenCode v2/launch/unit** volvieron a pasar (12.74 s, dos avisos Pydantic). El gate de composición volvió a pasar con el código final. La actualización AST aislada de los cuatro módulos produjo **202 nodos y 371 relaciones**, sin sobrescribir el grafo global. El SDK MCP real completó initialize/list_tools con el proceso stdio del repositorio y verificó `assign`/`send_message` entre sus **72 herramientas**. Una sesión CAO real únicamente con OpenCode también devolvió HTTP 201 y registró una conexión positiva en `mcp-startup.log`; la captura adicional de stderr del proceso confirmó el arranque FastMCP. Se cerró su runtime aislado. Estas dos comprobaciones no sustituyen la aceptación de callbacks entre los tres proveedores.

### T032–T037 — 2026-10-05

TDD previo: ocho fallos y cuatro pases al reproducir ausencia de límite tmux, cleanup que oculta entrega y contrato de fases ausente; cinco fallos al introducir la prueba de readiness Claude. Las primeras pasadas también detectaron un fixture incompleto, mocks apuntando a la frontera antigua y dependencia de OpenCode de la frase final del prompt; se corrigieron sin omitir esas regresiones. Evidencia actual: 497 + 464 + 18 pruebas en archivos distintos, cuatro skips; gate PASS (cinco contratos conservados); Graphify aislado de siete módulos (534 nodos, 1127 aristas). Colaboración real PASS con dos callbacks y resultado LAST verificado. Se preservaron snapshots del incremento para revisar cambios sobre el workspace ya modificado. [Informe completo](correcciones-adicionales-20261005.md). Las conclusiones durables quedan en este spec y su informe; no se duplican en el vault ni se copian hechos generados del grafo.

### Más pruebas reales — 2026-10-05

Dos rondas funcionales acreditadas con Claude `ad65089e`, Codex `df4b5de9` y OpenCode `b0ac158a`: recibos actuales verificados, dos callbacks nuevos y reutilización de las mismas terminales. El runner original falló por un marcador partido entre líneas; la comprobación independiente de recibos/secuencias/callbacks/síntesis pasó sin alterar estado interno. HTTP real: 24 creaciones concurrentes, 11 negativos, reinicio y datos corruptos PASS. Chromium real: creación/completado/recarga, título HTML inerte y recuperación de errores PASS. Pruebas existentes: 5 Python + 1 Node PASS. Original intacto.

Se reprodujeron aceptación de UTF-16/32 fuera del contrato UTF-8 y límite de cuerpo no documentado. Salud CAO: 34 HTTP 200 y 3 ReadTimeout entre 37 muestras; causa exacta abierta. T020/SC-006 siguen abiertos por faltar el escenario único de implementación/integración dirigido por los proveedores. [Informe y límites](pruebas-reales-adicionales-20261005.md).

### Auditoría profunda y correcciones T038–T043 — 2026-10-05

Corregidos contrato UTF-8/Unicode de la demo, exclusión del eco de contrato en LAST/recibos OpenCode, bloqueo del bucle HTTP por SQLite/PATH y colisión de snapshots del mismo run_id. Runner reproducible conserva secuencia, generación y callbacks nuevos. **483 pruebas CAO pasan**, siete avisos; **11 API demo pasan en Python 3.12 y 3.14**, una frontend Node; HTTP concurrente, persistencia y Chromium reales pasan. Gate final PASS: cinco contratos conservados, cero rotos. Revisión del incremento sobre snapshots iniciales y Graphify final aislado: siete archivos, 759 nodos, 1855 relaciones, sin fuentes fallidas, cero tokens LLM; grafo global preservado.

Prueba real final: Claude `438f1c36`, Codex `45639115`, OpenCode `72dd6886`, dos rondas, exactamente dos callbacks nuevos, las mismas terminales, LAST actual verificado. 14 muestras de salud HTTP 200, máximo 0,19 s. Runtime propio eliminado; datos originales preservados. Cinco módulos vigilados sin cambios, cuatro importaciones verificadas por hash; script_runner ejercitado por pruebas específicas, no por esta revisión cross-provider. El fallo intermitente anterior de observación del hijo se conserva en el informe, sin atribuirle una causa no probada. T020/SC-006 permanecen pendientes por el escenario integral de implementación/integración dirigido por proveedores.

[Informe de causas, correcciones, evidencia y límites](auditoria-profunda-correcciones-20261005.md). Las conclusiones durables quedan en este spec; no se duplican logs ni Graphify en el vault.

### Cierre integral T020/SC-006 — 2026-10-05

**PASS**: Claude `b18708c5` delegó implementación real a Codex `57461dac` y OpenCode `b5c33b31` sobre una copia del proyecto del usuario. API/resumen y frontend implementados, callbacks y recibos verificados, integración y tests ejecutados por Claude; 15 API + 3 frontend PASS también en comprobación externa. Chromium probó el mismo artefacto y el reinicio preservó estado. 19 muestras health HTTP 200, máximo 0,19 s; gate cinco contratos conservados, cero rotos. Original y código CAO preservados; runtime propio eliminado. La primera pasada interrumpida y su causa no demostrada permanecen documentadas. [Aceptación integral y límites](aceptacion-integral-20261005.md).

### Cierre del diagnóstico nativo T044/T045 — 2026-10-05

Reproducido SIGSEGV en watchdog faulthandler con los Python locales y servidor CAO aislado; core/GDB prueban acceso a metadatos liberados. Se sustituyó sólo el muestreador del runner por hilo Python/GIL con diagnóstico nativo conservado. TDD: dos fallos antes, 16 casos PASS después. Gate cinco contratos conservados; colaboración real final dos rondas PASS con Claude/Codex/OpenCode y pilas muestreadas activamente. Atribución del cierre histórico muy consistente, sin inventar su core/señal ausentes. [Investigación, pruebas y corrección](investigacion-cierre-nativo-20261005.md).
