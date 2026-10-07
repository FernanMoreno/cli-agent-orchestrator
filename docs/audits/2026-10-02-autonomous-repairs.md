# Correcciones y aceptación autónoma — 2026-10-02

Correcciones implementadas y verificadas en `main`. La versión final aprobó aceptación nativa con recibo tardío auténtico y reinicio, sin órdenes correctivas, verify ni retries del operador. La fase normal previa también pasó. La conclusión de estas reparaciones sustituye el FAIL anterior para los límites aquí comprobados; no acredita ejecución nativa de proveedores sin instalación funcional. No se han realizado commits, pushes ni merges.

## Fallos reparados

- OpenCode v2 podía seleccionar el resultado de un intento anterior, delimitado por un marcador de duración antiguo, aunque la respuesta actual contenía el recibo correcto. La extracción reconoce el recibo vigente y el compositor inactivo, elimina el contrato de entrada reflejado y rechaza recibos citados, generaciones distintas y procesamiento activo. Una captura nativa real se conserva como fixture con el nonce sustituido; su reproducción selecciona la respuesta actual y verifica el recibo.
- Tras reiniciar CAO, los terminales conservaban su identidad pero perdían la lectura FIFO. Un observador reconstruye exclusivamente la captura existente después de comprobar identidad de backend, encarnación y exclusión de Work. No inicializa proveedores ni envía texto o Enter. Se conserva el dialecto persistido de OpenCode/Kimi y la misma generación del recibo. El apagado drena los lectores y la observación en curso.
- La aceptación descubrió que un mensaje de coordinación podía entrar durante `initialize()` y convertirse en el primer prompt de Codex. El trabajador seguía procesando ese mensaje hasta que CAO agotaba el timeout de inicialización. La admisión compartida bloquea entradas antes de publicar el terminal diferido; solo su inicializador posee la capacidad del primer envío. Los mensajes bloqueados siguen pendientes y se entregan una vez, después de la tarea original.
- El texto nativo `Go usage limit exceeded` ahora se reconoce como cuota agotada. La cola y la tarea se conservan; no se declara éxito ni se repite el trabajo.
- Copilot estaba localizado en PATH, pero su shim de Windows fallaba en WSL con código 127 y un `.bat` inexistente. La prueba efectiva de capacidades tiene timeout y propaga el diagnóstico nativo. No se confunde ese fallo de instalación con una espera de 60 segundos o con falta de login.
- Un modelo confundió su ID con el del supervisor y envió el recibo únicamente por callback. El contrato compartido identifica expresamente el terminal actual y exige el recibo en la respuesta final visible, aunque se haya enviado el resultado por `send_message`. El buzón existente admite un remitente declarado; sus filas no sustituyen la prueba terminal.

- FastMCP rechazaba IDs de terminal que el modelo emitía como enteros de ocho dígitos. `send_message` acepta únicamente esa representación numérica exacta, la convierte sin pérdida a texto y conserva los IDs de texto, incluidos los ceros iniciales. Rechaza booleanos, flotantes y números fuera de ocho dígitos. La prueba usa el parser real de FastMCP.
- Un frame COMPLETED antiguo podía abrir la verificación de una nueva generación antes de confirmar su transporte y CAS `prepared -> sent`. La admisión compartida reserva la preparación/envío/CAS y libera su reserva en `finally`, incluso después de un fallo incierto. No consume intentos ni arma deadlines durante esa reserva; la observación auténtica posterior sigue autorizada y el recibo conserva su bloqueo contra tareas nuevas. Ocho escenarios verifican esa frontera con los cuatro adaptadores estrictos reales.
- Codex presentaba comentarios de progreso con el mismo compositor de una respuesta final; durante un redraw sin spinner se declaraba COMPLETED prematuramente. Un turno activo exige recibo final extraído o un marcador nativo `Worked for` vinculado al contrato vigente. La finalización sin recibo sigue entrando en reconciliación acotada; un comentario, un contrato reflejado y una respuesta histórica no gastan ese presupuesto.

- El prompt compartido de restricciones blandas enumeraba categorías CAO como si fueran nombres literales de herramientas disponibles. En aceptación nativa, Codex declaró que no tenía `execute_bash` ni `fs_*`, aunque su CLI dispone de equivalentes nativos. El formateador compartido distingue categorías de permisos y herramientas nativas, conserva el deny-all y el alcance exacto de herramientas/MCP, y evita buscar plugins ajenos como sustitución. Sus seis consumidores mantienen la misma lista de permisos; solo cambia su significado explicado al modelo.

- Claude también podía iniciar verificación durante comentarios/herramientas o después de un resumen intermedio seguido de actividad. La admisión exige recibo vigente o resumen nativo final posterior a la última celda del turno, vinculado al contrato actual. La encuesta opcional posterior al turno ya no se extrae como respuesta; el filtro requiere su panel exacto, duración final y compositor, sin una nueva respuesta o tarea entre ambos. Se conservan preguntas genuinas, varios paneles históricos y la reconciliación de un final sin recibo. Una captura auténtica conserva la reproducción previa y el fixture saneado.

## Proyecto de pruebas original

Las correcciones externas se aplicaron a `cao-turn-recovery-demo-20261002-f9086a25`: GET `/api/stats`, referencias coherentes a los controles de limpieza y confirmación antes del borrado. Las pruebas usan almacenamiento aislado. La API conserva los contratos existentes de edición, identidad de nota y estado completado.

