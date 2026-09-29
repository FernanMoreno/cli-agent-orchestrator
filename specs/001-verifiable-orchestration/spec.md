# Feature Specification: trabajo colectivo verificable

**Feature Branch**: `main` (sin crear rama ni alterar trabajo existente)

**Created**: 2026-09-22

**Status**: Diseño aprobado; implementación incremental en curso. La integración universal y el cierre del programa siguen pendientes.

**Input**: «haz todo ai_workflow.md sobre todos», aplicado a los 19 ejes y seis
pendientes de `docs/aipm-orchestration-roadmap.md`, analizados contra código.

## User Scenarios & Testing

Cada historia es una entrega verificable. R01–R19 identifican ejes originales;
O01–O06 identifican pendientes operativos. Las capacidades parciales no eliminan requisitos.

### User Story 1 - Recuperar trabajo y evidencia (Priority: P0)

Como operador, necesito identificar operaciones e intentos y recuperar resultados después
de perder un terminal o reiniciar el servidor.

**Why this priority**: distingue trabajo terminado, no recibido e incierto.

**Independent Test**: ejecutar launch, inbox, hijo, handoff y workflow con proveedor de
prueba; interrumpir y recuperar cada frontera de entrega y persistencia.

**Acceptance Scenarios**:

1. **Given** una operación admitida, **When** se reinicia antes del envío, **Then** conserva
   identidad, contrato e intento sin ejecución duplicada.
2. **Given** un envío incierto, **When** expira el lease, **Then** se declara conciliación
   y no se repite automáticamente.
3. **Given** un resultado validado y persistido, **When** se elimina el terminal, **Then**
   el padre recupera resultado y estado de limpieza.
4. **Given** cancelación o reemplazo de intento, **When** llega evidencia antigua, **Then**
   no revive trabajo cancelado ni sobrescribe el resultado vigente.
5. **Given** un consumidor desconectado, **When** retoma eventos, **Then** recupera orden
   durable o huecos explícitos sin transiciones inventadas.
6. **Given** un launch, hijo, handoff o step gestionado, **When** se reinicia tras la
   admisión, **Then** recupera el vínculo exacto entre origen, intento y generación;
   una entrada histórica sin ese vínculo no adquiere autoridad nueva.
7. **Given** una entrega enviada sin acuse del receptor, **When** cambia el terminal
   o se reinicia el servidor, **Then** no se infiere recepción ni se reenvía por
   esa sola ausencia; sólo un acuse autenticado y durable cambia ese estado.
8. **Given** un intento Work gestionado, **When** su proxy MCP sobrevive al reinicio,
   **Then** conserva acceso sólo mientras siguen vigentes el binding, grant,
   generación y lease exactos; si se pierde el proxy, queda en conciliación sin
   reemitir credencial ni entrega.

### User Story 2 - Ejecutar con autoridad y recursos acotados (Priority: P0)

Como operador, necesito elegir proveedores, permisos y presupuestos y evitar sobrescrituras
entre agentes o ampliaciones de privilegios.

**Why this priority**: ejecutar en paralelo requiere efectos autorizados y recursos reservados.

**Independent Test**: someter dos jobs con presupuestos y reservas incompatibles; verificar
rechazo antes de asignar recursos, reparto justo y revocación durante ejecución.

**Acceptance Scenarios**:

1. **Given** un job con allowlist, **When** un hijo solicita un proveedor excluido, **Then**
   se rechaza antes de lanzar procesos aunque el catálogo conozca ese proveedor.
2. **Given** un grant revocado, **When** se repite una petición o se cambian parámetros,
   **Then** no se autoriza un nuevo efecto y queda registrada la decisión.
3. **Given** dos escritores de una ruta, **When** compiten, **Then** sólo uno obtiene
   permiso de escritura compartida; el otro espera o usa aislamiento.
4. **Given** dos jobs elegibles de igual prioridad, **When** se liberan plazas, **Then**
   ninguno obtiene dos turnos consecutivos mientras el otro no ha recibido uno.
5. **Given** dependencias circulares, **When** se solicita planificación, **Then** se
   rechaza el ciclo identificando sus participantes.
6. **Given** un launch provisionado, **When** el cliente cambia selector, job, grant,
   contrato o snapshot, **Then** sólo la provisión vigente del operador autenticado
   determina la autoridad efectiva; un dato del cliente no la amplía.
