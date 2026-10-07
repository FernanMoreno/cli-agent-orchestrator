# Prueba real aislada de spec007

**Fecha local**: 2026-10-02. **Veredicto histórico**: FR-017/SC-006 acreditados sobre `cao-turn-recovery-demo-20261002-f9086a25`, con intervenciones documentadas. Esta evidencia no corresponde al proyecto de colaboración que el usuario entregó después; véase la revalidación del 2026-10-05 al final. La aceptación actual de spec007 sigue abierta.

## Recursos y fuente probada

Proyecto conservado: `/mnt/c/users/ferna/onedrive/escritorio/cao-turn-recovery-demo-20261002-f9086a25`. Las implementaciones de `api/server.py`, `CONTRACT.md` y `frontend/` fueron escritas por los proveedores reales durante la primera ejecución; el runner no escribió implementación de la demo. La segunda ejecución auditó e integró esa misma aplicación conservada.

Dos instancias propias sucesivas, sin solapamiento: `cao-spec007-20261002-f9086a25` y `cao-spec007-20261002-353bbc8e`. API loopback 19882; demo loopback 19982, servidor interno 8092. Misma imagen integrada base `sha256:2aebbefefe7becaeb738f812a09de36baad9e9e2ff6ec323806cc9ca964ad941`, con snapshot del paquete candidato y web compilada montado read-only. Agentes en tmux del mismo contenedor; montaje del proyecto exacto. No se montó el Escritorio completo ni se modificó `cao-personal`.

Los manifiestos privados contienen SHA-256 por archivo, excluyendo bytecode/caché Python. El checksum del manifiesto identifica exactamente cada snapshot, sin requerir commit:

| Snapshot | UTC | SHA-256 del manifiesto |
|---|---|---|
| Primera implementación/demo | 2026-10-01 22:53:33 | `9889dcf5d440a5c847cc49cfeee8d49beee27e627166befaba30baf9e1431162` |
| Segunda ejecución antes de corrección de identidad | 2026-10-01 23:09:34 | `cf19bcc4f2b979f71f9c89bd5bcb39b763d7bb1489ecc7956f14df9d5145b77e` |
| Paquete final, identidad y consumidores corregidos | 2026-10-01 23:19:32 | `aae9579be3435b516a92dd76f7100253089131aa75b12ef9b3721819b3e9ba92` |

Comparación final del manifiesto con **todos** los archivos actuales de `src/cli_agent_orchestrator`, incluyendo web compilada y excluyendo únicamente bytecode/caché: igualdad exacta, cero diferencias. Los snapshots anteriores son evidencia histórica; la aceptación final de recuperación/mensajería se apoya en el último snapshot.

Evidencia privada: `/home/felni/.local/share/cao-spec007-20261002-f9086a25` y `/home/felni/.local/share/cao-spec007-20261002-353bbc8e`. Se conservan manifiestos, respuestas API, transcripciones, identidad de procesos saneada, prueba de navegador, cierre y scripts del runner bajo permisos owner-only. Credenciales y recibos completos no se reproducen en este informe.

## Preparación e intervenciones

Se prepararon antes de cada lanzamiento credenciales privadas y perfiles propios `turn-supervisor`, `turn-api`, `turn-frontend`, reutilizando las suscripciones/modelos disponibles. `cao install turn-frontend --provider opencode_cli` terminó con exit 0 antes de cualquier sesión de cada instancia. OpenCode arrancó mediante el entorno candidato sin enlaces ni reparación de PATH, y sin instalar perfiles durante la ejecución.

La primera creación del contenedor falló antes de iniciar agentes por faltar el montaje privado de sockets; se corrigió el runner y se recreó exclusivamente su contenedor vacío. La segunda instancia necesitó esperar al backend detrás de nginx antes de crear sesión; una solicitud temprana recibió 502 sin crear agente.

La primera tarea real de Claude respondió `Login expired`. Su turno pasó por `LAST=202` y reconciliación con `LAST=409`; la verificación manual conservó incertidumbre. La cancelación reinició únicamente su pane; el cliente agotó 180 segundos esperando la inicialización con login vencido, y la consulta posterior confirmó `cancelled`. Una copia anterior preparada y vigente de credenciales privadas permitió una **nueva tarea explícita**, con otra generación y el mismo supervisor. Después el usuario renovó el login existente; la segunda ejecución copió esas credenciales renovadas antes del lanzamiento. No hubo modificación global de autenticación ni repetición automática de una entrega incierta.