Verificación ya ejecutada: 13 pruebas HTTP del demo, 30 comprobaciones HTTP reales y 15 comprobaciones en Chromium, incluyendo guardar/cancelar, estadísticas, cancelación y aceptación de limpieza, persistencia tras recargar y ausencia de errores de JavaScript. SHA-256 original de `notes.json`: `9540bfae64b96d11a5d146ca2008d733b816518fa5b8c56f5e7597941d97ca59`.

## Cobertura y límites

Los 13 adaptadores registrados están cubiertos por reconstrucción y admisión de inicialización; los casos de reinicio utilizan SQLite y tmux reales. Claude, Codex, OpenCode y Gemini disponen del contrato estricto de recibos. Los otros nueve adaptadores mantienen su capacidad declarada; no se les atribuye un protocolo que no implementan.

Hay cuatro ejecutables localizados. Claude, Codex y OpenCode participan en aceptación nativa. Copilot requiere corregir su instalación de WSL; los otros nueve ejecutables no están instalados. Estas limitaciones no se cuentan como pruebas nativas aprobadas. Las cuotas y las decisiones del modelo se documentan por separado de los fallos de CAO.

## Verificación ejecutada

- Regresión de proveedores, monitor, recibos, reinicio y buzón: 2739 aprobadas, 13 omitidas, dos avisos de deprecación.
- Contratos actualizados de herramientas blandas, lanzamiento, permisos vacíos y verificación: 493 aprobadas, tres omitidas, dos avisos de deprecación. Los conjuntos se solapan; no se suman como casos únicos.
- MCP y contratos compartidos anteriores: 587 aprobadas; la última regresión y el gate de 493 vuelven a cubrir las fronteras modificadas después.
- Admisión durante transporte/CAS con los cuatro adaptadores estrictos reales: ocho aprobadas.
- La fase nativa normal completó coordinación PEER_CONTRACT/PEER_ACK, retorno al supervisor, recibos verificados y entrega de todos los mensajes, con exactamente dos asignaciones y notas intactas. No hubo mensajes correctivos, llamadas verify ni retries del operador.
- Graphify AST actualizado de 385 fuentes byteidénticas: 12631 nodos, 34439 relaciones, cero tokens LLM. Los hashes coinciden con el código de aceptación.

- Última regresión de integración/recuperación: 438 aprobadas. Tras ajustar el filtro puro de encuesta, 239 pruebas de Claude y recibos compartidos vuelven a pasar, incluidos 17 casos nuevos.
- La versión final aprobó recibo tardío y reinicio. En ambos casos cerró el supervisor, verificó ambos hijos y entregó todos los mensajes. Recibo tardío: testigo auténtico registrado, reconciliación forzada, recuperación sin nueva orden, dos hijos succeeded, todos los mensajes entregados y supervisor completado.

## Revisión de composición

Subsistemas revisados: catálogo y gestores de proveedores, persistencia de terminal/recibo, admisión de input, backend tmux, FIFO, monitor de estado, buzón, observación tardía y lifespan de API. Vecinos revisados: Work, colocaciones remotas, reconexión Herdr, callbacks del supervisor y consumidores de resultados.

Invariantes: identidad y encarnación antes de conectar output; ninguna observación obtiene autoridad para reenviar; primer prompt reservado al inicializador; rollback del buzón a PENDING si no hubo entrega; RECONCILE si el transporte pudo aceptar el texto; generación/digest y liquidación atómica antes de liberar el turno; ningún presupuesto de verificación antes de concluir el transporte/CAS; tarea de observación drenada al cancelar; compatibilidad con variantes históricas y exclusión de terminales Work/remotos según su propietario.

Las pruebas usan SQLite, FIFO, tmux, HTTP y Chromium reales para sus fronteras respectivas. Los dobles de inicialización permiten reproducir un frame IDLE antes de que `initialize()` retorne; la cola y sus estados se verifican en SQLite real. Los contratos de arquitectura pasan: cinco conservados, cero rotos. Los contratos MCP se verifican mediante FastMCP real; los estados compartidos se prueban con SQLite. Pact no añade una frontera independiente a estas llamadas monolíticas. La aceptación final verificó una sola orden por fase, recibo tardío auténtico y reinicio sin nueva orden. Conservó identidades, verificó ambos hijos, entregó todos los mensajes y cerró el supervisor. Los hashes de las 385 fuentes coinciden entre ejecución, código final y Graphify; los tres contadores de intervención del operador permanecieron en cero.

Veredicto de composición: **PASS WITH RISKS**. Las correcciones y fronteras disponibles pasan; Copilot requiere reparar su instalación WSL y nueve ejecutables no están instalados. La restricción por prompt conserva su carácter blando y no se anuncia como un mecanismo nativo de permisos. Los turnos agotados por cuota conservan su incertidumbre y no se presentan como éxito.

## Evidencia y temporales

Evidencia conservada (`evidence/2026-10-02-autonomous-repairs/evidence-index.json`; artefacto histórico local): logs, scripts, resultados sanitizados y hashes originales. Se excluyen credenciales, bases nativas completas y streams ANSI sin sanear. Las ejecuciones exploratorias o anteriores a la última corrección no acreditan el comportamiento final.

Los servidores y sesiones propios se cierran antes de borrar sus temporales. Todas las pruebas, sesiones y servidores propios terminaron. No quedan rutas propias spec008-repair-* en /tmp ni cao-spec008-repair-* en /var/tmp; se eliminaron también los cachés temporales de estas pruebas. La limpieza está registrada en cleanup-progress.json. Se conserva el proyecto original y el trabajo ajeno.

Los artefactos históricos locales mencionados en este informe se conservan fuera del conjunto publicado; no son evidencia de una ejecución nueva. Las comprobaciones actuales de cierre se registran en el spec 015.
