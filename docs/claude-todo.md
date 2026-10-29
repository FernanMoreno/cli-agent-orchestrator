# Claude Code: análisis y pendientes

T214 R3 FALLIDA: apareció código en línea además de negrita; limpieza correcta
y lanzador cerrado. T217 añade su proyección estructurada reversible manteniendo
el contenido literal: 380 pruebas Python y ocho reproducciones reales PASS,
arquitectura 5/0 y revisión Claude sin bloqueos. Falta el recorrido nativo con
las fuentes nuevas; la aceptación previa de v2 permanece intacta.
[R3](../specs/025-provider-mcp-profiles/validation-t214-native-r3.md) ·
[T217](../specs/025-provider-mcp-profiles/validation-t217-component.md).

T216: corrección integrada para validación con análisis CommonMark limitado,
contenido nativo exacto y recibo/identidad intactos. 369 pruebas Python y cuatro
reproducciones de capturas reales PASS; arquitectura 5/0 y revisión Claude sin
bloqueos. Falta aceptación del recorrido nativo con estas fuentes.
[Componente y límites](../specs/025-provider-mcp-profiles/validation-t216-component.md).

2026-10-29 · T215 completada como diagnóstico real: cuatro parejas selladas
confirman que el render omite los delimitadores de negrita Markdown. Cuatro
reproducciones con el lector nativo vinculado pasan; T216 corrige esta frontera
con análisis estructurado y conservación exacta del contenido. T214 R2 continúa
FALLIDA por timeout del recibo; limpieza verificada y lanzador cerrado. No se
reabre la aceptación previa de preguntas/ACK/reinicio de v2.
[Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t215-native-r2.md).

2026-10-29 · T214 R1 real: consulta MCP completada, pero aceptación FALLIDA
por4rechazos de comparación nativa/renderizada, sin CAS final.296,982s y
limpieza/credenciales/fuentes intactas. El verificador automático sí se ejecutó.
T215 añade captura privada acotada:431pruebas PASS/20subtests/ClaudeGO;
falta capturar/reproducir el par real antes de corregir el comparador.
[R1](../specs/025-provider-mcp-profiles/validation-t214-native-r1.md) ·
[Captura](../specs/025-provider-mcp-profiles/validation-t215-component.md).

2026-10-29 · T214: caso nativo `task_query` implementado, una consulta y
receptor sin instrucciones, sin mensajes ni reinicio.402pruebasPython distintas
y17Node PASS, arquitectura5-0/ClaudeGO. Falta la ejecución real con lanzador
actualizado; T214 no se cierra por estas pruebas offline.
[Caso y límites](../specs/025-provider-mcp-profiles/validation-t214-case.md).

2026-10-29 · T213: evidencia acotada de `get_local_cao_task` implementada;
306 pruebas y20subtests PASS, tipos/arquitectura5-0/revisiónClaudeGO.
Comprueba tarea, solicitante y estados sin registrar sus contenidos ni IDs.
Los timeouts de importación originales se conservan; la regresión posterior
pasa con los límites intactos. T214 (llamada nativa de esta operación) sigue
pendiente. Preguntas/ACK/reinicio de OpenCode v2 mantienen su aceptación.
No cambia permisos ni cierra OC2-04 global.
[Alcance](../specs/025-provider-mcp-profiles/validation-t213.md).
T214: corregido el adaptador generado;311pruebas de regresión y8controles
finales PASS (314distintas), arquitectura5-0/ClaudeGO. Falta implementar y ejecutar
el caso nativo de una llamada; preparación no equivale a aceptación.
[Preparación](../specs/025-provider-mcp-profiles/validation-t214-preparation.md).

Estado actual (2026-10-29): T197/T210 tienen aceptación acotada del recorrido
Claude R6 con mensajes/reinicio y parada gestionada explícita; T211 tiene
aceptación acotada Codex R2 de cancelación FIFO tras desconexión HTTP con B
activo y limpieza normal. Default/500ms, CX09/T013 completos, otras operaciones
y aceptación global conservan sus gates propios.
[R6](../specs/025-provider-mcp-profiles/validation-t197-native-r6.md) ·
[T211](../specs/025-provider-mcp-profiles/validation-t211.md).

T212: corregidos17diagnósticos heredados; mypy0 en5módulos y68pruebas PASS.
El control8s usa dependencias byte-idénticas en Linux y código CAOS original;
los fallos de importación en frío se conservan. No es tipado global ni nueva
aceptación nativa. [T212](../specs/025-provider-mcp-profiles/validation-t212.md).

Los bloques fechados siguientes conservan la evolución y los fallos históricos.

Antecedente T209/T210 (2026-10-28): cierre MCP antes del CLI integrado en
main6d443125. Regresión PostgreSQL/API/SDK347 PASS y rutas finales29 PASS;
control nativo sin modelo PASS: cierre494ms dentro de6s, catálogo3/calls0,
sello normal/CLI0/MCPretirado y15contenedores/19redes preservados. T209 cerrado
en ese alcance explícito; en esa fecha T210/T197 seguían pendientes del recorrido
real de mensajes y reinicio, aceptado después sólo en el modo R6 indicado arriba. No activación global. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t210.md).


T208: observador de EOF/señales/salida y estado del proceso implementado,24pruebas PASS y revisión GO. Conserva errores originales, privacidad y límites; diagnóstico r2 completo: SIGINT/SIGTERM y SIGKILL confirmado a~0,5s, antes de EOF y de la limpieza del lanzador; sin operaciones pendientes ni salida70 del guard. Fuente535 intacta/procesos retirados. Emisor no observado; cierre automático sigue FALLANDO y T197 abierto. [Validación](../specs/025-provider-mcp-profiles/validation-t208.md).

T207: guarda MCP de EOF físico implementada de forma opt-in; revisión independiente GO. Se corrigió además un bloqueo reproducido del ejecutor tras cancelar un hilo. La regresión final pasó50pruebas; tipos/formato/arquitectura5-0 pasan. Primera prueba nativa directa FALLÓ: Claude0 sin sello normal MCP dentro de6s;39,026s/mínimo10581MiB,533fuentes intactas y procesos retirados. EOF/señal/vencimiento no observados; T208 instrumentará esas transiciones. No se activó por defecto ni se cerró T197. [Estado y límites](../specs/025-provider-mcp-profiles/validation-t207.md).

T206 yT204 aceptados en su alcance: EOF controlado real1491,912ms y después denegación nativaClaude (catálogo3/ofertaHTTP2/errorcorrelacionado/0MCPcalls), cierre1330,143ms. Fuente7af13f34/530pins,96pruebas PASS; ambos grupos retirados/sinlogin/API/PG. Backend local sintético; modoEOF explícito, automáticoT197 yCL07 global siguen abiertos. [T206](../specs/025-provider-mcp-profiles/validation-t206.md) · [T204](../specs/025-provider-mcp-profiles/validation-t204.md).

T205 diagnóstico cerrado: proxy recibeSIGINT2 durante salida nativa, lanzaKeyboardInterrupt y su limpieza solicitaSIGTERM; waitstatus real del MCP -15 tras ese SIGTERM del proxy, sin EOF/marcador normal.83pruebas PASS, caso40,542s/mínimo10621MiB, grupo retirado/0turnos/sinlogin/API/PG. Preflight sigue FAILED; T204/CL07/automáticoT197 abiertos. EOF controlado es propuesta todavía. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t205.md).

Histórico T204r2:74pruebas PASS/composición5/0/ClaudeGO para verificadores. Preflight nativo r2 conecta3herramientas y0llamadas, pero falla cierre limpio del proxy tras salida0 de Claude;43,618s/mínimo10543MiB, grupo retirado,0turnos/login/API/PG. Import privado de r1 reproducido y corregido antes de r2. T204/CL07 siguen abiertos; falta resolver apagado antes del turno sintético. [Resultados y límites](../specs/025-provider-mcp-profiles/validation-t204.md).

T203 cerrado en su alcance: Codex nativo supervision11herramientas, inspección propia/hijo y cancelación de tercero activo denegada antes del POST; recibo/turno intactos. API/PG reales, actores MockCLI, recibo sembrado y auth local desactivada;0turnos de modelo. R2PASS/100,333s/mínimo9695MiB, limpieza/originales intactos.44pruebas nuevas+60regresiones previas. T007/T008/global siguen abiertos. [Resultado y límites](../specs/025-provider-mcp-profiles/validation-t203.md).

Conciliación de casillas: AUTH/LOCAL/CATALOG completados sólo en su alcance compartido de implementación, inventario y controles offline (T036:79operaciones/7perfiles). Las aceptaciones nativas por operación y AUTH-03 siguen separadas. CL-08 cerrado por T202 explícito; T197 automático y Work permanecen separados. [Base y límites](../specs/025-provider-mcp-profiles/validation-t036.md).

