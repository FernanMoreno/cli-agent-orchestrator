# Investigación profunda del cierre nativo

Fecha: 2026-10-05. Referencia: cierre de `cao-integrated-acceptance-20261005-4u7yfgrh` durante la aceptación integral.

## Conclusión probada y límite histórico

**Se reprodujo y localizó un defecto real del instrumento de diagnóstico:** `faulthandler.dump_traceback_later` puede provocar SIGSEGV en los Python instalados al recorrer frames cuyos metadatos se liberan concurrentemente. El reproducer mínimo, la regresión del runner y un servidor CAO aislado caen dentro del watchdog; el control sin watchdog y el muestreo desde Python no caen.

**El mecanismo está demostrado; la atribución retrospectiva del primer episodio es una inferencia fuerte.** Aquel log termina con un frame ilegible dentro del volcado, pero no se guardaron core ni código/señal de salida. No se inventa esa evidencia perdida ni se atribuyen todos los errores históricos a este defecto.

Se corrigió únicamente el diagnóstico generado por `scripts/validate_collaboration_demo.py`; se conservaron el reporte de fallos nativos, los sondeos y los hashes de módulos. No se modificó el núcleo CAO ni se actualizaron Python/dependencias. La aceptación SC-006 previa sigue acreditada; esta investigación añade prueba del mecanismo y una regresión.

## Síntoma y primeras fronteras

El servidor del primer intento respondió health y consultas hasta dejar de aceptar conexiones. El log se cortó durante `Timeout (0:00:40)!`, con una entrada de frame no legible tras llamadas de SQLAlchemy. Eso no demuestra que SQLAlchemy originara el fallo: el muestreador estaba leyendo la pila de un worker que lo usaba.

El cierre precedió al cleanup. No se observaron errores de transporte tmux como frontera inicial ni hubo evidencia de que un proveedor terminara el servidor. El contador `oom_kill` observado fue cero; el fallo controlado termina con SIGSEGV (-11), distinto del SIGKILL que se esperaba de un OOM killer.

## Hipótesis y pruebas de falsificación

| Hipótesis | Evidencia / conclusión |
|---|---|
| Fallo del watchdog nativo | Reproducer independiente de CAO/SQLAlchemy/proveedores cae; traza GDB dentro de faulthandler_thread/dump_frame; hipótesis confirmada como mecanismo |
| SQLAlchemy causa necesaria | Descartada para el mecanismo: el reproducer mínimo sólo usa librería estándar |
| Proveedor/CLI causa necesaria | Descartada para el mecanismo: el servidor controlado cae antes de recibir tareas de proveedores |
| Agotamiento de memoria causa necesaria | Descartado para la reproducción mínima; controles con el mismo allocator completan y la caída es acceso a metadatos liberados |
| Cambiar a Python 3.14.4 lo resuelve | Descartado: dos reproducciones bajo debug allocator terminan en SIGSEGV |
| El primer episodio histórico fue exactamente esta caída | Muy consistente, pero no certificable sin su core/señal; se conserva esa limitación |

## Experimentos aislados

El proceso controlador recoge `subprocess.returncode`, stdout/stderr y cores propios. Los cores se limitaron a 64 MiB por proceso; no se activó una política global de core dumps. GDB y librerías se extrajeron a `/tmp`, sin instalar paquetes del sistema. No se adjuntan dumps de memoria al repositorio.

La carga mínima crea y libera objetos de código en un worker. Se compara: watchdog nativo; ausencia de volcado; `faulthandler.dump_traceback` llamado desde un hilo Python que conserva el GIL en las compilaciones probadas. Los intervalos se aceleraron para exponer la carrera; no se presenta como una repetición exacta de la frecuencia histórica de 40 segundos.

