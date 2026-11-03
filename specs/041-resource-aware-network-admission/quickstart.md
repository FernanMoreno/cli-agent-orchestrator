# Validation guide — 041

H2.2b.2, resultado 2026-11-03:320 PASS/20 módulos;286 IDs previos relevantes+34 nuevos. El primer ciclo ready no acredita capacidad: esperar la señal del monitor real con recuperación intacta. PostgreSQL, cancelación y poison contrastados; evitar repetir close sobre un actor retirado cuyo error terminal es esperado. [Recibo](validation-t014-original-control-owner.json), [composición y límites](composition-t014-original-control-owner.md).

H2.2b.2: mantener separadas las pruebas unitarias del actor con contextos instrumentados y el recorrido root firmado/PostgreSQL real. Verificar mismo PID/Thread/task/session/driver/backend y más de veinte solicitudes, no veinte modelos. Observar cargo de mensajes antes de visibilidad, denegación por recursos, cancelación/poison, readiness sin capacidad y cierre sin crédito prematuro. Startup ausente/otro ledger/shared-unready no abre SQL. Reutilizar keeper/perfil PG y serializar los casos reales; no usar los ejemplos unitarios como emisor productivo.

H2.2b.1: antes de la regresión, reproducir API ausente en test/services/test_admission_command_thread.py. Usar barreras/eventos, no sleeps, para observar cargo previo a entrada, retorno previo a salida, start fallido y join tardío. Conservar pruebas de falta de recursos, presión y capacidad desconocida. No interpretar hilos de callback como modelos ni como actor/CLI productivos. Declarar separadamente las pruebas PostgreSQL reutilizadas y los casos nuevos sin SQL.

1. Leer spec/plan/tasks y el manifiesto del intento. Verificar main, estado git, memoria y dueño/identidad exacta del PostgreSQL existente. No iniciar contenedores alternativos ni modelos.
2. Ejecutar los controles puros de política y reservas con COVERAGE_FILE y temporales privados. Esperado: adaptación por CPU/memoria/almacenamiento, desconocido/caducado denegados y reservas iniciadas retenidas tras cancelación.
3. Tras integrarM1, ejecutar la selección InboxService/API/contexto/orden/barridos/drenaje y sampler. La profundidad de cola y4workers de una fixture no se presentan como límite de producto.
4. Con código/runtime congelados y memoria>=6144MiB, ejecutar la aceptación real deM1 y conservar fuente, valores de política, actividad y cierre. Los deadlines originales no cambian.
5. M2 exige competencia real entre dos coordinadores sobre PostgreSQL, reservas atómicas, límites combinados y fallos inciertos. M3 exige incorporación real de nodos y encargo ordinario completado; no basta broker con Deployment creado.
6. Ejecutar `project-composition-check "$(cat .ai/project-name)"`, revisión, diff y verificación antes de integrar. Mantener pendientes los hitos sin ejecución; conservar la diferencia entre controles offline y aceptación real.

## Control del Bridge implementado en T010

En el despliegue del Bridge, CAO_ADMISSION_POLICY_FILE activa el mismo controlador que usa el API. Debe ser ruta absoluta a un archivo regular privado del operador, sin enlaces; contiene política, no credenciales ni claims de jerarquía. Sin la variable permanece compatibilidad; una configuración indicada inválida no habilita fallback de INPUT. Se necesita autenticación JWT ya configurada, esquema ResourceEnvelope aprovisionado y owner/writer separado explícito. init_runtime_db verifica dependencias y no crea autoridad.

Antes de conectar el canal, Bridge abre el control exclusivamente en127.0.0.1 con puerto efímero y anuncia su URL al operador en el log de arranque. La propiedad AdmissionControlServer.url permite consumo directo desde composición; los programas no deben extraer un contrato del texto del log. El ready file conserva solo PID+newline. GET/POST /admission/resource-envelope y POST /admission/resource-envelope/revoke requieren JWT admin cao:admin; el token de canal no sirve. Los DTOs y CAS son los mismos del API. Registro con cobertura incompleta mantiene capacidad cero; cambiar permisos del cliente no sustituye autoridad del servidor.

