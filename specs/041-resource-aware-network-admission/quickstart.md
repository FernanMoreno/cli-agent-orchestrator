# Validation guide — 041

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
