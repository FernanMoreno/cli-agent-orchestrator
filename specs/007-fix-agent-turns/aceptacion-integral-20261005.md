# Aceptación integral T020 / SC-006

Fecha: 2026-10-05. Proyecto original: `C:\Users\ferna\OneDrive\Escritorio\cao-collaboration-demo-20261001-cf8017b2`.

## Resultado

**T020 y SC-006: PASS.** Los tres proveedores reales implementaron e integraron una función pequeña en una copia del proyecto real. Se probó esa misma implementación mediante HTTP y Chromium. La revisión anterior y el navegador separado quedan como evidencia histórica; esta pasada sí une delegación, implementación, mensajes, integración y navegador.

**Veredicto operativo: PASS WITH RISKS.** El primer intento terminó abruptamente y su causa exacta no está demostrada. Esa pasada no cuenta como aceptación. La repetición controlada completó todos los criterios; no se afirma inmunidad a fallos nativos ni se presenta el primer cierre como corregido.

## Qué implementaron

- API: `GET /api/summary` devuelve directamente `{"total":N,"completed":X,"pending":N-X}`, con enteros, sin modificar tareas. Archivo inexistente/vacío: ceros. Datos corruptos: JSON 500. Métodos no admitidos: JSON 405.
- Frontend: `#task-summary` muestra `Completadas: X de Y`; consulta el resumen al iniciar y después de crear/completar. Ante error muestra `Resumen no disponible` y recupera el resultado al recargar.
- Se reutilizaron el almacenamiento, validación, rutas, formulario y lista existentes. No se reconstruyó la aplicación ni se añadió otra base de datos.

Los cambios de la función pertenecen **al artefacto de prueba**. El proyecto original y su `data/tasks.json` se conservaron; el código CAO tampoco cambió en esta aceptación. No se aplicó automáticamente esta función al original, no se hizo commit ni se publicó.

## Proveedores y contribuciones

| Proveedor | Terminal | Trabajo acreditado |
|---|---|---|
| Claude Code / sonnet | `b18708c5` | Delegó mediante CAO, recibió callbacks, inspeccionó los cambios, ejecutó ambos conjuntos de tests y escribió INTEGRATION.md |
| Codex / gpt-6.1-sol | `57461dac` | Implementó API, cuatro pruebas API nuevas y contrato |
| OpenCode / opencode-go/gpt-6-luna | `b5c33b31` | Implementó interfaz, consulta/refresco y dos pruebas frontend nuevas |

Los perfiles se prepararon e instalaron **antes** de entregar la tarea. Los trabajadores se crearon por `assign` del supervisor; sus mensajes regresaron por `send_message`. No se repararon rutas ni se instalaron perfiles a mitad de la ejecución. No hubo reemplazos de trabajadores, forzado de recibos ni modificación de estados para simular éxito.

## Secuencia real e integración

1. Arranque CAO/tmux/HOME/DB/puerto privados; logins locales reutilizados en el entorno aislado.
2. Instalación de los tres perfiles y primera tarea a Claude.
3. Claude delegó a los dos trabajadores con el mismo contrato y cerró la fase de despacho con su recibo.
4. Codex envió una nota de progreso; OpenCode devolvió implementación y pruebas frontend.
5. Claude ejecutó las pruebas mientras la API todavía estaba incompleta: 15 casos, 14 fallos/subfallos. Detectó `/api/summary` aún ausente y **no** creó INTEGRATION.md ni declaró aceptación. Esperó el resultado final de Codex. Esto es una fase parcial registrada, no un fallo final ocultado.
6. Codex implementó la API y envió el callback final. Ambos trabajadores terminaron con LAST y recibos actuales verificados.
7. Claude ejecutó de nuevo las suites: 15 API y 3 frontend PASS; produjo INTEGRATION.md y una respuesta actual verificada con `INTEGRATION_READY`. No necesitó modificar código de integración.
8. El comprobador externo volvió a ejecutar las suites del mismo artefacto y probó la aplicación real mediante Chromium y HTTP.
9. Se reinició el servidor de la aplicación, se verificó persistencia y se compararon manifiestos del original y hashes CAO.
10. Se archivaron los archivos generados/captura/diagnósticos y se eliminaron únicamente los recursos temporales propios.

