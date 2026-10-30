# Tasks: Admisión dinámica y capacidad distribuida

## Phase 1 — Diseño y fuentes

- [x] T001 Contrastar fuente, conocimiento y Graphify; guardar decisiones e interfaces en specs/041-resource-aware-network-admission/research.md y plan.md.
- [x] T002 Definir estados, incertidumbre y contratos por hitos en specs/041-resource-aware-network-admission/data-model.md y contracts/admission.md.

## Phase 2 — Fundamentos

- [x] T003 Crear RED y política pura de recursos finitos/frescura/capacidad en test/services/test_admission_policy.py y src/cli_agent_orchestrator/services/admission_policy.py.
- [x] T004 Crear RED y dueño de reservas reserved/started/released con exclusión y deuda en test/services/test_delivery_admission.py y src/cli_agent_orchestrator/services/delivery_admission.py.

## Phase 3 — US1 admisión local dinámica (M1)

- [x] T005 [US1] Implementar observaciones de host/cgroup con límites efectivos, cobertura y reloj explícitos en src/cli_agent_orchestrator/services/admission_resources.py y test/services/test_admission_resources.py.
- [x] T006 [US1] Añadir política validada próxima-lectura, retención de última válida y muestreo acotado de presión PostgreSQL en src/cli_agent_orchestrator/services/admission_settings.py y admission_postgres.py; usar un archivo privado de parámetros del operador separado de settings.json/ConfigService; probar tipos, recarga atómica, ámbitos, falta de visibilidad y retiro físico de sondas.
- [x] T007 [US1] Crear envelope de recursos/atestación explícita del operador en PostgreSQL, ligado a identidad, generación y namespaces derivados por el servidor, con Principal admin verificado; implementar loader opaco en src/cli_agent_orchestrator/services/admission_bootstrap.py, repositorio/migración y test/postgres/test_admission_bootstrap.py. Ni presencia peer, settings, env ni perfiles prueban jerarquía completa.
- [x] T008 [US1] Conectar ciclo de observación/política al dueño local con reducción inmediata, recuperación estable y cierre en src/cli_agent_orchestrator/services/admission_monitor.py, delivery_admission.py y sus pruebas.

## Phase 4 — US4 orden y saturación (M1)

- [ ] T009 [US4] Crear intento común reservado para llamadas directas, barridos y consumo de avisos en src/cli_agent_orchestrator/services/inbox_service.py; preservar ordinal/Context/registry hasta handoff y PENDING al diferir.
- [ ] T010 [US4] Contabilizar entrega gestionada Work y ejecución remota en su dueño real sin desviar autoridad en src/cli_agent_orchestrator/services/inbox_service.py y servicios de envío existentes; probar liberación física, cancelación y encarnación.
- [ ] T011 [US4] Adaptar fixtures de cuatro slots a presupuestos controlados manteniendo todas las aserciones de orden, cursor incremental, equidad, contexto y drenaje en test/services/test_inbox_*.py y test/integration/test_inbox_resource_admission.py.

## Phase 5 — US3 dominios compartidos (M2)

- [ ] T012 [US3] Definir migración PostgreSQL de dominios/reservas con propietario y revisión en src/cli_agent_orchestrator/clients/postgres/migrations.py y src/cli_agent_orchestrator/clients/admission_repository.py; probar competencia nativa en test/postgres/test_admission_reservations.py.
- [ ] T013 [US3] Integrar reservas conjuntas y consumo Work en dominios host/nodo/almacenamiento/cuota, sin ampliar el pool auditado, en src/cli_agent_orchestrator/services/delivery_admission.py y test/postgres/test_admission_reservations.py.

## Phase 6 — US5 recuperación (M2/M3)

- [ ] T014 [US5] Implementar drenaje/reconstrucción y estados inciertos con encarnación/revisión en src/cli_agent_orchestrator/clients/admission_repository.py y test/integration/test_admission_recovery.py; ni expiración ni cancelación liberan por sí solas.

## Phase 7 — US2 distribución (M3)

- [ ] T015 [US2] Incorporar observación/reserva de destino a encargos ordinarios elásticos nuevos, comprobando identidad y preparación antes del primer efecto, en src/cli_agent_orchestrator/services/elastic_assignment_service.py y test/integration/test_elastic_capacity_admission.py.
- [ ] T016 [US2] Validar incorporación de nodo, datos antiguos, conflicto y ausencia de migración de terminales vivos en test/integration/test_elastic_capacity_admission.py; preservar dueño e incertidumbre del intento durable.

## Phase 8 — US6 diagnóstico

