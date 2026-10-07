# Data model

IntegrationCapability: id, origins[{ref,sha}], behavior, disposition(adapt/import/equivalent), task_ids, status(pending/in_progress/verified), selected_paths, acceptance_evidence. verified requiere evidencia y ninguna tarea pendiente de su conducta.

ExecutionAttempt: conservar Work/native-child/receipt generation y transacciones actuales. Nuevas columnas runtime/incarnation/deferred failure son aditivas, migraciones idempotentes e inventario de recovery actualizado.

TurnObservation: sequence y completed sequence por terminal; estado observado no es receipt generation. Terminal.turn continúa conteniendo diagnóstico007.

GoalCheckpoint/ContinuationOrder: ligada a Work/attempt/generation; criterio actual, artefactos/revisión, progreso, correcciones usadas/límite, siguiente acción, escalado. Identidad única de orden antes del efecto, estados pending/claimed/settled/reconcile, grants revalidados.

ExecutionPlan: plan-v1 histórico conserva significado; plan-v2 liga contenido/material/scope congelados y aprobación. Publication guarda revisión condicional y resultado recuperable.

ExecutionRuntime: runtime_id, incarnation/channel epoch, ubicaciones de terminal, op_id, deadline y resultados. Frames/cleanup antiguos no afectan replacement; conservación de row/fence ante fallo de DB.