| Python / allocator | Caso | Procesos | Resultado |
|---|---|---:|---|
| 3.12.13 / normal | Watchdog, 2 s | 5 | 5 completan; no reprodujo en esta ventana |
| 3.12.13 / normal | Sin watchdog, 2 s | 5 | 5 completan |
| 3.12.13 / normal | Muestreo Python, 2 s | 5 | 5 completan |
| 3.12.13 / debug | Watchdog, hasta 3 s | 4 | **4 SIGSEGV (-11)**, aproximadamente 0,13–0,15 s |
| 3.12.13 / debug | Sin watchdog, 3 s | 4 | 4 completan |
| 3.12.13 / debug | Muestreo Python, 3 s | 4 | 4 completan |
| 3.12.13 / normal | Watchdog, metadatos únicos, 5 s | 6 | 6 completan; no reprodujo en esta ventana |
| 3.14.4 / debug | Watchdog, hasta 3 s | 2 | **2 SIGSEGV (-11)** |
| 3.14.4 / debug | Sin watchdog, 3 s | 2 | 2 completan |
| 3.14.4 / debug | Muestreo Python, 3 s | 2 | 2 completan |

Son 39 procesos mínimos. `PYTHONMALLOC=debug` no es una condición observada en el primer intento; es una herramienta de falsificación que marca memoria liberada y hace visible el acceso incorrecto. Su activación no demuestra la frecuencia del fallo con allocator normal.

## Prueba nativa: GDB y memoria liberada

El core mínimo registra SIGSEGV en esta cadena:

```text
PyUnstable_InterpreterFrame_GetLine
  dump_frame
  dump_traceback
  _Py_DumpTracebackThreads
  faulthandler_thread
```

