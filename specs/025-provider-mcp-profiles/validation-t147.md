# T147 — autoridad de una creación fallida

2026-10-25. FR125/SC109; corrección acotada de T143 sobre main `2d375d890946868cc41295ebfc2c0c458b13905d`.

## Reproducción y corrección

Dos casos con PostgreSQL real reprodujeron el borrado de una fila lista de otra sesión: colisión del ID generado al insertar y fallo anterior al INSERT al crear la ventana. Ambos alcanzaban la reversión exterior por ID, aunque ese intento no hubiera publicado ninguna fila. RED: 2 fallos, 31,90 s; los mismos dos casos pasan tras la corrección, 28,57 s. Se conservan ambos recibos privados.

La creación registra el éxito confirmado del INSERT. Sin esa confirmación, la reversión exterior evita transición de hijos ajenos, FIFO, monitor, proveedor, capacidades y borrado de fila/recibos. El trabajador conserva la compensación de su propia ventana bajo el bloqueo de sesión. Un commit confirmado en el servidor cuyo retorno falla conserva su fila como evidencia, aunque su ventana se compense: no se afirma que sea un terminal utilizable ni se borra suponiendo propiedad.

Resolver el repositorio no acredita haber creado el worktree. Solo el retorno correcto de su creación permite retirarlo por un fallo posterior; la operación usa el trabajador privado ya existente que se drena ante cancelación. Los marcadores de inicialización se añaden después de reservar el arranque y dentro del bloque compensado. Cada intento retira solo los que añadió, antes de liberar el arranque; la compensación por cancelación lo hace desde su trabajador real.

La revisión de Claude encontró que el nuevo retorno temprano dejaba `planned` un recibo propio creado antes de un fallo conocido del backend. Un control PostgreSQL lo reprodujo: 1 fallo y 1 control de colisión correcto, 32,80 s. Se registra también el éxito de `plan_native_child`; solo entonces se marca ese recibo como fallido mediante el trabajador privado. Una colisión con un recibo previo no concede permiso para modificarlo. Si falla la transición secundaria se conserva el error original. Este control no acredita una autorización distribuida por generación.

## Composición y límites

Subsistemas cambiados: creación/reversión, marcadores de arranque y asignación de worktrees. Vecinos revisados: gestor de proveedores, recibos PostgreSQL, bloqueo de sesión y compensación por cancelación, inicialización diferida, admisión Work y publicación de perfiles. Invariantes: ausencia de efectos sobre recursos ajenos antes de publicación confirmada, compensación de ventana propia, retención de commit ambiguo, conservación de marcadores previos, recibos ajenos intactos y continuidad de la reversión tras publicación correcta. Se consultó Graphify sin regenerar sus cambios ajenos y se contrastó con código. No se cambia esquema ni contrato público; no hay una nueva interfaz independiente que requiera Pact.

La suite dirigida incluye creación normal e idempotente, worktrees, fallos diferidos, preguntas pendientes, cancelación/bloqueos de sesión, admisión/retirada T146, publicación de proveedores, perfiles y orden de primera tarea. Las pruebas usan PostgreSQL propio con imagen e identidad verificadas; backends controlados evitan inferencia de modelos. El propietario permanece hasta terminar pytest y se comprueba el cierre y la ausencia de su contenedor exacto. Se mantiene la cobertura configurada y el mínimo de 6.144 MiB, sin modificar límites.

Aceptación final: **221 correctas, 2 avisos, 140,55 s**, cobertura enfocada configurada 21 %. Se admitió con 12.335 MiB disponibles; propietario cerrado en 149,585 s y las 10 fuentes sin cambios. Claude GO acotado `167d9b6c256249b785546f39c1dd6bda`, tras corregir el hallazgo de `3eae72f572a741c2806415c06385deb5`. Veredicto: **PASS WITH RISKS para T147; T143 permanece abierto**.

Validación final y revisiones: consultar [recibo de T147](validation-t147.json). Black/isort y arquitectura: 5 contratos conservados, 0 rotos. La revisión de Claude es de lectura; Codex ejecutó las pruebas. Luna revisó propiedad y fronteras restantes sin ejecutar pruebas. Los resultados previos y RED se conservan, sin presentarlos como aceptación final.

T143 sigue abierto: KAS escribe perfiles deterministas antes de adquirir propiedad del ID; un nuevo recibo de hijo puede asociarse a una fila existente sin recibo previo; faltan garantías por generación/CAS tras un INSERT correcto, sidecars en barridos, frescura/caché lista de Herdr y alcance distribuido. La cancelación después de crear un worktree conserva el hueco previo de reconciliación: no se afirma su compensación completa. El nuevo guardado de éxito no resuelve estas fronteras. Si falla la retirada del worktree en el retorno temprano, su error puede reemplazar al error de creación; no se amplía aquí esa semántica heredada de compensación.

No se ejecutaron CLI ni modelos reales, ni se acredita aceptación nativa de OpenCode/Codex/Claude sobre estos bytes. Hay que repinar los lanzadores antiguos con todas las fuentes actuales, incluido `services/provider_lifecycle.py`, antes de usarlos. Credenciales, instalaciones globales, QEMU y jobs históricos sin cambios. Los 12 cambios ajenos de Graphify se preservan.

Conocimiento durable: transacción Obsidian `save-20261025-caos-failed-create-authority`, aplicada a `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`. Lint estricto: 0 incidencias, 14 páginas.