Se solicitaron verificaciones explícitas de recibos auténticos que llegaron tarde, tanto para turnos del supervisor normal como de trabajadores. La entrada de integración inicialmente recibió 409 con `delivery_may_have_occurred:false`; solo se volvió a solicitar después de verificar el turno vigente. No se escribieron recibos en almacenamiento ni se suprimió su comprobación.

Claude interpretó erróneamente una observación anterior de Codex como ausencia actual de `app.js`; el operador le pidió inspeccionar el archivo realmente existente y continuar la integración. El mismo supervisor retiró recibos literales de su documento de auditoría antes de conservarlo como artefacto público. OpenCode entró en un bucle de selección de herramientas durante la segunda auditoría: se canceló ese turno y se le dio una nueva tarea acotada utilizando directamente la CLI oficial, conservando su identidad.

La prueba encontró un defecto real después de esa cancelación: `respawn-pane` heredaba el ID del supervisor en vez del override del trabajador. Se conservó la evidencia; los mensajes con ese remitente incorrecto **no cuentan** como prueba de ida/vuelta. Tras corregir el código, se refrescó el snapshot y se reinició exclusivamente el backend de la instancia propia, conservando DB, tmux y las tres identidades. El runner pausó su supervisor privado de procesos; al pausar también el hilo JWKS hubo un fallo de preparación de autenticación local. Se sustituyó ese listener por uno privado con el **mismo issuer, key material y scopes**, sin desactivar autenticación. Esa intervención corresponde al runner aislado, no a una reparación del despliegue personal. Los procesos pausados/listener quedaron cerrados al eliminar la instancia propia.

El navegador del host carecía de `libasound.so.2`; la prueba utilizó Chromium headless existente mediante un contenedor temporal propio `--rm` con las dependencias de la imagen integrada, sin instalar dependencias globales.

## Evidencia real y resultados

Primera ejecución: Claude `9ae0b45d` delegó mediante CAO `assign` a Codex `5e130bd8` y OpenCode `836e9116`. Ambos escribieron su implementación y reportaron al supervisor. Mensaje 1 OpenCode → Codex (`PEER_CONTRACT`) y mensaje 4 Codex → OpenCode (`PEER_ACK`): ambos `delivered`. El supervisor integró y arrancó la app. La primera prueba de navegador añadió una nota, la marcó y acreditó persistencia tras recarga.

Segunda ejecución: Claude `e8133ce6` delegó auditorías reales a Codex `bdfefff9` y OpenCode `a72ecfac`; se conservan `BACKEND_FINAL_AUDIT.md` y `FRONTEND_FINAL_AUDIT.md`. Antes de la corrección, los mensajes 2 y 3 utilizaron el ID heredado del supervisor y quedan excluidos de aceptación.

Sobre la fuente final se entregó una **nueva tarea de control** al mismo OpenCode, se canceló expresamente su generación pendiente mediante la API y se obtuvo `cancelled=200`. Los procesos de OpenCode después del reset contenían `CAO_TERMINAL_ID=a72ecfac`; Claude y Codex conservaban sus respectivos IDs. La tarea posterior no repitió la auditoría ni la implementación y produjo este intercambio auténtico mediante CAO:

| Mensaje de la segunda instancia | Remitente | Destinatario | Resultado |
|---|---|---|---|
| 4 `PEER_FINAL_CONTRACT CORRECTED_ID` | OpenCode `a72ecfac` | Codex `bdfefff9` | delivered |
| 6 `PEER_FINAL_ACK CORRECTED_ID` | Codex `bdfefff9` | OpenCode `a72ecfac` | delivered |
| 7 Confirmación real del ACK | OpenCode `a72ecfac` | Claude `e8133ce6` | delivered |

Se conservó el mismo supervisor de esa instancia durante el defecto, refresco y continuación. Tras el reinicio privado del backend, se enviaron turnos de disponibilidad/integración **nuevos y explícitos**, sin reenviar mensajes ni repetir tareas anteriores. El supervisor comprobó las auditorías y arrancó la aplicación existente; `FINAL_COLLABORATION.md` contiene su registro saneado.

