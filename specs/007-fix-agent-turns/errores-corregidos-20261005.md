# Errores detectados y corregidos — 5 de octubre de 2026

Este documento conserva la primera fase de correcciones del día. **Actualización posterior:** [las correcciones adicionales](correcciones-adicionales-20261005.md) acreditan delegación, ambos callbacks y síntesis verificada entre Claude, Codex y OpenCode. El escenario de aplicación/navegador sigue pendiente.

| Error | Consecuencia | Corrección |
| --- | --- | --- |
| OpenCode esperaba un registro sin las comillas del formato logfmt real. | Rechazaba una conexión MCP válida y agotaba el plazo. | Reconocer `message="mcp connected" server=<id>` con coincidencia exacta del servidor. |
| La espera incluía MCP deshabilitados. | Esperaba una conexión que el CLI no iba a iniciar. | Omitir `enabled: false` de v1 y `disabled: true` de v2. |
| Claude conservaba un presupuesto de conexión MCP insuficiente y podía diferir las herramientas CAO. | El coordinador empezaba sin poder delegar. | Cargar el MCP propio al inicio y usar el presupuesto de inicialización del proveedor, respetando overrides explícitos del entorno. |
| El log privado dependía de una ubicación XDG que el CLI no produjo. | La espera no encontraba evidencia de conexión. | Capturar explícitamente stderr con `--log-level info --print-logs`, manteniendo el TUI en stdout. |

La identificación del MCP propio se comparte entre los tres proveedores para evitar reglas duplicadas. Las carpetas privadas usan modo 0700 y el log modo 0600. No se cambiaron las reglas de recibos, cancelación ni almacenamiento de mensajes.

## Verificación

- Suite Claude/Codex/OpenCode v2/resolución MCP/timeouts: **556 aprobadas, 3 omitidas**. Este run precede a la última corrección de captura OpenCode.
- Suite afectada después de esa última corrección, OpenCode v2/launch/unit: **102 aprobadas**. Parte de esta suite se solapa con la anterior; sus cifras no se suman.
- Pruebas del demo solicitado: **5 API y 1 frontend aprobadas**.
- Gate de composición final: **cinco contratos conservados y cero rotos**.
- SDK MCP real: conexión correcta y **72 herramientas** anunciadas.
- OpenCode real a través de CAO: **HTTP 201 y conexión positiva en el log privado**.
- Diff revisado y `git diff --check` sin errores. Se preservaron cambios previos y no se hicieron commits.

## Pendiente en la primera fase (histórico)

La prueba conjunta llegó a lanzar los tres proveedores en el mismo proyecto, pero no entregó los dos callbacks ni produjo una síntesis final verificable. Los últimos intentos tuvieron cierres MCP y timeouts HTTP con memoria muy limitada y swap agotada. La conexión aislada funciona; eso no demuestra la causa exacta de todos los fallos conjuntos.

También se detectó una incompatibilidad del prompt de prueba con el contrato de turnos: una tarea abierta sin recibo bloquea los callbacks posteriores. Las nuevas pasadas usan etapas explícitas y joins existentes; no se añadió un bypass de reconciliación ni se acredita continuación de una tarea abierta.

T020/T027 y SC-006/SC-007 permanecen pendientes, incluida la prueba en navegador. Véanse [validación real](real-validation.md), [evidencia](implementation-evidence.md) y [revisión de composición](composition-review.md).