La aceptación no descansa en un texto de “terminado”: la comprobación exige LAST HTTP 200, estado `verified`, generación, secuencia positiva completada, código generado en ambos subsistemas, INTEGRATION.md, tests externos y comportamiento del navegador.

## Comprobaciones ejecutables

| Comprobación | Resultado |
|---|---|
| API generada por Codex, ejecutada por Claude y comprobador | **15 tests PASS** |
| Frontend generado por OpenCode, ejecutado por Claude y comprobador | **3 tests PASS** |
| Datos vacíos | API ceros y UI `Completadas: 0 de 0` |
| Crear tarea por formulario | API/UI `Completadas: 0 de 1` |
| Completar por checkbox | API/UI `Completadas: 1 de 1` |
| Recargar | Tarea completada y resumen conservados |
| Resumen HTTP 500 controlado | UI `Resumen no disponible`, lista sigue disponible |
| Recuperar resumen | Texto correcto tras quitar el fallo y recargar |
| Título con HTML | Texto inerte, sin etiqueta img ni ejecución del atributo |
| Errores JavaScript no controlados | **0 pageerrors** |
| Reinicio ordenado del servidor | Resumen `{"total":1,"completed":1,"pending":0}` conservado |
| Sondeos salud CAO | **19 HTTP 200**, máximo **0.19 s** |
| Gate arquitectura actual | **5 contratos conservados, 0 rotos** |
| Manifiestos | Original/datos preservados y cinco módulos CAO sin cambios; cuatro importaciones verificadas contra fuente |
| Limpieza | Runtime propio eliminado; archivo de evidencia conservado |

Memoria disponible observada: aproximadamente 622–2036 MiB; swap prácticamente agotada. No se cerraron procesos de otros proyectos. Las 483 regresiones CAO de la auditoría precedente siguen correspondiendo al mismo código; se reutiliza esa evidencia, no se presenta como otra ejecución de 483 casos en esta pasada.

## Revisión de composición y diff

Fronteras revisadas: delegación/configuración/readiness → implementación compartida → mensajes/recibos → integración/tests → lectura de persistencia → HTTP → DOM → mutaciones/refresco → reinicio/recuperación.

- Los trabajadores escribieron archivos separados; Codex fue propietario de CONTRACT.md/API/tests API y OpenCode de frontend/tests frontend.
- GET calcula desde los datos validados actuales, sin copia persistente ni cache nueva; el reemplazo atómico existente conserva la lectura de un archivo completo.
- El resumen se refresca después de mutaciones exitosas. Un fallo de su consulta no deshace una creación o completado ya confirmado.
- Recibos y mensajes conservan autoridad/secuencia/generación existentes; los avisos de progreso no se interpretaron como implementación terminada.
- Se conservaron validación UTF-8/Unicode, errores existentes y tareas originales. Las pruebas anteriores de API/frontend siguieron pasando.
- No hay migración de datos/esquema ni servicio externo versionado. Se usaron contratos HTTP y dependencias reales; no hizo falta Pact ni un contenedor adicional.
- Revisión del incremento: seis archivos modificados por trabajadores y un INTEGRATION.md nuevo del coordinador, sin modificaciones del producto CAO. Diff guardado en `/tmp/cao-integrated-artifact.diff`.

Graphify: consulta previa de assign/mensajes/inbox/recibos contrastada con fuente. Extracción AST final sobre cuatro archivos del artefacto: **69 nodos, 134 relaciones, cero fuentes fallidas, cero tokens LLM**. No se sustituyó el grafo global del repositorio. No corresponde refrescar estructura CAO por una validación que no cambia su implementación.