T202 cerrado: recorrido real r4 aceptado en5fddafaf/510fuentes. Dos mensajes/filas/eventos, reinicio y sello normal mediante EOF explícito dentro de6s;14contenedores originales conservados,37identidades/PG retirados y login intacto.178,052s/mínimo9324MiB. No acredita cierre automático T197 ni Work/perfiles/global. [Resultado actual](../specs/025-provider-mcp-profiles/validation-t202.md).

T202: captura de inventario compartida implementada,16pruebas PASS y consultas Docker privadas verificadas. Dos ventanas de60s fallan (24/66altas-bajas); el armado rechaza antes de credenciales/servicios/modelo. No hubo reintento nativo; T202/T197 automático siguen abiertos y r3 permanece fallido. [Evidencia](../specs/025-provider-mcp-profiles/validation-t202.md).

T202 real:2mensajes/2filas/2eventos/1reinicio y cierre EOF con sello dentro de6s correctos;35identidades/PG propios retirados. Aceptación global FALSE por contenedor preexistente ausente al final, con actividad Docker Testcontainers observada. No se rebaja preservación; T202/T197 automático siguen abiertos.268pruebas PASS y revisión GO. [Resultado y límites](../specs/025-provider-mcp-profiles/validation-t202.md).

T201 cerrado: SDK real distingue EOF normal de SIGINT/SIGTERM; sólo EOF previo permite sello normal. T202 implementado en diagnóstico opt-in,268pruebas PASS/composición/ClaudeGO; pruebas nativas pendientes. T197 automático sigue abierto. [Diagnóstico](../specs/025-provider-mcp-profiles/validation-t201.md) · [T202](../specs/025-provider-mcp-profiles/validation-t202.md).

T200 completado como diagnóstico: Claude registra SIGINT→SIGTERM, CLI/MCP desaparecen antes de DELETE sin sello. Código/señal real de terminación no acreditados; T197 sigue abierto. T201 aislará retorno/excepción del SDK stdio sin modelo ni cuenta. [Evidencia](../specs/025-provider-mcp-profiles/validation-t200.md).

T197r2: dos send_message reales correctos,2filas/2eventos AG-UI y turnos/recibos exactos a través de1reinicio; perfil/filtro MCP atestados. Gate completo fallido: HTTP200 de exit sin sello normal en6s;33identidades/PG retirados y507pins/login intactos. T200 investiga cierre sin repetir modelo. [Resultado](../specs/025-provider-mcp-profiles/validation-t197.md).

T199 cerrado: aviso inicial Security notes reconocido sólo en viewport completo/actual, una vez.249pruebas PASS, arquitectura5-0/ClaudeGO y receptor real HTTP201/IDLE en13,487s;507pins,15identidades/PG retirados y login intacto. Parada antes de emisor: T197 mensajes sigue abierto. [Resultado](../specs/025-provider-mcp-profiles/validation-t199.md).

T197 real falló al arrancar el receptor: HTTP500/provider_init_timeout,146572ms; no llegó al emisor ni a mensajes/MCP/reinicio. Limpieza completa,13identidades retiradas,505bindings y login originales intactos. Preparación239PASS sigue diferenciada de aceptación. T198 capturará el viewport antes de compensar; causa aún no probada. [Resultado](../specs/025-provider-mcp-profiles/validation-t197.md).

T196 corregido:12RED/4controles→144pruebas distintas PASS (125interacción/reinicio+19consumidorOpenCode), arquitectura5-0/ClaudeGO. Metadata nativa para rechazos, sin401porID ni rate_limit_event informativo; timeout/aceptación/recibos previos intactos. Sin nuevo modelo ni cambio productivo. [Resultado](../specs/025-provider-mcp-profiles/validation-t196.md).

T195 real PASS en00d3dac5: espera nativa única interrumpida/recuperada, workers distintos/plazo decreciente,2respuestas/ACK exacto/AG-UI estable. CLI46,62s,total72,712s,min10063MiB,505pins/limpieza/originales intactos.102pruebas offline/arquitectura5-0/ClaudeGO. CL-09 cerrado; T012 matriz y gates globales separados. [Resultado](../specs/025-provider-mcp-profiles/validation-t195.md).

T195: modo existente de reinicio durante espera activa habilitado y aceptado para Claude;102pruebas PASS/arquitectura5-0/ClaudeGO. Conserva barreras, descenso estricto del plazo, ACK y límites. CL-09 cerrado; T012 agregado sigue abierto. [Evidencia](../specs/025-provider-mcp-profiles/validation-t195.md).

Auditoría de pendientes históricos: T079/T092/T117 ya cubiertos por T101/T134/T135, cerrados sin repetir modelos ni modificar recibos fallidos. Comparaciones históricas no observadas y gates globales siguen separados. [Contraste](../specs/025-provider-mcp-profiles/validation-pending-reconciliation-20261028.md).

Claude interacción actual PASS enf77e3e66:2preguntas/2respuestas/ACK con identidad/versiones/recibos exactos y reinicio/AG-UI estable. CLI37,64s, total70,898s/min10090MiB,505pins y limpieza/originales intactos. Falta reiniciar durante espera activa: CL-09/T012 siguen abiertos; no explica504 histórico ni acepta cuota/Work/global. [Resultado](../specs/025-provider-mcp-profiles/validation-claude-interaction-restart-current.md).

T190 Codex real PASS en090f01d8:2send_message exitosos/2filas PostgreSQL entregadas/2eventos AG-UI, reinicio API e identidad preservada, paquete completo43assets y ejecutable real observado antes de cadafase.100,586s/min9314MiB,25identidades propias retiradas/limpieza y credenciales intactas. Cierre MCP por EOF diagnóstico; no acepta cierre automático, ACK exacto, Work ni gates globales. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t190-native-r5.md).

T193 compartido: corregido el DELETE reintentado que podía matar una ventana sustituta tras cleanup diferido. Guarda PostgreSQL exacta/indexada antes de crear, migración corev3 aditiva;421pruebas distintas PASS/arquitectura5-0/ClaudeGO, hermanos y tombstones preservados. Sin nueva aceptación nativa; T143 amplio/T013 siguen abiertos. [Evidencia](../specs/025-provider-mcp-profiles/validation-t193.md).

T192 compartido: recreación de sesión rechazada cuando la limpieza de una fila antigua queda pendiente; conserva propietario y permite reintento.127pruebas distintas PASS, incluidas3integraciones PostgreSQL/tmux, arquitectura5-0/ClaudeGO. No añade aceptación nativa ni cierra CL-10/T143. [Evidencia](../specs/025-provider-mcp-profiles/validation-t192.md).

T189 PASS acotado: Codex0.164 app-server MCP→API autenticada→1fila PostgreSQL/1evento AG-UI exactos; reinicio sin reenvío,401sin efecto y denegación nativa del cliente.101,572s/498pins/10170MiB, limpieza y login intactos;66pruebas pasan. Los actores registrados/receptor son mock_cli: no cierra recorrido de terminal Codex, autorización por operación, Work ni CX-05/global. [Evidencia](../specs/025-provider-mcp-profiles/validation-t189.md).

T188: catálogo MCP nativo de Claude2.1.303 validado en los7perfiles × completo/vacío/parcial:21PASS; aislamiento real3/12 tambiénPASS.53pruebas offline, arquitectura5/0;23sesiones nativas sin turnos de modelo, red/login aislados y procesos retirados. No acredita llamadas autorizadas, efectos PostgreSQL/AG-UI ni aceptación global. [Evidencia](../specs/025-provider-mcp-profiles/validation-t188.md).

T170 compartido: corregida la salida de `cao agent` para tuberías/automatización; JSON según su política existente y texto humano en TTY.107 pruebas pasan en los9subcomandos, incluidos errores/interrupción/hilos; arquitectura5/0 y revisión sin bloqueos. Conserva los payloads, permisos y transportes; no añade aceptación nativa de proveedores. [Evidencia](../specs/025-provider-mcp-profiles/validation-t170.md).

T186: PASS real OpenCode v2 native_tool en169fdc44; dos APIs/CLI JSON/MCP12, Unicode/replay31,129s y tarea cancelada36,761s con hijo/worker físicamente detenidos.63sondeos, Git/4retiradas/cierre normal,561pins y mínimo8644MiB. Corregido FileExistsError del observador con1RED→GREEN y112PASS/arquitectura5/0; fallos originales y cierres derivados conservados. T168streaming/OC2-04 global siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t186.md).

T185: timeout nativo Codex180s PASS en5f034ca3; HTTP504 en208,728s con arranque, verify200/pending sin reenvío, generación ajena409 y cancelación actual200 en25,875s. Cinco procesos viejos retirados, reemplazo listo/0peticiones y limpieza completa; hijo conserva reconcile/timeout/settled_at.323PASS/5avisos,26controles nuevos incluidos, arquitectura5/0,1744pins y PG cerrado. Backend sintético; todaCX08/otros proveedores/global siguen separados. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t185.md).