El cierre cierra admisión, espera lector/handlers/INPUT y sockets, retira actores de fondo y después HTTP/control/monitor. Esto no ejecuta aceptación M1: aún corresponden T011 y el intento físico T019 con fuente/política/mediciones congeladas. Véanse validation-t010-control.json y composition-t010-control.md.

Integración de código T001–T011 validada: los controles Inbox ahora comparan presupuestos variables sin convertir valores de fixtures en límites productivos. El siguiente gate M1 es T019: manifiesto nuevo con fuente/runtime/política congelados, calibración por invocación serial, contraste independiente de recursos/ámbitos y cierre físico. Los173controles T011 no sustituyen ese intento ni autorizan claims de jerarquía sin evidencia.


## Activación compartida T013

Configurar CAO_ADMISSION_MODE=shared junto con CAO_ADMISSION_POLICY_FILE en el entorno de despliegue; es selección fría y no se deduce de perfiles/URIs. La política privada y el JWT admin siguen siendo requisitos distintos. Preparar migraciones vigentes resource_admission y provisionar explícitamente dominios/membresías/créditos con escritor autorizado antes de activar: ni el selector ni el endpoint crean autoridad. Las entregas permanecen denegadas mientras no exista confirmación.

Con JWT admin cao:admin enviar POST /admission/shared/activate y cuerpo JSON {"version":1} al API o control loopback del Bridge. Respuesta version1/activated=true confirma el control de ese lifetime, no observación física de red; repetir en el mismo runtime es idempotente. Fallo devuelve503 fijo sin habilitar datos. Campos extra o tipos convertidos se rechazan422. Un bind fallido deja el coordinador detenido y retenido para drenaje; retirar/reconstruir el lifetime antes de reactivar.

Esta ruta no sustituye configuración versionada PostgreSQL/SDK/UI T022/T023 ni aceptación física T019/T020. Véanse [validación](validation-t013-production-activation.json) y [revisión](composition-t013-production-activation.md).


## Prerrequisito de muestreo T019

El sampler contrasta mnt_id real de los descriptores con mountinfo, conserva los directorios para leer controladores y descarta cambios de vista. La sonda readonly vigente comprobó resolución/cierre pero mantiene cobertura parcial; no permite reclamar ancestros invisibles, autoridad de almacenamiento o aceptación de carga. Para T019 aún se necesitan lecturas independientes desde el host del árbol de ancestros y el negativo de un límite real más estrecho en carga propia controlada. No cambiar límites de trabajos ajenos ni sustituir ese gate por fixtures. [Validación](validation-t019-cgroup-visibility.json).


## Descubrimiento T014 de backend/SPI

ResourceDomainAuthority.outstanding_resources exige Principal JWT admin verificado y devuelve SharedDebtPage inmutable; el caller de composición debe poseer su propia sesión de control financiada. Pasar after=page.next_cursor continúa por keyset; None solo acaba ese barrido, nunca demuestra ausencia global, muerte o cierre físico. Reiniciar desde after=None descubre inserciones UUID anteriores al cursor. El resultado conserva cabeceras y no autoriza liberar/adoptar cargos. No hay endpoint HTTP/SDK/UI de este inventario en este corte. [Validación](validation-t014-debt-discovery.json).


## T014 — catálogo HTTP y aborto previo al efecto

El estado anterior de catálogo solo SPI corresponde al checkpoint anterior. Ahora POST /admission/shared/debt requiere JWT admin y runtime shared activado con su propio crédito; usa version1/limit1..1000/after opcional. El cursor es descriptivo. Para abortar un preparing1 mapeado, conservar primero un action_id UUID y enviar version1/action_id/operation_id/expected_revision1 a POST /admission/shared/abort-preparing. Respuesta confirmada conserva ambos UUID y released/revision2; ante503 o cancelación, repetir exactamente el mismo action_id/payload para reconciliar el acuse. Nunca interpretar el error o una página vacía como rollback/liberación.409 exige refrescar el contexto; no reejecutar trabajo. No usar este endpoint para active/uncertain/metadatos ni jobs históricos. Código/esquema de participantes deben coordinar migración5; no rolling upgrade transparente. [Validación](validation-t014-preparing-abort.json).


## Cierre de Work nativo