## Primer intento interrumpido

El intento `cao-integrated-acceptance-20261005-4u7yfgrh` alcanzó los tres proveedores y pruebas parciales, pero el servidor dejó de aceptar conexiones. El log termina durante un volcado periódico faulthandler, con una entrada de frame ilegible en un worker de SQLAlchemy. No se capturó el código/señal exacto de salida ni un core; por eso no se diagnostica una causa raíz a partir de ese texto. El contador observado `oom_kill` era cero, así que tampoco se atribuye al OOM killer.

Experimento controlado: se retiró del **instrumento de prueba** `dump_traceback_later(40, repeat=True)`. Permanecieron faulthandler para fallos nativos, sondeos health, registro de módulos cargados, runtime de producción y comprobación de recibos. La repetición pasó. Esto permite ejecutar la aceptación y señala el volcado periódico como hipótesis; **no demuestra que provocase el cierre**, ni corrige una causa desconocida del producto.

El fallo histórico anterior de observación de `/proc/<pid>/environ` sigue sin causa individual demostrada. No se volvió a observar aquí; la colisión de snapshots corregida tiene sus pruebas directas. Estos límites no se borran al cerrar SC-006.

## Evidencia y reproducción

- Artefacto integrado: INTEGRATION.md (`/tmp/cao-investigation-evidence-20261005/cao-integrated-acceptance-20261005-u9iypvre/implemented-project/INTEGRATION.md`; artefacto histórico local)
- Captura Chromium (`/tmp/cao-investigation-evidence-20261005/cao-integrated-acceptance-20261005-u9iypvre/integrated-browser.png`; artefacto histórico local)
- Código generado: `/tmp/cao-investigation-evidence-20261005/cao-integrated-acceptance-20261005-u9iypvre/implemented-project`.
- Logs finales: `/tmp/cao-integrated-acceptance-repeat.log`; primer intento: `/tmp/cao-integrated-acceptance-final.log`.
- Gate: `/tmp/cao-integrated-composition-final.log`.
- Huellas/importaciones/memoria y scripts exactos de prueba: `/tmp/cao-investigation-evidence-20261005/cao-integrated-acceptance-20261005-u9iypvre`.
- AST: `/tmp/cao-pending-final-ast.json`; diff: `/tmp/cao-integrated-artifact.diff`.

Los tests pueden repetirse sobre el artefacto:

```bash
.venv/bin/python /tmp/cao-investigation-evidence-20261005/cao-integrated-acceptance-20261005-u9iypvre/implemented-project/tests/test_server.py
node --test /tmp/cao-investigation-evidence-20261005/cao-integrated-acceptance-20261005-u9iypvre/implemented-project/tests/test_frontend.js
project-composition-check "$(cat .ai/project-name)"
```

El archivo local conserva los scripts exactos del escenario y navegador; requieren los mismos CLI/logins, Playwright/Chromium y librerías disponibles en este entorno. Son herramientas de evidencia para este entorno, no una nueva distribución del producto.

## Cierre de AI_WORKFLOW.md

Estado Git inicial guardado; validación significativa sobre spec/plan/tasks existentes; plan de escenario añadido antes de escribir en la copia; conocimiento durable reservado al informe/spec; Graphify y fuente revisados; implementación por proveedores; tests reales → gate → revisión de composición/diff → AST acotado → documentación → verificación final de hashes/manifiestos/cleanup.

T020/SC-006 se cierran por esta aceptación integral. No se amplía la implementación CAO ni se declara resuelto retrospectivamente un fallo cuya causa no está demostrada.

### Investigación posterior del cierre

El mecanismo del watchdog se reprodujo con core y GDB, y se corrigió el diagnóstico del runner. La atribución al primer episodio es muy consistente, aunque su core/señal no fueron conservados. Véase [investigacion-cierre-nativo-20261005.md](investigacion-cierre-nativo-20261005.md). La aceptación integral descrita arriba conserva su evidencia histórica.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
