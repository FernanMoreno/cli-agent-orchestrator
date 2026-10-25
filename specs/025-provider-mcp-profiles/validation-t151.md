# T151 — retirada Herdr con ausencia exacta y propietario de routing

2026-10-25. FR129/SC113; corrección acotada de T143 sobre main `4582847e539a3dab51512d3954cbdf36195d7afb`. [Fuentes y recibos](validation-t151.json).

## Estado contrastado y fallos

La frescura de los dos barridos de descubrimiento ya estaba implementada: bloqueo de sesión, filas actuales y snapshot Herdr fresco y limitado a esa sesión. Dos controles de descubrimiento antiguo/snapshot nuevo pasaron sobre la base. No se presenta ese comportamiento como una corrección nueva.

Sí se reprodujeron diez fallos con PostgreSQL y adaptadores MockCLI reales sin iniciar agente: los tres recorridos —startup, cruce DB y panel stale— borraban por SQL directo sin retirar el proveedor/cache/archivos, ignoraban la negativa de limpieza y la prueba nativa desconocida; un panel con etiqueta viva cuya nueva ubicación no pudo resolverse perdía fila y routing. RED:10 fallos,2 controles correctos,38,73 s. Los mismos doce controles pasan tras la corrección,42,62 s.

## Retirada autorizada

El helper común conserva dispatch Work y admisión lógica de retirada. Rechaza colocación remota, relee metadatos y contrasta la tupla de transporte capturada. También vuelve a comprobar propiedad activa/retención dentro de esa admisión. Solo una prueba nativa `cleanup_terminal_exact(close=False)` con resultado `ABSENT` permite desmontar recursos. `UNKNOWN`, presencia, excepción o limpiezaFalse conservan fila, caché, archivos y routing para reintento. No se mata ni cierra un panel por etiqueta reutilizable.

Se evita `capture_terminal_snapshot` en la ruta ordinaria: no se leen historia ni cwd de una etiqueta que podría pertenecer a un reemplazo. La limpieza usa el cwd persistido, con la comprobación existente del ID de worktree. Desmonta FIFO/monitor/proveedor antes de entrar al lease físico final del gestor; después usa el borrado central con sidecars y prueba KAS existentes. No se emiten eventos de plugin nuevos.

La fase de sesión completa ocurre en un trabajador privado propietario, incluido el bloqueo de sesión y el snapshot fresco. `unregister_inbox=False` evita que el nuevo trabajador mutile mapas Herdr; el valor por defectoTrue deja a los demás llamadores sin cambio. La cancelación repetida espera al trabajador real. Una pasada posterior puede retirar solo routing si la fila sigue ausente, no hay proveedor en caché en una consulta O(1) y la ausencia nativa es exacta. Sin metadatos pero con proveedor se conserva el propietario privado. Si la observación fue explícitamente «fila ausente» y aparece una fila antes del trabajador, se rechaza su retirada.

La etiqueta viva con resolución fallida ahora conserva los mapas y reintenta; el error de resolución no equivale a ausencia.

## Traspaso entre trabajador y bucle

Durante el desarrollo se reprodujo una carrera adicional introducida por separar las fases: una inscripción nueva con el mismo ID podía perder routing al aplicar el resultado antiguo, incluso reutilizando el mismo pane_id compacto.2 RED,29,04 s; no se atribuyen a la base anterior.

Cada inscripción actual recibe un testigo opaco; el trabajador captura uno por candidato en O(1), y el bucle solo aplica la baja si todavía coincide. El guardado del testigo y los cambios de mapas son memoria, sin I/O bajo el mutex. Las bajas completas retiran el testigo; no se guarda historial. Una inscripción nueva elimina solo su alias anterior, restablece estado Kiro/working propio y no borra un pane que ahora pertenece a otro terminal. Se prueban10.000 inscripciones completadas sin entradas históricas. El contador de retiradas se incrementa únicamente tras una baja aplicada, con control del log.

## Validación y composición

La primera enfocada dio139 correctas y5 fallos de fixtures antiguos que modelaban SQL directo sin prueba/metadatos. Se corrigieron solo esas cinco preparaciones: registros completos, prueba exactaABSENT y observación del alias central. El caso que ya observaba fila ausente exige ahora ausencia de SQL y solo eliminación de routing; el control Work mantiene la comprobación de todos los IDs antes del cierre.144 correctas,115,61 s. Tras incorporar el testigo,146 correctas,115,10 s. La pasada amplia previa581 correctas,351,92 s precede a los últimos ajustes de contador/ausencia explícita; se conserva como histórica, no como aceptación de los bytes finales.

Aceptación final: **582 correctas,3 deselecciones,8 avisos,368,33 s**; memoria inicial12.692 MiB. El propietario PostgreSQL verificó identidad, cerró y acreditó ausencia de su contenedor en384,832 s; los14 hashes de fuente permanecieron iguales. Arquitectura:5 contratos conservados,0 rotos. La revisión final Claude condicionó el GO al contador de bajas; se corrigió y Luna revisó ese ajuste y la ausencia explícita sin nuevos bloqueos. Conocimiento guardado mediante `save-20261025-caos-herdr-ghost-retirement`; lint estricto correcto. Hashes y recursos están en el JSON. Incluye sesión/Flow, T145/T146/T150, creación y borrado, KAS, API/MCP, canal remoto, Herdr y gestor/publicación. Los archivos de prueba son temporales y los backends controlados. Se mantienen cobertura, deselecciones y límites; no se ejecutan gates manuales aplazados ni agentes/modelos nativos.

Subsistemas revisados: descubrimiento Herdr, registro/routing, caché de proveedores, desmontaje/filas/sidecars, sesión/Work y cancelación. Contratos: prueba de ausencia antes de efectos, retención ante negativa, tupla capturada, orden monitor/gestor, parada real del trabajador, CAS de routing y ausencia de historial. No cambia esquema ni API pública; no se requiere un contrato Pact nuevo. Graphify se consultó sin regenerar sus12 cambios ajenos y se contrastó con fuente. Las revisiones Claude/Luna fueron de lectura; Codex ejecutó las pruebas.

## Límites y siguiente pendiente

El traspaso protege routing actual; no es generación/CAS distribuida sobre fila, ficheros o proceso nativo. La fase de panel stale conserva su ausencia anterior de bloqueo de sesión y depende de metadatos/prueba exacta. Las consultas iniciales y la retención de tombstones diferidos siguen teniendo trabajo síncrono en el bucle y una captura por etiqueta anterior a este cambio; no se afirma offload completo ni corrección de toda evidencia histórica. El ejecutor compartido conserva su capacidad y esperas; no se inventa un pool nuevo.

La revisión detectó además la inversión **previa** sesión→dispatch de Herdr frente a dispatch→sesión al crear con `fence_parent=True`. El timeout de dispatch existente de10 s limita la espera, pero no demuestra progreso oportuno. T152/FR130/SC114 queda sin marcar para reproducir y corregirla sin reducir límites. La tarea todavía no representa una función implementada ni un fallo runtime acreditado.

T143 conserva SQL directo, generación/reutilización ABA, bases distintas/otros procesos, workers escapados, worktrees y límites de publicación/limpieza KAS. No se acredita aceptación nativa actual de OpenCode/Codex/Claude: deben repinarse lanzadores con todas las fuentes, incluido `services/provider_lifecycle.py`. Credenciales, instalaciones globales, QEMU y jobs históricos sin cambios.