Consulta final de las tres terminales: `state=verified`, `LAST=200`. OpenCode obtuvo resultado auténtico de su nueva generación aunque su NativeChild histórico siguiera cancelado. CLI oficial `cao agent status a72ecfac --json`: exit 0, `status=completed`, turno `verified`, sin acciones pendientes. La prueba final de navegador comprobó POST de una nota=201, PATCH=200, recarga con checkbox marcado y API `done:true`; cero errores JavaScript y cero respuestas HTTP fallidas relevantes. Captura conservada: `demo-preview.png` en el proyecto; ambas pruebas de navegador están registradas privadamente.

## Cierre y límites

Ambas sesiones propias se eliminaron por la API con 200; listados posteriores de cada instancia: cero sesiones. Ambos contenedores propios se detuvieron y eliminaron por nombre exacto, incluido el cierre del supervisor pausado, backend reemplazado, listener JWKS y app. Los contenedores de navegador `--rm` ya no existen. Comprobación final: ningún contenedor de estos nombres, ningún listener en 19882/19982, `cao-personal` todavía `running/healthy`. Proyecto y evidencia permanecen conservados; no se hicieron commits, pushes, publicaciones ni mensajes externos.

Esta prueba real acredita los tres proveedores, delegación, mensajes auténticos ida/vuelta, recuperación sobre la fuente final e integración funcional en navegador, **con intervención documentada**. No ejecutó de forma real casos adversariales de recibo antiguo/eco, carreras verify/cancel, fallo de persistencia, permisos RO, expiración/renovación del bearer local, reinicio con tarea original activa ni diálogos de permiso legítimos. Los escenarios deterministas de recibos, concurrencia, persistencia y reinicio se describen con sus resultados concretos en implementation-evidence.md; no se deduce su éxito del navegador. Permisos RO y expiración/renovación del bearer local no se verificaron específicamente en esta aceptación. Tampoco garantiza que el modelo elija siempre la herramienta correcta ni acredita una garantía general para procesos detached tras borrar solo una sesión: el cierre aquí incluye eliminar su contenedor propio.

## Revalidación en el proyecto de colaboración del usuario — 2026-10-05

**Proyecto solicitado**: `C:\Users\ferna\OneDrive\Escritorio\cao-collaboration-demo-20261001-cf8017b2`. CAO se inició en un HOME, puerto y servidor tmux temporales. El runner copió el proyecto a `/tmp`, verificó igualdad de manifiestos antes de la ejecución y eliminó el runtime temporal al terminar. Claude fue coordinador; Codex iba a revisar API y OpenCode v2 frontend. No se llegó a crear ninguna terminal trabajadora ni se ejecutó prueba de navegador.

La primera pasada envió la tarea por el endpoint manual `/terminals/{id}/input`; la captura mostró el texto aún en el compositor. Se cambió el runner para usar `initial_message`, que emplea la verificación de entrega de CAO. En la segunda pasada Claude recibió la tarea y pasó a `processing`, pero no pudo cargar las herramientas CAO y se detuvo sin leer ni editar el demo.

El registro privado de Claude (`mcp-logs-cao-mcp-server`) identificó la causa: el proceso stdio de `cao-mcp-server` falló durante la importación de Python con `OSError: [Errno 12] Cannot allocate memory`, incluso al enumerar módulos en el checkout de CAO. Claude reintentó el transporte y terminó con `CONNECTION_CLOSED`; por tanto no hubo `assign`, callbacks ni síntesis. Al capturar el entorno: 15 GiB RAM total, 586 MiB disponibles, 4 GiB de swap usados y 0 B libres. No se cerraron procesos de otros proyectos para liberar memoria. El runtime propio se limpió; esta evidencia no acredita la integración real corregida.

Las suites deterministas finales de los proveedores Codex/OpenCode v1/v2 sumaron **406 passed, 3 skipped**; hubo 2 avisos de deprecación Pydantic. Esos resultados cubren los cambios T023–T026, pero no sustituyen el callback real. T020, T027, SC-006 y SC-007 quedan pendientes hasta repetir la colaboración con recursos suficientes; también queda pendiente la integración en navegador sobre este proyecto.

