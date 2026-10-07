# Pruebas reales adicionales del proyecto de colaboración

> Actualización: los fallos reproducidos de esta validación se investigaron y corrigieron en [auditoria-profunda-correcciones-20261005.md](auditoria-profunda-correcciones-20261005.md), con nuevas pruebas y límites explícitos.


Fecha: 2026-10-05. Proyecto original: `C:\Users\ferna\OneDrive\Escritorio\cao-collaboration-demo-20261001-cf8017b2`.

## Resultado

**Colaboración funcional verificada en dos rondas y aplicación verificada con HTTP y Chromium reales.** La comprobación independiente acreditó los recibos actuales de los tres agentes, mensajes nuevos y reutilización de terminales. Las pruebas de la aplicación terminaron correctamente. No se declara una ejecución global libre de fallos: hubo tres timeouts de salud y el primer comprobador de segunda ronda falló por buscar literalmente un marcador partido entre líneas.

Se trabajó sobre copias del proyecto original, usando su implementación existente. El original se conservó: comparación SHA-256 por archivo antes/después, excluyendo bytecode Python. CAO utilizó HOME, base de datos, puerto loopback y socket tmux privados. Los proveedores utilizaron autenticación local disponible; no se publicaron credenciales. Los perfiles temporales se instalaron antes de lanzar la tarea. No se modificó código de producción en esta ampliación de pruebas.

## 1. Tres proveedores reales, dos rondas

| Papel | Proveedor/modelo | Terminal | Último turno / completado | Estado del recibo final |
|---|---|---|---|---|
| Supervisor | Claude Code / sonnet | `ad65089e` | 8 / 8 | `verified` |
| API | Codex / gpt-6.1-sol | `df4b5de9` | 4 / 4 | `verified` |
| Frontend | OpenCode / opencode-go/gpt-6-luna | `b0ac158a` | 2 / 2 | `verified` |

### Primera ronda

Claude creó los dos trabajadores mediante `assign`. Cada trabajador revisó los archivos reales de su área y envió hallazgos mediante `send_message`. Ambos callbacks quedaron entregados a Claude; el supervisor devolvió una síntesis API/Frontend. Se verificaron respuestas LAST HTTP 200 y cierre de los dos trabajadores antes de reutilizarlos.

### Segunda ronda

El comprobador envió tareas adicionales mediante el endpoint real de entrada a las mismas terminales Codex/OpenCode. Los trabajadores volvieron a leer código y respondieron a Claude mediante CAO. Los mensajes nuevos quedaron entregados con identificadores **3** (OpenCode) y **4** (Codex); ambos contenían `RONDA2`. Claude terminó con una nueva síntesis marcada `RONDA2_FINAL`.

La comprobación independiente exigió simultáneamente:

- Recibo `verified`, respuesta LAST HTTP 200 y secuencia actual completada para cada trabajador: Codex 4 y OpenCode 2.
- Marcador de segunda ronda en la respuesta final de ambos trabajadores, contemplando saltos de línea de la terminal.
- Los dos callbacks nuevos entregados y procedentes de los trabajadores esperados.
- Síntesis final nueva de Claude, con recibo verificado.
- Exactamente las mismas tres terminales en la sesión.
- Igualdad de los manifiestos de la copia revisada y el original.

Resultado: **PASS**. Generaciones finales: Claude `35e0be64e0dc5c79fc20b13aa8373097`; Codex `6502f1ebc2a62f490ea3599d997bbd18`; OpenCode `1effbe57999afda471706bdf33af7232`.

**Límite:** la segunda ronda fue enviada por el comprobador, no asignada de nuevo por Claude. Los trabajadores hicieron revisión estática, no desarrollo ni ejecución de pruebas. No se probó mensajería directa Codex↔OpenCode.

### Fallo del primer comprobador

El runner principal terminó con código 1: `Worker receipt not verified: b0ac158a marker=RONDA2_DONE`. OpenCode había escrito `RONDA2_DO` y `NE` en líneas separadas. El endpoint devolvía HTTP 200, `turn.state=verified` y `turn_sequence=turn_completed=2`.

