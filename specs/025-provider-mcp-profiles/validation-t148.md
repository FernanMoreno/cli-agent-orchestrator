# T148 — publicar KAS después de acreditar la fila

2026-10-25. FR126/SC110; parte acotada de T143. Base main `8772281eab6e7e3df093d5521b0053d8df12c927`. [Fuentes y recibos](validation-t148.json).

## Qué se reprodujo

La hipótesis previa de sobrescritura era incorrecta: `_publish_private_runtime_text` usa enlace duro sin reemplazo y rechaza contenido distinto en archivos existentes. Se corrige esa conclusión en el plan, el recibo T147 mediante aclaración posterior y Obsidian. Las fuentes y resultados históricos de T147 se conservan.

El fallo real estaba en el orden: antes del INSERT, la creación KAS podía materializar archivos ausentes con un ID que pertenecía a otra fila. También los dejaba al fallar el backend o ante un commit ambiguo. Seis escenarios PostgreSQL/archivos temporales reprodujeron esos efectos —tres fallos de registro por dos estados de archivos, ausentes o solo artefacto—; el control de publicación después de registro también falló. Un octavo control conservó contenido distinto y pasó, confirmando que no había sobrescritura. RED: 7 fallos, 1 correcta, 35,78 s. Tras corregir el orden, los mismos 8 controles pasan en 35,52 s.

## Cambio y contrato de fallo

La compilación, resolución del perfil y validación KAS siguen precediendo a cualquier asignación. Solo se mueve la instalación de archivos: después del retorno correcto del trabajador de backend/INSERT y antes de FIFO, construcción del proveedor o inicialización. El comando nativo se inicia después en `KiroCliProvider.initialize`; el backend asigna primero una shell y no necesita esos archivos. El perfil congelado y su digest conservan su contrato.

La instalación usa el trabajador privado existente y fija su indicador de finalización solo tras devolver ambos archivos. La cancelación repetida espera su parada real antes de liberar el propietario local. Conserva la fila, shell y cualquier prueba completa/parcial para reconciliación, como en la cancelación previa a inicializar: no se afirma que el proveedor esté listo.

Mover la escritura reveló otro riesgo: un fallo por conflicto o publicación parcial ya ocurre con fila confirmada, y la reversión general borraría por ID archivos anteriores. El nuevo guardado de publicación incompleta conserva registro/shell/pruebas y propaga el error sin construir proveedor, ejecutar CLI ni alcanzar ese borrado. Si este intento insertó un recibo de hijo, se registra `reconcile` con `kas_publication_failed`; una transición secundaria fallida no sustituye el error original. Los recursos quedan pendientes de reconciliación explícita, sin reintento automático ni declaración de éxito.

## Composición

Se revisaron creación, PostgreSQL, publicación privada, gestor/restauración, inicialización KAS, reserva de arranque, cancelación y reversión, además de las rutas de limpieza que retiran pruebas. Graphify se consultó sin regenerar los 12 cambios ajenos; se contrastaron los llamadores con código. No cambia esquema ni API pública; no hay interfaz nueva entre consumidores independientes que exija Pact.

Controles: colisión PostgreSQL con ID válido de ocho hexadecimales; archivos ausentes/parciales/previos; backend y commit ambiguo; orden positivo y perfil congelado; fallo de publicación conservando prueba/fila; cancelación repetida durante escritura, trabajador de registro cancelado y ausencia de lanzamiento nativo. La regresión añade creación normal/idempotente/diferida, preguntas, Work, bloqueos de sesión, T146/T147, perfiles y lanzamiento/restauración/política KAS. PostgreSQL propio con identidad/imagen verificadas y propietario mantenido hasta salir pytest; se conservan cobertura y límites. No se ejecutan CLI/modelos reales ni se amplían permisos KAS de producción.

La primera regresión se interrumpió para incorporar el estado explícito del recibo señalado en revisión; ese recibo se conserva como incompleto y no acredita aceptación. Aceptación final: **310 correctas, 2 avisos, 199,71 s**, con15 casos nuevos. Cobertura enfocada configurada23 %, sin alterar criterios. Memoria inicial12.445 MiB frente al mínimo6.144; propietario cerrado en208,215 s, contenedor exacto ausente y12 fuentes sin cambios. Black/isort pasan; arquitectura5 contratos conservados/0 rotos. Claude GO acotado `078991a404df41bc87ce3e43b5223fda`, tras diagnóstico `3c7afe215dc14d33a65a57c42a3d0723` y revisión `f8992f9d9e414d309f26848292d886b4`; Luna auditó las fronteras y corrigió la interpretación del no-replace. Las revisiones fueron de lectura; Codex ejecutó y verificó las pruebas. Veredicto: **PASS WITH RISKS para T148; T143 permanece abierto**. Fuente fijada y detalles en el JSON de T148.

## Límites pendientes

El registro puede ser visible antes de terminar la prueba: el lector frío rechaza una prueba ausente/incompleta; el token de arranque protege retirada y no bloquea ese lector. Se comprueba el rechazo, sin prometer espera o disponibilidad continua. Una cancelación con archivos completos tampoco demuestra que initialize haya ocurrido.

El par de archivos no es transaccional; el borrado ordinario posterior sigue por ID y no demuestra propiedad individual de archivos antiguos. Siguen pendientes recibos preasignados a una fila ajena sin hijo previo, generación/CAS, sidecars en barridos, frescura/caché lista de Herdr, trabajadores escapados y distribución bajo T143. No es aceptación nativa actual de OpenCode/Codex/Claude: los lanzadores antiguos deben repinarse con toda la fuente, incluido `services/provider_lifecycle.py`.

Credenciales, instalaciones globales, QEMU y jobs históricos sin cambios. No se tocan archivos KAS del operador: las pruebas usan directorios temporales.

Conocimiento durable: transacción `save-20261025-caos-kas-registration-publication`, aplicada a la nota `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`. Lint estricto:0 incidencias,14 páginas. Graphify no se regeneró: la estructura de llamadas se conserva y sus12 cambios ajenos permanecen intactos.