7. **Given** un hijo o step de workflow, **When** se admite o se revoca su origen,
   **Then** usa identidad y grant propios explícitos, acotados al job y al padre;
   nombre, sesión y caller ID no prueban procedencia.
8. **Given** un padre autenticado por su intento Work, **When** solicita hijo o
   handoff, **Then** sólo elige referencias de hijo/receptor preprovisionadas que
   el servidor valida contra permisos vivos; no obtiene sus Principals ni
   credenciales, y cada actor se autentica por separado.
9. **Given** un backend sin aislamiento probado de proxy y descriptor por intento,
   **When** se prepara ejecución Work, **Then** se rechaza antes de crear sesión
   o ventana; un token en entorno o un backend legacy no satisfacen el contrato.
10. **Given** un backend Work Linux candidato, **When** se comprueba su frontera con
    intentos hermanos y políticas de rutas, comandos, red e IPC, **Then** sólo se
    admite el intento si cada dimensión del contrato efectivo se impone durante
    launch, efectos posteriores y recuperación; una dimensión no demostrada o
    un intento revocado se rechaza antes del efecto.
11. **Given** un launch Work durable, **When** el dispatcher lo ejecuta, **Then** el
    intento recorre exclusivamente el backend Work registrado, liga ACK, identidad,
    GO y cleanup al item/intento/generación/revisión/contrato vigentes, y cada
    `exec` descendiente sólo alcanza contenido ejecutable inmutable del contrato.
    Loader invocado, shebang, `execveat`, memfd, fork/doble fork y FDs se rechazan o
    quedan confinados de forma comprobable; Tmux/Herdr no son fallback. Un cleanup
    incierto requiere conciliación y nunca redelivery automática. T097/C08
    quedaron aceptadas en el perfil Ubuntu QEMU guest fijado en el plan. Como
    el proyecto no tiene ni prevé un host de despliegue, el backend permanece
    sin registrar por decisión de alcance y Work no se habilita. Cualquier
    despliegue futuro requiere una tarea nueva y preflight en su host concreto.
12. **Given** un host Linux que ejecuta Bubblewrap Work, **When** se crean namespaces
    y proxies por intento, **Then** el componente Work corre con una cuenta de servicio
    no root, no interactiva y exclusiva; herramientas autorizadas cruzan sólo por un
    proxy nuevo por intento, dos intentos concurrentes no comparten socket, secreto ni
    respuesta, y un efecto incierto queda en conciliación sin replay.
13. **Given** un candidato Bubblewrap Work, **When** ejecuta `preflight_work`,
    **Then** exige `CAO_WORK_BROKER_ACCOUNT` configurada, rechaza UID efectivo root,
    UID configurado root, cuenta inexistente, UID efectivo distinto o shell interactiva;
    estos rechazos ocurren antes de consultar Landlock o ejecutar/probar Bubblewrap.
    Sólo permite continuar si el UID efectivo coincide con la cuenta OS configurada y
    su shell es `nologin` o `false`.

### User Story 3 - Compartir conocimiento revisable (Priority: P1)

Como participante, necesito distinguir propuestas de conocimiento aprobado y demostrar
qué contexto recibió cada agente sin exponer secretos ni otros ámbitos.

**Why this priority**: memoria no revisada o cambiante no debe convertirse en autoridad implícita.

**Independent Test**: publicar y revisar conocimiento, congelar contexto, modificar memoria
viva y arrancar otro hermano; comparar identidad y contenido del snapshot acordado.

**Acceptance Scenarios**:

1. **Given** una afirmación propuesta, **When** otro agente solicita instrucciones,
   **Then** no se incorpora como instrucción compartida aprobada.
2. **Given** un snapshot congelado, **When** cambia memoria viva, **Then** hermanos y
   continuaciones que comparten snapshot reciben la misma revisión.
3. **Given** un secreto o datos de otro ámbito, **When** se publican, consultan o exportan,
   **Then** la política impide exposición y registra acceso sin copiar el secreto.
4. **Given** una fuente sustituida o borrada, **When** se consulta conocimiento dependiente,
   **Then** su invalidez es visible y no se reutiliza silenciosamente.

### User Story 4 - Continuar e intervenir sin duplicar efectos (Priority: P1)

Como operador, necesito pausar, aprobar, revocar o trasladar trabajo a otro proveedor
admitido conservando contrato, resultados y responsabilidad de cada decisión.

**Why this priority**: cuota y aprobación no deben borrar historia ni límites.

**Independent Test**: pausar después de producir un artefacto y continuar con otro proveedor
de prueba; comprobar integridad, autorización y ausencia de reejecución.