T183/T184: corregida la circularidad entre el recibo pendiente y el arranque tras cancelar. Codex nativo en49297912 PASS:409 para generación ajena,200/cancelled26,075s para la actual; salida de cinco procesos viejos, reemplazo disponible y0peticiones en3,161s, join/limpieza estables y padre API200.1116PASS/3opt-in previos;28controles nuevos, arquitectura5/0 y mypy canónico4archivos. Primer FAIL conservado; timeout/global/otros proveedores siguen separados. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t183.md).

T182: validada la propiedad de cancelación compartida con PostgreSQL real y barreras: desconexión del llamante, duplicados, fallos de reset/readiness/CAS, generaciones obsoletas y progreso de otro terminal.11controles nuevos y157regresiones PASS, arquitectura5/0, GOClaude y PG cerrado. Sin cambio productivo ni aceptación de cancelación nativa/FIFO/timeout/global. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t182.md).

T181: API gestionada Codex80×50 PASS en436dc0ae: parcial→401 da502/error/failed34,325s; parcial→cuota da409/quota_wait/reconcile28,690s. TTY80desde arranque y cada petición, capturas2/3líneas, join/veredicto y limpieza estables; padre220accesible.435regresiones/24controles finales solapados/arquitectura5/0,1737pins y recursos propios cerrados. Backend sintético; cancelación/timeout/footerless/global siguen separados. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t181.md).

T180: corregida la detección de celdas HTTP/cuota envueltas en80columnas. Tres capturas CLI0.164 en e7129f7a distinguen continuaciones nativas de citas indentadas; consumidor2cc5c66b/24clasificaciones/1735pins.668regresiones y60controles adicionales pasan;3opt-in previos/arquitectura5/0. Fuente y pruebas iniciales conservadas, procesos/PG cerrados. Sin aceptación de recorrido gestionado estrecho, pipe-pane crudo, footer ausente ni cancelación/timeout. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t180.md).

T179: corregida cuota tras respuesta parcial mediante estado tipado ERROR/WAITING_QUOTA y comprobación estructural compartida. Captura59d6cd0d; fuente corregida b128b126 y recorrido real gestionado PASS409/quota_wait/reconcile28,790s, viewport propio220x50, join/veredicto estables tras limpiar y padre accesible.560PASS/3opt-in previos/44nuevos, arquitectura5/0;1731pins y procesos/PG cerrados. Sin cuota real, auto-resume ni aceptación de cancelación/timeout. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t179.md).

T178: recorrido gestionado Codex parcial→401 PASS en05f1c369: API502/error/failed en35,311s, captura visible única220x50 vinculada al hijo, join y veredicto conservados tras limpieza. Padre accesible; procesos/PG cerrados y1729pins intactos.199PASS/3controles nuevos/arquitectura5/0. Backend sintético, sin login del operador. Quedan wrap/footer ausente, cuota parcial y cancelación/timeout. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t178.md).

T177: corregido un error HTTP nativo tras respuesta parcial: antes se clasificaba completed/processing; ahora ERROR sin alterar el recibo. Capturas CLI0.164 en5c646430 y consumidor de3fc61f; cita correcta, entrada multilínea y diálogos con separadores conservados.399PASS/3omisiones opt-in previas/24casos nuevos, arquitectura5/0 y procesos/PG cerrados. No acepta aún este caso por API gestionada, cuota parcial, errores envueltos ni cancelación/timeout. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t177.md).

T176: API gestionada Codex PASS en303d3f65: auth/throttle→502/error/failed y cuota→409/quota_wait/reconcile; join conserva identidad/veredicto tras limpieza explícita. CLI0.164 real con backend HTTP sintético aislado, sin login del operador. 172 contratos PASS, 18 nuevos, arquitectura5/0;1726pins y cierre de procesos/PG verificados. No acepta cuota real, auto-resume, cancelación activa ni todoCX-08. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t176.md).

T175: corregida la clasificación Codex de errores HTTP nativos `■` y cuota `You’ve`, con guardas para citas, avisos y cuotas antiguas. 416 pruebas pasan, 3 comprobaciones opt-in preexistentes quedan omitidas; arquitectura5/0. Tres capturas CLI0.164 aisladas sin login y 12 clasificaciones en80525802; recursos propios cerrados. No cierra el recorrido gestionado API/recibo, cancelación ni todoCX-08, y no transfiere aceptación a otros proveedores. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t175.md).

T174: baseline interactivo Codex PASS en 7b2ea7d7: Unicode exacto sin normalizar, HTTP200/12,267s y recibo del hijo succeeded/settled/cleanup/join coincidente. 120 contratos distintos y arquitectura5/0; fuente, login y limpieza propios verificados. R1 falló por el ejemplo antiguo de perfiles; corregido y conservado. No acepta todo CX-08 ni recorridos nativos de otros proveedores. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t174.md).

T173: corregido el filtro MCP del servidor para comandos de texto; 194 pruebas y arquitectura 5/0 pasan. Codex CLI 0.164.0 valida los 7 perfiles con filtros completo/vacío/parcial: 21 casos nativos sin modelo y par de catálogos 3/12, en 96f3eaae. Dos controles stdio prueban el rechazo directo del servidor. No acredita efectos autorizados, permisos globales ni aceptación nativa OpenCode/Claude. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t173.md).

T171: la espera explícita MCP compartida recupera solo errores de conexión dentro del presupuesto original.137 pruebas/PG replay y arquitectura5/0 pasan; sin nueva aceptación nativa de este proveedor. [Evidencia](../specs/025-provider-mcp-profiles/validation-t171.md).

## Estado específico del proveedor · 2026-10-26

**Diagnóstico Unicode nativo actual PASS** en35a728c1: Claude Code2.1.303/Sonnet4.6 solicitado, HTTP200/21,204s, recibo succeeded/join y limpieza íntegra;1508pins, fuente/binario/operador intactos. [Resultado y límites](../specs/025-provider-mcp-profiles/validation-claude-unicode-current.md). R1harness422 fallido conservado; el504 histórico sigue sin explicarse. La aceptación ampliada de Claude continúa pendiente: catálogo/autorización MCP, interacción/reinicio y mensajes. No alterar login;T032 ampliada sigue aplazada. T168/T169 pertenecen a OpenCode/control plane y no acreditan esos gates.

[Revisión cruzada actual](../specs/025-provider-mcp-profiles/validation-all-providers-current.md):141 comprobaciones pasan; distingue contratos offline, diagnóstico nativo y aceptación por proveedor. Las pruebas reales intensivas siguen serializadas; los perfiles y contratos comunes se comprueban para los tres.

## Registro de avances y evidencia compartida

Actualización 2026-10-26 · T168: preflight real PASS; resultado Unicode/replay del trabajador PASS33,403s; cancelación activa agotó75s y sigue pendiente. Recursos cerrados y fallo conservado. Añadidas observaciones estructuradas privadas;74 controles pasan, sin forzar JSON al modelo. [Resultado acotado](../specs/025-provider-mcp-profiles/validation-t168-native.json).

Actualización 2026-10-26 · T168: quinto preflight pasa arranque/catálogo; cierre detectó DELETE repetido. Recuperación propia confirmada, recibo original fallido conservado. Ledger de retiradas y lector de cancelación corregidos;72 controles pasan. **Aceptación real pendiente**. [Evidencia](../specs/025-provider-mcp-profiles/validation-t168-preparation.md).

Actualización 2026-10-26 · T168: lector de disponibilidad alineado con T166 (estado tipado y viewport propio),55 controles pasan y admisión endurecida. Cuarto fallo conservado; **nuevo preflight/aceptación nativa pendientes**. [Evidencia](../specs/025-provider-mcp-profiles/validation-t168-preparation.md).

Actualización 2026-10-26 · T168: tercer preflight confirma emparejamiento JSON; inspector de modelo corregido para verificar --model en el proceso propio,44 controles pasan. **Nueva ejecución nativa pendiente**, sin inferencia en los fallos previos. [Evidencia](../specs/025-provider-mcp-profiles/validation-t168-preparation.md).

Actualización 2026-10-26 · T169: peer pair/accept --json y consumidor tipado integrados,39 controles y20 contratos PostgreSQL pasan. Segundo preflight identificó el parser de texto; **nuevo preflight nativo T168 pendiente**. Auditoría propone T170, sin implementarlo. [Resultado](../specs/025-provider-mcp-profiles/validation-t169.md) y [auditoría](../specs/025-provider-mcp-profiles/structured-output-audit.md).

Actualización 2026-10-26 · T168: preparado recorrido separado de emparejamiento CLI/API/MCP real a trabajador OpenCode nativo; primer preflight fallido antes de inferencia; corregida la configuración del peer compartido y el cierre no creado;20 controles pasan. **Repetición real y aceptación pendientes**. [Alcance](../specs/025-provider-mcp-profiles/validation-t168-preparation.md).

