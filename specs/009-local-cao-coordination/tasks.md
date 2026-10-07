# Tareas: Coordinación entre instancias CAO locales

**Entrada**: [Documentos de diseño](.) — especificación, plan, investigación, modelo de datos, contratos y guía de aceptación.\
**Prerrequisitos**: plan.md, spec.md, research.md, data-model.md, contracts/\
**Pruebas**: Las pruebas de regresión están en `test/services/test_local_peer_*`, `test/mcp_server/test_local_peer_transport.py` y las suites de integración relacionadas. La aceptación con dos procesos ya verificó descubrimiento, emparejamiento explícito y permisos recíprocos; falta probar una asignación real con un perfil de proveedor configurado.

## Fase 1: Preparación

**Propósito**: Crear la superficie mínima de pares locales y preservar la asignación y el transporte fleet existentes.

- [x] T001 Añadir identidad persistente de perfil CAO y generación de proceso en `src/cli_agent_orchestrator/services/local_peer_identity.py`.
- [x] T002 Añadir el registro SQLite privado del usuario, validación de anuncios obsoletos y tabla de leases compartidos con adquisición atómica por proyecto en `Path.home()/.aws/cli-agent-orchestrator/local-peers/registry.sqlite3`, usando `src/cli_agent_orchestrator/services/local_peer_registry.py`.

## Fase 2: Base compartida

**Propósito**: Fijar límites de proyecto, persistencia y autorización antes de exponer emparejamiento o tareas.

- [x] T003 Añadir registros de permisos de par, retos, proyectos y tareas coordinadas en `src/cli_agent_orchestrator/clients/database.py`.
- [x] T004 Implementar identidad canónica de proyecto, comparación del directorio Git común y comprobación de rutas contenidas en `src/cli_agent_orchestrator/services/local_peer_service.py`.
- [x] T005 Implementar emparejamiento temporal de un solo uso y capacidades limitadas a proyecto/acciones en `src/cli_agent_orchestrator/services/local_peer_auth.py`.
- [x] T006 Añadir validación de peticiones loopback y montar un router dedicado en `src/cli_agent_orchestrator/api/local_coordination_routes.py` y `src/cli_agent_orchestrator/api/main.py`.
- [x] T007 Implementar transiciones de estado y propiedad idempotente de operaciones en origen/destino en `src/cli_agent_orchestrator/services/local_peer_service.py`.

## Fase 3: Historia 1 — Conectar instancias CAO del mismo equipo (P1)

**Objetivo**: Encontrar, identificar, autorizar y vincular perfiles CAO activos al mismo proyecto local.

**Aceptación independiente**: Iniciar dos perfiles en una PC, descubrirlos, confirmar un emparejamiento temporal y verificar el mismo proyecto en ambos; comprobar que un perfil sin pares sigue funcionando.

- [x] T008 [US1] Añadir rutas de descubrimiento de instancias y reto de identidad activa en `src/cli_agent_orchestrator/api/local_coordination_routes.py`.
- [x] T009 [US1] Implementar creación, aceptación y vencimiento de emparejamientos con confirmación de proyecto/scopes en `src/cli_agent_orchestrator/services/local_peer_auth.py`.
- [x] T010 [US1] Añadir `cao peer list`, `cao peer pair`, `cao peer accept` y presentación/confirmación de identidad local en `src/cli_agent_orchestrator/cli/commands/peer.py`.
- [x] T011 [US1] Registrar el grupo de comandos peer en `src/cli_agent_orchestrator/cli/main.py`.
- [x] T012 [US1] Añadir comprobación de proyecto compartido y errores de discrepancia en `src/cli_agent_orchestrator/api/local_coordination_routes.py`.

## Fase 4: Historia 3 — Proteger proyecto y credenciales locales (P1)

**Objetivo**: Limitar permisos del par al proyecto/acciones acordados y evitar compartir tokens maestros o sobrescribir cambios en silencio.

**Aceptación independiente**: El par puede operar solo dentro de sus permisos CAO; peticiones sin permiso o fuera del proyecto se rechazan; secretos no aparecen en prompts, entorno del proveedor ni logs; tareas de escritura simultáneas se aíslan o bloquean.

- [x] T013 [US3] Aplicar scopes exactos de par/proyecto/acción a cada lectura, escritura o cancelación en `src/cli_agent_orchestrator/services/local_peer_auth.py` y `src/cli_agent_orchestrator/api/local_coordination_routes.py`.
- [x] T014 [US3] Evitar el reenvío del token maestro y redactar capacidades de par en logs/errores en `src/cli_agent_orchestrator/services/local_peer_service.py` y `src/cli_agent_orchestrator/utils/logging.py`.
- [x] T015 [US3] Rechazar hosts arbitrarios, callbacks, escapes por enlaces simbólicos y rutas fuera del binding en `src/cli_agent_orchestrator/services/local_peer_service.py`.
- [x] T016 [US3] Detectar escritores simultáneos del proyecto y devolver conflicto/reconciliación explícitos en `src/cli_agent_orchestrator/services/local_peer_service.py`.