- [ ] T017 [US6] Exponer diagnóstico estructurado autorizado de política, presión, reservas y cobertura sin secretos en src/cli_agent_orchestrator/api/main.py y test/api/test_admission_status.py.
- [ ] T018 [US6] Probar liberación por etapa, trabajo por mensaje acotado, contaminación de diagnóstico y presupuestos combinados en test/integration/test_inbox_resource_admission.py y test/api/test_admission_status.py.

## Phase 9 — Aceptación e integración

- [ ] T019 Congelar manifiesto e implementar/ejecutar aceptación M1 real con política dinámica, presión y recursos contrastados; registrar recibos en specs/041-resource-aware-network-admission/quickstart.md y validation-m1.json.
- [ ] T020 Ejecutar aceptación M2/M3 con coordinadores y nodos reales, reservas e identidad verificadas; registrar recibos en specs/041-resource-aware-network-admission/validation-network.json.
- [ ] T021 Ejecutar suites aplicables, composición, revisión independiente, diff y verificación; actualizar documentos y conclusiones durables en specs/041-resource-aware-network-admission/tasks.md sin cerrar gates no ejecutados.

## Dependencies and Execution

T001→T002→T003→T004. T005/T006/T007 permitenT008; T008→T009→T010→T011 completa integraciónM1. T012→T013→T014 completaM2; T015→T016 conM2 habilitaM3. T017/T018 contrastan diagnósticos y etapas. T019/T020 requieren su código, manifiesto y cierre antes deT021. Revisores pueden leer fuentes independientes en paralelo; root mantiene la única escritura en este checkout. No se ejecutan pruebas nativas intensivas a la vez.

Los hitos se informan por separado. Ninguna casilla documental acredita implementación. Los valores de presupuesto de fixtures son escenarios y no constantes de capacidad del producto.

T003/T004 checkpoint: políticaRED41fallos esperados; siguiente intento41políticaPASS/16reservasRED; actualGREEN59/59sin errores ni omisiones, fuente y soporte conservados (39.81s pytest). Revisión Codex de aritmética, estado físico y generación sin bloqueos. Son primitivas puras todavía sin conexión a InboxService; no acreditanM1/M2/M3. Estas pruebas usan el marcador existente offline_database porque no acceden a SQL; la aceptación nativa y sus prerrequisitos permanecen sin cambios. Recibos privados: /home/felni/.cache/caos-spec041-20261030.

T005 checkpoint: observadorLinux/cgroupv2 implementado con cobertura explícita. La revisión encontró tres fronteras incorrectas y se reprodujeron4fallos antes de corregir: archivos no-root en raíz real, namespace parcial atribuido como completo, y margen aplicado después de mínimos independientes. La selección final pasa93/93, tipos en3módulos y composición5/0; revisión Codex final sin bloqueos en el alcance. La sondaLinux real lee CPU/memoria pero mantiene cobertura parcial por almacenamiento y jerarquía no verificados, sin conceder admisión. Solo un bootstrap de confianza puede atestiguar jerarquía/raíz; esa integración sigue enT006/T008. Sin conexión al consumidor ni aceptaciónM1/M2/M3.

T006 checkpoint: política estricta y sonda PostgreSQL implementadas sin conexión al consumidor. La revisión reprodujo FIFO bloqueante, JSON duplicado/anidado y apertura física incierta antes de entregar un handle; corregidos con rechazo explícito, retención atómica y bloqueo de nuevas sondas sin testigo de cierre. Selección final: 158/158 (156 controles puros y 2 nativos con PostgreSQL), sin fallos, errores ni omisiones; fuente y soporte intactos, tipos de 5 módulos PASS y composición 5/0. Se confirmó el PostgreSQL existente, con 0 clientes, 0 bases y 0 roles de prueba restantes y dueño reutilizable vivo. Revisión Codex PASS en este alcance. Véase el [recibo T006](validation-t006.json). No acredita integración M1 ni reservas M2/distribución M3. T007/T008 permanecen pendientes; recargar configuración no permite sustituir un sampler incierto.

T007 checkpoint parcial: historial main y protección de ACL implementados; se conserva import_receipts como último grupo. Tras reproducir las rutas de permisos y corregir la preparación de vistas y el regrant del reset de pruebas, la selección final pasa 245/245, sin fallos, errores ni omisiones; fuente/soporte intactos, tipos de 3 módulos PASS, formato de 6 archivos PASS y composición 5/0. PostgreSQL existente cerrado a 0 clientes, bases y roles de prueba; dueño y contenedor se conservan. Revisión Codex de esquema/ACL y grants/reset PASS. Véase el [recibo de esquema T007](validation-t007-schema.json). T007 sigue pendiente del escritor administrativo CAS, la procedencia del Principal y el loader opaco ligado al proceso/namespaces; el consumidor conserva cuatro slots y no se acreditan M1/M2/M3.