Actualización 2026-10-26 · T167: corregida la clasificación de tareas rechazadas por ocupación y luego no encontradas;118 pruebas pasan, con dos APIs/PostgreSQL reales y trabajadores mock_cli. **Nueva aceptación nativa entre instancias pendiente**; T166 conserva su fuentefda0c1be. [Resultado y límites](../specs/025-provider-mcp-profiles/validation-t167.md).

Actualización 2026-10-26 · T166: **PASS real OpenCode v2 coordination12/0 en fda0c1be**, con lectura autorizada de peers, dos mensajes/ACK/AG-UI/reinicio y limpieza propia;186s y mínimo8429MiB. Fallo previo de importación preservado, causa no demostrada. T166 cerrado; **OC2-04 completo y gates globales siguen abiertos**. [Resultado y límites](../specs/025-provider-mcp-profiles/validation-t166.md).

Registro previo de preparación T166 (superado por el PASS anterior): preparado el caso OpenCode v2 de coordinación12/0 y la atestación mínima del resultado MCP;156 pruebas del fixture y50 del observador pasan. En ese punto el recorrido real estaba pendiente; se conserva como historia de preparación. [Preparación y límites](../specs/025-provider-mcp-profiles/validation-t166-preparation.md).

Actualización 2026-10-26 · T165: corregida y validada la compensación de inicialización compartida. Esto no traslada los recibos nativos anteriores al HEAD nuevo ni cierra perfiles/gates globales. [Resultado actual](../specs/025-provider-mcp-profiles/validation-t165.md).

Publicación, 2026-10-20: el gate común reprodujo el NFR de memoria a 1,010 s en el diagnóstico serial y a 2,200 s en la ejecución completa con ocho trabajadores, frente al límite de 1 s. Esta última terminó con 18.263 pruebas correctas, una fallida y 83 omitidas; el push fue rechazado. Es evidencia del backend, separado de los perfiles y recorridos Claude. [Resultados y límites de evidencia](../specs/021-postgresql-persistence/publication-nfr-20261020.md).

Fecha de revisión: 2026-10-20. Lista de trabajo, sin activación de herramientas ni cambios de permisos. Registrar la versión concreta de Claude Code y el modelo; la división v1/v2 de OpenCode no se traslada a este adaptador.

## Estado y límites de la evidencia

- El adaptador configura MCP por terminal mediante `--mcp-config` y `--strict-mcp-config`, instala el overlay de capacidad y espera una señal de disponibilidad del MCP CAO. Requiere recibos de turno y utiliza restricciones nativas mediante `--disallowedTools`.
- Hay una aceptación histórica de preguntas/respuestas/ACK con AG-UI y reinicio. No acredita automáticamente la fuente final ni todos los perfiles propuestos.
- Los diagnósticos Unicode de 2026-10-19 alcanzaron la instrucción pero acabaron con HTTP 504 al agotar 180 s. El error histórico de comparación no ASCII no se reprodujo; la causa concreta del timeout sigue sin establecerse.
- La ausencia de un error explícito de autenticación no prueba que no haya bloqueo interactivo: uno de esos diagnósticos observó una señal heurística de login. No modificar, borrar ni renovar el login del operador durante la investigación.
- Los mensajes genéricos tienen resultados históricos parciales: envíos persistidos y eventos observados no equivalen a aceptación si falta el sello final de auditoría o la limpieza acreditada.
- La restricción de tres herramientas del proceso hijo de OpenCode no está acreditada como una restricción equivalente en Claude Code.

## Herramientas por perfil

| Perfil propuesto | Herramientas | Condición |
| --- | --- | --- |
| Interacción humana | `cao_ask_user`, `cao_wait_for_user_answers`, `cao_acknowledge_user_answers` | Contrato completo de pregunta, espera y ACK. |
| Colaboración básica | `send_message`, `list_siblings` | Mensajes y descubrimiento autorizados. |
| Recuperación de interacciones | `cao_list_interactions`, `cao_get_interaction`, `cao_escalate_interaction` | Lectura por ámbito y escalado al actor autorizado. |
| Coordinación local | `list_local_cao_peers`, `assign_local_cao_task`, `get_local_cao_task`, `cancel_local_cao_task` | Exclusivo de coordinadores autorizados. |
| Supervisión | `inspect_terminal_turn`, `verify_terminal_turn`, `cancel_terminal_turn` | Identidad y generación verificadas antes de cancelar. |
| Conocimiento | `load_skill`, `memory_recall`; escritura opcional con `memory_store`, `report_outcome` | Permisos separados para lectura y escritura. |
| Trabajador elástico | `complete_assignment` | Solo para el trabajador elástico configurado. |

Empezar por interacción humana, colaboración básica y consulta de interacciones. `cao_answer_interaction` requiere una delegación válida y no autoriza al emisor a contestar su propia pregunta humana. Los permisos de herramientas nativas y los permisos MCP necesitan verificaciones separadas.

## Pendientes, en orden

- [ ] CL-01 Localizar la primera frontera incorrecta del HTTP 504: lanzamiento, disponibilidad MCP, entrega única del prompt, aceptación del turno, llamada al proveedor, espera interactiva, ejecución de herramientas, recibo y extracción final. Retener solo tiempos y categorías sanitizadas.
- [ ] CL-02 Diferenciar latencia del proveedor, cuota, autenticación y fallo del monitor. Reproducir con versión/modelo fijados y presupuesto exterior que permita observar el cierre; no elevar los 180 s como sustituto de un diagnóstico.
- [ ] CL-03 Corregir el defecto que se reproduzca y añadir una regresión de esa frontera. Conservar Unicode en prompts/respuestas y validar el formato de recibos en su límite; no declarar explicado el registro histórico sin evidencia causal.
- [ ] CL-04 Verificar preservación de las credenciales aisladas y archivado tras parada confirmada, incluso al fallar o renovar tokens. Mantener intactos el login original y los perfiles del operador; no promocionar copias automáticamente.
- [ ] CL-05 Inventariar catálogo y llamadas MCP por perfil. Comprobar qué controla realmente `--strict-mcp-config` y qué controla `--disallowedTools`; configurar servidores no equivale a limitar todas sus herramientas.
- [ ] CL-06 Definir e implementar perfiles de interacción, colaboración y coordinación; añadir las herramientas propuestas con permisos explícitos de cliente y servidor. Mantener la capacidad de interacción protegida frente a overrides.
- [ ] CL-07 Probar herramientas permitidas/denegadas, catálogos parciales, descubrimiento diferido, señal de disponibilidad, recursos/prompts y aislamiento entre terminales; no resolverlo con permisos globales.
- [x] CL-08 Completar una aceptación de mensajes genéricos con dos envíos, persistencia PostgreSQL, AG-UI, reinicio de API, sello de auditoría y limpieza. Investigar por separado el contrato externo `cao.work.message`.
- [x] CL-09 Completar la aceptación final de preguntas/respuestas/ACK y reinicio vinculada a fuente/runner actuales. Verificar recuperación acotada de espera, identidad estable y ausencia de duplicados; un reinicio por sí solo no prueba recuperación de una llamada interrumpida.
- [ ] CL-10 Validar concurrencia: trabajos y esperas acotados, cancelación por generación, ausencia de cruces de configuración/credenciales y limpieza propia. Medir latencia de control separada de la inferencia; una cuota del proveedor no es capacidad escalable de CAOS.
- [ ] CL-11 Actualizar Spec 022/023 con evidencia final y cerrar solo los criterios demostrados. Mantener aparte la reconciliación del job histórico `RECONCILE_REQUIRED`; no reinstalar ni reejecutar ese job como atajo.

## Limitación actual de autorización y catálogo pendiente

El helper `_tool_denied_reason` autoriza las operaciones que lo utilizan mediante el selector de servidor `@cao-mcp-server` o `*`; los nombres sueltos de herramientas en `allowed_tools` no son una allowlist MCP por operación. Las rutas de interacción tienen además su autoridad propia. El filtro histórico de tres herramientas solo se instala en el proceso hijo marcado; no es global. La base opt-in de perfiles integrada el 2026-10-20 añade otro filtro por hijo mediante el overlay, según el avance registrado al final; no convierte los nombres de allowed_tools en permisos MCP por operación.

- [x] CL-AUTH Implementar o extender una política del servidor por perfil/herramienta para los nuevos perfiles, antes de dar por efectiva la separación propuesta. Verificar tanto catálogo como invocación directa y conservar las comprobaciones de autoridad de cada operación.
- [x] CL-LOCAL Documentar y probar que la coordinación local pasa hoy por `_tool_denied_reason("assign", local_only=True)` y requiere identidad de terminal y contexto del proyecto; habilitar solo el nombre en el cliente no basta.
- [x] CL-CATALOG Auditar también `handoff`, `assign`, `assign_elastic`, `list_native_children`, `join_native_child`, `get_handoff_result`, `delete_terminal`, `answer_user_prompt` y `emit_ui`, más las familias de workflows, trabajo, Beads, Ralph y escritura/borrado de memoria. Decidir explícitamente por perfil qué se expone y qué se deniega; la tabla anterior es una propuesta inicial, no un inventario exhaustivo ni una exclusión ya implementada.