**Acceptance Scenarios**:

1. **Given** un intento pausado por cuota, **When** se prepara continuidad, **Then** el
   paquete distingue trabajo completado, pendiente e incierto.
2. **Given** un artefacto cambiado después de exportar, **When** se intenta continuar,
   **Then** se rechaza su reutilización hasta revisión explícita.
3. **Given** una aprobación sobre una revisión, **When** cambia el contrato, **Then**
   la aprobación antigua no autoriza la nueva revisión.
4. **Given** un padre cancelado, **When** un hijo sigue vivo, **Then** se registra la
   propagación y la limpieza sin fingir la terminación del hijo.

### User Story 5 - Ver estado y capacidades coherentes (Priority: P1)

Como operador, necesito que todas las interfaces describan el mismo trabajo y permitan
seleccionar proveedores compatibles sin interpretar pantallas de forma distinta.

**Why this priority**: las decisiones requieren evidencia común de estado y capacidades.

**Independent Test**: reproducir transiciones ante los distintos clientes y comparar
estado, texto, contadores y señal visual; validar selección antes de asignar recursos.

**Acceptance Scenarios**:

1. **Given** trabajo en ejecución y terminal visualmente inactivo, **When** se observa
   cualquier interfaz, **Then** permanece visible el trabajo en ejecución.
2. **Given** una capacidad requerida ausente, **When** se solicita la operación, **Then**
   se explica incompatibilidad antes de crear recursos.
3. **Given** un backend sin observación para un proveedor, **When** se solicita esa
   combinación, **Then** se rechaza explícitamente o utiliza una ruta genérica probada.

### User Story 6 - Compartir entre nodos y recuperar versiones (Priority: P1)

Como operador de varios nodos, necesito escrituras autorizadas, conflictos visibles
y recuperación sin perder revisiones ni aceptar formatos incompatibles.

**Why this priority**: un endpoint remoto no demuestra consistencia ni recuperación.

**Independent Test**: dos clientes escriben la misma revisión, uno pierde conexión y
se recupera desde una copia; comprobar conflictos, revocación y tombstones.

**Acceptance Scenarios**:

1. **Given** dos escritores sobre una revisión, **When** el primero publica, **Then**
   el segundo recibe conflicto sin sobrescribir silenciosamente.
2. **Given** una partición, **When** se pierde acceso a la autoridad, **Then** el cliente
   no declara confirmada una escritura compartida local.
3. **Given** una copia coherente, **When** se restaura aisladamente, **Then** resultados,
   grants, eventos y memoria mantienen referencias y revisiones.
4. **Given** un formato mayor no admitido, **When** se recibe, **Then** se rechaza antes
   de mutar estado.
5. **Given** un operador autenticado y autorizado para el corte interno, **When** solicita
   un lease de backup, **Then** la autoridad Work registra de forma durable identidad,
   owner, alcance de writers registrados, época/fence y caducidad; sólo esa autoridad
   puede crearlo o revocarlo, sin aceptar identidad del body ni habilitar
   entrada pública o MCP.
6. **Given** un lease vivo, **When** se captura DB y objetos declarados, **Then** la
   captura prueba antes y después que no hay writers Work registrados activos,
   revalida identidad/época/fence/caducidad/revocación e inventario de origen y
   publica evidencia ligada al manifest, identificando objetos fuera del
   alcance registrado como sólo íntegros; nunca declara quiescencia global
   ni cobertura de writers no registrados.
7. **Given** expiración, revocación, writer nuevo, fence cambiado, reinicio o fallo
   parcial durante el corte, **When** se intenta publicar, **Then** no se entrega
   receipt válido ni se promueve un manifest sin evidencia completa; los restos
   se aíslan, se conserva evidencia durable de rechazo con motivo no sensible
   y el siguiente intento requiere un lease nuevo verificable.
8. **Given** bundle v1 histórico, formato futuro o runtime lector/escritor mixto,
   **When** se verifica o captura, **Then** v1 conserva sólo lectura estructural
   sin ascenso a corte, los formatos futuros se rechazan y un writer incapaz de
   respetar el fence no puede operar sobre la autoridad nueva.

### User Story 7 - Mantener y liberar con pruebas verificables (Priority: P2)

Como mantenedor, necesito contratos localizables, configuración coherente y validaciones
que distingan garantías probadas, casos omitidos y deuda histórica.

**Why this priority**: evita duplicación y declaraciones de release sin evidencia.

