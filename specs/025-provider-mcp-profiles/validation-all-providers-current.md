# Comprobación actual por proveedor

2026-10-26. Fuente ejecutada: `671c9c8f01a8174ef79096c822dce9f66229c071`. [Recibo acotado](validation-all-providers-current.json). Dos auditorías READ independientes de Luna, revisadas contra fuentes y recibos por Codex; no activaron CLIs ni tocaron credenciales.

## Contratos actuales

141 PASS, 2 avisos, 146,57s. Se comprobaron los cinco módulos indicados en el recibo: builders de configuración, perfiles/filtros nativos, preflight y catálogo/dispatch MCP mediante FastMCP y procesos stdio Python. Se verificaron sus marcas offline_database y autostart PostgreSQL desactivado. Todos los pins de fuente del recorrido R7 y los cinco módulos de test permanecieron intactos. No se cambian permisos ni respuestas finales de modelos.

Esta selección cubre contratos usados por los tres proveedores; no ejecuta los tres CLIs nativos ni equivale a aceptación del catálogo efectivamente ofrecido por cada modelo. Los recorridos nativos intensivos se serializan por memoria y propiedad de recursos.

| Proveedor | Evidencia nativa relevante | Gates actuales independientes |
| --- | --- | --- |
| OpenCode | R7 de esta fuente: preflight229,855s y Unicode/replay34,101s pasan; cancelación no se invocó porque la señal textual faltó antes de completed. Fallo native_model_75_second_deadline y167 capturas conservados; salida original MCP con excepción, cierre físico derivado sin aceptación. | T168 cancelación activa; luego perfiles/escenarios globales restantes. La propuesta de herramienta/hijo vivo es conceptual, no implementación ni cobertura de streaming. |
| Codex | Diagnóstico del19-octubre con dos preguntas/ACK/reinicio/AG-UI; fuente seleccionada histórica, no aceptación del árbol actual. | CX-01–04/CX-AUTH-03 catálogo/autorización nativos; CX-06–08 interacción/recuperación; CX-05 mensajes persistidos. send_message y cao.work.message se verifican por separado. |
| Claude | Diagnóstico actual en35a728c1: Unicode sin herramientas, Claude Code2.1.303/Sonnet4.6 solicitado, HTTP200/21,204s, recibo/join y limpieza;1508pins/operador intactos. [Recibo y límites](validation-claude-unicode-current.md). No explica504 ni acepta los recorridos ampliados. | CL-01–04 diagnóstico Unicode/504 actual; CL-05–07/CL-AUTH-03/CL-CATALOG autorización; CL-08/09 mensajes/interacción/reinicio. Login intacto y T032 aplazada. |

## Fuente y límites de las auditorías

Codex: adapter codex.py, mcp_tool_policy.py, validation-t007.md/validation-t036.md, Spec022 tasks y Spec023 tasks, validation-t132-work-message.json. Claude: adapter claude_code.py, mcp_tool_policy.py, middleware del servidor, test_claude_mcp_tool_filter.py, validation-t031.md y su lista específica. T168 contiene modelo opencode-go/glm-5.3-flash; T169 es CLI de emparejamiento. Ninguno acredita Codex o Claude. Las versiones/modelos históricos son evidencia del recibo, no fijaciones actuales para nuevos lanzadores.

Los tres documentos de pendientes distinguen ahora el estado específico de su proveedor del historial compartido. JSON estricto se exige a las operaciones automáticas que lo necesitan; no se fuerza JSON al modelo para ocultar variaciones de texto. Los contratos HTTP, MCP, AG-UI y CLI conservan su semántica propia.

## Composición y conservación

PASS de los contratos comprobados:5contratos de arquitectura conservados/0rotos. Fuente productiva y547pins de R7 intactos después de la suite; cinco módulos de test sin cambios. Ambos propietarios PG y todos los procesos/raíces de R7 quedaron cerrados antes de iniciar esta suite offline. El recibo MCP original de R7 mantiene fallo de salida de contexto; su prueba derivada solo acredita cierre físico/SQL, sin aceptación ni salida normal inventada. Credenciales del operador intactas en el recibo real; auditorías/tests no renovaron login. Graphify no se regenera: no hay cambio estructural productivo y sus archivos ajenos se preservan. Obsidian save-20261026-caos-cross-provider-gates actualizó cuatro páginas existentes mediante transacción guardada; lint estricto0.

Revisión READ de evidencia Claude4966b7b122404dc98c2419327b38e0e7: GO; sumas/estados/tiempos y límites por proveedor coherentes. Se explicita en el recibo que los cinco módulos de test permanecieron intactos y que el cierre MCP derivado no es salida normal ni aceptación.

La suite de contratos141PASS conserva su ejecución671c9c8f. El diagnóstico Claude posterior se ejecutó en35a728c1 y tiene recibo separado; no se relabelan fuentes ni se acepta Codex/OpenCode a partir de su PASS.
