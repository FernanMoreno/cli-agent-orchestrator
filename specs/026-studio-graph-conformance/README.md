# De la maqueta a la red real de CAOS

**Fecha:** 2026-10-23. **Estado:** auditoría y 15 specs de brechas; no implementación nueva.

La prioridad es que **el grafo represente lo que sucede realmente**: organización registrada, autoridad efectiva, asignaciones admitidas, trabajo y dependencias, comunicación y decisiones confirmadas. Las agrupaciones de presentación no crean equipos ni permisos.

Empieza por la [evaluación de cada detalle](audit.md). El [inventario estructurado](inventory.json) enlaza cada detalle a su requisito. [Conformidad conjunta](spec.md) define cómo probar que la representación y la ejecución coinciden.

## Specs individuales y propiedad

| Spec | Propiedad de esta entrega y relación previa | Dependencias nuevas |
|---|---|---|
| [026 — Conformidad entre la red real y CAOS Studio](../026-studio-graph-conformance/spec.md) | Gate de conformidad e inventario; no redefine el motor. | Ninguna |
| [027 — Equipos, subequipos y pertenencia reales](../027-agent-team-topology/spec.md) | Nuevo contrato de organización. Debe decidir adaptación de group sin romper semántica. | Ninguna |
| [028 — Coordinación efectiva y delegación entre equipos](../028-team-delegation-authority/spec.md) | 006/009/025 conservan asignación y permisos; añade coordinación por equipo. | 27 |
| [029 — Identidad, procedencia y contrato del grafo de ejecución](../029-execution-graph-provenance/spec.md) | 017-T010/T011/T013/T014 conserva lectura de ejecución; precisa identidad y procedencia. | 27 |
| [030 — Conexiones y detalles coherentes en todos los niveles](../030-graph-hierarchy-aggregation/spec.md) | 017-T012/T020/T021 conserva canvas y consultas; concreta agregación en cada nivel. | 27, 29 |
| [031 — Comunicación real y ciclo de entrega en el grafo](../031-graph-message-lifecycle/spec.md) | 023 y 024 conservan cada canal; 017-T014 conserva lectura ambiental. | 29 |
| [032 — Trabajo, bloqueos, fases y ruta crítica verificables](../032-graph-work-dependencies/spec.md) | Work/Beads y 020 conservan despacho; 017 proyecta sus dependencias. | 29 |
| [033 — Decisiones, destinatarios y desbloqueo real](../033-graph-human-decisions/spec.md) | 016 conserva preguntas y autoridad; 017-T011/022/025 conservan sus gates. | 29, 32 |
| [034 — Actualización viva, recuperación y aislamiento de acceso](../034-graph-live-recovery/spec.md) | 011 y 017-T012/T022 conservan transporte y recuperación. | 29 |
| [035 — Instancias reales y red distribuida observable](../035-graph-instance-federation/spec.md) | 018 y 017-T023 conservan multi-instancia; precisa asociación y cobertura. | 29, 34 |
| [036 — Crear tareas y actuar sobre trabajo real desde Studio](../036-studio-work-commands/spec.md) | 019 y Work/Beads conservan comandos; esta entrega conecta el recorrido. | 28, 29, 32 |
| [037 — Búsqueda, filtros y navegación con contexto real](../037-studio-discovery-context/spec.md) | 019 y 017-T021 conservan navegación y búsqueda. | 29, 30 |
| [038 — Perfiles, prompts, conocimiento y skills efectivos](../038-studio-agent-resources/spec.md) | 019/025 conservan biblioteca y permisos por perfil. | 29 |
| [039 — Workflows y Spec Kit trazables hasta el trabajo ejecutado](../039-studio-workflow-traceability/spec.md) | 012/019/020 conservan workflows y Spec Kit de producto. | 28, 29, 32, 36 |
| [040 — Lectura, accesibilidad y operación de la red](../040-studio-graph-usability/spec.md) | 017/019 conservan accesibilidad; aplica al incremento de red real. | 30, 33, 37 |

Estas son descomposiciones trazables de carencias concretas, no reemplazos de 016–025. Al implementar una entrega se actualizará el requisito y tarea original correspondiente con enlaces a la misma evidencia; no se construirán dos motores o proyecciones competidoras. Si una tarea anterior ya quedó integrada, se revalida y reutiliza; no se reabre solo por aparecer en esta lista.

## Orden propuesto de entrega

1. **027 y 029:** organización registrada y lectura con identidad/procedencia. Diseñar primero los contratos; no reinterpretar grupos antiguos silenciosamente.
2. **028, 031 y 032:** conectar autoridad, asignaciones, mensajes y dependencias a efectos reales; programación y permisos siguen siendo responsabilidad del servidor.
3. **033 y 034:** decisiones con confirmación real, actualización y recuperación. Las decisiones también pueden avanzar en paralelo al transporte una vez pactado 029.
4. **030, 035 y 037:** agregación progresiva, enlaces entre instancias y navegación sobre fuentes autorizadas. Su diseño puede adelantarse; la aceptación exige las dependencias indicadas.
5. **036, 038 y 039:** completar comandos y recursos/workflows con sus contratos existentes.
6. **040 y 026:** verificar calidad en cada incremento y cerrar conformidad conjunta solo con recorridos reales. La accesibilidad se aplica desde el primer incremento, no se pospone al final.

Todos los elementos tienen valor y alcance propios; 026 se diseña primero como gate transversal, aunque su aceptación final depende de los incrementos pertinentes. No hay permiso para omitir gates existentes por completar una nueva spec.

## Uso de Spec Kit

Cada directorio tiene spec.md, plan.md inicial, tasks.md, evidence.md y checklist. Las specs siguen la plantilla resuelta del proyecto. No se cambia de rama: se trabaja en main preservando cambios ajenos.

El plan es inicial: contratos detallados, migraciones, formatos y presupuestos se completan en T002 antes de implementar. Las tareas de implementación permanecen sin marcar. Para trabajar una feature concreta se debe indicar explícitamente su directorio; el puntero global de Spec Kit se conserva para no desviar el trabajo abierto en 008/025 u otras sesiones.

## Aceptación y límites

- Una comparación visual o una prueba con datos sintéticos no demuestra organización o ejecución reales.
- Todos los detalles visibles deben poder explicarse desde fuentes autorizadas de la misma revisión o declarar su incertidumbre.
- No se afirma capacidad infinita ni se ejecutan cargas de un millón de agentes por solicitar el diseño escalable.
- Se conservan los gates de 017: actualización p95 <2 s con 20 agentes/100 relaciones; apertura p95 <3 s y selección p95 <200 ms con 100 agentes/500 relaciones en el entorno de referencia.
- Esta entrega solo crea documentación y comprobaciones estáticas. [Validación de la entrega](validation.md) registra los resultados reales y los límites de revisión.