## Fuentes

- [Adaptador Claude Code](../src/cli_agent_orchestrator/providers/claude_code.py), [overlays](../src/cli_agent_orchestrator/providers/mcp_interactions.py) y [servidor MCP](../src/cli_agent_orchestrator/mcp_server/server.py).
- [Tareas y diagnósticos Spec 022](../specs/022-provider-agui-restart/tasks.md), [recorrido común](../test/live_provider/interaction_cli_journey.py) y [documentación Claude Code](claude-code.md).

No se han iniciado proveedores ni leído contenidos de credenciales para redactar esta lista. Los resultados históricos y los pendientes de validación deben permanecer diferenciados.

## Avance integrado 2026-10-20: base de perfiles y diagnóstico

Aplicación de Spec Kit a todos los pendientes: [Spec 025](../specs/025-provider-mcp-profiles/spec.md), [plan](../specs/025-provider-mcp-profiles/plan.md) y [tareas con correspondencias](../specs/025-provider-mcp-profiles/tasks.md). Los gates reales de Spec 022/023/024 conservan sus resultados y siguen separados.

- [x] CL-AUTH-01 Base opt-in del servidor: `interaction` limita a las tres operaciones existentes; `collaboration` añade únicamente `send_message`, `list_siblings`, `cao_list_interactions`, `cao_get_interaction` y `cao_escalate_interaction`. Filtrado exacto de catálogo y llamadas directas, incluidos desconocidos; recursos y prompts denegados. [Inventario actual](../specs/025-provider-mcp-profiles/catalog-inventory.md): 79 herramientas registradas, sin activar MCP Apps opcional. La autoridad interna de cada operación se conserva.
- [x] CL-AUTH-02 El overlay consume `cao_tool_profile` en la declaración MCP canónica, protege la variable interna y rechaza selecciones desconocidas o comandos no reconocidos antes del lanzamiento. Codex protege también esa política frente a overrides MCP. Los filtros vacíos/parciales del cliente se conservan; no se conceden permisos nativos adicionales.
- [ ] CL-AUTH-03 Completar propagación/permisos nativos y preflight por perfil antes de aceptación real ampliada. Con el marcador histórico presente se conserva la intersección a tres herramientas; sin él, T161/T166 acreditan8/12 en v2. Ninguno acredita todos los perfiles v1. Los pendientes AUTH/01–04 de alcance mayor no se cierran por esta base.

La configuración opt-in es `"cao_tool_profile": "interaction"` o `"collaboration"` dentro de una entrada cuyo comando sea el MCP CAO canónico. No se ha aplicado a las configuraciones del operador ni ejecutado proveedores para esta fase. La lista sigue describiendo propuestas donde no se indica expresamente integración.

La espera de completado ahora adjunta observaciones cerradas al 504 (`wait_diagnostics`): último estado, trabajo observado, errores/candidatos de recibo y extracción intentada. La matriz real conserva únicamente esa proyección sanitizada al fallar, en vez de perderla tras el assert. No cambia el presupuesto de 180 s, los criterios de completado, cuota, entrega ni reintentos. Esto mejora CL-01; no demuestra autenticación, latencia ni causa histórica del timeout.

Verificación de esta fase: 127 pruebas offline de contratos/configuración/diagnóstico y límites del harness pasaron; 237 pruebas adicionales seleccionadas de proveedor/configuración pasaron (36 de inicialización/diálogos fuera de esa selección). Sin aceptación real nueva. El estado de revisión independiente y composición se registra en [validación](../specs/025-provider-mcp-profiles/validation.md).

Configuración de la base: un comando de texto con perfil utiliza `env`; si también declara un mapa `environment`, el lanzamiento se rechaza para evitar perder la política en Codex/Claude. El formato de comando en lista de OpenCode utiliza `environment`. La propagación desde el instalador nativo continúa en T007.

## Continuación T007 — 2026-10-20

- Integrado: el preflight de OpenCode v2 comprueba todas las herramientas del perfil efectivo a partir de `environment` generado. Un perfil `collaboration` requiere sus ocho herramientas; con el marcador histórico la intersección sigue siendo exactamente tres. Configuración inválida o política en el campo `env` ignorado se rechaza. Se conservan Code Mode desactivado, colisiones y resolución de la última regla.
- Integrado: Codex emite `enabled_tools` como intersección del perfil y el filtro explícito del cliente. Un filtro vacío sigue vacío; un parcial no se amplía; sin filtro se anuncian solo las herramientas del perfil. El comportamiento sin perfil conserva su configuración previa.
- Corregido: el instalador nativo de OpenCode descartaba `cao_tool_profile` y podía informar éxito. Hasta completar la propagación por agente, rechaza ese metadato y los marcadores internos en `env`/`environment` antes de cambiar artefactos nativos, contexto o proveedor registrado. No se almacena política por agente en el servidor compartido ni en un sidecar de concesiones best-effort.
- Corregido: el CLI devolvía código 0 cuando el servicio rechazaba la instalación. Ahora sale con error y conserva el mensaje `Error:`. Las instalaciones correctas siguen con código 0. Las descargas/copias de fuente y el staging explícito `--env` previo conservan sus efectos existentes; no se promete rollback global.

T007/T022 siguen abiertos: falta publicación de política nativa por agente con protección frente a instalaciones parciales y activar su preflight cuando proceda sin opt-in de interacción. Los perfiles ampliados no tienen aceptación real por estos cambios. El diagnóstico causal del timeout de Claude y el binario v1 fijado siguen pendientes. Evidencia del incremento en [validación T007](../specs/025-provider-mcp-profiles/validation-t007.md).

Durante esta continuación se reprodujo además una carrera del test de catálogo nativo: `/doc` podía responder antes de que su recibo privado `service.json` fuera legible. Se corrigió la espera dentro del mismo límite de 15 s, sin cambiar el límite exterior de 45 s ni los criterios de catálogo/limpieza. Se conservan el fallo y la ejecución corregida con el binario v2.0.18 fijado. Es una validación de catálogo; no acredita la aceptación real ampliada ni explica el 504 de Claude.

Verificación final de esta continuación: **319 pruebas offline pasadas**, más **1 comprobación nativa de catálogo** pasada con OpenCode v2.0.18; cinco contratos de arquitectura conservados. El fallo previo del catálogo se conserva junto al resultado corregido. Revisión de Claude integrada y recibos de fuente en [validación T007](../specs/025-provider-mcp-profiles/validation-t007.md). Ningún gate de aceptación ampliada se cierra por estos resultados.

## Continuación T007/T027 — preflight explícito sin opt-in

OpenCode v2 ahora valida las concesiones de cada hijo CAO activo con perfil explícito aunque el terminal no solicite el canal de interacción. Las concesiones ausentes/parciales, el segundo servidor sin concesiones, las colisiones, el agente no seleccionado y los comandos de texto inválidos fallan antes del lanzamiento. Los perfiles no anuncian por sí solos el canal de preguntas; se conservan los hijos deshabilitados y el comportamiento legado sin perfil.

La selección final pasó **279 pruebas**, incluidas **20 regresiones nuevas** de inicialización, con cero fallos/omitidas. Composición pasó 5 contratos/0 rotos y Black/isort pasaron. Es un cambio del adaptador OpenCode; no amplía permisos de Codex/Claude ni acredita un recorrido real nuevo. T007/T022 y el NFR global siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t027.md).

## Continuación T022 — política nativa por agente

Implementado en OpenCode: la instalación consume perfiles válidos y publica un agente nativo con estados pendiente/listo y vinculación a su configuración. Los fallos intermedios y las entradas compartidas sustituidas impiden el arranque; una reinstalación o refresh correcto lo repara. CAO v2 aplica el perfil en su copia privada, sin contaminar el MCP compartido. Se conservan el opt-in histórico de tres herramientas, los permisos por operación y los lanzadores persistentes. Los agentes con esta marca, incluso al volver a un perfil vacío, requieren CAO v2; la invocación directa de OpenCode no aplica esta política.

Pasaron **527 pruebas**, incluidas **72 nuevas**, tipado de **404 archivos**, estilo y composición **5/0**. Revisión de Claude integrada. T022 queda cerrado como implementación; T007 y las aceptaciones reales/globales siguen abiertos. No cambia permisos de Codex/Claude ni explica su diagnóstico histórico. [Resultados, fallos preservados y límites](../specs/025-provider-mcp-profiles/validation-t022.md).

