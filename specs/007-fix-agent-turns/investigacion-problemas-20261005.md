# Investigación adicional: MCP, timeouts HTTP y callbacks

Fecha: 2026-10-05. Proyecto de prueba: `C:\Users\ferna\OneDrive\Escritorio\cao-collaboration-demo-20261001-cf8017b2`.

## Resumen

Se separaron tres problemas que aparecían juntos. Hay evidencia directa del cierre de MCP por un error de asignación de memoria durante la importación de dependencias; una reproducción controlada de esperas de tmux sin límite; y una incompatibilidad entre las instrucciones de delegación asíncrona y el contrato de recibos. La colaboración completa entre Claude, Codex y OpenCode sigue sin estar validada.

Esta investigación no modifica el comportamiento de producción. Los cuatro arreglos de arranque ya documentados siguen siendo cambios anteriores. Los experimentos nuevos usan procesos, puertos, sockets, HOME y una copia temporal del proyecto propios.

## 1. Cierre del MCP: causa capturada

### Experimento

Se repitió el arranque real de Claude con el MCP de CAO. Un envoltorio temporal mantuvo stdin/stdout de MCP y capturó stderr y el código de salida del mismo ejecutable de producción. No se imprimieron credenciales.

Dos intentos de arranque terminaron **antes de establecer la conexión**:

| Intento | Código de salida | Duración | Frontera que falló |
| --- | --- | --- | --- |
| MCP 1242434 | 1 | 21,23 s | Importación de módulos de FastMCP |
| MCP 1242770 | 1 | 6,79 s | Importación de módulos de Rich |

Ambos produjeron:

```text
OSError: [Errno 12] Cannot allocate memory
```

El error aparece en `importlib._bootstrap_external._fill_cache`, al leer directorios de dependencias de `.venv` situadas en `/mnt/c/Users/ferna/OneDrive/Escritorio/CAOS`. Claude muestra después `CONNECTION_CLOSED`, porque su proceso MCP ya terminó.

En siete muestras durante la ejecución, `MemAvailable` estuvo entre **324,8 y 444,0 MiB**; `SwapFree` fue cero. El contador global `oom_kill` leído durante la prueba fue cero: estos procesos salieron por excepción, y no hay evidencia de que el OOM killer los eliminara.

### Conclusión y límites

La excepción de asignación de memoria explica directamente estos dos cierres. La presión de memoria es un agravante observado. No se ha aislado cuánto aporta el sistema de archivos de Windows/WSL al fallo de lectura de directorios. Tampoco se puede atribuir retrospectivamente cada cierre anterior de Codex/OpenCode a esta causa: esos intentos no conservaron stderr del proceso MCP.

Aumentar el timeout no corrige una excepción que mata el proceso. Antes de repetir la aceptación de los tres proveedores, debe haber recursos suficientes para importar y ejecutar sus MCP. No se cerraron procesos ajenos ni se alteró la configuración global de memoria/swap.

## 2. HTTP: esperas de tmux localizadas; timeout anterior aún no explicado por completo

### Evidencia real nueva

Se añadió un watchdog independiente que consulta `/health` y un volcado periódico de todas las pilas Python del servidor aislado.

Las siete consultas registradas a `/health` respondieron **HTTP 200**, entre **0,20 y 0,56 segundos**. La tarea llegó a Claude; este informó correctamente que el MCP no estaba disponible. No hubo colaboración ni se crearon trabajadores.

Una captura de pilas mostró dos esperas de transporte:

- Hilo de entrega: `terminal_service.send_input → tmux.send_keys → subprocess.run → communicate/wait`.
- Watchdog FIFO: `_check_pipe_liveness → _probe_pane → tmux.get_history → libtmux → communicate`.

El hilo principal seguía en el ejecutor de asyncio. Esta ejecución no reproduce un bloqueo global del event loop ni el timeout HTTP de 45 segundos de la prueba anterior.

### Reproducción controlada

Se creó un servidor tmux propio con socket privado. La captura normal con `TmuxClient.get_history` terminó correctamente. Después se suspendió únicamente ese servidor con `SIGSTOP`:

```text
NORMAL_CAPTURE=0 CAPTURE_FINISHED 92
FROZEN_TMUX_CAPTURE_STILL_WAITING_AFTER_6S=True
RESUMED_CAPTURE=0 CAPTURE_FINISHED 92
```

La captura permaneció esperando seis segundos y terminó cuando se reanudó el servidor. El socket y el servidor de prueba se eliminaron al finalizar.

### Fuente contrastada

- `clients/tmux.py`, `get_history`: usa `pane.cmd("capture-pane", ...)`.
- El libtmux instalado ejecuta `Popen(...).communicate()` sin timeout.
- `clients/tmux.py`, `send_keys`: varias llamadas a `subprocess.run`, incluido `load-buffer`, carecen de timeout.
- Los endpoints de sesión, terminal y output ya usan `asyncio.to_thread`; el monitor y la entrega de inbox también descargan su trabajo. Añadir otra capa de `to_thread` duplicaría una protección existente.

### Conclusión y trabajo pendiente

