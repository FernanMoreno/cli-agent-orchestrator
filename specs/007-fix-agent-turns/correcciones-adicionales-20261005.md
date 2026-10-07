# Correcciones adicionales y colaboración real verificada

Fecha: 2026-10-05. Continúa [la investigación](investigacion-problemas-20261005.md) sobre el proyecto `cao-collaboration-demo-20261001-cf8017b2`.

## Cambios aplicados

### 1. Esperas de tmux acotadas

`clients/tmux_transport.py` limita cada invocación a cinco segundos. El cliente usa ese transporte para listados, capturas, pegados y limpieza. El libtmux instalado llama al constructor compartido `tmux_cmd` directamente desde sus listados, evitando `Server.cmd`; por eso se adapta ese constructor una vez en el proceso CAO, sin cambiar globalmente `subprocess` ni copiar las clases del proveedor.

- Un timeout se comunica como resultado de transporte desconocido.
- Los listados y las consultas de existencia no lo convierten en «terminal ausente».
- Un fallo de limpieza no sustituye el fallo original de entrega ni provoca repetir una tarea ya enviada.
- Se preservan selección de socket, configuración, color y la representación de stdout/stderr/returncode que usan los consumidores.
- La captura de un tmux privado suspendido termina con error dentro del presupuesto; vuelve a funcionar después de reanudarlo.

El límite corresponde a cada comando, no a toda una operación que realice varios comandos. No cambia los plazos de ejecución del trabajo del modelo.

### 2. Contrato coherente de recibos y callbacks

El texto compartido de `providers/base.py` define que el recibo corresponde al turno actual. En coordinación asíncrona, después de aceptar las asignaciones se puede cerrar la fase de despacho: enumerar trabajadores y resultados pendientes, imprimir el recibo y devolver el control.

Los callbacks llegan como turnos posteriores con recibos propios. La síntesis final requiere recibir y revisar los resultados. Cerrar despacho no significa que los trabajadores o el objetivo global hayan terminado.

Se alinearon la skill canónica de supervisor, sus dos copias distribuidas, el perfil `code_supervisor` y el ejemplo de asignación. Las tres copias de la skill se comprobaron idénticas.

No se eliminaron fences ni se cambiaron hashes, generaciones, CAS, cancelación o el parser de recibos. Se conserva la frase final que delimita la instrucción para que OpenCode no confunda un recibo eco del prompt con una respuesta. Una entrega incierta sigue requiriendo reconciliación; no se repite automáticamente.

### 3. Claude espera al descubrimiento real del MCP

La primera repetición con los dos cambios anteriores mostró un nuevo problema: Claude recibió la tarea mientras su MCP aún conectaba. `alwaysLoad` y una pantalla libre no bastaban.

La configuración del MCP propio ahora lleva una ruta privada y un nonce nuevos por lanzamiento. El middleware de FastMCP publica la señal atómicamente, con modo 0600, cuando un cliente completa el procesamiento de `tools/list`. Claude comprueba el nonce actual antes de declarar completada su inicialización. Una señal antigua no sirve; los MCP de terceros y perfiles nativos sin MCP configurado por CAO no reciben esta espera.

El SDK stdio real comprueba que `initialize` por sí solo no produce la señal y que `list_tools` sí la produce, anunciando `assign` y `send_message`. Los tests también cubren descubrimiento fallido, consultas internas del servidor y timeout antes de initialized.

Si el MCP muere durante la importación por falta de memoria, la señal no aparece: no se entrega la primera tarea como si las herramientas estuvieran disponibles. Esto protege el arranque; no aumenta la RAM disponible ni elimina las limitaciones del sistema de archivos Windows/WSL.

## Prueba conjunta real: PASS

Se usó una copia exacta y aislada del proyecto indicado por el usuario, con los tres proveedores reales y sus logins existentes. No se sustituyó ningún proveedor por un mock.

| Papel | Proveedor | Terminal |
| --- | --- | --- |
| Coordinador | Claude Code | `3d68c5f3` |
| Revisión API | Codex | `5f781c28` |
| Revisión frontend | OpenCode | `8e86711e` |

Secuencia observada:

