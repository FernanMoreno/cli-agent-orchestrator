# T077 — revalidación nativa tras la corrección del lector privado

Fecha: 2026-10-22. Fuente preparada: main 8057fcc6. Estado: preparación cerrada; todavía no acredita una ejecución nueva.

Cada inventario histórico de 507 archivos (v1 R23 y v2 R13) difiere en un único archivo actual: providers/opencode_cli.py. La comparación de fuente sitúa el cambio en _copy_private_state_file y _state_file_metadata (T074): aperturas no bloqueantes antes de verificar archivo regular, cierre de descriptores en fallos y eliminación solo del destino creado por el intento. Los otros 506 hashes coinciden. T075 cambió fixtures offline que no forman parte de estos inventarios. Work T076 ya pasó por su contrato distinto, sin inferencia nativa.

Se preparan copias privadas cerradas con el modelo explícito GLM-5.3-Flash y los criterios anteriores. No se atribuye aceptación LongCat ni se repite su rechazo sin una señal distinta. La matriz de perfiles, autenticación, concurrencia y aceptación global permanece separada. Los recibos y resultados nuevos se registrarán cuando se ejecuten y revisen; no se cierran T011/T013/OC-05 por esta preparación.


Preparación y controles, 2026-10-22: la copia v2 mantiene el AST de aceptación de R13, normalizados solo HEAD, rutas, hashes y etiquetas. El self-check actual pasó 202 controles puros, 5 de catálogo, 8 de rango de salida, 10 de R10, 13 de R11 y 35 de marcadores. Una segunda ejecución registró el SHA del runner y los pins antes/después, HEAD y fecha; no arrancó proveedor. La revisión independiente de Claude admite la preparación v2, pendiente de apertura y ejecución. Los 1.301 controles históricos de reinicio están identificados como históricos, no como una ejecución nueva.

El gate v1 exige una generación nativa completa ligada al mismo HEAD. El recibo histórico R23b está ligado a 75b2ad42 y no satisface 8057fcc6; se prepara una nueva generación con el mismo modelo y límites, antes del recorrido administrado. No se reetiqueta el recibo anterior ni se omite el requisito. Se verificaron en vivo los hashes de ambos binarios y la identidad, imagen, estado y puerto del PostgreSQL existente; las ejecuciones reales siguen pendientes.

La auditoría de OC-06/T013 señaló tres riesgos para reproducir después del recorrido fijado: ORDER BY del inbox genérico sin desempate de ID, exclusión de entrega limitada al proceso y copia síncrona de DB/WAL desde inicialización async. Son hallazgos de fuente; todavía no acreditan duplicado, desorden observado ni una latencia medida. No se cambia código del proveedor durante esta revalidación.

Además, se ejecutaron 1.288 aserciones de readiness e identidad durable sobre el candidato T077 actual, con reloj, HTTP y SQL simulados. Pasaron sin servicios ni modelos; el resultado queda vinculado al SHA del candidato. La comparación estructural histórica R12/R13 no se cuenta como una prueba actual.


Verificación adicional de root: 41 regresiones de copia de estado y aislamiento v1 pasaron en 14,87 s; 61 controles v1 y 26 del observador pasaron sobre el paquete cerrado final. La generación quedó cerrada con 30 controles puros/estáticos y una variante propuesta que cambia únicamente las dos constantes de ejecución; ambos hashes quedan preparados para su revisión antes de abrirla. Se corrigió en ese lanzador privado la discrepancia entre el nombre protegido contra sobrescritura y el nombre del recibo escrito. El gate de composición pasó: 412 archivos, 1.834 dependencias, 5 contratos mantenidos y 0 rotos.

El diagnóstico privado de OC-06 reprodujo dos envíos de una misma fila al forzar dos instancias reales de InboxService sobre un almacén simulado compartido. La topología documentada utiliza un singleton por servidor; el resultado demuestra una limitación condicional de varios consumidores y no un incidente de la instalación actual. La compilación SQL confirmó que ORDER BY solo incluye created_at; no se observó reordenamiento efectivo en PostgreSQL. Ambos puntos requieren su siguiente tarea de concurrencia y no cambian la aceptación de T077.


