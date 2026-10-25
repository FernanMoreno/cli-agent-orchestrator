# T152 — orden sesión antes de dispatch del padre

2026-10-25. FR130/SC114 sobre main `d13aa413949af33f07bf63102cd508e7a0d3ae64`. Corrección aceptada en su alcance acotado. [Fuentes y recibos](validation-t152.json).

## Reproducción y primera frontera incorrecta

La revisión de T151 identificó en fuente una inversión previa. Se reprodujo antes de modificar producción con PostgreSQL real, el helper Herdr `_cleanup_ghost_session` y `create_terminal(fence_parent=True)`. Herdr adquiere sesión y lee sus filas; su callback de snapshot espera el intento de sesión de creación. Los wrappers delegan en los locks reales sin sustituir sus límites.

En la base, creación obtuvo dispatch del padre y después intentó sesión. Herdr intentó dispatch mientras retenía sesión y agotó el timeout existente de10 s. La prueba falló porque Herdr no alcanzó su comprobación nativa exacta; creación pudo continuar al liberarse la sesión. RED:1 fallo,2 avisos,38,49 s. Propietario PostgreSQL cerrado, ausencia exacta de su contenedor y14 hashes intactos;43,646 s de propietario. No es un deadlock infinito ni un diagnóstico del proveedor/modelo.

## Corrección y contratos conservados

Se cambia únicamente el orden de entrada de contextos de creación: sesión, dispatch del padre, stack de retiradas. La construcción anterior del contexto dispatch no adquiere el lock. Coincide con Herdr, Session/Flow y observación/reconexión. La única llamada de producción con fence_parentTrue viene de assignment_service, sin dispatch externo retenido.

Todas las comprobaciones actuales de Work, fila/sesión/encarnación del padre, reclaim, generación de turno y puntero de sesión permanecen bajo ambos locks y antes de efectos backend/registro. El rollback de fallo de registro sigue dentro de ambos. Planificación previa del hijo, recibos/reconcile, cancelación con trabajador propietario, inicialización/plugin fuera de la sección y permisos no cambian. Los límites de dispatch10 s y nativo75 s se conservan.

## Pruebas y revisión

Cinco casos nuevos: la carrera con ausencia exactaUNKNOWN que conserva al padre; tres rechazos por encarnación/generación/sesión antes de efectos; y fallo SQL con excepción original y rollback del hijo bajo ambos locks. La primera enfocada dio5 correctas,2 avisos,39,50 s;44,614 s de propietario, cerrado y contenedor ausente.

Claude detectó que una aserción dentro del callback kill_window podía ser capturada por el manejo de errores de producción. Se reemplazó por un snapshot inmutable del conjunto de locks observado y una aserción posterior, fuera de esa captura. No se presenta la enfocada anterior como prueba suficiente de esa aserción final. La primera ampliada se interrumpió deliberadamente al recibir esa revisión: SIGINT solo al pytest hijo propio, conservando vivo su propietario hasta cerrar PostgreSQL y verificar ausencia. No cuenta como aceptación. El recibo de esa interrupción se conserva.

Regresión final corregida: **597 correctas,3 deselecciones configuradas,8 avisos,386,29 s**. Memoria inicial12.592 MiB, por encima de6.144. Propietario PostgreSQL con identidad verificada y cerrado; contenedor exacto ausente,401,063 s. Los14 hashes de producción y el archivo de prueba corregido permanecieron iguales. Incluye los casos corregidos, Herdr/gestor/publicación, creación/rollback/cancelación, Session/Flow, KAS, API/MCP/canal remoto y admisión/reintentos de asignaciones por servicio y HTTP. PostgreSQL real y backend/proveedor controlados: sin CLI/modelo ni uso de login.

## Revisión de composición

Subsistemas y vecinos: creación, admisión de asignaciones, locks sesión/Work, Herdr, Session/Flow, observación/reconexión, compensación y recibos de hijos. Se revisan exclusión/autoridad, orden, fallo parcial y propagación de excepción, cancelación y recursos. No se modifican API pública, esquema, eventos, configuración ni migraciones; no se necesita un nuevo contrato Pact. Graphify25 aristas de sesión consultadas sin regenerar12 archivos ajenos y verificadas con fuente. La llamada/dependencia estructural no cambia.

Arquitectura: **5 contratos conservados,0 rotos**. Claude dio GO tras comprobar la corrección de su hallazgo; Luna revisó el cambio y Codex ejecutó los controles y revisó el diff final. La comparación AST de producción acredita que solo cambió el orden de entrada/salida de los dos contextos. Veredicto de composición: PASS WITH RISKS, con los límites siguientes. Conocimiento persistido mediante `save-20261025-caos-parent-session-lock-order`, en `wiki/concepts/Provider reconstruction ownership.md`, `wiki/index.md`, `wiki/log.md` y `wiki/hot.md`; lint estricto0 incidencias.

## Límites

Creación puede retener sesión mientras espera hasta10 s el dispatch de un padre ocupado. Esto no garantiza ausencia de contención ni un límite global de espera de sesión/trabajador. La sesión sigue protegida solo dentro de un proceso. La prueba concurrente acredita el dispatch del padre observado; no inventa un contrato para cualquier futura clave.

Siguen separados los fallos parciales internos del backend durante create, T143 generaciones/reutilización ABA/otros procesos, trabajo síncrono de mantenimiento retenido y demás límites de vida. No acredita aceptación nativa actual de OpenCode/Codex/Claude; deben repinarse lanzadores con todas las fuentes cambiadas, incluido provider_lifecycle.py. No se tocan credenciales, instalaciones, QEMU ni jobs históricos.
