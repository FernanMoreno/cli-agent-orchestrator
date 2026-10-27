# Comprobación actual por proveedor

T185: timeout nativo Codex180s PASS en5f034ca3; HTTP504 en208,728s con arranque, verify200/pending sin reenvío, generación ajena409 y cancelación actual200 en25,875s. Cinco procesos viejos retirados, reemplazo listo/0peticiones y limpieza completa; hijo conserva reconcile/timeout/settled_at.323PASS/5avisos,26controles nuevos incluidos, arquitectura5/0,1744pins y PG cerrado. Backend sintético; todaCX08/otros proveedores/global siguen separados. [Recibo y límites](validation-t185.md).

T183/T184: corregida la circularidad entre el recibo pendiente y el arranque tras cancelar. Codex nativo en49297912 PASS:409 para generación ajena,200/cancelled26,075s para la actual; salida de cinco procesos viejos, reemplazo disponible y0peticiones en3,161s, join/limpieza estables y padre API200.1116PASS/3opt-in previos;28controles nuevos, arquitectura5/0 y mypy canónico4archivos. Primer FAIL conservado; timeout/global/otros proveedores siguen separados. [Recibo y límites](validation-t183.md).

T182: validada la propiedad de cancelación compartida con PostgreSQL real y barreras: desconexión del llamante, duplicados, fallos de reset/readiness/CAS, generaciones obsoletas y progreso de otro terminal.11controles nuevos y157regresiones PASS, arquitectura5/0, GOClaude y PG cerrado. Sin cambio productivo ni aceptación de cancelación nativa/FIFO/timeout/global. [Recibo y límites](validation-t182.md).

T181: API gestionada Codex80×50 PASS en436dc0ae: parcial→401 da502/error/failed34,325s; parcial→cuota da409/quota_wait/reconcile28,690s. TTY80desde arranque y cada petición, capturas2/3líneas, join/veredicto y limpieza estables; padre220accesible.435regresiones/24controles finales solapados/arquitectura5/0,1737pins y recursos propios cerrados. Backend sintético; cancelación/timeout/footerless/global siguen separados. [Recibo y límites](validation-t181.md).

T180 Codex envuelto: capturas nativas80x50 y corrección del parser2cc5c66b,24clasificaciones/1735pins;668regresiones y60controles adicionales pasan, arquitectura5/0. Distingue celdas nativas de citas y conserva220sin cambiar dimensiones productivas. No acredita API estrecha, rawpipepane, footerless, cancelación/timeout ni otros proveedores. [Recibo y alcance](validation-t180.md).

T179 cuota parcial Codex: corrección tipada y aceptación gestionada b128b126,409/quota_wait/reconcile28,790s, una captura propia220x50 y join/limpieza estables.560PASS/3opt-in previos,44nuevos y arquitectura5/0; procesos/PG cerrados. Sin transferencia a otros proveedores ni aceptación de cuota real, auto-resume o cancelación/timeout/global. [Recibo y alcance](validation-t179.md).

T178 cierra el recorrido gestionado Codex de parcial→401 en05f1c369: API502/failed35,311s, captura visible vinculada220x50, join/veredicto/limpieza exactos y padre accesible;199PASS/arquitectura5/0. No acepta cuotas parciales, cancelación/timeout, rawclassification ni otros proveedores. [Recibo y alcance](validation-t178.md).

T177 corrige Codex partial→HTTPerror en de3fc61f, basado en capturas CLI0.164/5c646430.399PASS/3opt-in omitidos,24nuevos, arquitectura5/0. Mantiene citas, diálogos y entrada multilínea. Aceptación del parser sobre viewport; no API gestionada parcial, rawpipepane, cuota parcial ni otros proveedores. [Recibo y límites](validation-t177.md).