La instrucción que falla intenta leer desde `rcx = 0xdddddddddddddddd`; otros campos contienen `0xdddddddd`. El allocator debug de Python rellena la memoria liberada con 0xDD. Es evidencia directa de que el volcado accedió a metadatos liberados, no simplemente de un timeout. [Documentación oficial del allocator](https://docs.python.org/3.12/c-api/memory.html#debug-hooks-on-the-python-memory-allocators).

Se utilizó el ejecutable exacto de `.venv/bin/python`, cuyo SHA-256 es `021044895e95be79dc2f110367607e684119afbc8ce75f6f0eec94844e0acec7`. GDB emitió avisos sobre una sección ELF del binario y posible mismatch; se verificó que la ruta resuelta y el contenido del ejecutable coincidían. La traza y los registros quedan conservados, incluidos los avisos. No se afirma disponer de todos los símbolos de depuración.

El reporte upstream #158200 describe una carrera de watchdog/metadatos con un reproducer parecido y un SIGSEGV nativo. Nuestros experimentos verifican la clase de mecanismo en **los binarios locales**; no se asume que coincidan todas las líneas internas entre versiones. [Reporte de CPython](https://github.com/python/cpython/issues/158200).

La implementación 3.12.13 distingue la API llamada desde Python del worker nativo asíncrono; la ruta del watchdog recorre las pilas sin serializarse con el GIL del hilo que puede estar cambiando sus frames. [Fuente de faulthandler](https://github.com/python/cpython/blob/v3.12.13/Modules/faulthandler.c), [fuente de tracebacks](https://github.com/python/cpython/blob/v3.12.13/Python/traceback.c).

## Servidor CAO real, sin proveedores

Se lanzaron tres procesos independientes con app/SQLite/Uvicorn reales, HOME/DB/tmux/puerto privados y allocator debug. No se añadieron objetos de código artificiales a estos servidores. El watchdog se aceleró a 1 ms; el muestreo Python a 20 ms.

| Diagnóstico | Arranque | Salida natural | Solicitudes | Fallos de transporte |
|---|---|---|---:|---:|
| Watchdog nativo | No llegó a health | **-11 / SIGSEGV** | 0 | No aplica |
| Sin watchdog | Disponible | Ninguna durante carga | 1776 | 0 |
| Muestreo Python | Disponible | Ninguna durante carga | 1848 | 0 |

Las solicitudes mezclan health, listado de sesiones y un ID de terminal deliberadamente inválido; no se presentan todas como HTTP 200. Los controles se cerraron mediante SIGTERM propio: su retorno final -15 corresponde al teardown, no a un cierre espontáneo. El core del servidor que cae muestra `dump_frame → dump_traceback → _Py_DumpTracebackThreads → faulthandler_thread`.

El core del servidor quedó truncado por el límite de 64 MiB y GDB advirtió regiones no disponibles. La señal y los frames superiores se pudieron leer; no se presenta como un core completo. El core mínimo aporta la prueba detallada del puntero liberado.

## Corrección y regresión del runner

Antes del cambio, `test_validation_diagnostics.py` falló en ambos casos: uso del watchdog inseguro y proceso hijo SIGSEGV (-11). Después: **16 tests PASS**, incluyendo los 14 casos existentes del comprobador de recibos.

El instrumento generado ahora:

- conserva `faulthandler.enable()` para fallos nativos;
- usa un hilo Python daemon y `Event.wait(40)` antes de cada muestra;
- llama `faulthandler.dump_traceback(all_threads=True)` desde Python con GIL;
- registra `Event.set` en atexit para la salida normal;
- conserva manifiesto de módulos cargados, sondeos HTTP, perfiles y límites del runner.

La prueba nueva acelera sólo la frecuencia del sampler en el hijo de prueba, usa allocator debug y libera metadatos continuamente. No se desactiva el diagnóstico para conseguir un PASS. Recibos, CAS, nonces, secuencias y generaciones no cambian.

La estrategia está validada en compilaciones con GIL. No se extrapola a builds free-threaded ni a todas las condiciones posibles de fallo nativo. El GIL también significa que un volcado lento puede retrasar brevemente trabajo Python; se conserva el intervalo normal de 40 s y se verifican sondeos HTTP durante la prueba real.

## Aplicación del workflow

Git inicial preservado; diagnóstico antes de producto; spec/plan/tasks ampliados antes del cambio del runner; Graphify del runner contrastado con fuente; TDD red/green; matrices y dependencias reales; gate actual **cinco contratos conservados, cero rotos**; revisión del incremento; AST final aislado de runner/test: **9 nodos y 29 relaciones**, sin fuentes fallidas. No se sustituyó el grafo global.

Las conclusiones durables permanecen en este informe y spec007. Matrices, scripts, trazas y cores propios quedan bajo `/tmp/cao-native-investigation/`; no se copian cores ni resultados Graphify al vault. No hubo commits, instalación global, actualización de Python ni cierre de procesos ajenos.

## Evidencia local

- `matrix-312.json`, `matrix-debug-312.json`, `matrix-metadata-default.json`, `matrix-314.json`: matrices mínimas.
- `gdb-minimal-exact.txt`, `gdb-freed-frame.txt`: señal, stack, registros y disassembly.
- `server-matrix.json`, `gdb-cao-server.txt`: servidor real y lectura limitada de su core.
- `diagnostic-red.log`, `diagnostic-green.log`: regresión previa y 16 casos actuales.
- `runner-increment.diff`, `final-ast.json`, `composition-final.log`: revisión, estructura y arquitectura.
- `providers-safe-final.log`: repetición final con CLI reales y el instrumento corregido.

Base de todas esas rutas: `/tmp/cao-native-investigation/`.

## Verificación final con tres proveedores

**PASS** con Claude `00558498`, Codex `f6623696` y OpenCode `933d2f2b`: dos rondas, las mismas tres terminales, exactamente dos callbacks nuevos en la segunda, LAST y recibos actuales verificados. El proyecto original y la copia permanecieron sin cambios durante esta revisión. No se atribuye a los agentes la ejecución de tests/browser; esta pasada valida colaboración y el diagnóstico, no repite SC-006 de implementación.

14 muestras health HTTP 200, máximo 0.38 s. El servidor emitió 4 muestras de pila desde el sampler seguro; el diagnóstico estuvo activo. No hubo cierres inesperados. Cuatro módulos importados corresponden por hash a la fuente actual; cinco módulos vigilados permanecieron intactos. Memoria disponible aproximadamente 1099–2185 MiB. Runtime propio eliminado; logs archivados en `/tmp/cao-investigation-evidence-20261005/cao-real-audit-20261005-calq_hzw`.

T044 y T045 verificadas: mecanismo reproducido, diagnóstico corregido y aceptación actual comprobada. La explicación del primer cierre es ahora respaldada por reproducciones nativas y servidor real; permanece la limitación estricta de no disponer del core histórico. No se promete inmunidad general del intérprete.