T007 cierre de componentes: writer administrativo CAS y loader opaco ligados al proceso/store/namespaces/vista implementados. Se verifica el Principal antes de resolver credenciales, se rechaza dueño incorrecto y se conserva la última revisión sin fallback; revocación vacía es EnvelopeNotFound y replay no añade historia. Tras 3 regresiones de revisión y un fallo conservado de medición global de presión, la selección final pasa 300/300 sin fallos, errores ni omisiones; inventario independiente y contribución exacta del backend retenido, sin cambiar SQL productivo ni cobertura. Fuente/soporte intactos, tipos de 3 módulos PASS, formato de 6 archivos PASS y composición 5/0. Captura /proc real en proceso propio PASS, con perfil/target sintéticos y sin conceder autoridad. PostgreSQL conserva dueño/contenedor y termina con 0 clientes, bases y roles de prueba. Revisión Codex PASS y reglas durables en Obsidian con lint estricto limpio. Véase el [recibo T007](validation-t007.json). El consumidor sigue en cuatro slots; T008 y la integración/aceptación M1/M2/M3 permanecen pendientes.

T008 checkpoint del ledger: huellas inmutables, presupuesto por cantidades, totales incrementales, recarga CAS y liberación física de almacenamiento implementados. Cuatro pruebas RED conservadas; primera selección 70 PASS y ampliación final 266 PASS, sin fallos, errores ni omisiones, con PostgreSQL real. Tipos 2 módulos PASS, formato 4 archivos PASS, composición 5/0 y revisión Codex sin defectos materiales. PostgreSQL conserva dueño/contenedor y termina con 0 clientes, bases y roles de prueba. Véase el [recibo del ledger T008](validation-t008-ledger.json). T008 sigue abierto: faltan ciclo de observación, autoridad fresca, reducción/recuperación estable y cierre; el consumidor conserva cuatro slots y no se acredita M1/M2/M3.

T008 cierre de componentes: monitor único con política y autoridad frescas antes/después del I/O, un sampler PostgreSQL persistente, techo por cantidades, reducción inmediata y recuperación con 3 muestras sanas distintas por defecto. CPU conserva el inicio del intervalo y vuelve a warming tras un hueco. Se reprodujeron cuatro fallos de composición y una carrera de entrada previa al lock; corregidos con poda atómica de handoffs pendientes, validación individual de tiempos, retiro físico y tickets de lifecycle. Selección final 283 PASS, sin fallos/errores/omisiones, fuente/soporte intactos; tipos 4 módulos PASS, formato 7 archivos PASS y composición 5/0. Tercera revisión Codex sin bloqueadores. PostgreSQL conserva dueño/contenedor y termina con 0 clientes, bases y roles de prueba. Véanse [recibo T008](validation-t008-monitor.json) y [revisión de composición](composition-t008.md). El consumidor sigue en cuatro slots: T009 debe incluir wiring y productor operativo de atestación; no se acepta M1/M2/M3 ni se repite la rampa real sin integración.

T009 checkpoint de integración explícita: InboxService(admission=owner) comparte reservas entre directo, avisos y ambos barridos, sin extraer Context/registry al denegar. Hilos admitidos sin un segundo techo fijo, cursor cerrado antes de esperar, cola de completados y drenaje de todos los hijos con primer error conservado. Cada hijo físico tiene su propio conteo antes de lease.start, incluso si Thread.start falla tras crearlo. El reinicio fallido deja el servicio cerrado. Se conservan todos los intentos RED/GREEN y el control adicional de start físico; final 163 PASS, sin fallos/errores/omisiones (3 warnings), incluidos dos recorridos PostgreSQL reales PENDING→denegación→entrega. Tipos 1 módulo PASS, formato 3 archivos PASS, composición 5/0 y tercera revisión Codex PASS acotada. PostgreSQL termina 0 clientes/bases/roles de prueba, dueño/contenedor intactos. Véanse [recibo T009](validation-t009-inbox.json) y [composición T009](composition-t009.md). T009 sigue abierto: singleton API/enrolamiento/notificaciones del monitor/callback Herdr aún no conectados; las instancias sin dueño explícito mantienen el modo anterior de cuatro slots. No se acepta M1/M2/M3.
