# T076: revalidación del contrato externo Work en la fuente actual

Fecha: 2026-10-22. Fuente fijada: `aef644058efb5d9cc868bfa8e94322ba5c1dcc9b`. FR059/SC043. No se ha reproducido un defecto nuevo de producto.

## Contratos y evidencia previa separados

| Contrato | Evidencia existente | Límites |
| --- | --- | --- |
| MCP nativo `send_message`, v1 | R23 en `75b2ad42`, dos terminales y mensajes, PostgreSQL/AG-UI/reinicio | GLM explícito; no acredita Work ni todas las operaciones/perfiles |
| MCP nativo `send_message`, v2 | R13 en `c93b1be7`, dos mensajes y recibos durables tras reinicio | No acredita Work ni la matriz completa de autenticación/modelos |
| Externo `cao.work.message` | R4a en `2b3cb03c`, dos trabajadores Docker reales, replay y reemplazo autorizado | Sin inferencia nativa de OpenCode; recibo histórico conservado |

La comparación del manifiesto original, cuyo hash coincide con el recibo público, encontró 45 de 46 archivos de fuente idénticos y ambos lanzadores idénticos. Cambió `api/main.py`: los propietarios AST afectados son CreateTerminalBody, CreateSessionBody y create_terminal_in_session por los campos de descubrimiento de T068. No se alteraron los propietarios de ejecución Work, pero un hash de archivo distinto impide atribuir automáticamente aceptación a esta fuente.

## Preparación y criterios

Se creó otra copia privada; sus dieciséis archivos fijados son idénticos byte a byte al recorrido R4a. El manifiesto nuevo fija los 46 blobs actuales, los dos lanzadores y esos dieciséis archivos. Se mantuvo cerrado hasta terminar la regresión y verificar memoria/identidades; la apertura quedó registrada con su hash propio. La revisión de Claude fue de lectura y aprobó el orden de apertura, sin ejecutar comandos.

Se preservan dos procesos vivos antes de cada envío, dos mensajes Unicode y ACK, identidad durable pese a request-id nuevo, efecto incierto tras respuesta perdida, admisión explícita de reemplazos con autoridad actual, SSE redaccionado, reinicio de API, cierre en 25 segundos, mínimo de 6144 MiB y limpieza completa del namespace propio. No se actúa sobre targets o jobs históricos.

Los controles puros de runner y controlador pasaron con la copia cerrada. La primera preparación dejó la lista de paths pendientes vacía aunque el control de plantilla cerrada exige dos entradas: se corrigió únicamente el manifiesto y el mismo código pasó. Un control histórico auxiliar dependía de una ruta relativa al baseline anterior; no forma parte del recorrido actual ni se atribuye como evidencia actual. El Git bajo el entorno mínimo real y la importación desde src actual se verificaron; el intérprete continúa fijado a Python 3.13.15. PostgreSQL y la imagen base Docker existentes mantienen las identidades esperadas.

## Resultados locales y preflight

Pasó la unión de cuarenta módulos Work: los 34 de la regresión T066 y seis módulos de contratos de mensaje/PostgreSQL/origen/AG-UI/API/Docker. Se conserva la cobertura configurada y se incluyen pruebas de integración mediante el filtro `not e2e`. No equivale al backend completo ni subsana el gate global NFR histórico.

La selección terminó con **620 passed, 49 warnings en 808,80 s**. La composición automatizada pasó con **412 archivos, 1834 dependencias, 5 contratos kept y 0 broken**. Los logs completos se conservan en el directorio privado T076.

Hubo dos rechazos anteriores a la provisión: primero el entorno de llamada omitía `VIRTUAL_ENV`; después la metadata histórica de PostgreSQL conservaba un puerto publicado anterior. La reproducción de solo lectura aisló el segundo fallo en `fixtures._service_from_metadata_path`: host, contenedor, imagen, run-id, perfil y ruta de contraseña coincidían, pero `published_port` no. Se conservó intacta la metadata histórica y se escribió una copia privada cambiando únicamente ese puerto por el de la inspección actual. El controlador volvió a verificar toda la identidad y una consulta administrativa de solo lectura pasó. El directorio del intento rechazado estaba vacío antes de los diagnósticos; no se había creado el ledger ni el namespace Work. El mensaje genérico de targets retenidos no demuestra que ese intento provisionara recursos.