El puerto productivo registra el token del efecto externo automáticamente. Un backend confiable declara su ciclo completo con observe_native_effect, señala begin_native_effect antes del primer arranque posible, conserva handles con retain_native_effect_owner y emite confirm_native_effect_closed solo tras su prueba nativa completa. Nunca emitir ese acuse desde un finally genérico ni desde el mero retorno/error del callback. SQL, proceso y helpers tienen cierres distintos; un native owner incierto conserva costes y receptor. Esto no introduce un límite de agentes ni sustituye la recuperación durable T014. [Validación](validation-t014-work-physical-ownership.json).


## Custodia después de un cierre físico persistido

Con el runtime shared ya activado y el control del custodio financiado, un JWT admin puede consultar POST /admission/shared/debt. Solo una operación cuyo coordinador original haya registrado el cierre puede finalizarse por POST /admission/shared/release-witnessed: version1, UUID de acción persistido antes del envío, UUID de operación y revisión esperada del cierre. La revisión depende del historial; no asumir siempre3. No enviar binding, actor ni un supuesto testigo. Un estado releasing del inventario no demuestra por sí solo elegibilidad.

Ante200, comprobar los mismos UUID, state=released y revisión+1. Ante503/cancelación/confirmación perdida, repetir la misma acción y payload; nunca volver a ejecutar Work como reparación. Ante409 conservar el estado y distinguir resource_closure_unproven de resource_operation_conflict; ni ausencia del PID ni fin de página conceden permiso. Los créditos de metadatos siguen cargados. La aceptación con SQL/HTTP y procesos propios se detalla en [validación](validation-t014-closure-handoff.json); no sustituye los gates físicos M1/M2/M3.


## Helpers que terminan después del join

El backend debe conservar cada handle exacto y confirmar solo su cierre nativo completo. Esa confirmación no elimina los observers de readers/proxy: si siguen vivos, el token queda registrado en su propio DeliveryAdmission. Monitor, coordinador compartido y stop reobservan automáticamente; no hace falta token.finish manual ni existe una nueva herramienta MCP para forzarlo. Un stop puede conservar retired=False mientras quede helper; tras su terminación positiva un siguiente intento puede cerrar, sin borrar el cargo.

Un contenedor/árbol realmente incierto no entra en ese registro por el mero fin de Popen, por tiempo o por ausencia de owners. El resultado clean del registro no demuestra cierre de esos otros holders. Work puede permanecer reconcile aunque CPU/memoria/sesiones ya hayan vuelto; no reejecutarlo ni cerrar jobs históricos como reparación de recursos. [Evidencia](validation-t014-native-late-retirement.json).


## Ciclo terminal y sello C1

El Bridge configurado para admisión compartida usa la misma raíz de composición con ciclo terminal explícito. Detiene transporte/coordinador, une el control aceptado y registra el sello con su crédito de control todavía financiado; después cierra y drena todos los participantes. El stop normal de API no sella: reutiliza la misma generación/crédito al reconstruir el lifetime. No existe un nuevo endpoint ni ajuste de modelo para forzar un sello o retirar un crédito.

El esquema resource_admission vigente pasa a7 mediante bootstrap controlado del dueño; runtime solo verifica. Una generación sellada niega nueva cobertura/reserva/inicio, sin negar lecturas, ACKs exactos o cierre original. Si falta coordinador/control o hay cierre incierto, no se infiere sello ni cierre y el crédito sigue cargado. Una consulta de diagnóstico/completion todavía financiada tampoco convierte el sello en prueba de fin físico: el custodio y testigo completos siguen pendientes. [Evidencia](validation-t014-generation-seal.json).


## Supervisor Bridge C2a (opt-in, componente validado)

En el entorno Python configurado del proyecto puede lanzarse `python -m cli_agent_orchestrator.runtime_channel.bridge_supervisor` en lugar del entrypoint directo. Requiere CAO_ADMISSION_MODE=shared, CAO_ADMISSION_POLICY_FILE absoluto privado y la configuración normal existente de target/token/URL/autenticación Bridge. No es necesario reinstalar globalmente. El adaptador resuelve el target canónico del padre y elige argv fijo; no se configura ni reutiliza manualmente el FD interno. El hijo publica antes de arrancar su runtime de admisión y el padre verifica el emisor/binding antes del ACK. El arranque SQL/backend que ya precedía ese punto sigue distinguiéndose.

