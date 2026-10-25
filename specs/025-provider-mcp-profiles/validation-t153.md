# T153 — retirada KAS al reciclar Flow

2026-10-25. FR131/SC115 sobre main `05da979c69e5459bf8117a5581ca3b3cceb60e06`. Corrección aceptada en su alcance acotado. [Fuentes y recibos](validation-t153.json).

## Reproducción y causa

Las dos ramas de `_recycle_flow_session` borraban filas mediante el helper de sidecars diferidos sin retirar el par privado KAS (grant y snapshot fuente). Además, no exigían prueba nativa exacta antes de retirar proveedor/fila KAS. Ni la limpieza de proveedor Kiro ni el helper de sidecars conceden esa autoridad.

Se reprodujeron ambas omisiones con filas PostgreSQL, archivos KAS instalados por el helper real y caché/leases reales del gestor con un adaptador MockCLI controlado. El backend es controlado; no se ejecuta Kiro, agente ni modelo. Primera pasada:8 fallos y4 controles correctos,33,38 s. Se repitió antes de cambiar producción, añadiendo aislamiento explícito del directorio de sidecars y sus dos archivos: **8 fallos y4 controles correctos,37,80 s**. Fallos: dos pares que sobreviven al borrado y seis casos UNKNOWN/presente/excepción que aun permitían eliminar fila/caché. Los cuatro controles preservaban evidencia ante error SQL previo o negativa del proveedor. Ambos propietarios PostgreSQL cerraron y comprobaron la ausencia de sus contenedores;14 hashes de fuente intactos.

## Corrección

Un helper de Flow comprueba solo las filas KAS seleccionadas mediante `cleanup_terminal_exact(close=False)` y acepta únicamente el resultado reclaimed del contrato existente. La rama activa conserva su orden previo de busy/FIFO/monitor/kill y sitúa la comprobación antes de limpiar cualquier proveedor. La rama ausente filtra primero los tombstones retenidos y después comprueba el lote. Si una prueba es desconocida, presente o lanza, devuelveFalse para todo el lote antes de retirar caché, filas o archivos de evidencia. No deduce ausencia del booleano session_exists.

Un segundo helper conserva la lista original ordenada y una llamada al mismo callback SQL por el helper de sidecars existente, incluido su resultado/error. Solo tras retorno confirmado elimina el par KAS de cada fila seleccionada, aún dentro de los fences físicos finales del proveedor y los guards previos sesión/dispatch/retirada lógica. No cambia el helper general de sidecars ni la política de selección.

## Validación y revisión

Los mismos12 casos pasan:31,14 s,2 avisos; propietario36,494 s, cerrado y contenedor ausente. La enfocada ampliada pasó105 casos en77,97 s,2 avisos; propietario83,554 s, cerrado y contenedor ausente. Incluye28 nuevos controles y Flow, borrado por lote y leases de sidecars. Después se añadió un control mixto de selección real y una aserción de orden cleanup/fence; requieren la pasada final.

Veintinueve casos nuevos cubren ambas ramas y resultados exactos aceptados, negativa/incertidumbre/excepción, lotes mixtos, tombstones excluidos, no-KAS y otros IDs, provider cleanup/fenceFalse, startup y busy, SQL antes del commit y error tras commit real, y fallo parcial de unlink con warning. Se comprueba preservación de bytes y caché donde corresponde, callback/IDs originales y error original. Los callbacks no ocultan aserciones bajo captura de excepciones.

Claude confirmó la causa en lectura y dio GO al cambio; Luna revisó los controles finales de selección/unlink/fence y dio GO acotado. Ambos son revisión de fuente, no ejecuciones independientes. Codex ejecuta las pruebas.

Regresión final: **626 correctas,3 deselecciones configuradas,8 avisos,386,56 s**, incluidos los29 casos nuevos y los controles finales corregidos. Memoria inicial12.203 MiB frente al mínimo6.144; propietario PostgreSQL con identidad verificada, cerrado y contenedor exacto ausente en406,036 s. Los14 hashes de producción y el archivo de pruebas permanecieron iguales. Arquitectura: **5 contratos conservados,0 rotos**. Composición: PASS WITH RISKS descritos más abajo. La comparación AST conserva selección/guards y expresión SQL originales salvo las dos nuevas puertas y el helper final. Graphify se consultó sin regenerar12 archivos ajenos; el mapa AST privado actualizado y el control de dependencias verifican los nuevos helpers. Conocimiento guardado mediante `save-20261025-caos-flow-kas-retirement` en `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`; lint estricto0 incidencias.

## Composición y límites

Se revisan selección de encarnación/retención, Work y startup, ciclo de proveedor, prueba nativa, SQL, sidecars y archivos KAS. No cambian API pública, esquema, eventos, permisos, configuración ni migraciones; no se requiere un contrato Pact nuevo. PostgreSQL y archivos son reales y aislados; backend/proveedor de ejecución controlados.

Una prueba temprana por KAS está cubierta por los guards actuales dentro del proceso hasta SQL. Se confía en el contrato exacto ya existente de Herdr/tmux y su alcance; no se atribuye una identidad global nueva al backend. El código nuevo realiza una llamada exacta por candidato KAS y dos unlinks por par retirado, sin nuevo mutex global, historial de locks ni consultas de metadatos por fila. Los backends existentes pueden repetir escaneos nativos completos en cada llamada; no se acredita coste total lineal ni aceptación de escala masiva.

En la rama activa FIFO/monitor/session pueden haber cambiado antes de detectar incertidumbre: no hay rollback atómico. Un recycle sin filas elegibles puede retornarTrue conservando todos los tombstones por semántica anterior; no acredita que estos se retiraran. Un error tras commit conserva archivos aunque la fila ya no exista. Unlink sigue siendo best-effort por archivo: puede quedar un grant o snapshot huérfano, con aviso.

Generación/reutilización ABA, otros procesos/bases, SQL directo, worktrees y workers escapados siguen en T143. No se acredita aceptación nativa actual de OpenCode/Codex/Claude; repinar fuentes y lanzadores antes de recorridos reales. Credenciales, instalaciones, QEMU y jobs históricos sin intervención.
