# Revisión de composición de spec007

Fecha: 2026-10-02. Gate ejecutado sobre el código final: **PASS**; cinco contratos de imports conservados, cero rotos.

**Vigencia**: el PASS de abajo es histórico para el snapshot del 2026-10-02. La revalidación de proveedor del 2026-10-05 está registrada al final de real-validation.md y quedó incompleta por `OSError: Cannot allocate memory` al iniciar el MCP de Claude. La aceptación de composición cross-provider de este cambio continúa abierta.

## Fronteras revisadas

Proveedor/instalación: validación y launch comparten resolución absoluta y entorno. El perfil nativo se prepara antes, no se instala durante initialize. La configuración V2 usa un directorio privado propiedad de la ejecución; cleanup no borra configuración ajena.

Recibo/recuperación: tabla aditiva por terminal/generación conserva el CHECK original prepared/sent/result_verified. Settlement y resultado público estable comparten transacción; cancelling/cancelled bloquean éxito tardío. Un hijo cancelado histórico no se revive; una nueva generación solo se libera tras cancelación confirmada. Borrado de terminal elimina su historial de recuperación.

Monitor/persistencia: solo detectar respuesta final inicia el presupuesto automático. Intentos y fecha sobreviven reinicio; deadline independiente evita depender de nuevos frames. La publicación de reconcile conserva waiting_user_answer/waiting_quota bajo el lock del monitor. Un trabajador y un supervisor sin NativeChild siguen el mismo contrato.

Cancelación/backend/inbox: flock de despacho serializa reset y settlement. El job de cancelación continúa aunque se desconecte HTTP; adquisición abandonada libera su lock eventual. Fence precede reset; agente debe quedar listo antes de confirmar cancelled. Se reusa FIFO y transcripción. Readiness se publica después de soltar el lock para que inbox entregue pendientes por su flujo habitual. No se vuelve a escribir la tarea original.

API/consumidores: autorización read para consulta y write para acciones; Work-owned se rechaza y ownership inaccesible falla cerrado. Generación obligatoria rechaza acciones stale. Error tipado distingue ausencia de prueba de fallo interno. Web descarta respuestas viejas y conserva acceso FULL; CLI/MCP no convierten pending en success.

## Comprobaciones

781 pruebas Python, 105 web, build TypeScript/Vite y composición pasan. La integración real de tmux utiliza un servidor privado y lo cierra. La segunda ejecución Claude/Codex/OpenCode valida el snapshot final; resultados e intervenciones en `real-validation.md`.

La revisión del diff se centró en persistencia, observador/deadline, eventos, locks, reset, restauración, autorización y consumidores. Se corrigieron carreras detectadas de generación, diálogo, cancelación y resultados tras nueva tarea. Se preservaron cambios previos ajenos y no se hizo commit.

## Compatibilidad y reversión

No se reconstruye tabla de recibos ni se cambia su enum. La tabla nueva es aditiva. Una versión anterior ignora el overlay de cancelación y por tanto no debe retomarse sobre turnos cancelados sin resolverlos antes: rollback seguro exige dejar terminales sin turnos pendientes/cancelados activos y conservar copia de la DB. No se ofrece migración destructiva ni se borra historial para simular compatibilidad.

## Fallos de composición corregidos

- Un respawn sin comando repetía el comando inicial del pane. La shell explícita impide repetir la ejecución cancelada.
- Respawn perdía CAO_TERMINAL_ID del trabajador y heredaba el ID del supervisor. Fuente y procesos reales confirmaron la primera frontera incorrecta. El ID autoritativo se pasa ahora por argumento obligatorio al backend y `respawn-pane -e`; la regresión tmux lee abcd1234 en shell de trabajador sobre sesión super001. El mensaje anterior con remitente erróneo se excluye de aceptación.
- Los consumidores CLI/web sobrescribían diálogo/cuota aunque el backend los conservaba. Se corrigió esa proyección y se prueban seis combinaciones de espera/recuperación.
- Las revisiones corrigieron carreras de generación, publicación de espera, cancelación desconectada y nueva tarea tras cancelación de hijo histórico.