## Continuación T031 — diagnóstico de completado y turno real Claude

Integrados contadores de sondas de recibo e intentos de recuperación del prompt y la señal existente de entrega verificada por recuperación. Pasan por el HTTP 504 y el sanitizador cerrado, que conserva compatibilidad con el formato anterior. No cambia plazos, reintentos, permisos ni criterios de completado. Pasaron **214 pruebas**, tipado de **404 archivos**, estilo y composición **5/0**; revisión de Claude completada.

El diagnóstico real corregido con Claude Code **2.1.303** y modelo solicitado `claude-sonnet-4-6` completó una instrucción Unicode: **HTTP 200 en 21,909 s**, marcador exacto, recibo nativo `succeeded` asentado y limpieza confirmada. El primer verificador privado falló por leer `output` en vez de `last_message`; su fallo se conserva y no se atribuye al producto. PostgreSQL/fuente/binario permanecieron verificados y los metadatos de las credenciales del operador no cambiaron. T031 cerrado; T006/CL-01–04 siguen abiertos porque el 504 histórico no se reprodujo. Este turno sin herramientas no acredita preguntas/ACK/AG-UI/reinicio, perfiles ampliados, mensajes Work ni gates globales. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t031.md).

## Continuación T032 — filtro explícito de Claude

Implementada la propagación de `enabled_tools` para hijos MCP Claude con perfil CAO explícito: la lista vacía deniega todo y una parcial solo reduce el perfil. El servidor aplica el mismo conjunto inmutable al catálogo y a las llamadas directas. Se protegen los marcadores internos; el comportamiento legado sin perfil y los permisos por operación se conservan.

Se reprodujeron **25 fallos/2 correctas** antes del cambio; la primera selección corregida pasó **66 pruebas**. Tipado **404 archivos**, composición **5/0** y revisiones de producto/pruebas completados. La selección ampliada y las nuevas pruebas del proceso stdio real siguen pendientes: el control de memoria rechazó su arranque por estar por debajo de 6.144 MiB. El usuario indicó mantener pendiente esa validación. T032/T007 no se cierran por esta evidencia parcial. [Resultados y límites](../specs/025-provider-mcp-profiles/validation-t032.md).

## Continuación T033 — negativa de política ignorada en ruta nativa

Corregido el bypass de configuración: un perfil Claude con `native_agent` y `cao_tool_profile` ya no devuelve un comando que descarta la política. Se rechaza esa combinación, incluso con valores nulos/inválidos, entradas Pydantic o permisos nativos amplios. La ruta nativa sin política CAO explícita y el perfil CAO completo conservan su comportamiento.

Pasaron **243 pruebas**, incluidas **36 nuevas**, tras reproducir **30 fallos y 6 controles correctos**. Los dos casos retocados durante la revisión de aislamiento también pasaron en una ejecución unitaria serial. Tipado **404 archivos**, composición **5/0** y estilo correctos; revisión realizada solo con Codex. T033 cerrado. No se ejecutaron CLIs de proveedor ni se reactivaron la selección de 16 módulos o los dos casos stdio aplazados de T032. T007 y las aceptaciones reales/globales siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t033.md).

## Continuación T034 — filtros descartados al instalar OpenCode

Corregida una pérdida de configuración en la instalación nativa de OpenCode: combinar `cao_tool_profile` con `enabled_tools` no nulo se rechaza antes de publicar artefactos, en vez de instalar silenciosamente el perfil completo. Incluye listas vacías/parciales y valores malformados. Los filtros nulos/ausentes y la preparación legada conservan su comportamiento. No se ha implementado aún la propagación nativa del filtro ni se reconstruyen filtros perdidos en instalaciones antiguas.

Pasaron **52 pruebas**, incluidas **29 nuevas**, tras reproducir **24 fallos/28 controles correctos**; tipado **404 archivos**, estilo y composición **5/0**. Revisión solo con Codex, sin CLIs reales ni cambios de credenciales o permisos de otros proveedores. T034 cerrado; propagación nativa, T007, T032 ampliada aplazada y gates reales/globales siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t034.md).

## Continuación T035 — propagación nativa de filtros OpenCode

Implementadas listas `enabled_tools` válidas por agente: vacía deniega todo, parcial intersecta el perfil y los nombres desconocidos no amplían. El formato nativo v2 vincula filtros inmutables al agente y su configuración; el formato v1 sin filtro conserva sus bytes y binding. El hijo privado recibe el filtro protegido, sin ponerlo en el MCP compartido. El preflight usa la misma intersección y exige las tres herramientas si se solicita interacción. Se conserva el rechazo de valores malformados. Esto sustituye únicamente el rechazo de listas válidas de T034.

Verificación final: **193 pruebas** en siete módulos, compatibilidad exacta con el código anterior en **12 casos**, tipado **404 archivos**, estilo y composición **5/0**. Revisión solo con Codex. Sin CLI real, autenticación ni PostgreSQL; T032 ampliada no reanudada. T035 cerrado; el resto de la lista OpenCode, autoridad ampliada, v1 real, T007 y gates reales/globales siguen en curso. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t035.md).

## Continuación T036 — catálogo ampliado y autoridad

Integrados siete perfiles cerrados: interacción, colaboración, `coordination`, `supervision`, `knowledge`, `knowledge_writer` y `elastic_worker`. Cada perfil añade solo su familia; la colaboración básica no obtiene delegación, supervisión ni escritura. Auditadas las **79 operaciones** del catálogo: las restantes se deniegan también por llamada directa. Asignación general, handoff, borrado, Work/workflows, Beads/Ralph y borrado de memoria siguen fuera de estos perfiles.

La supervisión ahora exige metadatos actuales del terminal propio o de un hijo asignado directamente al llamante antes de inspeccionar/verificar/cancelar; conserva permisos del servidor, generación y autoridad Work. Coordinación mantiene el gate `assign` local con identidad/proyecto, y el cierre elástico exige trabajador configurado. Conocimiento conserva los controles existentes de ámbito, memoria y aprendizaje; escritura es un perfil separado.

Pasaron **181 pruebas** de catálogo/transporte/autoridad/preflight y ocho comprobaciones suplementarias de los cuatro handlers locales sin identidad/proyecto, sin enviar tareas ni mensajes. Tipado **404 archivos**, estilo y composición **5/0**, revisión solo con Codex. Las casillas cerradas de OpenCode corresponden a implementación y contratos offline; **OC1 y la aceptación real de OC2/OC-05/06 siguen pendientes**, junto a los lanzadores externos y modalidades de autenticación. T032 aplazada no se reanudó. [Inventario completo](../specs/025-provider-mcp-profiles/catalog-inventory.md) y [evidencia/límites](../specs/025-provider-mcp-profiles/validation-t036.md).

## T032/T047 — validación aplazada completada

La instrucción expresa de terminar lo aplazado reanudó esta validación. La selección actual de 16 módulos reprodujo una expectativa antigua de metadatos tras T035: 604 correctas/una fallida. Corregida la prueba para exigir filtros protegidos parcial/vacío e identidad de capacidades aislada, la misma selección pasó **605 pruebas**, incluidos los **dos procesos MCP stdio reales**. Estos ejecutan Python/CAOS; no se lanzó Claude Code ni un modelo ni se usó su login. Se conservan los fallos y las negativas anteriores por memoria. T032 se cierra en su alcance de transporte/regresión, no como aceptación real autenticada del proveedor ni de T007 completo. [Evidencia actualizada](../specs/025-provider-mcp-profiles/validation-t032.md).


Preparación global compartida, 2026-10-22: R4 actualizó HEAD/digest/rutas sobre main73bb0c0b y pasó seis grupos puros de control de archivos, con aprobación de Claude en modo lectura. Las comprobaciones de fuente, intérpretes y PostgreSQL precedieron al bloqueo de memoria; **no arrancaron** los tres NFR fríos, la suite completa, Apps ni el ratchet. Los gates siguen cerrados tras tres admisiones bloqueadas por memoria y los límites/coberturas originales no cambian. No se detuvieron las pruebas Angular ajenas observadas. [Preparación, bloqueo y límites](../specs/025-provider-mcp-profiles/global-gate-preflight-20261022.md). Los resultados históricos de memoria/cobertura y la aceptación global de este proveedor siguen pendientes.

Actualización global compartida, 2026-10-22 (R5/T132): la memoria permitió ejecutar el primer NFR frío cubierto sobre main5628bdf6, que **falló en 1.023152965s** frente a `<1s`. No se ejecutaron los otros dos NFR, backend completo, Apps ni ratchet. Un diagnóstico separado conservó el fallo y 21 verificaciones/20 auditorías, situando 523.697ms en el catálogo sin demostrar aún una corrección segura. Limpieza propia verificada; no se cambian límites ni se cierra aceptación global. [Recibo y diagnóstico actual](../specs/021-postgresql-persistence/current-source-nfr-20261022.md). Trabajo posterior solo con Codex por instrucción del usuario.