No se forzó ningún recibo ni se cambió estado interno. Una comprobación independiente normalizó espacios para el marcador, pero siguió exigiendo el recibo real, la secuencia actual, callbacks nuevos y síntesis del supervisor. Se conserva el fallo original como evidencia de un defecto del comprobador, junto al PASS independiente.

Además, la respuesta LAST de OpenCode incluyó parte del texto de instrucciones del recibo, con el recibo redactado. Es una observación de extracción/presentación que requiere diagnóstico adicional; no impidió verificar el cierre. No se considera corregida.

## 2. Aplicación real: HTTP, concurrencia y persistencia

Se inició el `ThreadingHTTPServer` real del proyecto, con su `Handler`, en un puerto loopback libre y datos de una copia temporal. La prueba no ejerció el comando de arranque con puerto fijo 8091.

| Caso | Resultado |
|---|---|
| Health real | HTTP 200, `{"status":"ok"}` |
| 24 POST concurrentes, ocho clientes | 24 identificadores distintos; ninguna tarea perdida |
| Cinco cuerpos de creación inválidos | HTTP 400 |
| Tres cuerpos de actualización inválidos | HTTP 400 |
| Actualizar tarea inexistente | HTTP 404 |
| DELETE sobre `/api/tasks` | HTTP 405 |
| Acceso a archivo fuera de rutas estáticas | HTTP 404 |
| Reiniciar el servidor tras cambios | Lista completa conservada, igual a la previa al reinicio |
| Archivo JSON corrupto: GET y POST | HTTP 500; el POST no sobrescribió el archivo corrupto |
| Restaurar el archivo válido | GET vuelve a HTTP 200 |

Los casos negativos fueron **11**. También se ejecutaron las pruebas existentes de la copia: **5 casos Python y 1 caso Node, todos aprobados**.

La prueba de persistencia demuestra conservación tras un cierre ordenado. No prueba apagado brusco ni durabilidad ante pérdida eléctrica.

## 3. Navegador Chromium real

Playwright ejecutó un Chromium headless instalado, conectado al servidor real. Se realizaron acciones mediante formulario y checkbox, sin sustituir JavaScript ni las respuestas exitosas de la API:

1. Crear una tarea con `ñ` y un título con `<img src=x onerror="window.__xss=1">`.
2. Comprobar que aparece como texto, sin crear un elemento `img` ni ejecutar el manejador.
3. Completar la tarea desde el checkbox.
4. Recargar y comprobar que continúa completada y deshabilitada.
5. Inyectar HTTP 500 en la carga de tareas: aparece el error y no se muestra una lista vacía como si la carga hubiera funcionado.
6. Inyectar HTTP 500 en PATCH: el checkbox vuelve a pendiente y queda habilitado para reintentar.

Resultado: **PASS**, cero excepciones JavaScript registradas por `pageerror`. Los dos casos de fallo de navegador usan respuestas HTTP controladas por Playwright; el resto usa la API real.

### Preparación del navegador

La versión de Playwright esperaba un Chromium distinto del instalado. Se seleccionó explícitamente el ejecutable disponible. Después faltaba `libasound.so.2`: se descargó el paquete `libasound2t64` y se extrajo en `/tmp`, usando `LD_LIBRARY_PATH` solamente para la prueba. No se instaló un paquete global.

Un intento con agentes y navegador simultáneos se interrumpió, cerrando únicamente los procesos del navegador de esta prueba. La pasada final se ejecutó después de limpiar el runtime de proveedores y terminó correctamente. La captura está en `/tmp/cao-demo-browser-more.png`.

## 4. Discrepancias del contrato reproducidas

Estos hallazgos afectan a la **demo**, no al transporte CAO:

### D1. La API acepta codificaciones fuera del contrato

`CONTRACT.md` declara peticiones JSON UTF-8. Se enviaron cuerpos UTF-16 y UTF-32 con `Content-Type: application/json; charset=utf-8`: ambos obtuvieron HTTP 201.