## Dictamen

**PASS WITH RISKS**. Aceptación real final completada con las intervenciones y límites registrados; recursos propios cerrados. 781 pruebas Python, 105 web, build y gate pasan; la suite centrada de 31 pruebas de backend/servicio/tmux confirman identidad. Revisión independiente sin bloqueantes. Dependencias reales utilizadas: SQLite, tmux privado y proveedores reales en contenedor propio. Contratos HTTP y generación cubiertos por pruebas API y consumidores; no se añadió Pact porque proveedor y consumidores se mantienen en este repositorio. Riesgos y casos omitidos se explican en implementation-evidence.md y real-validation.md.

## Revisión de composición de T023–T026 — 2026-10-05

- **Subsistemas modificados**: configuración de inicio de Codex; runtime OpenCode v2, sus datos/autenticación por terminal y la puerta MCP previa a completar `initialize()`.
- **Vecinos revisados**: perfil MCP instalado, `terminal_service`/creación diferida de terminal, manager de proveedores, helper de configuración privada v2, `send_input` y cleanup del proveedor. La tarea solo se entrega después de que `initialize()` retorna.
- **Contratos comprobados**: Codex solo fuerza `required`/timeout en el MCP propio de CAO, después de overrides del perfil; OpenCode v2 solo espera si ese MCP figura configurado, usa log/data home privados, preserva el `auth.json` local por symlink y elimina los artefactos al limpiar.
- **Arquitectura**: `project-composition-check "$(cat .ai/project-name)"` pasó; Import Linter analizó 393 archivos, 5 contratos conservados y 0 rotos.
- **Graphify**: extracción AST aislada de los tres módulos cambiados produjo 141 nodos y 254 relaciones; se contrastó con la fuente y no se sobrescribió el grafo global previamente modificado.
- **Pruebas**: suites focalizadas completas Codex/OpenCode/init-timeout: 406 passed, 3 skipped, 2 avisos de deprecación. Prueba real intentada contra CAO/tmux y el proyecto pedido: Claude empezó la tarea, pero su proceso MCP murió al importar Python con `OSError: Cannot allocate memory`; no llegó a crear Codex/OpenCode ni callbacks. No se ejecutó navegador. No se usó Pact: estos proveedores y consumidor CAO evolucionan dentro de este repositorio.
- **Dependencias reales**: servidor CAO y tmux privados llegaron a iniciar; la dependencia MCP stdio falló por presión de memoria del entorno. El gate aislado no sustituye esta integración real.
- **Riesgos restantes**: el evento OpenCode v2 `mcp connected server=...` es evidencia del log local de la versión instalada, sin contrato público estable; falta validarlo en una sesión real. También falta repetir la coordinación sin error de recursos y la parte de navegador.
- **Dictamen actual**: **FAIL — aceptación cross-provider incompleta**. Los cambios deterministas pasan, pero no hay prueba de ida/vuelta real para este snapshot.

## Revisión de las correcciones T028–T030 — 2026-10-05

