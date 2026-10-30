# Tasks: Admisión dinámica y capacidad distribuida

## Phase 1 — Diseño y fuentes

- [x] T001 Contrastar fuente, conocimiento y Graphify; guardar decisiones e interfaces en specs/041-resource-aware-network-admission/research.md y plan.md.
- [x] T002 Definir estados, incertidumbre y contratos por hitos en specs/041-resource-aware-network-admission/data-model.md y contracts/admission.md.

## Phase 2 — Fundamentos

- [x] T003 Crear RED y política pura de recursos finitos/frescura/capacidad en test/services/test_admission_policy.py y src/cli_agent_orchestrator/services/admission_policy.py.
- [x] T004 Crear RED y dueño de reservas reserved/started/released con exclusión y deuda en test/services/test_delivery_admission.py y src/cli_agent_orchestrator/services/delivery_admission.py.

## Phase 3 — US1 admisión local dinámica (M1)

- [x] T005 [US1] Implementar observaciones de host/cgroup con límites efectivos, cobertura y reloj explícitos en src/cli_agent_orchestrator/services/admission_resources.py y test/services/test_admission_resources.py.
- [ ] T006 [US1] Añadir política validada próxima-lectura, retención de última válida y muestreo acotado de presión PostgreSQL en src/cli_agent_orchestrator/services/settings_service.py, config_service.py y admission_resources.py; probar tipos, caducidad y falta de visibilidad.
- [ ] T007 [US1] Conectar ciclo de observación/política al dueño local con reducción inmediata, recuperación estable y cierre en src/cli_agent_orchestrator/services/delivery_admission.py y test/services/test_delivery_admission.py.

## Phase 4 — US4 orden y saturación (M1)

- [ ] T008 [US4] Crear intento común reservado para llamadas directas, barridos y consumo de avisos en src/cli_agent_orchestrator/services/inbox_service.py; preservar ordinal/Context/registry hasta handoff y PENDING al diferir.
- [ ] T009 [US4] Contabilizar entrega gestionada Work y ejecución remota en su dueño real sin desviar autoridad en src/cli_agent_orchestrator/services/inbox_service.py y servicios de envío existentes; probar liberación física, cancelación y encarnación.
- [ ] T010 [US4] Adaptar fixtures de cuatro slots a presupuestos controlados manteniendo todas las aserciones de orden, cursor incremental, equidad, contexto y drenaje en test/services/test_inbox_*.py y test/integration/test_inbox_resource_admission.py.

## Phase 5 — US3 dominios compartidos (M2)

- [ ] T011 [US3] Definir migración PostgreSQL de dominios/reservas con propietario y revisión en src/cli_agent_orchestrator/clients/postgres/migrations.py y src/cli_agent_orchestrator/clients/admission_repository.py; probar competencia nativa en test/postgres/test_admission_reservations.py.
- [ ] T012 [US3] Integrar reservas conjuntas y consumo Work en dominios host/nodo/almacenamiento/cuota, sin ampliar el pool auditado, en src/cli_agent_orchestrator/services/delivery_admission.py y test/postgres/test_admission_reservations.py.

## Phase 6 — US5 recuperación (M2/M3)

- [ ] T013 [US5] Implementar drenaje/reconstrucción y estados inciertos con encarnación/revisión en src/cli_agent_orchestrator/clients/admission_repository.py y test/integration/test_admission_recovery.py; ni expiración ni cancelación liberan por sí solas.

## Phase 7 — US2 distribución (M3)

- [ ] T014 [US2] Incorporar observación/reserva de destino a encargos ordinarios elásticos nuevos, comprobando identidad y preparación antes del primer efecto, en src/cli_agent_orchestrator/services/elastic_assignment_service.py y test/integration/test_elastic_capacity_admission.py.
- [ ] T015 [US2] Validar incorporación de nodo, datos antiguos, conflicto y ausencia de migración de terminales vivos en test/integration/test_elastic_capacity_admission.py; preservar dueño e incertidumbre del intento durable.

## Phase 8 — US6 diagnóstico

- [ ] T016 [US6] Exponer diagnóstico estructurado autorizado de política, presión, reservas y cobertura sin secretos en src/cli_agent_orchestrator/api/main.py y test/api/test_admission_status.py.
- [ ] T017 [US6] Probar liberación por etapa, trabajo por mensaje acotado, contaminación de diagnóstico y presupuestos combinados en test/integration/test_inbox_resource_admission.py y test/api/test_admission_status.py.

## Phase 9 — Aceptación e integración

- [ ] T018 Congelar manifiesto e implementar/ejecutar aceptación M1 real con política dinámica, presión y recursos contrastados; registrar recibos en specs/041-resource-aware-network-admission/quickstart.md y validation-m1.json.
- [ ] T019 Ejecutar aceptación M2/M3 con coordinadores y nodos reales, reservas e identidad verificadas; registrar recibos en specs/041-resource-aware-network-admission/validation-network.json.
- [ ] T020 Ejecutar suites aplicables, composición, revisión independiente, diff y verificación; actualizar documentos y conclusiones durables en specs/041-resource-aware-network-admission/tasks.md sin cerrar gates no ejecutados.

## Dependencies and Execution

T001→T002→T003→T004. T005/T006 permitenT007; T007→T008→T009→T010 completa integraciónM1. T011→T012→T013 completaM2; T014→T015 conM2 habilitaM3. T016/T017 contrastan diagnósticos y etapas. T018/T019 requieren su código, manifiesto y cierre antes deT020. Revisores pueden leer fuentes independientes en paralelo; root mantiene la única escritura en este checkout. No se ejecutan pruebas nativas intensivas a la vez.

Los hitos se informan por separado. Ninguna casilla documental acredita implementación. Los valores de presupuesto de fixtures son escenarios y no constantes de capacidad del producto.

T003/T004 checkpoint: políticaRED41fallos esperados; siguiente intento41políticaPASS/16reservasRED; actualGREEN59/59sin errores ni omisiones, fuente y soporte conservados (39.81s pytest). Revisión Codex de aritmética, estado físico y generación sin bloqueos. Son primitivas puras todavía sin conexión a InboxService; no acreditanM1/M2/M3. Estas pruebas usan el marcador existente offline_database porque no acceden a SQL; la aceptación nativa y sus prerrequisitos permanecen sin cambios. Recibos privados: /home/felni/.cache/caos-spec041-20261030.

T005 checkpoint: observadorLinux/cgroupv2 implementado con cobertura explícita. La revisión encontró tres fronteras incorrectas y se reprodujeron4fallos antes de corregir: archivos no-root en raíz real, namespace parcial atribuido como completo, y margen aplicado después de mínimos independientes. La selección final pasa93/93, tipos en3módulos y composición5/0; revisión Codex final sin bloqueos en el alcance. La sondaLinux real lee CPU/memoria pero mantiene cobertura parcial por almacenamiento y jerarquía no verificados, sin conceder admisión. Solo un bootstrap de confianza puede atestiguar jerarquía/raíz; esa integración sigue enT006/T007. Sin conexión al consumidor ni aceptaciónM1/M2/M3.
