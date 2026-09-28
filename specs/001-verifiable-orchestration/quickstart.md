# Guía de validación

Estado: preparación. Las suites nuevas de tasks.md aún no existen. No copiar comandos
de pruebas futuras y presentar un error de archivo ausente como regresión reproducida.

## Prerrequisitos

- Ejecutar desde la raíz y revisar `git status --short`.
- Usar `.venv/bin/python` del proyecto; no instalar o actualizar dependencias para esta revisión.
- DB, HOME de proveedores de prueba y archivos deben ser temporales según fixtures existentes.
- No ejecutar live_provider ni cargar credenciales salvo autorización explícita.
- Confirmar que `.ai/project-name` apunta al registro de ESTE checkout, no a otro proyecto.
- Activar rutas nuevas sólo con esquema, contratos y grants de su etapa verificados.

## Comprobación de artefactos

```bash
bash .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
git diff --check
```

Esperado: feature directory correcto y documentos disponibles; sin errores de whitespace
en cambios propios. Revisar aparte los cambios preexistentes sin modificarlos por conveniencia.

## Baseline local ejecutable existente

```bash
.venv/bin/python -m pytest -o addopts= -q \
  test/clients/test_workflow_run_migration.py \
  test/services/test_frozen_run_memory.py \
  test/services/test_turn_receipt_delivery.py \
  test/test_real_provider_matrix_contract.py
```

Este comando verifica piezas actuales, no el programa completo. Registrar resultado fresco.
La ejecución anterior al plan obtuvo 85 passed; no se reutiliza como signoff de cambios futuros.

```bash
.venv/bin/python -m pytest -o addopts= -x -q test/services/test_manifest_freeze.py
```

Fallo conocido del checkout: baseline no disponible por hashing de archivos no versionados
mayor a 64 MiB. T045 debe aislar repositorio de prueba y cubrir ausencia explícita de baseline;
no cambiar presupuesto o excluir silenciosamente código para conseguir verde.

## Gate de composición

```bash
project-composition-check "$(cat .ai/project-name)"
```

Si falta .ai/project-name, diagnosticar registro antes de escribirlo. Nombre registrado
observado: caos. El comando con nombre explícito permite diagnosticar launcher, pero no
resuelve que Import Linter sólo cubra cao_workflow. Revisar paquetes antes de ampliar reglas.

## Validación por entrega, después de crear las suites

| Entrega | Pruebas objetivo | Evidencia esperada |
|---|---|---|
| Foundation | test/services/test_work_reducer.py; test/clients/test_work_migrations.py | transiciones válidas y migración/rollback verificables |
| US1 | test/clients/test_work_repository.py; test/services/test_work_service.py; test/e2e/test_work_lifecycle.py | cinco entradas recuperables, sin redelivery incierta |
| US2 | test/services/test_work_authority.py; test/services/test_work_reservations.py; test/services/test_work_scheduler.py; test/integration/test_work_admission.py | grant antes de efecto y nunca más de dos reservas con diez contendientes |
| US3 | test/services/test_knowledge_revisions.py; test/services/test_knowledge_policy.py; test/services/test_delegation_snapshot.py; test/integration/test_knowledge_context.py | decisiones aplicadas y snapshots idénticos |
| US4 | test/services/test_work_continuation.py; test/services/test_work_decisions.py; test/integration/test_work_cancellation.py; test/e2e/test_work_continuation.py | continuidad íntegra y cese/fencing anterior |
| US5 | test/api/test_work_projection.py; web/src/test/work-state.test.tsx; pruebas Rust; test/e2e/test_herdr_generic_status.py | igualdad de estado y observación herdr real |
| US6 | test/api/test_knowledge_authority.py; test/services/test_recovery_bundle.py; test/integration/test_work_recovery.py; test/integration/test_knowledge_multinode.py | conflicto CAS, partición explícita y restore sin relanzamiento |
| US7 | test/api/test_workflow_revision.py; test/services/test_step_contract.py; test/services/test_config_service.py; test/services/test_script_error_kind.py; test/test_real_provider_matrix_contract.py | edición protegida, contrato efectivo y error durable |

Ejecutar pytest con `-o addopts=` y seleccionar marcadores explícitos para pruebas de
integración; los defaults excluyen e2e/integration. mock_cli debe seguir sin credenciales.
Web usa el script test de web/package.json; Rust usa cargo test desde tui/.
No adjudicar PostgreSQL, herdr, tmux o navegador real a una prueba con mocks.

## Matriz real y upstream

El workflow actual `.github/workflows/real-provider-e2e.yml` es manual. Tras autorización,
seguir docs/real-provider-e2e.md con manifiesto de proveedores/modelos/cuentas elegido.
Registrar por celda arranque, entrega, hijo, inbox, cancelación, cuota, continuidad,
cleanup y reconciliación. Un skip de cuota no demuestra continuidad.

Upstream: T081 sólo lee refs y prepara resolución; T085 requiere petición explícita.
No ejecutar merge, push ni publicar release para validar documentación o contratos.

## Cierre

Después de implementación: tests, gate, revisión de composición, diff, decisión Graphify,
decisión de conocimiento y verificación fresca. Evidencia en workflow-status.md debe
incluir comando, resultado, alcance y límites. Toda modificación posterior invalida signoff.