- **Fronteras**: configuración de perfil y timeout del proveedor; resolución de identidad del MCP compartida; comando de Claude; adaptador/configuración privada OpenCode; stderr real del CLI; puerta de readiness; creación diferida y entrega de la primera tarea.
- **Invariantes**: solo CAO fuerza carga en Claude; los overrides MCP del pane sobreviven a las asignaciones por defecto. Solo un MCP habilitado activa la espera OpenCode. INFO y stderr se capturan por terminal, en carpeta 0700 y archivo 0600; el TUI conserva stdout. La coincidencia del nombre es exacta y no acepta prefijos de otros servidores. La limpieza sigue limitada al directorio propio. No se alteraron almacenamiento, generaciones, recibos, cancelación ni reglas de inbox.
- **Pruebas**: RED de logfmt/disabled (4 fallos) y RED de captura explícita (2 fallos); GREEN OpenCode v2/launch/unit final, 102 passed. Claude/Codex/OpenCode v2/resolución MCP/timeouts: 556 passed, 3 skipped. Ambos runs mostraron dos avisos Pydantic existentes. La prueba de shell preserva presupuestos explícitos 120000/180000 ms y el caso de terceros conserva su política de carga.
- **Arquitectura y estructura**: gate final PASS, 393 archivos, 1654 dependencias, cinco contratos conservados. Graphify final aislado: 202 nodos y 371 relaciones; imports y llamadas contrastados con la fuente. No se reescribió el grafo global previo.
- **Dependencias reales**: SDK stdio con 72 herramientas; OpenCode real inicializado mediante CAO (HTTP 201 y señal privada positiva). Los intentos conjuntos llegaron a crear los tres proveedores, pero no acreditaron ambos callbacks ni síntesis. Hubo cierres MCP y timeouts de API durante presión de memoria; la causa exacta de todos los cierres no está demostrada.
- **Riesgos y aceptación**: la interpretación de logfmt depende del CLI instalado 2.0.18. Las tareas de coordinación se validan por etapas; no se implementó continuación de una tarea abierta ni un bypass de reconciliación. La composición con los tres proveedores y la prueba de navegador permanecen abiertas. **No se declara PASS de aceptación global.**

### Revisión de T032–T037 — 2026-10-05

Transportes tmux/libtmux, proveedor Claude, descubrimiento MCP y contrato de fases revisados con sus vecinos API/FIFO/monitor/inbox/recibos/cancelación. Sin cambio de almacenamiento, CAS, generación o parser. Timeout de comando acotado; transporte incierto no autoriza reentrega; cleanup conserva el fallo original; señal privada actual requerida antes de primera tarea Claude; cierre de despacho conserva resultados pendientes. Arquitectura: PASS, 396 archivos/1660 dependencias, cinco contratos conservados. Pruebas aplicables: 979 passed, cuatro skipped. Dependencias reales: tmux suspendido/reanudado, SDK MCP stdio y los tres proveedores con SQLite y proyecto copiado. Colaboración de revisión: PASS, ambos callbacks y síntesis LAST verificada. Escenario navegador pendiente y presión de memoria externa permanece como límite. [Informe detallado](correcciones-adicionales-20261005.md).

### Más pruebas reales — 2026-10-05

Dos rondas funcionales acreditadas con Claude `ad65089e`, Codex `df4b5de9` y OpenCode `b0ac158a`: recibos actuales verificados, dos callbacks nuevos y reutilización de las mismas terminales. El runner original falló por un marcador partido entre líneas; la comprobación independiente de recibos/secuencias/callbacks/síntesis pasó sin alterar estado interno. HTTP real: 24 creaciones concurrentes, 11 negativos, reinicio y datos corruptos PASS. Chromium real: creación/completado/recarga, título HTML inerte y recuperación de errores PASS. Pruebas existentes: 5 Python + 1 Node PASS. Original intacto.

Se reprodujeron aceptación de UTF-16/32 fuera del contrato UTF-8 y límite de cuerpo no documentado. Salud CAO: 34 HTTP 200 y 3 ReadTimeout entre 37 muestras; causa exacta abierta. T020/SC-006 siguen abiertos por faltar el escenario único de implementación/integración dirigido por los proveedores. [Informe y límites](pruebas-reales-adicionales-20261005.md).

### Auditoría de T038–T043 — 2026-10-05

Fronteras revisadas: ingreso HTTP/UTF-8/Unicode/persistencia/frontend; captura OpenCode/contrato/recibo/generación/LAST; lifespan/driver/SQLite/coordinador/event loop/health; snapshot privado/ejecución/barrera FD/cleanup. Las conexiones SQLite se abren y cierran dentro del mismo worker; el epoch sigue comprobado dentro de la transacción del claim; tareas y subprocess asyncio permanecen en el bucle. No cambia el esquema ni se debilita el fencing. Snapshot creado exclusivamente y limpieza limitada a su propia ruta: dos ejecuciones con el mismo run_id conservan su contenido independiente.