Actualización compartida2026-10-25: T140 corrige la equidad del inbox al unificar la admisión de eventos de estado en2Q lugares, conservando límites/orden/cierre.129 pruebas del controlador, API y PostgreSQL pasan; esto es infraestructura compartida y no una ejecución nativa reciente de este cliente ni un cierre de sus gates. [Alcance y evidencia](../specs/025-provider-mcp-profiles/validation-t140.md).


Actualización compartida2026-10-25: T141 elimina el bloqueo global de reconstrucción en ProviderManager.get_provider. Las restauraciones de terminales distintos avanzan de forma independiente y los lectores del mismo terminal siguen esperando a su recibo restaurado antes de ver el adaptador.187 regresiones pasan, incluida PostgreSQL con adaptadores Codex reales sin iniciar CLI/modelo; arquitectura5/0 y revisión de Claude/Luna. Esto no cierra las carreras previas de creación/limpieza, OC-06 ni la aceptación nativa actual de este cliente. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t141.md).


Actualización compartida2026-10-25: T142 corrige solapamientos del registro de proveedores: la reconstrucción no pisa la creación explícita, una limpieza anterior no elimina al propietario nuevo y el listado usa una instantánea. Se conservan las opciones de lanzamiento y la limpieza reentrante devuelveFalse para retener el reemplazo y sus metadatos.241 regresiones pasan, incluida PostgreSQL/perfiles/recibos; esto no acredita el ciclo completo hasta borrar la fila, varias instancias ni inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t142.md).


Actualización compartida2026-10-25: T144 completa la integración asíncrona de T142. La publicación del proveedor y el tramo final ya autorizado de reversión se ejecutan fuera del bucle, conservando contexto y propiedad del trabajador ante cancelación repetida.273 regresiones pasan, incluida PostgreSQL, creación/worktrees y los controles anteriores; arquitectura5/0. Esto no acredita cancelación segura de todo el ciclo ni inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t144.md).


Actualización compartida2026-10-25: T145 corrige la reconstrucción entre limpieza y borrado: retira cualquier adaptador reaparecido y protege el borrado individual y los barridos finales de sesión/Flow. Mantiene el monitor fuera del bloqueo para evitar una inversión de orden.398 regresiones pasan;35 controles finales verifican los avisos posteriores de Flow y los20 casos nuevos. PostgreSQL real, arquitectura5/0, revisión Claude/Luna y recursos propios cerrados. T143 sigue abierto para publicación/inicialización concurrente, identidad por fila y alcance distribuido; no acredita inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t145.md).


Actualización compartida2026-10-25: T146 reserva el arranque antes de publicar su fila y aplaza la retirada extranjera antes de efectos, también en Herdr y canal remoto. La publicación comprueba el registro actual; los permisos de compensación no se heredan en callbacks normales.676 pruebas pasan con PostgreSQL, perfiles, Work, API/MCP y Herdr; arquitectura5/0 y revisión Claude/Luna. Una cancelación con trabajadores sin parada demostrada conserva el propietario hasta reiniciar su proceso; no acredita aceptación nativa actual ni cierra toda T143. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t146.md).


Actualización compartida2026-10-25: T147 corrige la reversión de una creación fallida que borraba una fila ajena con el mismo ID. Exige publicación confirmada antes de limpiar por ID, conserva commits ambiguos y marcas previas, retira solo worktrees creados y liquida solo recibos propios.221 regresiones pasan con PostgreSQL; arquitectura5/0 y revisión Claude/Luna. T143 sigue abierto para preasignación KAS/hijos, generación, sidecars, Herdr y alcance distribuido; no acredita inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t147.md).


Actualización compartida2026-10-25: T148 publica las pruebas KAS solo después de confirmar el registro y antes del proveedor. Los archivos existentes ya rechazaban reemplazar contenido distinto; se corrigió la creación de archivos ausentes con un ID rechazado. Fallo parcial conserva fila/prueba y marca un hijo propio reconcile; cancelación espera al trabajador real.310 regresiones pasan, arquitectura5/0, revisión Claude/Luna y PostgreSQL propio cerrado. T143 sigue abierto para recibos preasignados, generación/distribución, sidecars/Herdr y reconciliación; no acredita inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t148.md).


Actualización compartida2026-10-25: T149 impide planificar un hijo sobre un ID ocupado y registrar un terminal sobre un hijo ajeno ya planificado. PostgreSQL comparte admisión por tabla/ID entre planificación y ambos escritores local/remoto; el hijo requiere su UUID exacto, padre/proveedor/perfil y estado planned.521 regresiones pasan, con carreras observadas, proceso independiente y rollback; arquitectura5/0 y revisión Claude/Luna. T143 sigue abierto para borrado/reutilización ABA, SQL directo, otras bases y sidecars/Herdr; no acredita inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t149.md).


Actualización compartida2026-10-25: T150 corrige archivos de error/inicialización completada que quedaban tras barridos de sesión y reciclado Flow. Ordena publicación y borrado con leases por terminal, sin bloquear IDs ajenos durante I/O ni conservar claves históricas. Mantiene la operación SQL por lote, IDs/resultado/error y evidencia de filas excluidas o resultados ambiguos.419 regresiones pasan;3 deselecciones configuradas intactas, arquitectura5/0 y revisión Claude/Luna. T143 sigue abierto para Herdr/SQL directo, generaciones/otros procesos, KAS posterior y reconciliación; no acredita inferencia nativa actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t150.md).


Actualización compartida2026-10-25: T151 retira fantasmas Herdr solo con ausencia nativa exacta y limpieza central confirmada; conserva estado ante incertidumbre y etiquetas vivas sin ubicación. El trabajador bloqueante conserva su propiedad y el bucle aplica bajas con testigo de inscripción actual, sin historial por ID.582 regresiones pasan;3 deselecciones intactas, arquitectura5/0 y revisión Claude/Luna. T152 recoge una inversión previa de bloqueos pendiente de reproducción; T143 y la aceptación nativa actual siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t151.md).


Actualización compartida2026-10-25: T152 reproduce y corrige la inversión de bloqueos entre creación con padre y limpieza Herdr. Creación toma sesión antes de dispatch, mantiene comprobaciones de identidad/generación y rollback bajo ambos; límites intactos.597 regresiones pasan,3 deselecciones configuradas intactas y arquitectura5/0. Claude revisó la corrección de una aserción de prueba y dio GO; auditoría Luna y PostgreSQL cerrado. T143 y aceptación nativa actual siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t152.md).


Actualización compartida2026-10-25: T153 reproduce y corrige pares KAS huérfanos y retirada sin prueba nativa en ambas rutas de reciclado Flow. Exige ausencia exacta antes de limpiar proveedores y retira solo pruebas seleccionadas tras SQL confirmado.626 regresiones pasan,29casos nuevos,3 deselecciones configuradas intactas y arquitectura5/0; GO Claude/Luna, PostgreSQL cerrado y Obsidian actualizado. Conserva evidencia ante incertidumbre/error SQL; parada previa y unlink parcial siguen explícitos. T143 y aceptación nativa actual continúan abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t153.md).


Actualización compartida2026-10-26: T154 corrige el worktree huérfano al cancelar una asignación con retorno positivo antes de enviar la creación backend. Drena también la limpieza y conserva cancelación original, trabajo sucio y commits sin integrar; fases posteriores se retienen.683 regresiones pasan,15casos nuevos,3 deselecciones configuradas intactas y arquitectura5/0. Se corrigió una contaminación de fixture detectada por los controles existentes; GO Claude/Luna, PostgreSQL cerrado y Obsidian actualizado. T143 y aceptación nativa actual siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t154.md).


Actualización compartida2026-10-26: T155 impide borrar la fila de una creación cancelada sin comprobar vinculación del intento, encarnación actual y ausencia nativa exacta. Conserva fila/worktree ante incertidumbre, cambio o negativa de retirada.710 regresiones pasan,27casos nuevos,3 deselecciones intactas y arquitectura5/0; GO Claude/Luna, PostgreSQL cerrado y Obsidian actualizado. Los simuladores de éxito ahora aportan prueba explícita sin relajar sus aserciones. Retener metadata no acredita readiness ni una clasificación duradera nueva; T143 y aceptación nativa/global siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t155.md).


Actualización compartida2026-10-26: T156 registra inicialización cancelada del intento reconocido antes de liberar admisión y bloquea mensajes ante fallo conocido, sin denegar UNKNOWN globalmente. Reutiliza PostgreSQL/sidecar; si ambos fallan, conserva solo cuarentena local y no promete recuperación tras reinicio.818regresiones pasan,27casos nuevos,3deselecciones intactas y arquitectura5/0. Se reprodujo y corrigió un fixture T152 que contaminaba la ruta de logs, manteniendo sus controles y los39fallos originales. PGcerrado/ausente, fuentes verificadas, revisión Claude/Luna y Obsidian actualizado. T143, carrera anterior a compensación y aceptación nativa/global siguen abiertos; siguiente recorrido: v1 mixto ligado a fuente actual. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t156.md).


