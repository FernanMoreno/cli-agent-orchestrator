# Investigación y decisiones — Spec Kit en CAOS

Fecha: 2026-10-10. Alcance: especificación de producto, sin implementación ni cambios de dependencias.

## Fuente oficial y compatibilidad

[GitHub Spec Kit](https://github.com/github/spec-kit), consultado el 2026-10-10, describe fases de especificación, planificación, tareas, implementación y convergencia con agentes compatibles. La integración propuesta reutiliza estas fases y aporta coordinación y seguimiento en CAOS. La invocación de skills pertenece al agente y puede variar entre integraciones; instalar una herramienta de terminal no ejecuta automáticamente las fases.

La configuración local indica versión 0.16.3 y las integraciones Claude y Codex. Esto describe este repositorio y no prueba compatibilidad de cualquier instalación del producto con la versión actual upstream. Se requerirá una matriz comprobada de versión, proveedor, fase e invocación. No se fija una nueva dependencia en esta especificación.

## Evidencia local verificada

| Fuente | Hallazgo | Consecuencia |
|---|---|---|
| [.specify/integration.json](../../.specify/integration.json) y [init-options.json](../../.specify/init-options.json) | Configuración de desarrollo con Claude/Codex y versión 0.16.3 | Reutilizar instalación compatible; separar configuración del repositorio de capacidad del producto |
| [.specify/feature.json](../../.specify/feature.json) | Un selector de feature compartido | Contexto por ejecución; aislamiento o serialización si el agente usa ese selector |
| [AI_WORKFLOW.md](../../AI_WORKFLOW.md) y [constitución](../../.specify/memory/constitution.md) | Artefactos spec → plan → tasks; implementación con comprobaciones y cierre por evidencia | Respetar reglas del proyecto sin confundir generar documentos con implementar |
| [code_supervisor.md](../../src/cli_agent_orchestrator/agent_store/code_supervisor.md) | Delega desarrollo/revisión; incluye objetivo, rutas, restricciones y aceptación en mensajes | Añadir identidad de tarea y revisión; el prompt del supervisor no sustituye control de ejecución |
| [beads_service.py](../../src/cli_agent_orchestrator/services/beads_service.py) | decompose_preview transforma líneas en títulos con descripción vacía, máximo 32 tareas | No sirve como importador semántico de tareas Spec Kit; hará falta validación y previsualización específicas |
| [assignment_service.py](../../src/cli_agent_orchestrator/services/assignment_service.py) | Operaciones durables con clave y generación; asignaciones ordinarias separadas de Managed Work | Evitar entrega duplicada y no usar assign ordinario para eludir autoridad del trabajo gestionado |
| [work_workflow_plans.py](../../src/cli_agent_orchestrator/services/work_workflow_plans.py) | Preparación/revisión y arranque con snapshots y materiales identificados | Considerar estos contratos al conectar aprobación documental con ejecución; no construir una aprobación paralela desconectada |
| [work_service.py](../../src/cli_agent_orchestrator/services/work_service.py) | Entrega incierta y sustitución condicionada a reconciliación | Recuperación desde identidades existentes, sin reinterpretar timeout como ausencia de trabajo |
| [models/workflow.py](../../src/cli_agent_orchestrator/models/workflow.py) y [constants.py](../../src/cli_agent_orchestrator/constants.py) | Registro de construcciones reservadas y unidades disponibles | No convertir marcadores de paralelismo en promesa de motor DAG o loops ya implementados |

La búsqueda de `spec.?kit`, `tasks.md` y `specify` en `src/cli_agent_orchestrator` no identificó un importador/orquestador de fases Spec Kit. La estructura `.specify` existente corresponde al desarrollo del repositorio; la nueva integración se especifica como trabajo pendiente.

## Decisiones de producto

1. **Proceso por feature**: contexto y revisiones explícitos, con recuperación; el selector global de desarrollo no es identidad de ejecución.
2. **Integración real de fases**: agente compatible ejecuta las skills disponibles; CAOS conserva entradas, salidas y resultado comprobado.
3. **Tareas normalizadas**: identidad estable, origen, dependencias y aceptación. Un marcador de paralelismo expresa una propuesta, no una garantía de independencia.
4. **Autorización antes de implementar**: revisión explícita por defecto; políticas delegadas limitadas y visibles. Los documentos importados son datos, no autoridad.
5. **Una autoridad de ejecución**: especificación, tareas y Beads pueden proyectar el trabajo; la entrega y sus efectos requieren el contrato autorizado de CAOS.
6. **Convergencia limitada**: criterios, evidencia e iteraciones trazables. Casillas del documento, texto del agente y proceso terminado no acreditan éxito.
7. **Sincronización revisable**: cambios externos generan diferencias e invalidación localizada; no alteran retroactivamente intentos activos.

## Composición y riesgos pendientes de planificación

| Frontera | Invariante y escenario de aceptación |
|---|---|
| Proyecto → agente de fase | Proyecto/feature/constitución vigentes; dos features no cruzan selector ni archivos |
| Agente → artefactos | Documento válido ligado a la revisión esperada; reinicio tras escritura se reconcilia |
| Documentos → tareas | Identidad estable y referencias resolubles; reimportar no duplica y cambios externos no sobrescriben |
| Tareas → autorización → entrega | El material autorizado coincide con el despachado; cambios y permisos obsoletos impiden nuevas entregas |
| Entrega → resultado | Acuse, proceso y aceptación separados; timeout requiere reconciliación |
| Pregunta → continuación | Identidad de fase y vigencia; dos respuestas no reanudan dos veces |
| Revisión → corrección → cierre | Evidencia por criterio, límites finitos y omisiones visibles |
| Instancias → front/grafo | Identidad cualificada y acceso por recurso; desconexión no habilita acciones sobre revisiones obsoletas |

La planificación deberá decidir persistencia/transacciones de nuevas entidades, contratos de eventos y lectura, límites de importación, tratamiento de rutas/enlaces simbólicos y formatos, actualización de snapshots, aislamiento de procesos de fase, retención y compatibilidad. También determinará cómo proyectar tareas en Beads sin dos fuentes de estado, y cómo delegar implementación por tarea sin que una invocación genérica de implement procese todo el conjunto a la vez.

## Composition Review Report

- **Subsistemas cambiados**: documentos de especificación y selector local de feature; ningún subsistema de ejecución modificado.
- **Fronteras revisadas**: Spec Kit local, supervisor, Beads, asignaciones, Managed Work, workflows y dependencias 016–019.
- **Contratos comprobados contra fuente**: asignación ordinaria frente a autoridad gestionada, reconciliación, snapshot del plan y reserva de modos.
- **Comprobaciones ejecutadas**: inspección de fuente, resolución de plantilla y validación documental; no pruebas de producto, arquitectura, contratos ni gate de composición porque no hay implementación en este cambio.
- **Escenarios**: definidos en spec.md para futura aceptación; no ejecutados como pruebas de integración.
- **Dependencias reales**: repositorio y documentación oficial; no ejecución de agentes o instalaciones externas.
- **Riesgos**: importación semántica, selector compartido, versiones/invocación por proveedor, normalización y autoridad de tareas, recuperación entre archivos y estado durable.
- **Veredicto**: PASS WITH RISKS para el diseño documental; implementación y aceptación funcional pendientes.

## Siguiente fase

Preparar plan.md y después tasks.md para esta feature. Antes de modificar ejecución: Graphify de impacto verificado contra fuente, diseño de contratos/persistencia/autoridad y escenarios de composición según AI_WORKFLOW.md. El diseño actual no acredita integración funcional ya disponible.
