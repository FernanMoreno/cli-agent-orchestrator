# Composition Review Report — 2026-09-30

Verdict: **PASS WITH RISKS para la composición y aceptación local**.
Esta revisión recoge la intervención inicial: sus gates locales pasaron y
T084/T085 seguían pendientes entonces. La aceptación posterior completó T084
para la matriz autorizada y T085 para el candidato aislado.
[Estado vigente](acceptance-status.md): cierre documental local, con O03 ampliado
y OpenCode v2 sin aceptar; sin certificación de producción.

## Changed subsystems

Resolución Work de provisiones, credenciales de script, API run-step, diario YAML,
cleanup de procesos y ciclo de vida del runtime servidor. Se preservó la worktree
previa T017/T019/T020. Ajustes de fixtures cubren clientes loopback, schema39,
contratos de YAML y directorio temporal del servidor. TUI verifica el campo
aditivo capabilities de la API, sin ocultar proveedores.

## Neighbors reviewed

API→workflow/script runner→WorkWorkflowOrigins→WorkProvisioning→WorkAdmission;
WorkService→reducer→WorkRepository; proceso Docker→receipt/result→projector→journal;
launch/inbox/lineage→snapshot; autoridad/grant→decisiones y reintentos;
recovery inventory→bundle/restore; API providers→TUI/Web.

## Contracts and invariants

| Frontera | Owner / transacción / prueba |
|---|---|
| Provisión ausente | sólo ausencia exacta permite legacy; principal verificado; ningún grant creado |
| Provisión presente | registro durable, fuente exacta, revisión y revocación revalidados en read snapshot |
| Credencial script | selector del servidor; sólo managed emite token ligado a run/generación; fallo no da fallback |
| Resultado YAML legacy | envelope redactado/acotado; BEGIN IMMEDIATE y CAS por generación/fingerprint/último contrato; se guarda antes de completion |
| Resultado Work | receptor autenticado, binding exacto, bytes/hash validados; intento equivocado no liquida step |
| Proyección | mismo store; relectura durable; CAS de step; no usa terminal idle como resultado |
| Fallo de proceso | exit no cero y evidencia de cese/cleanup, sin aceptar resultado inexistente |
| Cleanup recuperado | confirmación literal True; respuesta truthy mantiene fallo visible |
| Lifespan | runtime/gateway/result/projector se retiran sólo si siguen siendo los owners instalados |
| Reintento | decisión autorizada, fingerprint y revisión exactos; no replay por reinicio ni efecto incierto |
| Versiones | schema39, perfil recovery v30; v28/v29 verify-only; migración aditiva |
| Clientes | fixtures de estado comunes y capabilities aditivas; campos originales conservados |

Timeout/cancel conservan incertidumbre y no autorizan un segundo efecto. El
resultado aceptado gana frente a fallo tardío del proceso. Se revisaron rutas de
rechazo, concurrencia, duplicados, source drift, permisos y revocación.

## Architecture

WorkRepository sigue siendo dueño de estado/eventos Work; WorkService aplica el
reducer y CAS. API/terminal/memoria delegan las transiciones. Workflow journal es
la proyección run/step y no sustituye autoridad Work. El bloque managed run-step
se extrajo del handler legacy para mantener un solo orden de decisión de replay.

El quinto contrato Import Linter impide imports **directos** del projector a
entrypoints/providers/backends. La primera regla propuesta transitiva reprodujo
la dependencia preexistente journal→database→memory_service→terminal/provider.
No se presenta esa deuda indirecta como eliminada ni se extrae todo ese sistema
por conveniencia. Los cuatro contratos previos mantienen su alcance transitivo.

## Scenarios and real dependencies

SQLite real temporal: migraciones, CAS, eventos, autoría, snapshot, grants,
revocación, recovery, reserva y decisiones. HTTP real con servidor aislado y tmux/
mock_cli: entradas legacy y respuestas tras restart. Docker real 29.8.1 Linux/amd64:
14 casos T019/T020 (incluido lector EXIT retrasado), receipt/result, snapshot, no-result pendiente, exit23 y retry.
Web Vitest: 341 pruebas. Rust/TUI contra servidor temporal real: 252 pruebas.
Python: 2680 passed/4 skipped y gate adicional 368 passed/5 skipped. Herdr real
es evidencia histórica; su repetición fresca se omitió por CLI ausente.
Contratos internos tipados y tests HTTP/Rust sustituyen Pact: no hay un servicio
remoto separado desplegado al que publicar un contrato en esta entrega.

## Root causes found

1. Work exigía registro antes de comprobar ausencia de provisión, y emitía
   credenciales para todos los scripts: regresión 403/503 en legacy.
2. YAML declaraba completion sin conservar la respuesta libre en result_json.
3. La ruta HTTP mezclaba el protocolo managed con el dueño legacy de replay;
   tres pruebas de arquitectura detectaron decisiones y escritura en ese handler.
4. Shutdown conservaba objetos de autoridad/proyección del store anterior.
5. Recovery de Bubblewrap aceptaba valores truthy como confirmación de cleanup.
6. Fixtures antiguas no expresaban loopback, contratos de intento, versión30 ni
   las firmas nuevas; el HTTP fixture capturaba el checkout del operador
   (7,46 s medidos) contra un timeout de 5 s. Ahora captura su directorio temporal
   y declara ausencia de baseline, sin aumentar timeout ni omitir la prueba.

7. Docker comprobaba el fin de attach antes de que su lector consumiera EXIT.
   La regresión retrasó la lectura después de confirmar el fin del proceso y
   reprodujo cleanup sólo forzado. Ahora el lector determina EXIT/EOF; el plazo,
   identidad, exit code y cleanup siguen siendo obligatorios. RED: un fallo;
   GREEN: dos casos normales/retrasados pasados.

## Residual risks

No matriz real facturable sin cuota/presupuesto confirmado. No integración
upstream verificada a partir de la simulación. No host de producción ni registro
Work por defecto. O03 conserva combinaciones no probadas. Graphify completo no
se refrescó por el bloqueo OneDrive/9p; las conclusiones se contrastaron con fuente.
Resultados finales y comandos: workflow-status.md y completion-evidence.md.