Fuente: `api/server.py:90`, `json.loads` sobre bytes permite detectar otras codificaciones. Hace falta decidir si se exige UTF-8 estrictamente o si se amplía el contrato. No se cambió esa decisión en esta tarea de pruebas.

### D2. Límite de tamaño no documentado

Un título no vacío de 17.000 caracteres produjo HTTP 400. `api/server.py:87-92` limita el cuerpo a 16.384 bytes; el contrato no documenta ese máximo. El formulario tiene además `maxlength=200`, que no es el mismo límite.

No se propone quitar el límite automáticamente. La corrección debe acordar y documentar los límites de API y formulario, con pruebas en sus fronteras.

## 5. Salud de CAO y recursos

El watchdog tomó **37 muestras**: **34 HTTP 200**, máximo 1,96 s entre respuestas exitosas; **3 ReadTimeout** con presupuesto de consulta de 3 s. No se declara PASS de disponibilidad durante toda la ejecución.

En el runtime se observaron entre aproximadamente **298 y 1.668 MiB** de memoria disponible y swap libre igual a cero. `/proc/pressure/memory` mostró presión elevada durante el tramo degradado. Los timeouts coincidieron con presión de memoria y el intento de navegador, pero esta prueba no identifica su causa exacta ni demuestra que exista un nuevo bloqueo tmux.

Los procesos de otros proyectos y la configuración de RAM/swap se conservaron. Para cerrar este punto hace falta repetir la disponibilidad bajo carga controlada y capturar la primera frontera lenta cuando ocurra el timeout.

## Evidencia local y comandos

- `/tmp/cao-real-more-rounds.py` y `/tmp/cao-real-more-rounds.log`: runner inicial, revisión, segunda ronda y fallo literal del marcador.
- `/tmp/cao-round2-independent-check.py`, `/tmp/cao-round2-independent.log` y `/tmp/cao-round2-independent-result.json`: comprobación independiente de recibos/secuencias/callbacks actuales.
- `/tmp/cao-demo-http-more.py` y `/tmp/cao-demo-http-more.log`: HTTP, navegador, reinicio y datos corruptos; pasada final PASS.
- `/tmp/cao-demo-browser-more.cjs`: acciones Playwright.
- `/tmp/cao-demo-contract-more.py` y `/tmp/cao-demo-contract-more.log`: pruebas existentes y discrepancias UTF/tamaño.
- `/tmp/cao-investigation-evidence-20261005/cao-real-collaboration-20261005-t33ydkh4/`: logs privados del runtime y memoria, conservados antes de eliminarlo.

Comandos principales desde el repositorio:

```bash
PYTHONPATH=. .venv/bin/python /tmp/cao-real-more-rounds.py
.venv/bin/python /tmp/cao-round2-independent-check.py
LD_LIBRARY_PATH=/tmp/cao-demo-browser-deps/extracted/usr/lib/x86_64-linux-gnu \
  .venv/bin/python /tmp/cao-demo-http-more.py
.venv/bin/python /tmp/cao-demo-contract-more.py
```

Los scripts de proveedores y la comprobación independiente se ejecutaron mientras existía el runtime privado; sus identificadores y puerto corresponden a esta ejecución. No se pueden reutilizar sin adaptar esos valores o lanzar un nuevo runtime. Los archivos `/tmp` son evidencia local temporal; este informe conserva las conclusiones durables.

## Pendiente

- Resolver D1/D2 de acuerdo con el contrato deseado de la demo.
- Diagnosticar la extracción LAST de OpenCode que incluyó instrucciones de entrega.
- Revalidar disponibilidad CAO durante carga simultánea sin atribuir los timeouts a una causa no demostrada.
- SC-006/T020 permanecen abiertos: esta pasada acredita revisión colaborativa y navegador en ejecuciones separadas sobre copias equivalentes; no una única tarea de implementación e integración completa dirigida por los proveedores.