El handshake del adaptador usa30s por defecto; el supervisor común expone receive_binding(timeout_s=...) para composiciones con otro presupuesto explícito. Cambio de vista/ausencia de prueba rechaza el binding conservadoramente. La limpieza une al hijo propio sin declarar cierre por deadline ni enviar señales a un PID reciclado. SIGTERM/SIGHUP/SIGINT pasan por esa limpieza; SIGKILL o pérdida de Popen no aportan testigo. Un despliegue externo directo no se adopta por PID ni se modifica automáticamente.

No se devuelve capacidad de metadatos con este comando: el testigo del líder no demuestra descendientes, sesiones PostgreSQL o generación completa cerrados. El siguiente gate de T014 es el custodio durable, financiado y auditado con el conjunto físico completo y sello original. [Evidencia](validation-t014-process-exit.json).


## T014.C2b.P1 — atribución SQL

El supervisor opt-in activa automáticamente la etiqueta; no configurar manualmente CAO_INTERNAL_RESOURCE_BINDING_FD. Padre/hijo deben estar actualizados a protocolo2. Solo el entorno copiado del hijo pierde PGAPPNAME para evitar contradicción con el nombre explícito; el operador conserva su entorno y todos los demás controles libpq. Sin supervisor permanece caos. No devuelve créditos ni demuestra cierre de sesiones. [Validación](validation-t014-sql-origin.json).


## T014.C2b.P2a — cierre terminal de nuevas aperturas

No hay flag nuevo ni límite de agentes: el Bridge supervisado aplica automáticamente el bloqueo de aperturas al terminar su main, después del intento completo de cleanup. No se reinicia dentro del mismo proceso terminal. Procesos sin supervisor mantienen legado. El bloqueo no mata sesiones existentes ni devuelve créditos; las pruebas reales de inventario/retirada y los gates globales siguen pendientes. [Validación](validation-t014-sql-fence.json).


## T014.C2b.P2b — registro financiado previo a TCP

No hay activación global ni nuevo flag público. El componente trusted usa un Connection original de control dentro del scope activo y SqlIntentRepository.record_openings alrededor del worker físico; el recorder no se propaga automáticamente a workers/pools. La quota se provisiona por dueño explícito antes, con crédito/binding comprobados; el primer TCP de control no queda cubierto. No usar counters/label/lectura como prueba de ausencia o devolución de créditos. [Validación](validation-t014-sql-intents.json).


## T014.C2b.P2c.A — orden del bootstrap supervisado

El lanzador supervisado existente conserva comando/protocolo/configuración. Ahora su handshake ocurre antes del SQL inicial y el bootstrap original se reutiliza más tarde. Sin supervisor, el factory conserva el orden perezoso. No hay activación nueva de intents globales, permiso de bootstrap ni devolución de créditos. [Validación](validation-t014-pre-sql-bootstrap.json).


## T014.C2b.P2c.B — registro explícito previo al acuse

Composición programática confiable: usar owner.receive_binding_with_child_intent(channel=canal_original_pagado, repository=repo, journal_id=..., intent_id=..., destination=target) dentro del worker/scope original. Antes, el dueño aplica la migración2 mediante el mecanismo existente y provisiona quota del padre. No crear otro canal dentro del registro, no transferirlo de hilo y no interpretar el recibo como permiso de sesión. CLI por defecto sigue identidad-only; activación global/root/broker/custodio requieren trabajo adicional. [Validación](validation-t014-child-sql-intent.json).


## Composición explícita del cargo del padre — T014.C2b.P2c.C1

En el worker confiable, reservar e iniciar una reserva de almacenamiento dedicada antes de entrar en el lane de control. En modo compartido, coordinator.prepare(lease) debe confirmar la operación y crear hold; en local, lease.hold_retirement() debe preceder al débito. Dentro del scope original de control ya financiado, pasar session_reservation=lease a receive_binding_with_child_intent. Conservar supervisor.child_sql_session_charge y todo recibo/error; el finally ordinario usa lease.finish() y deja el cargo retenido. No abrir otra conexión para registrar este cargo ni copiar el canal/scope a un broker. Este ejemplo no activa CLI, concede permiso al hijo ni implementa custodia. [Validación](validation-t014-child-session-charge.json).


## Prueba controlada de primera apertura — T014.C2b.P2c.C2