**Independent Test**: recorrer trazabilidad completa, validar configuración, rechazar una
edición obsoleta y ejecutar gates aplicables en un entorno controlado.

**Acceptance Scenarios**:

1. **Given** una revisión antigua, **When** se edita un workflow, **Then** se informa
   conflicto y permanece intacta la revisión vigente.
2. **Given** un step con contrato efectivo, **When** se reinicia, **Then** se recuperan
   proveedor, modelo, perfil, permisos, límites y retry policy aplicables.
3. **Given** un fallo estructurado, **When** se consulta tras reinicio, **Then** prevalece
   el tipo durable en todas las rutas aplicables.
4. **Given** CI ordinaria, **When** corre la suite, **Then** no inicia modelos facturables.
5. **Given** matriz real autorizada sin cuentas o capacidades suficientes, **When** se
   informa resultado, **Then** omisiones no equivalen a garantías aprobadas.
6. **Given** una especificación histórica, **When** se revisa, **Then** queda clasificada
   con evidencia como vigente, superada, implementada o descartada.

### Edge Cases

- Caídas antes y después del envío, recepción, efecto externo y persistencia del resultado.
- Lease expirado con proveedor vivo; resultado tardío de generación antigua.
- Cancelación concurrente con éxito y revocación concurrente con admisión.
- Reinicio con inbox pendiente y resultado externo incierto.
- Rutas equivalentes, symlinks y worktrees con cambios sin integrar.
- Snapshots vacíos, truncados, redactados o imposibles de persistir.
- Baseline no disponible por presupuesto de hashing de archivos no versionados.
- Cuota agotada con reanudación autónoma del proveedor anterior.
- Retención, consumidores lentos y huecos de eventos.
- Conflictos de revisión, borrados y pérdida de autoridad remota.
- Configuración malformada, identidad ausente y secretos en diagnósticos.
- Selector ausente/ajeno, provisión sustituida o retirada entre resolución y efecto;
  reintento con la misma clave pero distinto origen o contrato.
- Acuse falso, duplicado, tardío o de generación/receptor revocado; recepción
  legacy sin vínculo autenticado y despliegue lector/escritor de distinta versión.
- Acceso cruzado entre intentos al proxy MCP o descriptor privado; secreto en
  prompt, entorno, terminal, delivery, logs o eventos; pérdida del proxy tras
  envío incierto, revocación de grant y aceptación del receptor no durable.

## Requirements

### Functional Requirements

- **FR-001 / R01**: Toda operación conserva identidad lógica e historial de intentos,
  padre, proveedor, contrato, lease, resultado y conciliación.
- **FR-002 / R02**: Cada intento distingue planificación, envío, recepción, ejecución,
  resultado y fallo; incertidumbre de entrega no autoriza reenvío automático.
  Sólo la aceptación durable acreditada por el receptor autenticado, ligada a
  entrega, intento y generación vigentes, cuenta como recepción; enviar o crear
  terminal no son acuse. El receptor acredita aceptación durable exacta antes
  de emitir `TaskReceivedReceiptV1`; `WorkService` persiste receipt y Work ACK
  en la misma transición, sin ascender ACK nativo o telemetría legacy.
- **FR-003 / R03**: El padre puede esperar, cancelar y recuperar hijos y resultados
  después de reiniciar, con renovación de lease y limpieza documentada. El
  vínculo de cada hijo/handoff/step gestionado conserva identidad de origen,
  intento y generación exactos; histórico sin procedencia no se certifica de nuevo.
  Perder el proxy de un intento enviado obliga a conciliar; no autoriza reemitir
  su credencial ni repetir la entrega.
- **FR-004 / R04**: Cada cambio de trabajo, mensaje, recibo o decisión deja evidencia
  durable ordenada; pérdida o retención se declara al consumidor.
- **FR-005 / R05**: Sólo ejecutan proveedores admitidos por job y capaces de realizar
  la operación con modelo y esfuerzo solicitados.
- **FR-006 / R06**: La autoridad procede de un grant durable revocable; parámetros de
  clientes o hijos no pueden ampliar sus límites. Sólo el operador autenticado
  provisiona internamente el launch para un principal y selector; cada hijo y
  workflow requiere identidad verificable y grant propio, explícito y revocable,
  sin herencia por nombre, sesión, caller ID o configuración del paso. Para un
  intento Work gestionado, CAO crea antes del efecto externo una credencial
  aleatoria de alta entropía y sólo persiste su digest en Work DB, ligado a
  instalación, principal, item, intento, generación, revisión del grant y
  lease/expiración. Cada petición revalida binding, grant y recuperación vivos.
  El secreto bruto sólo llega al proxy MCP del servidor mediante descriptor
  privado heredado; nunca entra en delivery, prompt, entorno, terminal, log ni
  evento. El padre sólo selecciona refs preprovisionadas de hijo/receptor;
  éstos se autentican separadamente y no heredan su credencial.
