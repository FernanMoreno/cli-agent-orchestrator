# T206 · EOF diagnóstico explícito antes de cerrar Claude

Implementado modo opt-in harness_requested_mcp_eof. Publicación de petición
completa por link exclusivo, nonce privado y lector estricto0600/no-follow.
El proxy rastrea RPC en ambas direcciones, exige buffers vacíos/0tools-call y
ningún byte listo antes de admitir; aborta si llega entrada tardía durante el
drenaje. El canal CLI debe seguir abierto hasta el EOF real de stdout MCP y
waitstatus0. Una RPC nueva del servidor después del EOF impide el cierre normal.

El worker exige secuencia exacta8eventos normales, ningún aborto/señal/limpieza
forzada, witness estricto, una sola instancia (incluye intentos fallidos antes
de witnessguard), y proxy_finished antes de cerrar CLIstdin. Revalida tras CLIexit0
con un solo plazo6s; sin fallback ni ampliación. Default y handlers se conservan.
Detección de datos tardíos cubre el drenaje hasta EOFstdout; no promete observar
bytes posteriores a la salida física del proxy.

TDD: RED por módulo ausente; controles reales de EOF sin cerrar entrada CLI,
RPC pendiente/bidireccional, fragmento parcial, bytes tardíos, señal, CLIEOF
temprano, RPC servidor tardía y helper6s. Selección final96PASS/26,60s con
regresión de lifecycle/proxy/cliente/HTTP/catálogo; composición5contratos/0rotos.
Se conservan fallos de configuración de nombres de archivo del test del helper,
corregidos para respetar los nombres del protocolo antes de cualquier nativo.

ClaudeGO condicionado a invocación/imports/builder/ROOTfresh. Su revisión detectó
la necesidad de comprobar otra vez RPC bidireccional al final; añadido y probado
con servidor que emite una petición después del EOF. Wrapper HTTP privado acotado
conserva cuerpos antes de clasificar primaria/auxiliar, sin headers/auth ni
proyección pública, para evitar perder la primera frontera de un fallo.

Estado: implementación y ambos controles nativos aceptados en su alcance. Se
ejecutó primero catálogo/apagado y después el caso sintético de denegación T204. No modifica
permisos/producto, no requiere API/PG/login, y no acepta apagado automáticoT197.

Revisión final Claude GO para integrar y control1 zero-turn: fuentes congeladas
contrastadas con scope-final,3imports del padre/12delworker y builder/env/nonce
comprobados. WriterHTTP real produjo2registros exclusivos0600 con readback antes
de servicios. ROOT sin launch/witness/marker/ledger previos; ejecución umask077.

## Aceptación nativa, 2026-10-28

Fuente main7af13f34/530pins, CLI2.1.303 fijado. Control1 zero-turn PASS42,510s,
mínimo10150MiB. Catálogo3 conectado,1initialize/1list/0calls; cierre MCP normal (wait0) antes
de CLIstdin, un proxy y ninguna señal. PeticiónEOF→wait0:1445,024ms; cierreCLI→
salida0:31,967ms; todo el cierre1491,912ms dentro de los6s.
[Recibo](validation-t206-native-preflight-r1.json).

Sólo después, con el mismo commit y scripts verificados en ROOT fresco, T204
PASS38,008s/mínimo9961MiB; cierre compartido1330,143ms y mismo orden normal.
[Recibo de denegación](validation-t204-native-client-r1.json). Se revalidaron
los530pins, los archivos ejecutados y su integridad antes de editar documentos;
ambos grupos retirados, descriptores recerrados, archivos ejecutados inmutables.
Namespace/máscaras probados y ninguna copia de login, API o PostgreSQL.

El modo es harness_requested_mcp_eof; native_automatic_shutdown_accepted=false
en ambos recibos. No se convierten las señales en éxito, no se modifica T197
ni se reclasifican T204r1/r2 o T205. Datos tardíos se vigilan hasta el cierre de
stdout MCP; no hay garantía posterior a la salida del proxy.

Revisión independiente de ambos recibos y alcance: Claude GO, sin hallazgos
bloqueantes. No da crédito al ámbito automático/producción/global.
