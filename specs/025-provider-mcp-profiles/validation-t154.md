# T154 — cancelación y worktree antes del backend

2026-10-26. FR132/SC116 sobre main `241dfe235898df1bfe8a040ad276654a1b8fc3fe`. Corrección aceptada en su alcance previo al backend. [Fuentes y recibos](validation-t154.json).

## Fallo reproducido y alcance

La asignación de worktree ya se ejecutaba en un trabajador propietario y se drenaba ante cancelación. El fallo no era un hilo escapado: al devolver con éxito, el trabajador marcaba worktree_created y la cancelación original saltaba al handler CancelledError, que omitía la compensación ordinaria de Exception.

Con PostgreSQL y un repositorio Git temporal real, dos casos (cancelación simple/repetida) dejaron checkout y rama sin backend ni fila. Otros dos controles preservaron correctamente una asignación fallida o checkout preexistente. RED:2 fallos,2 controles correctos,2 avisos,31,49 s; propietario PostgreSQL36,600 s, cerrado y contenedor ausente,14 fuentes intactas. Los mismos4 casos pasan tras el cambio:27,90 s, propietario33,166 s cerrado/ausente.

## Autoridad y corrección

Un testigo local backend_create_submitted se establece antes de enviar el trabajador de creación al executor. La compensación por cancelación solo puede retirar un worktree cuya asignación retornó positivamente, antes de ese envío y sin autoridad gestionada o transferida. Se ejecuta remove_worktree por el helper propietario ya existente, sin bloqueos nuevos; cancelaciones adicionales esperan a que termine y el handler vuelve a lanzar la cancelación original. Errores de limpieza se registran sin convertir cancelación en éxito.

Se conserva remove_worktree sin modificaciones: contenido modificado/no seguido/ignorado o estado incierto se pone en cuarentena; no force/reset/clean. Solo después de retirar el checkout limpio se intenta branch-d, que preserva commits sin integrar. No se decide autoridad por existencia de ruta, ID generado ni ausencia de fila. Las reservas native-child siguen planned para reconciliación de lease, sin transición nueva a failed.

Después del envío del backend se conserva el worktree, incluso si el compensador existente acaba borrando la fila; no devuelve prueba positiva de parada. Cancelación durante initialize conserva fila/worktree y startup incierto. No se amplía aquí la compensación tardía ni se afirma una solución de toda cancelación.

## Pruebas, aislamiento y revisión

Quince casos nuevos comprueban Git/refs/bytes reales y PostgreSQL: cancelación simple/repetida con/sin recibo hijo, asignación fallida/preexistente/ambigua, cancelación durante remove y progreso del bucle, cuarentena modificada/no seguida/ignorada, commits sin integrar, fallo de remove, retención durante initialize y durante la creación backend enviada. Backend/proveedor controlados, sin CLI de agente/modelo ni login; los únicos procesos externos nuevos son Git local en tmp_path.

La primera enfocada dio82 correctas y2 fallos en85,20 s: la fixture nueva combinaba monkeypatch y un patch dueño de TERMINAL_LOG_DIR, restaurando un MagicMock al final. Lo detectaron los controles existentes de salud/startup. Se corrigió la fixture con un patch anidado alrededor de yield, que sale antes de que create_mocks restaure su Path original. Se despejan variables Git heredadas, se aíslan configuraciones global/sistema y se desactivan hooks/firma solo en commits semilla temporales. No se tocaron fixtures antiguas ni se relajó el control.

La misma selección, con un control adicional del límite de envío, pasa85 casos en81,80 s,2 avisos; propietario87,697 s cerrado/ausente. Después se añadieron aserciones de mensaje de cancelación y fila ausente tras el compensador existente; las acepta la pasada final. Claude dio GO final a la corrección y al aislamiento; Luna auditó autoridad y escenarios en lectura. Las ejecuciones son de Codex.

Regresión final: **683 correctas,3 deselecciones configuradas,8 avisos,448,14 s**, con los15 casos nuevos y las últimas aserciones. Memoria inicial11.656 MiB frente al mínimo6.144; PostgreSQL con identidad verificada y propietario cerrado/contenedor exacto ausente en466,313 s. Los14 hashes de ejecución y el archivo de prueba permanecieron iguales; el helper worktree_service sin modificaciones tiene un hash adicional contrastado con la base. Arquitectura: **5 contratos conservados,0 rotos**. Composición: PASS WITH RISKS descritos abajo. Graphify consultado sin regenerar12 archivos ajenos; no hay nuevos nodos de función ni dependencias y el cambio AST está contrastado. Conocimiento guardado mediante `save-20261026-caos-cancelled-worktree` en `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`; lint estricto0 incidencias.

## Composición y límites

Se revisan planificación, Git, workers privados, startup/transferencia, backend/registro y proveedor. AST comprueba que solo cambian dos asignaciones del testigo y el bloque condicionado de compensación. No hay API/esquema/eventos/permisos/configuración/migración nuevos ni contrato Pact adicional. Los límites Git30 s, nativo75 s y memoria mínima6.144 MiB no cambian. No se introduce mutex global, historial ni otro executor.

La cancelación repetida conserva las semánticas existentes de contador de cancelaciones de asyncio; no se añade uncancel. Una limpieza fallida, una asignación sin retorno positivo o la cuarentena pueden dejar recursos en Git y logs; no se crea una cola automática de reintentos. Cancelaciones tras envío backend, propiedad gestionada/transferida/incierta y T143 distribuido/generaciones/SQL directo siguen separados. No es aceptación nativa actual de ningún proveedor.