Usar padre/hijo propios y reserva dedicada ya iniciada/preparada con hold. Lanzar first_sql_grant=True, ejecutar publisher explícito3 en el hijo antes de cualquier SQL y recibir con receive_binding_with_child_grant pasando canal original/journal/intento/destino/reserva/path. El hijo puede abrir una sola vez por ese adaptador en el mismo Thread/task; las sesiones existentes conservan uso. No activar automáticamente Bridge: faltan las siguientes sesiones financiadas. Conservar plan/cargo incluso si el hijo rechaza paquete o driver falla; no liberar sin custodio independiente. [Validación](validation-t014-child-sql-permission.json).


## Migración y solicitudes dentro del handshake — T014.C2b.P2c.C3.A

El dueño debe aplicar SQL3 antes de usar el journal; SQL2 se rechaza sin DDL implícito. Durante before_ack, registrar el arranque una vez y conservar su intent_id. Registrar cada plan posterior mediante record_child_followup con UUID independiente y sequence1,2,…; conservar recibos/errores. Tras expirar el handshake se rechaza el sujeto, incluso si quedan descriptores. No copiarlo a otro worker, reabrir el canal ni activar Bridge como si los planes financiaran sesiones. Faltan el broker persistente, financiación por solicitud y custodia. [Validación](validation-t014-child-followup-intents.json).


## Integración en un worker original — T014.C2b.P2c.C3.B

Provisionar SQL4 por el dueño antes de runtime. Crear supervisor, Connection de control y reserva de datos iniciada/preparada con hold dentro del mismo worker y conservarlos allí. Lanzar con sql_broker=True y first_sql_grant=True; el hijo publica su binding explícito antes del primer SQL. Confirmar la startup mediante receive_binding_with_child_grant. Atender solicitudes con serve_child_sql_request y esos mismos objetos; tratar Idle como ausencia temporal de mensaje y reprogramar mediante el scheduler, y None como cierre de transporte, nunca retiro físico. No mover esos objetos existentes a asyncio.to_thread ni crear un hilo por agente. Las aperturas posteriores del hijo pasan por el gate común. Mantener recibos y cargos incluso ante rollback, pérdida de respuesta o salida del hijo; usar reserva dedicada para no retener CPU/receptor innecesarios. La integración de Bridge, el scheduler productivo y el custodio aún no están activados. [Validación](validation-t014-sql-opening-broker.json).


## Llamante original antes de lanzar Bridge — T014.C2b.P2c.C3.C

El operador debe provisionar el journal SQL4 y aportar un worker de control ya financiado. Mantener activos permit.operation y connect alrededor de funded_bridge(channel=..., repository=..., journal_id=..., intent_id=..., session_reservation=...). En el cuerpo, atender solicitudes con serve_child_sql_request desde ese worker hasta cerrar/recoger el hijo; para apagado ordenado, servir también durante su cleanup financiado. No mover objetos a otro thread/task ni crear un hilo por agente. La raíz, su primer TCP y el driver persistente multinodo aún deben implementarse; invocar el CLI actual no activa este contexto automáticamente. [Validación](validation-t014-funded-bridge-startup.json).


## Verificar tipos sin éxito aparente de caché — T021.T

Ejecutar mypy src/ con un directorio de caché privado nuevo y repetir los dos entrypoints Bridge con otro directorio nuevo, manteniendo mypy.ini y las mismas opciones. Conservar logs/códigos de salida y comparar bytes de configuración con la base. Ejecutar los cuatro módulos de prueba listados en el recibo; no sumar casos solapados a ejecuciones previas ni interpretar este chequeo estático como aceptación nativa del proveedor. [Validación](validation-t021-typing.json).


### Alcance del control raíz T014.R.G

Los recorridos del componente usan PostgreSQL real y un proceso raíz aislado con claves efímeras de prueba: emisor propietario ya financiado, registro/commit SQL5, certificado, verificación local, un TCP raíz y journal propio ordinal2 en el mismo canal. Los negativos conservan cargos e impiden acceso al driver cuando firma o kernel difieren; pérdida de readback impide yield y fallo de claves tras commit mantiene el registro. Los tests unitarios con issuer controlado no sustituyen esos recorridos reales.