### Pasadas posteriores con las correcciones T028–T030

La primera repetición confirmó un timeout MCP de Claude a los 30000 ms. Tras corregir `alwaysLoad` y los presupuestos de conexión, Claude sí creó los trabajadores Codex y OpenCode: supervisor `bc8fc06a`, API `6d94122d`, frontend `2c0928d1`. No se entregaron callbacks: el supervisor había terminado su respuesta con la tarea aún abierta, sin recibo final, y pasó a reconciliación. Además, la consulta de API agotó su timeout de 15 s. Esta pasada prueba delegación real, pero no colaboración terminada.

Las siguientes pasadas utilizaron tareas explícitas por etapas y joins acotados. Se encontró que la ruta XDG asumida no producía el registro esperado; se sustituyó por captura de stderr documentada del CLI, con INFO y archivo privado. Una pasada con ese código creó supervisor `dff0aa9a`, Codex `00d86eb8` y OpenCode `00ca4e46`, pero el registro real de OpenCode mostró `message="mcp connect failed"` y `status.error="Connection closed"`. Codex llegó a `completed`; no hubo ambos callbacks entregados. Se detuvo únicamente ese runtime de prueba cuando dejó de poder completar la coordinación. La causa exacta de ese cierre no quedó capturada; la presión de memoria es una condición observada, no una explicación demostrada de todos los cierres.

Se aisló después la dependencia: el SDK MCP real completó initialize/list_tools con el ejecutable del repositorio y anunció 72 herramientas, incluidas `assign` y `send_message`. Una sesión real de OpenCode a través de CAO, con un wrapper temporal que únicamente captura stderr y hace exec del mismo MCP, devolvió HTTP 201 y una señal positiva en `mcp-startup.log`. El proceso MCP registró su arranque FastMCP. El runtime de diagnóstico se limpió. Esto acredita la conexión y la lectura real del registro corregido.

Las últimas pasadas serializaron el inicio de trabajadores para reducir el pico de memoria. También se corrigió el prompt de prueba que prohibía a Codex los comandos de lectura necesarios para inspeccionar archivos. La pasada final volvió a agotar una consulta HTTP de 45 s antes de acreditar los dos callbacks. Durante las ejecuciones se observaron entre 229 y 574 MiB disponibles y swap agotada; tras limpiar runtimes propios había aproximadamente 1.3–1.4 GiB disponibles. No se cerraron procesos de otros proyectos ni se modificó la configuración de memoria del sistema.

**Aceptación actual: incompleta.** T028–T030 tienen pruebas deterministas y pruebas reales de conexión aislada; T020/T027 y SC-006/SC-007 siguen pendientes. No se ejecutó navegador en estas pasadas. Los logs locales de diagnóstico están en `/tmp/cao-real-collaboration-20261005-*.log` y `/tmp/cao-opencode-mcp-diagnostic.log`; contienen trazas técnicas de runtimes temporales y no se publican.

### Investigación instrumentada adicional — 2026-10-05

Véase [investigacion-problemas-20261005.md](investigacion-problemas-20261005.md). Claude recibió la tarea, pero sus dos procesos MCP terminaron con código 1 y `OSError: [Errno 12] Cannot allocate memory` durante importación. `/health` respondió 200 en las siete muestras; esta ejecución no reproduce el timeout HTTP anterior. Captura tmux sin límite reproducida con servidor privado suspendido/reanudado. Contratos inbox/recibos: 66 passed. T020/T027 y la aceptación conjunta permanecen pendientes.

### Correcciones posteriores y colaboración real — 2026-10-05

Resultado **PASS** en la copia exacta del proyecto del usuario: Claude `3d68c5f3` delegó a Codex `5f781c28` y OpenCode `8e86711e`; ambos callbacks quedaron entregados; Claude devolvió síntesis API/Frontend y LAST HTTP 200 verificado. Manifiestos SHA-256 del original y de la copia intactos. Diez muestras `/health` HTTP 200, máximo 0,12 s. Sin errores de asignación de memoria en stderr MCP de este intento. T027/SC-007 acreditados; T020/SC-006 no se marcan completos porque falta el escenario de aplicación/navegador. [Detalles, verificaciones y límites](correcciones-adicionales-20261005.md).

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
