# T156 — inicialización cancelada retenida y entrada de mensajes

2026-10-26. FR134/SC118 sobre main, padre `5e5c710`. Aceptación acotada; [fuentes y recibos](validation-t156.json).

## Reproducción y causa

La compensación retenía la fila/shell cuando no podía demostrar ausencia nativa, pero liberaba startup y admisión sin registrar que el proveedor nunca se inicializó. El monitor podía observar UNKNOWN y send_input solo rechazaba ERROR. Con PostgreSQL real, Git/worktree temporal y backend controlado, GET devolvió200/UNKNOWN y POST/input devolvió200 y alcanzó send_keys, con supervisor y sin él:4RED. Un GET500 inicial pertenecía al fixture (turn_state Mock sin pareja), corregido antes de ese RED válido; sus recibos y una repetición se conservan.

## Corrección y autoridad

Solo después de comprobar la vinculación reconocida del intento y la encarnación actual, bajo sesión→dispatch→retirada y fuera de Work, se publica deferred_init_failure con kind creation_cancelled. Reutiliza el JSON existente, tres intentos PostgreSQL y el sidecar atómico existente. Se registra antes de intentar parar: si la ausencia exacta permite borrar la fila, se retira también la evidencia; si no, GET puede publicar ERROR sin depender del monitor.

El transporte compartido rechaza fallo conocido antes de proveedor/recibo y relee evidencia justo antes de los bytes. Devuelve acción reconcile y entrega no ocurrida. No se rechaza UNKNOWN globalmente. El texto de control también queda bloqueado; la recuperación por tecla especial mantiene su contrato. No hay esquema, turno fantasma ni transición nueva de recibos de hijo.

Si fallan ambos almacenes, se conserva el marcador local pending y el propietario uncertain; el finisher no libera las marcas de creación diferida/supervisor. Se preserva la cancelación original. Puede impedir incluso retirada explícita durante ese proceso; no promete durabilidad tras reinicio sin evidencia persistida.

## Validación y revisión

14 casos iniciales pasan en53,79s; después227 enfocadas en221,62s, con propietario PostgreSQL cerrado/contenedor exacto ausente en240,965s. Los26 casos funcionales finales cubren API, marcas DB/sidecar, estado UNKNOWN/IDLE, recibos, ambos fallos de almacenamiento con caller/defer, entrada válida sin marcador, segunda lectura, ABSENT/DELETED y observador fresco sin marcas locales. Los controles T155 de sustitución conservan metadata completa y los de Work/binding ausente exigen failure nulo.

La primera ampliada se interrumpió para aplicar dos ajustes Black antes de verificar los bytes finales; solo el pytest propio recibió SIGINT y PostgreSQL quedó cerrado/ausente en229,44s. No se cuenta como aceptación. AI_WORKFLOW sección6 exige ahora ejecutar los controles aplicables del hook antes de congelar hashes y lanzar regresión ampliada.

La primera ampliada completa detectó39 fallos tras778 correctas por una contaminación de fixture antigua: T152 mezclaba tres monkeypatches de TERMINAL_LOG_DIR con el ExitStack de create_mocks. El sentinel existente y un consumidor real reprodujeron2 fallos/5 controles en45,15s. Se anida ahora patch.object en un fixture dependiente y se añade un control de restauración; no se cambia producción para admitir MagicMock ni se relajan los39 controles. La tanda corregida de108 pruebas pasa en166,63s, con propietario cerrado/contenedor exacto ausente en185,568s, y acredita esa composición antes de la ampliada final. Los recibos fallidos permanecen.

Regresión final: **818 passed, 3 deselected, 8 warnings in 552.30s (0:09:12)**. Memoria inicial11406MiB frente al mínimo6144; PostgreSQL identificado y propietario cerrado/contenedor exacto ausente en566.869s.14fuentes de ejecución y3pruebas congeladas intactas. Black/isort pasan y se conservan las3deselecciones configuradas. Arquitectura5/0. Composición PASS WITH DOCUMENTED LIMITS; revisión READ de Claude sin P0/P1 y Luna sin falso verde bloqueante. Las pruebas las ejecutó Codex; proveedores/backend de ejecución controlados, no aceptación de un modelo nativo.

Diff revisado y mapa AST privado actualizado; Graphify canónico se conserva por12cambios ajenos. Obsidian actualizado con operación save-20261026-caos-retained-cancel-input en concepto/índice/log/hot; lint estricto sin incidencias, hot refrescado bajo500palabras.

## Límites abiertos y próximo recorrido

No cierra la carrera de entrada que ganó dispatch antes de la compensación, all-store-failure tras reinicio, fallback corrupto/ilegible, escritores SQL fuera de los guards ni CAS distribuido T143. El test de publicación durante preparación comprueba la segunda lectura mediante SQL controlado; no simula al compensador bloqueado por el mismo dispatch. Observador fresco simulado no equivale a reinicio nativo real. Retirada de fila no autoriza borrar worktrees tardíos. No se amplía autoridad Work ni catálogo/permisos MCP.

OC-06, OC1-05/OC2-04 amplios, autenticación/modelos y gates globales siguen abiertos. Los recibos nativos0fe/Work7f6 no acreditan esta fuente. Próximo: actualizar bindings y preflight del recorrido mixto nativo v1, con preguntas/respuestas/ACK/mensaje/reinicio y recursos propios, antes de ejecutarlo.