Actualización T176: tres errores Codex mediante API/CLI real y HTTP sintético pasan en303d3f65; auth/throttle502→failed, cuota409→reconcile, join y limpieza preservan veredicto.172 contratos/18nuevos y arquitectura5/0. No transfiere aceptación a OpenCode/Claude ni cierra cancelación, auto-resume, cuotas reales o todos los gates. [Recibo acotado](validation-t176.md).

2026-10-26. Fuente ejecutada: `671c9c8f01a8174ef79096c822dce9f66229c071`. [Recibo acotado](validation-all-providers-current.json). Dos auditorías READ independientes de Luna, revisadas contra fuentes y recibos por Codex; no activaron CLIs ni tocaron credenciales.

## Contratos actuales

141 PASS, 2 avisos, 146,57s. Se comprobaron los cinco módulos indicados en el recibo: builders de configuración, perfiles/filtros nativos, preflight y catálogo/dispatch MCP mediante FastMCP y procesos stdio Python. Se verificaron sus marcas offline_database y autostart PostgreSQL desactivado. Todos los pins de fuente del recorrido R7 y los cinco módulos de test permanecieron intactos. No se cambian permisos ni respuestas finales de modelos.

Esta selección cubre contratos usados por los tres proveedores; no ejecuta los tres CLIs nativos ni equivale a aceptación del catálogo efectivamente ofrecido por cada modelo. Los recorridos nativos intensivos se serializan por memoria y propiedad de recursos.

| Proveedor | Evidencia nativa relevante | Gates actuales independientes |
| --- | --- | --- |
| OpenCode | R7 de esta fuente: preflight229,855s y Unicode/replay34,101s pasan; cancelación no se invocó porque la señal textual faltó antes de completed. Fallo native_model_75_second_deadline y167 capturas conservados; salida original MCP con excepción, cierre físico derivado sin aceptación. | T168 cancelación activa; luego perfiles/escenarios globales restantes. La propuesta de herramienta/hijo vivo es conceptual, no implementación ni cobertura de streaming. |
| Codex | CX-06 PASS9d5ba9f8/50,23s y CX-07 PASS418a9853/50,03s: una llamada MCP recupera la espera HTTP interrumpida entre dos procesos API,19→3s, ACK exacto, AG-UI3 y limpieza propia;1510pins. Codex0.164.0/gpt-6-luna solicitado. [Recibo CX-07](../022-provider-agui-restart/validation-cx07-current.json). | CX-01–04/CX-AUTH-03 catálogo/autorización nativos; CX-08 Unicode/errores/cancelación; CX-05 mensajes persistidos. send_message y cao.work.message se verifican por separado. |
| Claude | Diagnóstico actual en35a728c1: Unicode sin herramientas, Claude Code2.1.303/Sonnet4.6 solicitado, HTTP200/21,204s, recibo/join y limpieza;1508pins/operador intactos. [Recibo y límites](validation-claude-unicode-current.md). No explica504 ni acepta los recorridos ampliados. | CL-01–04 diagnóstico Unicode/504 actual; CL-05–07/CL-AUTH-03/CL-CATALOG autorización; CL-08/09 mensajes/interacción/reinicio. Login intacto y T032 aplazada. |

## Fuente y límites de las auditorías

Codex: adapter codex.py, mcp_tool_policy.py, validation-t007.md/validation-t036.md, Spec022 tasks y Spec023 tasks, validation-t132-work-message.json. Claude: adapter claude_code.py, mcp_tool_policy.py, middleware del servidor, test_claude_mcp_tool_filter.py, validation-t031.md y su lista específica. T168 contiene modelo opencode-go/glm-5.3-flash; T169 es CLI de emparejamiento. Ninguno acredita Codex o Claude. Las versiones/modelos históricos son evidencia del recibo, no fijaciones actuales para nuevos lanzadores.

Los tres documentos de pendientes distinguen ahora el estado específico de su proveedor del historial compartido. JSON estricto se exige a las operaciones automáticas que lo necesitan; no se fuerza JSON al modelo para ocultar variaciones de texto. Los contratos HTTP, MCP, AG-UI y CLI conservan su semántica propia.