Está confirmado que un tmux que no responde puede retener una captura sin límite interno. La reproducción no demuestra por sí sola qué hizo que tmux dejara de responder durante el timeout anterior, ni que se agotara el pool de hilos.

El arreglo necesita límites de tiempo en la frontera de transporte y propagación de incertidumbre: una lectura fallida no puede convertirse en «terminal inexistente» o «trabajo terminado». Si una escritura vence tras un posible pegado, no puede reintentarse la tarea automáticamente. El cleanup también debe ser acotado y conservar el error original. No se aplicó un timeout aislado que ignore estas condiciones.

## 3. Callbacks: conflicto de protocolo confirmado

### Secuencia del bloqueo

1. El supervisor recibe una tarea con recibo de turno.
2. Delega con `assign` y termina su respuesta indicando que espera resultados, sin emitir el recibo porque la tarea global todavía no está completada.
3. La UI muestra finalización, pero `_gate_unverified_receipt_completion` exige verificar el recibo pendiente.
4. Sin recibo verificable, CAO pasa a reconciliación.
5. Los trabajadores envían resultados a inbox.
6. Inbox entrega cada callback mediante `send_input(..., task_delivery=True)`, es decir, como una nueva tarea.
7. `blocks_new_task_input_for_reconciliation` bloquea esa entrega mientras existe el recibo anterior. El callback se conserva como pendiente.

### Instrucciones que entran en conflicto

`examples/assign/analysis_supervisor.md` y las copias de `cao-supervisor-protocols` indican terminar el turno después de delegar y esperar que los mensajes lleguen automáticamente. La instrucción de recibo en `providers/base.py` exige haber completado la tarea antes de imprimirlo y declara que prevalece sobre instrucciones incompatibles.

La propiedad llamada `blocks_new_task_input_for_reconciliation` se activa para **cualquier recibo pendiente**, incluso en un proceso vivo; no solo para recibos recuperados después de un reinicio.

Esto explica el supervisor detenido con callbacks pendientes de la ejecución anterior. No es una pérdida demostrada del mensaje ni se arregla habilitando entrega eager: esta seguiría encontrando el bloqueo del recibo.

### Comprobaciones ejecutables

Se ejecutaron estas suites existentes:

```bash
.venv/bin/pytest -o addopts= -q \
  test/services/test_inbox_service.py \
  test/services/test_turn_recovery.py \
  test/services/test_turn_receipt_delivery.py
```

Resultado: **66 passed**, dos avisos de Pydantic, 19,73 segundos. Incluyen conservación de mensajes pendientes antes del pegado, reconciliación después de una entrega incierta, bloqueo por recibo activo y cancelación sin liberar prematuramente el turno.

Esas pruebas demuestran las protecciones actuales; no demuestran continuidad asíncrona de la tarea del supervisor.

### Corrección pendiente

Se necesita un contrato explícito que distinga terminar una fase de delegación de terminar la tarea global, o permita callbacks del mismo turno con identidad/generación verificables. Debe preservar reinicio, cancelación, remitente, orden y entrega única. No se debe simplemente borrar el recibo ni aceptar mensajes por estar la pantalla libre.

El perfil temporal de aceptación separa despacho y callbacks en fases. Eso evita el conflicto de instrucciones para ese experimento, pero no demuestra que los perfiles generales ya resuelvan el problema. La nueva ejecución falló antes, por el cierre del MCP.

## Evidencias y alcance

- Ejecución instrumentada: `/tmp/cao-real-investigation.log`.
- Archivo privado de diagnóstico: `/tmp/cao-investigation-evidence-20261005/cao-real-collaboration-20261005-l92i1ay1/`.
- Reproducción tmux: `/tmp/cao-tmux-stall-diagnostic.log`.
- Pruebas de contratos: `/tmp/cao-investigation-contracts-20261005.log`.
- Graphify consultado para monitor, terminal, tmux, inbox y recibos; las rutas relevantes se contrastaron contra la fuente.

Los archivos de `/tmp` pueden desaparecer; este documento conserva las conclusiones y resultados esenciales. No se vuelcan configuraciones de autenticación ni bases de datos completas en el repositorio. Los runtimes propios se retiraron al terminar.

## Revisión de composición

**Subsistemas revisados:** MCP/proveedores, API, transporte tmux, FIFO, monitor, recibos e inbox.

**Invariantes:** resultado durable antes de liberar el turno; no repetir un pegado incierto; conservar callbacks bloqueados; no transformar fallos de transporte en ausencia o éxito; aislamiento de procesos y sockets.

**Comprobaciones:** arranque real con stderr/código de salida; watchdog HTTP y pilas Python; tmux real suspendido/reanudado; 66 pruebas de contratos. No se cambió implementación, por lo que no se presenta un nuevo gate de arquitectura como evidencia de una corrección.

**Veredicto:** aceptación integral pendiente. Cierre MCP explicado para los dos intentos capturados; esperas de tmux reproducidas; conflicto de recibos/callbacks localizado. El origen exacto del timeout HTTP anterior y la ida/vuelta real entre los tres proveedores continúan abiertos.