- **FR-007 / R07**: Escrituras concurrentes respetan reservas o aislamiento y conservan
  evidencia de conflictos e integración de cambios.
- **FR-008 / R08**: La admisión respeta presupuestos, prioridad, equidad, backpressure
  y dependencias sin ciclos, sin superar reservas bajo concurrencia.
- **FR-009 / R09**: Conocimiento compartido conserva productor, trabajo, revisión
  fuente, evidencia, confianza, frescura y decisión de revisión.
- **FR-010 / R10**: Toda delegación identifica el contexto persistido que recibió,
  incluidas entradas ordinarias, hijos, handoffs y continuaciones.
- **FR-011 / R11**: Continuidad transporta contrato, contexto, artefactos verificados,
  trabajo completado, pendiente e incierto y motivo de sustitución.
- **FR-012 / R12**: Capacidades se declaran por proveedor y operación, sin tabla
  de compatibilidad por parejas de proveedores.
- **FR-013 / R13**: Todas las interfaces proyectan el mismo estado de trabajo,
  separado del estado del proceso y turno.
- **FR-014 / R14**: Publicación y acceso a memoria aplican autorización por ámbito,
  redacción, retención, tombstones y auditoría; acceso remoto exige identidad.
- **FR-015 / R15**: Memoria entre nodos tiene autoridad versionada, permisos por
  proyecto/job, conflictos explícitos y protocolo de recuperación.
- **FR-016 / R16**: Aprobaciones, pausas, reanudaciones, revocaciones y excepciones
  registran decisor, evidencia examinada, revisión y efectos autorizados.
- **FR-017 / R17**: Cada transición de dominio tiene un único dueño reutilizado por
  las interfaces; no se duplica ciclo de vida por cliente.
- **FR-018 / R18**: Datos y formatos tienen versión, compatibilidad explícita y pruebas
  de migración, backup, restauración y rollback aplicable. El backup interno
  con corte lleva lease server-owned de operador autenticado, limitado a
  writers Work registrados; su evidencia versionada no atribuye
  quiescencia global a productores ajenos al registro.
- **FR-019 / R19**: Matriz real es opt-in y registra escenarios del roadmap por
  proveedor, resultados, omisiones y limitaciones.
- **FR-020 / O01**: Edición de workflows rechaza revisiones obsoletas y cada step
  conserva su contrato efectivo con campos aplicables explícitos.
- **FR-021 / O02**: Opciones soportadas tienen registro versionado, precedencia
  documentada y coherencia entre configuración persistida y entorno.
- **FR-022 / O03**: Backends declaran y comprueban observación de estado; cada
  combinación admitida demuestra recepción, estado y limpieza.
- **FR-023 / O04**: Fallos de step aplicables conservan tipo durable; inferencia
  sólo sirve como compatibilidad identificable de registros antiguos.
- **FR-024 / O05**: Integración upstream tiene inventario de solapamientos, plan de
  resolución y verificaciones relevantes antes de una integración autorizada.
- **FR-025 / O06**: Cada especificación histórica marcada no implementada tiene
  clasificación sustentada y decisión explícita de pertenencia al fork.

### Key Entities

- **Job**: objetivo, proveedores admitidos, autoridad, presupuesto y estado global.
- **Work item**: operación lógica, padre, dependencias, contrato y resultado aceptado.
- **Work attempt**: ejecución concreta, generación, lease y evidencia de entrega.
- **Result / artifact**: contenido recuperable, integridad, productor y revisión validada.
- **Event**: transición o decisión ordenada, con retención declarada.
- **Grant / reservation**: autoridad o recurso concedido, ámbito, revisión y revocación.
- **Provisión de origen / acuse de recepción**: vínculo durable y versionado de
  principal, selector, trabajo y contrato; aceptación autenticada del receptor
  asociada a entrega, intento y generación, distinta de ejecución o resultado.