Diagnóstico compartido2026-10-26: T157 ejecutó v1 mixto real en70c1 con526runtime/11artefactos nuevos: preguntas/respuestas/ACK, mensaje, AG-UI e identidades/reinicio exactos, pero **resultado final FAILED** tras53HTTP202→409.99pruebas/133controles y generación auxiliar real PASS no acreditan ese resultado. Estado concreto del409 y causa sin demostrar; no repetir sin reproducción/observación útil. Limpieza completa y propietario PostgreSQL cerrado, fuentes/credenciales OpenCode intactas, arquitectura5/0. No cierra gates de OpenCode/Codex/Claude/global. [Recibos y siguiente límite](../specs/025-provider-mcp-profiles/validation-t157.md).


Actualización compartida2026-10-26: T158 corrige una carrera local pending→verified al leer LAST mediante una sola lectura ligada a la generación; mantiene texto vacío válido, ausencia/sustitución/cancelación cerradas y errores tipados. Añade diagnóstico409por enum y observer API de etapas opt-in/default-off, sin reenvío al modelo.215pruebas finales pasan, arquitectura5/0, PostgreSQL cerrado, GOClaude/Luna y Obsidian. Fallos del lanzador/fixture reproducidos y corregidos sin relajar controles. Causa realT157 y aceptación nativa/global siguen abiertas. [Evidencia y próximo recorrido](../specs/025-provider-mcp-profiles/validation-t158.md).


Diagnóstico/aceptación acotada2026-10-26: T159 v1 colaboración real **PASS** enb2b56b67: preguntas/respuestas/ACK, mensaje, AG-UI, reinicio/identidad y finalHTTP200 exactos. Generaciónaux27,597s; modelo27,594s/recorrido85,846s, mínimo10207MiB.123preflight/133controles; observadoresAPI completos9/19eventos con witness/CASsatisfactorios, sin409. No prueba causaT157 ni intentosprogramados/etapasv2.537pinsverificados, credencialesintactas, puertas cerradas y propietarioPG/contenedor/red eliminados. Arquitectura5/0, GOClaude/Obsidian. Sigue v2actual; perfilesampliados/OAuth/concurrencia/WorkT143/global separados. [Recibos y límites](../specs/025-provider-mcp-profiles/validation-t159.md).


Actualización2026-10-26: T160 añade provider_lifecycle al mapa exacto de orígenes cargados de v2; su archivo ya estaba en el inventario, pero eso no probaba el módulo importado.2RED/2controles→4GREEN,152regresiones pasan, arquitectura5/0, PGcerrado y GOClaude/Obsidian. Solo guard de procedencia; sin fallo nativo ni aceptación v2 nuevos. Repinará el próximo full8 desde el HEAD aceptado. [Recibos y límites](../specs/025-provider-mcp-profiles/validation-t160.md).


Aceptación acotada2026-10-26: T161 v2full8 real **PASS** enbfacaf8e/540pins:85,07s/fases14,897s y8,973s/min10677MiB; mensajes/ACK/AGUI/reinicio/recibos exactos. OfertaCAOMCP8emisor/0receptor en peticionesprimarias yargumentos48/51bytes exactos. HTTPsellado sin atestación al reciboactivo; peticionesmodel_mismatch y1intentono resuelto se conservan. Fuente/credencialesintactas, puertas/PGcerrados ycontenedor/redausentes. Arquitectura5/0, GOClaude/Obsidian. Quedan OC06/T143 y otros perfiles/OAuth/global/Work con suslímites; siguiente reproducciónentreprocesos, sin tocarjobshistóricos. [Evidencia](../specs/025-provider-mcp-profiles/validation-t161.md).


T162 validado: carrera real dosprocesos RED2fallos/2controles; final855PASS/3deselecciones intactas/8avisos y24compatibilidadPASS/2avisos. Pool estable256stripes con flock antes de leases locales, bulk ordenado y reentrancia; fallos de guard no mutan. Cuatro casos realPG/DrvFS pasan sin modelo. Fixtures usan rutas reales privadas; fallos intermedios preservados. Arquitectura5/0, GOClaude condicionado satisfecho, fuentes/propietariosPG verificados y Obsidian actualizado. Coordinación compartida cooperativa, sin CAS distribuido: lectura SQL caída después de borrado puede publicar evidencia. T143/OC06/global siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t162.md).


T163 validado: reproducción real PG/Git de 2 fallos y 1 control; 8 pruebas iniciales y 42 controles de compatibilidad pasan. Final: 887 PASS, 3 deselecciones configuradas intactas, 8 avisos, 684.98s. El rollback exige vinculación e incarnación, ausencia nativa exacta, limpieza del proveedor y borrado confirmado antes de retirar el worktree. Conserva recursos ante incertidumbre/error. El timeout del auxiliar se trazó a una importación de 10,577s; caché privada canónica, plazos y cobertura intactos, sin aceptación de arranque frío. PG cerrado, fuentes verificadas, arquitectura 5/0 y GO Claude. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t163.md). T143/OC06 y clasificación/input de fallos ordinarios retenidos siguen separados.


T164 validado: dos RED reales separados (entrada a shell retenido y liberación tras lectura fallida de identidad) corregidos. 80 controles enfocados y 893 regresiones pasan, 3 deselecciones configuradas intactas, 8 avisos, 676.87s. Evidencia genérica initialization_error en DB/fallback antes de liberar admisión; sin almacenes o lectura fiable, cuarentena solo local. Conserva identidad, autoridad, error original y contrato de cancelación. PG/contenedor/red cerrados, fuentes verificadas, arquitectura 5/0 y GO Claude. Sin nueva aceptación nativa/global. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t164.md).


T165 validado:2RED raíz/hijo por error real de SessionLocal→2GREEN;112 controles y4casos finales con fallo/sano y excepción atestiguada dentro de la transición. Final897PASS/3deselecciones configuradas intactas/8avisos en707.26s. El fallo best-effort al guardar estado del hijo ya no sustituye initialize ni omite la compensación T163/T164. Sin reintentos ni falsa confirmación de recibo. Fuentes y cierre PG/contenedor/red verificados, arquitectura5/0 y GOClaude. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t165.md). Siguiente: operación real de perfil coordination v2/OC2-04; sin cierre nativo/global.


T197r5: intento actual ligado a fef8462e/544archivos **FAILED en preflight**,9,964s/min10023MiB: OAuth caducado según el guard existente; no arrancaron API/CLI/modelo ni hubo mensajes/reinicio. Login sin modificar/renovar, PostgreSQL prestado conservado, copias privadas ausentes y lanzador cerrado. T209 conserva su PASS nativo de cierre acotado; T197/T210 siguen abiertos. No repetir contra la misma credencial caducada. [Recibo y límites](../specs/025-provider-mcp-profiles/validation-t210.md#original-full-journey-attempt-r5).


T197/T210 r6: **PASS acotado** en b22b85fa/544 archivos: dos mensajes Unicode, dos filas/dos eventos AG-UI, un reinicio y sello normal del SDK dentro de 6s; 146,319s/min9477MiB, 41identidades retiradas. Perfil colaboración con parada gestionada de producción, sin EOF solicitado por el harness. El recibo padre FALSE se conserva: resolvía el HOME después de eliminar workspace. Comprobación independiente de cierre y revisión Claude GO acreditan ausencia del staging y preservación del archivo privado de login; original intacto, propietario PG cerrado después de sus prestatarios. T197/T210 cerrados sólo en ese modo; default/500ms del proveedor/Work/global siguen separados. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t197-native-r6.md).


T211: corrección compartida FIFO integrada para validación: lector/decoder nuevo por reinicio, salida vieja encolada rechazada por identidad y epoch, fallo de retirada conserva el fence, recuperación sincroniza captura y seed. Regresión301PASS seguida de55controles tras consistencia de proveedor/imports; modelos sin rebajar criterios, tipos3módulosPASS/17errores heredados iguales a HEAD, arquitectura5/0 y ClaudeGO. No se repite cuota parcial: T179/T181 ya la aceptan. R2 nativo Codex0.164 pasa en106,33s/min9338MiB: desconexión HTTP real de A, cancelación hasta reemplazo listo sin reenvío, B verifica pendiente200/emite salida FIFO nueva y conserva recibo/proceso; cierre normal de22identidades/3wrappers y SQL0/propietarioPG cerrado. R1FAIL por doble cierre del descriptor queda conservado; corrección mínima revisada. T211 cerrado sólo en este alcance; CX09/T013 completos/otros proveedores/global siguen abiertos. [Evidencia y límites](../specs/025-provider-mcp-profiles/validation-t211.md).