## Composición y conservación

PASS de los contratos comprobados:5contratos de arquitectura conservados/0rotos. Fuente productiva y547pins de R7 intactos después de la suite; cinco módulos de test sin cambios. Ambos propietarios PG y todos los procesos/raíces de R7 quedaron cerrados antes de iniciar esta suite offline. El recibo MCP original de R7 mantiene fallo de salida de contexto; su prueba derivada solo acredita cierre físico/SQL, sin aceptación ni salida normal inventada. Credenciales del operador intactas en el recibo real; auditorías/tests no renovaron login. Graphify no se regenera: no hay cambio estructural productivo y sus archivos ajenos se preservan. Obsidian save-20261026-caos-cross-provider-gates actualizó cuatro páginas existentes mediante transacción guardada; lint estricto0.

Revisión READ de evidencia Claude4966b7b122404dc98c2419327b38e0e7: GO; sumas/estados/tiempos y límites por proveedor coherentes. Se explicita en el recibo que los cinco módulos de test permanecieron intactos y que el cierre MCP derivado no es salida normal ni aceptación.

La suite de contratos141PASS conserva su ejecución671c9c8f. El diagnóstico Claude posterior se ejecutó en35a728c1 y tiene recibo separado; no se relabelan fuentes ni se acepta Codex/OpenCode a partir de su PASS.

2026-10-27: Codex CX-06 tiene aceptación propia en9d5ba9f8 tras T171;
no se transfiere la ejecución141PASS ni los recibos de otros proveedores.
La corrección compartida cuenta con137contratos, incluidos PG/HTTP/replay,
arquitectura5/0 y revisión independiente. El recibo nativo no atestigua cuántos
reintentos internos ocurrieron ni interrupción HTTP en curso; CX-07 sigue abierto.

Actualización posterior: CX-07 cerrado por su propia ejecución418a9853,
50,03s y1510pins; un único wait nativo llegó a los procesos original/reemplazo
con19→3s, una sola respuesta ready y ACK exacto. El ConnectionError y la
unicidad de filas/eventos también se probaron sin modelo por separado.
La ejecución141PASS y el anterior reciboCX-06 mantienen sus fuentes y límites.

2026-10-27 · T173: corrección compartida del filtro de servidor para comandos de texto;194 contratos y arquitectura5/0. [Codex nativo en96f3eaae](validation-t173.md): siete perfiles × completo/vacío/parcial,21casos sin modelo y par adicional de catálogos3/12 pasan. Dos controles stdio independientes prueban el rechazo directo del servidor. Este avance solo cierra T173: el mapeo global allowed_tools, efectos autorizados de cada herramienta, modelos y aceptación nativa OpenCode/Claude conservan sus gates. Las ejecuciones141PASS y CX-06/07 mantienen sus fuentes originales.

2026-10-27 · [T174 Codex](validation-t174.md): baseline Unicode interactivo en7b2ea7d7 PASS12,267s, mismo recibo del hijo settled/cleanup/join,1720pins y propietarios cerrados. R1setup legacyprofile fallido conservado.120contratos distintos/arquitectura5-0. No cierra todoCX08 ni transfiere aceptación aClaude/OpenCode; los recibos anteriores conservan sus fuentes.

2026-10-27 · [T175](validation-t175.md): Codex80525802 clasifica las familias HTTPnativas capturadas401/429 comoERROR y capacidad explícita comoWAITING_QUOTA, sin convertir citas/avisos/cuotas antiguas en errores vigentes.416PASS/3opt-in omitidas,40casos nuevos,5/0arquitectura; tres capturas CLI0.164 sin operador/backend real y12clasificaciones finales. No acepta API/recibos de error, cancelación, otros proveedores ni todoCX08.
