# T150 — archivos de inicialización en borrados por lote

2026-10-25. FR128/SC112; parte acotada de T143. Base main `e08fc5969bca19ac905ac11b15683013921f89d1`. [Fuentes y recibos](validation-t150.json).

## Fallo y corrección

PostgreSQL y archivos temporales reales reprodujeron seis fallos: los barridos finales de sesión y los dos reciclados Flow eliminaban la fila pero dejaban su archivo de error o inicialización completada. Los dos controles de borrado ordinario pasaban. RED:6 fallos,2 correctas,37,24 s. Los mismos ocho controles pasan después,36,67 s.

La fase final ya autorizada de esos tres borrados por lote usa `_delete_terminal_rows_with_sidecars`. Mantiene exactamente una llamada al alias SQL original, con orden y duplicados originales, el resultado sin transformar y la misma excepción. Tras devolver correctamente SQL elimina ambos tipos de archivo solo para las claves incluidas. Si SQL falla, incluso después de un commit cuyo resultado no llegó, conserva todos los archivos y propaga la excepción original. No concede autoridad de borrado ni hace limpieza de runtime por sí misma.

La selección de IDs se conserva: sesión usa `[i for i in incarnation_ids if i not in deferred_ids]`; Flow mantiene sus dos selecciones previas. El reciclado Flow con backend existente ya borraba sus filas candidatas, incluidos los tombstones seleccionados; ahora también retira los archivos de esas filas eliminadas. Este cambio no revisa esa política ni amplía la lista. Los terminales excluidos o con retirada aplazada conservan fila y ambos archivos.

El anterior RLock global se sustituye por leases reentrantes por terminal, compartidos por los dos publicadores, el borrado ordinario y el helper por lote. Se cuentan titulares y esperadores antes de adquirir; solo se retira la entrada al liberar el último ámbito, comprobando su identidad. El mutex del registro protege memoria, sin DB ni archivos. El lote adquiere claves únicas ordenadas dentro de las retiradas de proveedor ya existentes. No se mueve monitor, FIFO ni plugins al tramo protegido. No se crea un mapa de IDs históricos ni un bloqueo global durante fsync/SQL.

## Controles y composición

31 controles enfocados pasan,41,85 s, antes de añadir dos controles de IDs retenidos. Incluyen carreras en ambos órdenes para los tres recorridos y ambos tipos de archivo. El guardado de registro del esperador y `refs == 2` acreditan la espera sobre la misma entrada; un publicador B completa mientras A permanece bloqueado. Después de la retirada, el publicador observa fila ausente o su archivo previo queda retirado. Los callbacks SQL fallidos o ambiguos conservan bytes y error. Los controles unitarios comprueban reentrada, excepción, adquisición interrumpida, argumentos duplicados/vacíos, resultado original y10.000 claves sin entradas ociosas.

Las pruebas usan PostgreSQL propio con imagen e identidad verificadas y directorios temporales para los archivos. Los backends y observadores controlados delimitan el fallo sin iniciar modelos ni CLI. La pasada ampliada incluye creación/borrado/diferidos, sesión, Flow, T145/T146, limpieza/tombstones, publicación KAS, plugin events, canal remoto y endpoints API/MCP. Se conservan límites, cobertura y deselectores configurados; no se ejecutan gates manuales aplazados.

Subsistemas revisados: publicación de error/completado, SQL de borrado, lifecycle de sesión/Flow, gestor de proveedores y eventos. Contratos: misma autoridad/lista de IDs/callback/commit/error; publicación ordenada con retirada, memoria limitada a claves activas, progreso independiente, liberación ante excepciones y conservación de evidencia no confirmada. Aceptación final: **419 correctas,3 deseleccionadas por la configuración existente,5 avisos,278,37 s**;33 casos nuevos. Cobertura enfocada configurada28 %, sin cambiar criterios.12.279 MiB iniciales frente al mínimo6.144; propietario cerrado en295,905 s y contenedor exacto ausente.14 fuentes sin cambios. Black/isort pasan; arquitectura/Import Linter5 contratos conservados/0 rotos. Fuente y detalles están en el recibo JSON. No cambia esquema ni interfaz pública y no corresponde un nuevo contrato Pact. Graphify se consultó sin regenerar sus12 cambios ajenos; los tres llamadores se contrastaron con fuente. Las revisiones son de lectura; Codex ejecutó las pruebas.

## Límites

La prueba de orden se limita a este proceso y lecturas DB concluyentes. Un publicador con una lectura DB fallida posterior al borrado conserva la política previa de escribir evidencia: se prueba explícitamente y no se presenta como ausencia garantizada de archivos. Un commit ambiguo también conserva archivos aunque la fila haya desaparecido. El fallo de unlink mantiene el tratamiento previo de aviso y mejor esfuerzo; no se afirma borrado durable de archivos ante errores del sistema de archivos.

SQL directo, borrados Herdr, `delete_terminals_by_session` sin llamador de producción, generaciones/reutilización ABA, otros procesos y limpieza KAS posterior siguen separados en T143. Los unlinks tras persistir estado DB o reparar un tombstone no se convierten aquí en CAS por generación. No se acredita aceptación nativa actual de OpenCode/Codex/Claude: antes de ejecutar CLI hay que repinar lanzadores con toda la fuente actual, incluido `services/provider_lifecycle.py`.

Credenciales, instalaciones globales, QEMU y jobs históricos sin modificaciones. Los cambios ajenos de Graphify se conservan.

Claude: diagnóstico `96fd8b5abbef46b7a1391f9e1ae0db3c`, revisión `78a26f733f1b4c79b4874c5dc7ecd327` y GO final del parche/controles `66561805079f4fed98ae2988fa03a31e`. Luna revisó leases, llamadores y orden sin hallar bloqueos. Las revisiones fueron de lectura; Codex ejecutó las pruebas. Veredicto: **PASS WITH RISKS para T150; T143 permanece abierto**.

Conocimiento durable: transacción `save-20261025-caos-bulk-deferred-sidecar-retirement` aplicada a `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`. Lint estricto:0 incidencias,14 páginas. Los dos archivos nuevos de pruebas se integran sin permiso de ejecución.