- **Knowledge record / snapshot**: afirmación revisable y contexto inmutable recibido.
- **Continuation package**: transferencia verificable de progreso y límites.
- **Human decision**: identidad, revisión examinada, motivo y efectos autorizados.
- **Validation evidence**: escenario, entorno, resultado y límites de demostración.
- **Offline cut lease / evidence**: identidad durable de operador y store,
  época/fence, TTL, alcance de writers Work registrados, inventario before/after
  y pruebas de cero writers activos; no concede restore ni autoridad de envío.

## Success Criteria

### Measurable Outcomes

- **SC-001**: El 100% de operaciones en los cinco caminos de entrada conserva
  identidad y evidencia tras reinicio.
- **SC-002**: La matriz de fallos no produce éxitos sin resultado recuperable validado,
  duplicados por reenvío incierto ni resurrecciones tras cancelación.
- **SC-003**: El 100% de intentos fuera de allowlist o grant se rechaza antes del efecto,
  incluidas continuaciones y descendientes.
- **SC-004**: Con diez solicitudes y dos plazas, nunca hay más de dos reservas activas;
  dos jobs elegibles satisfacen la equidad definida en US2.
- **SC-005**: El 100% de delegaciones de prueba usa el snapshot designado o declara
  que no pudo adquirirlo, sin sustituirlo por memoria viva.
- **SC-006**: Restaurar una copia mantiene integridad de todos sus resultados,
  decisiones y referencias; ningún trabajo incierto se reinicia automáticamente.
- **SC-007**: Todos los clientes muestran el mismo estado para cada transición probada.
- **SC-008**: Los 25 puntos tienen requisitos, tareas y evidencia de cierre o dependencia
  externa explícita; planificación no equivale a entrega.
- **SC-009**: CI ordinaria produce cero invocaciones facturables; matriz real informa
  éxito, fallo y omisión por separado.

## Assumptions

- Entradas existentes conservan compatibilidad mediante adaptadores; ninguna migración
  atribuye evidencia inexistente al trabajo histórico.
- La aprobación T093 del 2026-09-24 autoriza diseñar e implementar el contrato
  interno por fases; no activa por sí misma nuevas entradas públicas, proveedores
  ni una DB de operador. Legacy mantiene lectura/replay exacto sin ascenso a
  origen gestionado; versiones incompatibles fallan cerradas.
- La decisión T019 del 2026-09-25 acota una credencial interna por intento y un
  proxy MCP server-owned. Su diseño no activa MCP público ni cambia el ingreso
  ordinario `/sessions`; T017/T019/T035 siguen sujetos a sus gates separados.
- La aceptación T097/C08 pasó en el guest Ubuntu 26.10 fijado con QEMU TCG,
  orquestado desde GitHub-hosted `ubuntu-24.04` y descrito en plan.md. No hay
  host de despliegue previsto. Para T019 se eligió Docker local en dos papeles:
  un contenedor aislado por intento Work y un entorno Docker reproducible para
  la aceptación Bubblewrap. La aceptación local debe registrar imagen, digest,
  kernel y capacidades reales; Docker Desktop comparte el kernel Linux de su
  VM WSL2, por lo que esta evidencia sirve al desarrollo local y no certifica
  un host de producción. El backend permanece fuera de `WORK_BACKENDS` por
  defecto y sólo puede habilitarse desde composición local explícita. Si
  Bubblewrap no alcanza versión >=0.12.0, Landlock ABI >=9, namespaces y cuenta
  broker dedicada en el entorno concreto, el preflight rechaza antes del
  efecto; `/usr/bin/bwrap` 0.11.1 de este host no es apto.
- La aprobación de corte offline T069 del 2026-09-24 limita la garantía a
  writers Work registrados por el runtime. Un bundle v1 sólo prueba integridad
  estructural; ningún lease autoriza restore T070, proveedor, transporte o DB
  del operador. El corte no puede cerrarse sin pruebas del owner real.
- Se reutilizan proveedores, autenticación y almacenes actuales cuando cumplen contratos.
- La primera modalidad multinodo utiliza una autoridad de escritura por proyecto y varios
  clientes con control de revisión. No promete escritura desconectada ni alta disponibilidad.
- Trabajo local y preparación de validaciones están incluidos. Integrar upstream,
  publicar releases o consumir cuentas reales requiere autorización explícita independiente.
- Contratos del programa se revisan antes de modificar implementación. Capacidades
  existentes sólo se cierran cuando pasan su aceptación.
- Se requiere TDD para comportamiento nuevo y regresiones; pruebas de composición cubren
  fronteras reales de persistencia, backend y consumidores.