1. Claude recibió la tarea después del descubrimiento de herramientas del MCP.
2. Delegó ambas revisiones mediante CAO `assign`.
3. Cerró el despacho con su recibo mientras los trabajadores seguían trabajando.
4. OpenCode y Codex revisaron archivos reales y devolvieron hallazgos mediante CAO `send_message`.
5. El inbox de Claude registra ambos remitentes como `delivered`.
6. Claude produjo una síntesis con las secciones **API**, **Frontend** y acuerdo sobre **CONTRACT.md**.
7. La consulta de resultado LAST devolvió HTTP 200 con el resultado verificado del supervisor.
8. Los manifiestos SHA-256 confirmaron que ni el proyecto original ni la copia temporal cambiaron.

El watchdog registró diez respuestas HTTP 200 a `/health`, con un máximo de **0,12 segundos**. No hubo errores de asignación de memoria en los stderr MCP de esta ejecución. El runtime temporal y sus procesos propios se retiraron; la evidencia se archivó antes de limpiar.

Evidencia: `/tmp/cao-real-fixed-ready-collaboration.log`, archivo privado `/tmp/cao-investigation-evidence-20261005/cao-real-collaboration-20261005-mj7l2y49/`.

Este PASS acredita delegación, callbacks y síntesis de una revisión de archivos. No acredita creación de aplicación, ejecución en navegador o carga ilimitada de agentes. El harness no exige que todos los trabajadores hayan asentado sus propios recibos antes de cerrar el resultado del supervisor: valida los callbacks recibidos y el recibo final del coordinador.

## Verificaciones actuales

| Grupo | Resultado |
| --- | --- |
| Cliente tmux, lookup, panes, existencia estricta, inbox, recuperación, recibos, reinicio y Claude | 497 passed, 1 skipped |
| Codex, OpenCode, timeouts, assign y recuperación con tmux real | 464 passed, 3 skipped |
| Nuevas regresiones: transporte, fases, readiness Claude y MCP stdio real | 18 passed |
| Gate `project-composition-check caos` | PASS: 396 archivos, 1660 dependencias, cinco contratos conservados, cero rotos |
| Skill de supervisor: canónica y dos mirrors | Idénticas |
| Graphify AST de los siete módulos afectados | 534 nodos, 1127 aristas |
| Diff de este incremento y `git diff --check` | Revisados, sin errores |

Las tres suites de la tabla seleccionan archivos distintos: **979 pruebas aprobadas y cuatro omitidas**. Las dos primeras muestran avisos existentes de deprecación de Pydantic. No se suma la ejecución independiente del SDK stdio, que se solapa con la tercera suite.

Logs: `/tmp/cao-fix-final-suites-20261005.log`, `/tmp/cao-fix-provider-composition-20261005.log`, `/tmp/cao-startup-final-gates.log`, `/tmp/cao-fix-composition-20261005.log`. El grafo AST aislado está en `/tmp/cao-fix-graphify-20261005.json`; no se reemplazó el grafo global con un grafo parcial.

## Revisión de composición y límites

**Subsistemas cambiados:** transporte tmux, prompt de recibo, proveedor Claude, señal del MCP y protocolos de supervisor.

**Vecinos revisados:** API y pool de hilos, FIFO/watchdog, monitor, inbox, verificación de recibos, cancelación, NativeChild, providers Codex/OpenCode y configuración de herramientas.

**Contratos conservados:** error de lectura no equivale a ausencia o éxito; posible pegado no se reentrega; mensaje bloqueado permanece pendiente; callback no borra recibo anterior; señal MCP pertenece al lanzamiento actual; ninguna conclusión de fase declara completados los trabajadores.

**Dependencias reales usadas:** tmux, SQLite del runtime CAO, SDK MCP stdio y Claude/Codex/OpenCode con el proyecto real copiado. Las pruebas unitarias inyectan fallos en fronteras que serían difíciles de provocar de manera determinista con un modelo.

**Veredicto:** PASS para las correcciones y la colaboración de revisión ensayada. T027/SC-007 quedan acreditados. T020/SC-006 siguen pendientes en cuanto a creación de aplicación e integración en navegador. La causa exacta del timeout HTTP histórico no se reconstruyó; la espera ilimitada de transporte sí se corrigió y la ejecución conjunta actual no reproduce el timeout.

La falta de memoria observada anteriormente sigue siendo una limitación del entorno. No se mataron procesos ajenos ni se cambió swap. Los cambios están en el código del workspace; no se hizo commit, push, publicación ni reinicio de una instancia personal ajena al ensayo.