Suite final afectada: **483 passed, 7 warnings**, incluido runner, planes preparados, barrera privada, APIs, recibos, recuperación, inbox y OpenCode. Gate final: **PASS**, 396 archivos, 1660 dependencias, cinco contratos conservados, cero rotos. Revisión del incremento contra snapshots iniciales: contrato público conservado salvo rechazo de texto contrario al contrato documentado; sin pérdidas de datos existentes ni cambios ajenos revertidos. El fallo histórico intermitente de observación del entorno del hijo y su relación no demostrada con la colisión quedan registrados en el informe detallado.

Dependencias reales, prueba cross-provider final, actualización Graphify y límites de aceptación se registran en [auditoria-profunda-correcciones-20261005.md](auditoria-profunda-correcciones-20261005.md). La revisión de colaboración y el navegador separado no completan T020/SC-006.

### Aceptación integral T020/SC-006 — 2026-10-05

**PASS WITH RISKS** para el escenario integral. Claude `b18708c5` delegó implementación API a Codex `57461dac` y frontend a OpenCode `b5c33b31`; recibió avisos de progreso/resultados por CAO, ejecutó las dos suites e integró el mismo artefacto. Los dos trabajadores y el supervisor terminaron con recibos actuales verificados; ninguna ruta reparada ni recibo forzado. Los avisos de progreso no acreditaron finalización: el supervisor registró los tests aún rojos y esperó el callback final antes de escribir INTEGRATION.md.

Subsistemas del artefacto temporal: lectura validada/atómica de tareas → GET /api/summary → fetch/startup/POST/PATCH → resumen DOM y errores. Contrato directo con enteros y pending=total-completed; GET no modifica datos; métodos no admitidos dan JSON 405; datos corruptos dan JSON 500; resumen vuelve a consultarse después de mutaciones; fracaso de consulta no deshace una tarea creada/completada. Vecinos: endpoints existentes, validación UTF-8/Unicode, persistencia/reinicio y texto HTML inerte. Sin migración ni duplicación de almacenamiento.

Tests del mismo artefacto: 15 API y 3 frontend PASS; Chromium real verifica vacío/crear/completar/recargar/fallo controlado/recuperación, cero pageerrors; servidor HTTP real reiniciado conserva resumen y tarea. Gate CAO actual PASS: cinco contratos conservados, cero rotos. Revisión del diff: seis archivos modificados por trabajadores y un INTEGRATION.md del coordinador; original y código CAO preservados. No se añadió Pact ni mocks de base de datos: contrato HTTP y servidor/Chromium/proveedores reales aislados proporcionan prueba directa.

Primer intento terminó abruptamente durante un volcado periódico de pilas y no acredita aceptación. Repetición controlada sin ese volcado periódico pasó; reporting nativo de faulthandler, sondeos de salud, loader hashes y comprobaciones de recibos permanecieron activos. No se atribuye una causa raíz demostrada a aquel cierre. Artefactos, evidencia y límites en [aceptacion-integral-20261005.md](aceptacion-integral-20261005.md).

### Diagnóstico nativo T044/T045 — 2026-10-05

Frontera corregida: runner → sitecustomize de servidor/MCP → muestreador de pilas CPython. Watchdog nativo SIGSEGV reproducido con metadatos liberados; se conserva faulthandler.enable y el muestreo pasa a un hilo Python con GIL, Event.wait(40), daemon y parada atexit. Sin cambio de runtime CAO, datos, generaciones, recibos ni consultas SQL. Regresión antes: dos fallos, incluido hijo rc=-11; después: 16 tests del diagnóstico/comprobador PASS. Servidor CAO aislado con allocator debug: watchdog rc=-11; sin watchdog 1776 solicitudes, muestreo Python 1848 solicitudes, ambos sin fallos de transporte ni cierre natural. El rc=-15 de los controles corresponde al SIGTERM del teardown propio, no a una caída durante la prueba. Gate actual: cinco contratos conservados, cero rotos. Revisión de incremento: una sustitución del instrumento y nueva regresión; proceso/timeouts/nonce/CAS intactos. Prueba de tres proveedores y límites de atribución histórica en investigacion-cierre-nativo-20261005.md.
