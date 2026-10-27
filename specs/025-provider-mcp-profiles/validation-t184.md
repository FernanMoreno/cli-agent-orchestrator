# T184 · Separar arranque del CLI y completado de la tarea cancelada

Corrección integrada en `49297912`, validada con el recorrido nativo [T183](validation-t183.md). [Recibo estructurado](validation-t184.json).

La misma captura nativa devuelve idle sin recibo y processing con el recibo pendiente, tanto en crudo como por pantalla. Ese processing era correcto para la tarea anterior. La cancelación conservaba intencionadamente el recibo durante initialize, pero initialize reutilizaba el detector de tarea y el viewport inicial estaba deshabilitado después del envío. El reemplazo no podía acreditar disponibilidad.

La recuperación abre ahora un ContextVar solo después del reset propio confirmado y de reponer FIFO, exactamente alrededor de initialize. Vincula objeto proveedor, terminal, sesión, ventana y generación; se restablece en finally y no autoriza otro proveedor/tarea. Codex conserva el comportamiento nativo/event existente; en pipe-pane usa una comprobación de arranque separada, sin publicar estado ni completar tareas.

La comprobación exige pane fuera del shell, banner nativo actual, compositor vacío/conocido y footer; rechaza usuario, respuesta, trabajo, login, confianza, actualización y error. Solo admite IDLE. El parser ordinario nunca consulta el ContextVar y sigue respetando el recibo, incluso ejecutado mediante to_thread dentro del contexto. Se conservan presupuesto y sondeo originales.

Después de disponibilidad: CAS final, liberación local del recibo y nueva lectura ordinaria de pantalla fuera del bucle, todavía bajo el bloqueo de envío. El fallo de esa actualización informativa no deshace una cancelación confirmada. El evento normal se publica después de liberar el bloqueo. No hay reenvío del prompt ni cambio de permisos, esquema, protocolo remoto, límites o admisión.

La reproducción inicial del initialize real dio1RED/TimeoutError. Los negativos detectaron además una actualización parcial que debía vetar disponibilidad:1RED/23PASS conservados y corregidos. Final:28 casos nuevos;1116 regresiones pasan/3 omisiones anteriores/5avisos. Tras la representación JSON de la captura, los28 se repitieron con el mismo contenido decodificado y las mismas aserciones; el runtime no cambió. Arquitectura5/0 y mypy canónico4archivos PASS. La invocación inicial de mypy con alias y sus34diagnósticos se conservan; la diferencia no se atribuye a defectos nuevos o resueltos ni se presenta como typing global.

La captura JSON preserva exactamente el sufijo desde el inicio de la última línea de banner, incluida su indentación y filas vacías. El recibo contiene hash del productor, offset de bytes y hash del texto decodificado. Graphify se contrastó con las rutas de recuperación/proveedor/espera/monitor; no se reescribió el grafo ajeno. PostgreSQL real verificó CAS, bloqueo y ciclo de hijo; pruebas de contexto cubren aislamiento entre tareas/hilos, errores/cancelación, generación y binding incorrectos, backend nativo y fallo de actualización informativa.

La aceptación nativa posterior devuelve200/cancelled26,075s, proceso anterior retirado, reemplazo disponible y join/limpieza estables. El primer FAIL sigue ligado a su fuente. No acredita timeout, todaCX-08/CX-09, otros proveedores o garantías generales de FIFO. Se añadieron reglas de prevención a AI_WORKFLOW6.1; conclusiones duraderas se registran en Obsidian.

Revisión Claude en modo lectura: GO para código y recibos Markdown, sin ejecución independiente de pruebas ni auditoría de JSON. Codex comprobó directamente cifras, hashes y cierre. Obsidian actualizado mediante `save-20261027-caos-cancel-startup-readiness`, cuatro páginas y lint estricto sin hallazgos.
