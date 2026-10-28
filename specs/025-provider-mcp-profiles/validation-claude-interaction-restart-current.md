# Claude · preguntas/respuestas/ACK y reinicio actuales

2026-10-28. **PASS acotado** en `f77e3e66ab2fbd61216ca7dac90fb80e9338f61b`,
505archivos fijados, Claude Code2.1.303 y modelo solicitado `sonnet` (sin
atestación del modelo servido). [Recibo sin secretos](validation-claude-interaction-restart-current.json).

Se ejecutó el lanzador existente `interaction_cli_journey.run("claude_code",
agui_restart=True)`: dos preguntas (texto/opción única), dos respuestas humanas,
un wait y un ACK de ambas respuestas. Coinciden grupo, IDs, versiones y hashes
de recibos creados/devueltos; estado agent_acknowledged y salida0. Tres snapshots
AG-UI conservan identidad y redacción tras una sustitución de API. Las tres
herramientas usadas completan sin error; el alcance de herramientas es exacto.

CLI37,64s; adaptador/propietario/limpieza70,898s; mínimo10090MiB. Conserva75s,
6144MiB y límite de coste1USD del lanzador. Binario/fuente/original de credenciales
sin cambios, raíz temporal retirada, observador terminado, contenedor/red propios
PostgreSQL ausentes y recursos ajenos conservados. La copia privada de credenciales
Claude sigue el archivador existente antes de retirar la raíz; no se afirma que
se hayan borrado todos los archivos de archivo. Ejecutado privado conservado y
descriptor operativo re-cerrado. No hubo login, renovación ni instalación global.

El flujo incluye dos eventos informativos rate_limit_event y el clasificador de
señales marca provider_rate_limited; la ejecución completó todos sus gates. Esa
señal por coincidencia no prueba agotamiento de cuota ni acepta ese escenario.
No se publican cuerpos del modelo, credenciales, argumentos ni inventarios de
hashes de archivos con nombres sensibles.

Preflight: imports reales, firmas, protocolo context-manager sin adquirir servicio,
505pins y archivo0600; controles AST de marcadores frescos/antiguos/symlink y
rechazo de un propietario PostgreSQL externo cuya vida no se puede retener. Dos
hallazgos de la revisión inicial del adaptador se corrigieron antes de armar, y
la revisión final Claude dioGO. Se usa la fixture propietaria verificada porque
no había servicio previo activo; se mantiene hasta que los prestatarios cierran.

**CL-09/T012 siguen abiertos** para recuperación de una espera activa interrumpida:
este caso reinicia después de crear las preguntas y no la acredita. T006 conserva
el504 histórico sin causa demostrada; Unicode actual ya tiene su propioPASS.
No acepta mensajes Work, invocación/autorización de todos los perfiles, cuotas,
concurrencia ni gates globales. T032 permanece aplazada.