## Fase 5: Historia 2 — Coordinar tareas entre instancias (P1)

**Objetivo**: Enviar una asignación a otro CAO, ver progreso y recuperar resultado sin duplicarla.

**Aceptación independiente**: Enviar una tarea entre perfiles emparejados, consultar progreso/resultado, repetir la misma clave sin crear otro trabajador y mantener separados resultados de pares/tareas distintos.

- [x] T017 [US2] Admitir tareas de pares usando la idempotencia existente y creación de terminales en `src/cli_agent_orchestrator/services/assignment_service.py` y `src/cli_agent_orchestrator/services/local_peer_service.py`.
- [x] T018 [US2] Añadir rutas autenticadas de envío/estado/resultado con recibos de incertidumbre y conflicto en `src/cli_agent_orchestrator/api/local_coordination_routes.py`.
- [x] T019 [US2] Crear worktrees por tarea Git y adquirir/liberar el lease compartido del registro por proyecto para escrituras sin Git en `src/cli_agent_orchestrator/services/local_peer_service.py`, `src/cli_agent_orchestrator/services/local_peer_registry.py` y `src/cli_agent_orchestrator/services/terminal_service.py`.
- [x] T020 [US2] Reconciliar progreso y resultado en el origen a partir de los recibos de tarea/terminal del destino en `src/cli_agent_orchestrator/services/local_peer_service.py`.
- [x] T021 [US2] Exponer a agentes CAO el descubrimiento autorizado, envío de tarea y consulta de estado en `src/cli_agent_orchestrator/mcp_server/server.py`.
- [x] T022 [US2] Añadir consulta de estado y reconciliación a `cao peer status` en `src/cli_agent_orchestrator/cli/commands/peer.py`.

## Fase 6: Historia 4 — Detener o revocar un par (P2)

**Objetivo**: Revocar un par o manejar su cierre inesperado sin frenar los CAO restantes.

**Aceptación independiente**: Revocar en reposo y durante una tarea, rechazar futuras operaciones, conservar recibo interrumpido/reconciliable y mantener activos los otros perfiles.

- [x] T023 [US4] Añadir cancelación de tarea usando la parada del terminal e informar si el trabajador se detuvo de verdad en `src/cli_agent_orchestrator/api/local_coordination_routes.py` y `src/cli_agent_orchestrator/services/local_peer_service.py`.
- [x] T024 [US4] Añadir revocación de permisos y eliminación de pares, denegando al instante peticiones nuevas, en `src/cli_agent_orchestrator/services/local_peer_auth.py` y `src/cli_agent_orchestrator/api/local_coordination_routes.py`.
- [x] T025 [US4] Reconciliar tarea y lease de proyectos sin Git tras cierre/reinicio en `src/cli_agent_orchestrator/services/local_peer_service.py`.
- [x] T026 [US4] Añadir `cao peer revoke` y mostrar tareas que requieren reconciliación en `src/cli_agent_orchestrator/cli/commands/peer.py`.

## Fase final: Documentación y composición

- [x] T027 Documentar perfiles/puertos separados, emparejamiento, alcance del proyecto, worktrees y revocación en `docs/local-cao-coordination.md`.
- [x] T028 Actualizar la guía sin cambiar la semántica fleet/remota de `target_host` en `docs/tool-restrictions.md` y `docs/workflows.md`.
- [x] T029 Revisar compatibilidad y composición de estados/leases contra [spec.md](spec.md), [contracts/local-coordination.md](contracts/local-coordination.md) y [quickstart.md](quickstart.md).

## Dependencias

- La base (T001–T007) precede a las historias.
- US1 (T008–T012) descubre identidades y crea permisos; US3 (T013–T016) termina los límites de proyecto/seguridad antes de exponer tareas.
- US2 (T017–T022) depende de US1 y US3.
- US4 (T023–T026) depende de los registros durables de permisos y tareas.
- Documentación/composición (T027–T029) cierra la implementación.

## Oportunidades de trabajo paralelo

- Tras definir interfaces de identidad/registro (T001/T002), el esquema de datos (T003) y la validación de rutas (T004) pueden avanzar en archivos separados.
- En US1, la presentación CLI (T010) puede empezar una vez que el DTO del registro esté definido; las rutas de pairing T008/T009/T012 comparten contrato y deben revisarse juntas.
- La documentación puede iniciarse con el contrato CLI/HTTP final y reconciliarse al terminar.

## Estrategia MVP

1. Completar preparación y base común.
2. Entregar US1: descubrir y emparejar instancias locales.
3. Entregar US3: cerrar límites de proyecto y credenciales antes de delegar.
4. Entregar US2: coordinar tareas con idempotencia y aislamiento de cambios.
5. Entregar US4: parada, revocación y recuperación.