Con 7430 MiB disponibles y el preflight de imagen correcto, se inició el replay con la metadata actualizada, el mismo código privado y los pins de `aef64405`. Terminó con aceptación y limpieza verificadas; véase el resultado final siguiente. No se ha cambiado fuente de producto ni relajado ningún gate. T017 de Spec024 y T011 de Spec025 no se cierran con una selección acotada ni con un trabajador Docker sin inferencia. La revisión de composición T018 conserva su alcance propio.

## Resultado final: aceptación externa acotada

El replay real pasó en **184,418 s**, con **7295 MiB** disponibles en el preflight del runner (mínimo 6144). El [recibo público redaccionado](validation-t076-external-receipt.json) vincula la fuente, los pins, el runner sin cambios y los hashes de los recibos privados. No atribuye esta ejecución al commit documental posterior.

Se observaron dos trabajadores Docker vivos antes de cada envío; dos mensajes Unicode, dos eventos de envío y dos ACK de generación 2. Tras perder deliberadamente la respuesta del primer envío, el replay cambió request-id pero reutilizó message-id. El AG-UI durable reprodujo una sola vez el evento original desde el cursor anterior. Ambos reemplazos pasaron por resolve_replacement/admit_replacement con autoridad actual; el receptor sustituto hizo ACK del primer mensaje antes del reemplazo del emisor. El cierre de API conservó el gate de 25 s del runner fijado.

El controlador terminó en complete, exit0, owned_targets_removed y grupo del runner ausente. El lead repitió ocho comprobaciones exactas de ausencia Docker (cuatro contenedores y cuatro imágenes), verificó ausencia de los dos grupos API, configuración y copias de credenciales del controlador, y contrastó todos los pins actuales. PostgreSQL antes/después es idéntico. No se actuó sobre ningún job histórico. La revisión final de Claude fue de lectura y aprobó cerrar únicamente T076.

Limitaciones del harness: al cerrar SSE, dos hilos del lector privado registraron AttributeError en http.client; no cambiaron el resultado ni los eventos durables. El directorio privado runtime-home conserva identidad local efímera y archivos que apuntan a secretos ya eliminados; no se publica ni se declara borrado integral de toda la evidencia privada. La limpieza acreditada es la del namespace/targets/recursos propios y las copias de credenciales indicadas por el contrato.

## Revisión de composición

**PASS con alcance limitado.** Fuente de producto sin cambios en T076. Se revisaron WorkMessageService.execute/_send/_ack, WorkMessageRepository, el orden de locks/reauth de job, generación e idempotencia, y la proyección AG-UI metadata-only. Inserción y evento comparten transacción; el ACK condicional y su evento conservan atomicidad; el replay estable se resuelve antes de exigir receptor vivo. La proyección usa delta vacío e identidades durables sin cuerpo. La unión de 40 módulos incluye transacciones PostgreSQL reales, mensajes, origen, API, Docker y AG-UI; el recorrido real cruzó API→Docker→proxy→PostgreSQL→SSE y reemplazo de procesos. Los cambios previos de descubrimiento en main.py se verificaron mediante AST y los contratos actuales. Composición final: 5 contratos kept, 0 broken. No hay nuevas dependencias, esquemas o migraciones en esta entrega; no se regenera Graphify ni se altera su trabajo ajeno. Se revisó el diff documental y el recibo redaccionado.

T076 queda cerrado. T017 de Spec024 conserva la exigencia de suite completa; T018 conserva su cierre integral propio, y T011/OC-05 continúan abiertos para los recorridos nativos/proveedores, perfiles/modelos y gates globales restantes. Un trabajador Docker genérico no acredita inferencia nativa de OpenCode, Codex o Claude.
