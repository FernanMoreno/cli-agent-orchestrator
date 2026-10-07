# Cross-Subsystem Contracts: Cierre de brechas locales

**Fecha**: 2026-10-07\
**Spec**: [spec.md](../spec.md)

Este contrato define resultados que deben concordar entre productores, persistencia, consumidores y superficies visibles. No prescribe nombres de endpoints ni un formato de implementación.

## C-001 — Resultado de tarea

| Situación | Estado y recibo esperados | Efecto en proyecto |
|---|---|---|
| Completada | Ambos muestran completada y el mismo resultado | Se permite una tarea sucesora |
| Fallida | Ambos muestran fallo y razón disponible | El operador puede decidir reintentar |
| Cancelada y detenida | Ambos muestran cancelada | Se libera la reserva al confirmar que no hay escritura activa |
| Cancelación solicitada pero trabajador activo | Se muestra cancelación pendiente o estado equivalente | El proyecto permanece protegido |
| Par inaccesible | Resultado señala indisponibilidad, no una lista vacía ni éxito | La instancia local continúa disponible |

La transición terminal debe sobrevivir a lectura posterior, reconciliación y presentación por CLI/API/MCP. Una respuesta obsoleta no reemplaza un resultado terminal más reciente.

## C-002 — Autorización local

- Cada operación queda ligada a la instancia par, proyecto y acción autorizados.
- Una concesión revocada bloquea nuevas operaciones.
- Credenciales maestras reutilizables no cruzan perfiles.
- Un destino no confiable no recibe credenciales locales.
- La identidad autenticada se asocia con el propietario correcto antes de leer o mutar asignaciones.

## C-003 — Eventos en vivo

- La conexión solo entrega eventos a un usuario y ámbito autorizados.
- El cursor representa el último evento confirmado por el cliente.
- Tras reconexión, todo evento posterior llega al menos una vez y el cliente evita presentaciones duplicadas.
- La transición entre carga histórica y escucha nueva usa un cursor común o mecanismo equivalente que cierra la ventana de carrera.
- Mensajes de contexto embebido se validan por origen y correlación antes de resolver solicitudes.
- No se coloca una credencial reutilizable en URL ni en logs de acceso. Los tickets temporales, si se usan, son de un solo uso, tienen alcance mínimo y expiración breve.
- Timeout, cancelación y límites de lectura producen un estado visible y acotado.

## C-004 — Workflow y recuperación

- Los elementos no elegibles no consumen permanentemente capacidad de avance.
- Un error repetido activa espera acotada y no una reejecución rápida ilimitada.
- Una finalización limpia su tarea activa sin borrar resultados ni reemplazos nuevos.
- Campos inválidos o desconocidos se reportan antes de iniciar el trabajo.
- Una caída concurrente de un par o sesión produce un resultado válido y distinguible.

## C-005 — Prueba y publicación

- La prueba de proveedor requiere origen confiable y perfil autenticado aislado; sin ambos se bloquea antes de iniciar.
- Limpieza se ejecuta tras éxito, fallo y cancelación.
- Publicación manual y automática evalúan el mismo candidato y exigen los mismos controles.
- Cada control informa aprobado, fallido, omitido o no disponible.
- El candidato publicado corresponde exactamente al contenido que produjo los resultados aprobatorios.
- Versión, origen e inventario del artefacto pueden verificarse después de construirlo.

## C-006 — Plugin local

- El operador puede inspeccionar productor declarado, origen, revisión, integridad, compatibilidad y permisos.
- La UI distingue comprobación aprobada de datos ausentes o no verificados.
- La política local determina si un caso no verificado, modificado o incompatible puede habilitarse.
- Instalar o revisar no concede permisos automáticamente.

## C-007 — Evidencia y aceptación

- Las pruebas automatizadas cubren transición terminal, eventos, permisos, recuperación, candidatos de publicación y política de plugins.
- La aceptación local usa dos o más procesos CAO en la misma PC para las fronteras de coordinación.
- Proveedor no disponible por cuenta o región se informa como bloqueado/no disponible, no como éxito ni como fallo del producto.
- Toda excepción a un control obligatorio queda identificada; no se convierte silenciosamente en resultado aprobado.