Para repetir diagnósticos intensivos, usar el lanzador cerrado vinculado a fuentes/runtime y verificar previamente contenedor/coordinador existentes y mínimo6144MiB. Serializar los procesos pytest por cobertura y ownership. El recibo final registra los módulos/casos exactos y el archivo ejecutado. No presentar este recorrido como arranque CLI productivo, aceptación de proveedores/modelos, activación shared completa, custodia, M1/M2/M3 ni SDK/UI.

El principal de los recorridos de génesis procede de la factory interna verificada de pruebas. Estos casos no ejecutan el login ni la validación JWT en vivo del operador.


### Alcance H1 de reutilización

Los recorridos de `test_root_control_ports.py` conservan el arranque raíz firmado y prueban repetición de ledger/lecturas/auditoría/muestreo en el mismo backend con driver count1. Rollback/timeout readonly permiten una siguiente operación solo si el estado original se verifica; ACK o comprobación perdida tras commit conservan deuda y bloquean la sesión. El conjunto de pruebas también mantiene la negativa de activación shared sobre el préstamo vivo. Estos puertos no implementan aún el actor, ResourceBootstrap/EnvelopeRepository persistentes, monitor/CLI productivos ni custodia.


### T014.R.H2.1 — monitor y autoridad originales

`test_root_control_monitor.py` conserva el productor firmado de H1 e inserta el envelope por el owner financiado antes de emitir certificado. El root usa un driver/backend para ledger, lecturas y tres ciclos de monitor. Las observaciones Linux son deterministas; PostgreSQL, proceso raíz, roles, transacciones y presión son reales. Comprueba binding/revocación, target fresco, owner/worker/task ajenos, constructor y authority_read ante lock retenido, reentrada, rollback verificado/no verificable y comprobación postcommit perdida en reader y sampler. No valida muestreo físico M2, actor, wiring CLI/runtime ni activación shared.


### H2.2a — validación de comandos y diagnóstico

Las pruebas offline ejercitan costes/componentes,25 reservas, deuda, cancelación/inicio/finish físico, fork/thread/task/copia, generaciones y presión. Las de contrato HTTP son ASGI en proceso, con principal de factory verificada, sin login real. El rootPG firmado repite el monitor y reserva/inicia/retiene/termina25 comandos sin añadir TCP; mantiene deuda metadata y negativa de activación live-loan. Las observaciones Linux permanecen deterministas. Añadir a la selección los consumidores de diagnóstico/API/HTTP/fábrica además de los casos anteriores. No presentar estos resultados como actor/CLI desplegados,20modelos reales ni M2/SDK/UI global.

H2.3: emitir desde un canal owner ya financiado con quota/credits/envelope reales, separado del root sujeto. Verificar firma/clave/kernel/target antes de muestreo o TCP; observar startup RAM/CPU retenido mientras storage/normal data sigan desconocidos. Releer envelope completo por el canal original antes de ready. Conservar negativos replay/copia/fork/revocación/muestras desconocidas. Los controles instrumentados no aceptan M1 físico ni un emisor de despliegue central; no registrar capacidad conocida artificial para iniciar el actor.

El consumidor opt-in implementado se ejecuta con `python -m cli_agent_orchestrator.runtime_channel.root_control --trust-file /ruta/privada/trust.json --policy-file /ruta/privada/policy.json --scope instancia/control`. Los archivos deben ser absolutos, regulares, del usuario actual y sin permisos de grupo/otros. Trust tiene versión 1 y un objeto `keys` de identificadores a claves públicas Ed25519 de 32 bytes en Base64 canónico; policy usa el contrato de política existente. El modo root debe seleccionarse desde el lanzamiento del proceso: el flag no crea autorización.

La primera línea stdout es `{"type":"subject","value":...}`. El emisor externo valida ese sujeto y entrega por stdin una única línea JSON de activación firmada. El consumidor mide y reserva antes del primer TCP; solo después de verificar el primer ciclo devuelve `{"type":"ready"}`. Acepta líneas `{"type":"refresh"}` y `{"type":"stop"}`; EOF solicita cierre. Un cierre no demostrado devuelve estado 2 y conserva la obligación. No transportar claves privadas por este protocolo. El supervisor financiado y el transporte privado del paquete siguen pendientes; esta entrada no se conecta automáticamente a la API central.