Generación real v1: PASS en main 8057fcc6, salida natural 0, Unicode exacto, step_finish, petición con modelo/auth coincidentes, limpieza y preservación del operador. Recibo privado SHA-256 02af1c5569d906927679130f457817781454904cfd1f27c7716391cc4d237499. La primera ejecución administrada llegó a ambos terminales preparados, pero rechazó la admisión antes del primer mensaje: 6.024 MiB frente al mínimo de 6.144. Terminó con código 1, cero fases, limpieza propia completa y credenciales intactas. Se conserva ese fallo; no cuenta como aceptación.

Se identificaron 17 árboles duplicados de transportes MCP Playwright inactivos bajo el mismo proceso Codex. Antes de SIGTERM se verificaron UID, padre, inicio, hash del comando, CPU invariable y árbol exacto sin navegadores o CLI de agentes; se confirmó la salida de sus 51 procesos. Se mantuvo el transporte principal. Las muestras de memoria disponible fueron 7.531→7.832 y 7.713→8.041 MiB; son observaciones de recuperación, no un resultado NFR. R2 usa otro directorio/recibo y el mismo runner byte por byte, requisito de generación actual y límites intactos. Su resultado sigue pendiente.


Durante R2 se identificaron además 17 transportes duplicados del bridge Codex/Claude, todos hijos propios del mismo Codex y sin CLI hijos. Se verificaron identidad, hash del comando y CPU invariable antes de SIGTERM mediante pidfd; las 17 salidas quedaron confirmadas y se mantuvo el bridge activo. La muestra disponible pasó de 6.230 a 6.437 MiB durante el recorrido. No se detuvo una sesión Claude ni se tocó su login. Los mínimos de admisión permanecieron activos.


R2 terminó FAILED (135,596 s): la fase 0 persistió un mensaje y observó llamadas list_siblings/send_message completadas; el turno alcanzó verified y completed 1/1. Durante los 75 s, LAST no contuvo el recibo Unicode exacto. El resultado no acredita reinicio ni fase 1. Se verificaron limpieza de API/tmux/PostgreSQL, cero raíces privadas, preservación del operador y fuente. No se atribuye el fallo al proveedor ni a extracción sin observar la forma del resultado. La evidencia conservada permite afirmar la discrepancia, pero no reconstruir el texto final, que deliberadamente no se guardó.

Tras confirmar esa limpieza se inició v2 como validación independiente y serial; un PASS de v2 no cerraría v1 ni T077 completo. Se prepara T078 para clasificar sin texto la salida LAST y el viewport/extractor actual, conservando prompt, modelo, plazo y aceptación. [Recibos sanitizados](validation-t077-native-receipts.json).


V2 terminó FAILED en 133,769 s: el emisor pasó Unicode LAST y turno durable en 18,833 s; faltó el ACK/finalización del receptor a 75 s. La captura de fallo terminó en 48 ms: receptor reconcile con receipt_result_unverifiable, tres intentos, fila de recibo sent y generación coincidente. El mínimo de memoria admitida fue 7.447 MiB. No se llegó al reinicio ni a la segunda fase. API/tmux/PostgreSQL y estado privado se limpiaron; operador, fuente, binario e intérprete se preservaron. Se mantienen las capturas privadas del receptor para distinguir salida nativa y parser sin atribución prematura. Todos los lanzadores propios T077 quedaron cerrados después, conservando bytes ejecutados y recibos.

Resultado conjunto: T077 NO aceptado. La generación aislada y los controles offline pasaron; los dos recorridos administrados actuales fallaron por fronteras distintas. Las aceptaciones históricas permanecen ligadas a sus fuentes. No hay PASS actual que cierre v1/v2, OC-05/06 o la matriz/global.


Actualización2026-10-23: el diagnóstico T078 posterior pasó el recorrido v1 completo en8057 (dos mensajes,Unicode,AG-UI,reinicio y limpieza), sin reproducir el fallo R2; su causa sigue indeterminada. T079 v2, con perfil compatible y ACK obligatorio, falló y originó la corrección T080. Estos resultados posteriores no reetiquetan las ejecuciones T077 anteriores ni cierran los gates globales. Véanse [T078](validation-t078.md) y [T079](validation-t079.md).
