# Implementation Plan: Integración completa de mejoras del fork

**Branch**: main | **Date**: 2026-10-02 | **Spec**: [spec.md](spec.md)

## Summary
Integración por componentes, con ledger de cobertura. No se reemplazan árboles enteros: se adaptan comportamientos de referencias congeladas conservando contratos Work/007/browser y mejoras locales. La instrucción del usuario autoriza esta selección y desarrollo en main. El alcance se termina cuando todas las capacidades quedan implementadas o su equivalencia funcional está probada.

## Technical Context
- Python >=3.10, FastAPI/Pydantic2, SQLite/SQLAlchemy, FastMCP; tmux/herdr y backends Work.
- TypeScript/React18/Vite: web y MCP Apps; Rust TUI.
- Persistencia: modelos/repositorios existentes, migraciones aditivas; snapshots y artefactos con digest.
- Pruebas: pytest sin cobertura compartida, Vitest/build, cargo; fixtures de providers, modelo de interleavings y pruebas aisladas de backend.
- Plataforma: Linux/WSL/macOS compatibles con las capacidades vigentes; bridge y Kubernetes configurables.
- Límites: never replay incierto, selección por generación/identidad; autoridad local no se transmite a nodo remoto; presupuestos de captura, cola, corrección y launch independientes.

## Constitution Check
PASS antes y después de diseño: evidencia en auditorías; snapshot de cambios previos; secuencia Spec Kit antes de implementación; regresiones antes de fixes; cambios significativos con composición; cierre sólo con ledger completo. No commits/push/releases o activación externa. Desarrollo en main prevalece sobre sugerencias de worktree/feature branch. No hooks .specify/extensions.yml registrados.

## Componentes y orden
1. C01: P1 y recuperación local (agent_step/workflow_service, manager, terminal_service, Codex).
2. C02: política efectiva/roles/selección MCP, enforcement y Kiro install; diagnóstico shim y OpenCode/Kiro parser.
3. C03: consumidores API/CLI/MCP/ops/Apps/web/TUI y recursos Apps; cache/proyección/catálogo de grafo.
4. C04: lifecycle durable/backend identity/FIFO/env + Kimi Code/swarm + panes/layout/captions + protocolo causal adaptado.
5. C05: autoría/approval/plan-v2/scope/material/manifest; tareas/epics y checkpoints/continuación sobre Work.
6. C06: KAS profiles/policy; fleet multinodo + runtime_channel/bridge y ownership de remotos.
7. C07: contexto/progreso/controlador de follow-up, sweeps de recibos y eventos de continuación.
8. C08: ejemplos/build/deps/CI/docs, paridad/equivalencia, regresiones integradas y validación real aislada.

Cada componente es una entrega verificable y tiene tareas propias en tasks.md; no se reduce el alcance por ejecutarse después. Antes de modificar sus fronteras se consolidan notas de investigación del dominio en research.md. Archivos compartidos se editan secuencialmente o por un propietario único.

## Selección de alternativas
Orden: conservación de capacidades -> autoridad/identidad/evidencia durable -> recuperación/concurrencia -> compatibilidad -> pruebas -> complejidad. Branch ancestry/patch-id sólo localiza diferencias. Un equivalente necesita escenarios, pruebas y razón en coverage.json. Memoria/archive, perfiles CRUD, elastic/fleet CLI, TUI Rust y bounds de parsers actuales se conservan donde son más completos; capacidades ausentes se añaden.

## Contratos críticos
- Terminal.turn conserva objeto de recuperación; turn_sequence/turn_sequence_completed serán campos separados de observación.
- Final visual y backstop no verifican receipt ni autorizan retry.
- Manager no publica reconstrucción parcial; generación durable pendiente exige evidencia activa coherente.
- Errores post-send preservan terminal e incertidumbre; pre-send puede ser retryable si no hubo efecto.
- Contador/backoff/deadline locales sobreviven a DB caída; diagnóstico durable nunca habilita nueva entrega.
- Nuevas órdenes de continuación/follow-up durables son únicas por attempt/generation; ejecución exige grants actuales.
- Workflow publication usa CAS/lock y publicación atómica, plan-v2 no reinterpreta aprobaciones plan-v1.
- Runtime IDs e incarnaciones excluyen efectos de canales antiguos y cleanup sobre replacements.

## Project Structure
specs/008-integrate-fork-improvements/{spec,plan,tasks,research,data-model,quickstart}.md
specs/008-integrate-fork-improvements/{coverage.json,contracts/integration.md,checklists/requirements.md}
src/cli_agent_orchestrator/{providers,services,clients,api,mcp_server,ops_mcp_server,utils,models,backends,runtime_channel}
web/src; cao_mcp_apps/src; tui/src; test/{services,providers,clients,api,utils,runtime_channel}; scripts/examples/docs

## Verificación y cierre
Por tarea: regresión red/green, suite de frontera y revisión del diff. Por componente: contratos/concurrencia/errores parciales. Al finalizar: Python completo aplicable, frontends/builds/TUI, arquitectura y project-composition-check, system-composition-review, Graphify refresh comprobado contra fuente, ledger de equivalencia y evidencia real con límites. No usar número de tareas verdes como prueba de cobertura si quedan capacidades pendientes.

### Cierre operativo G04

La operación remota ya dispone de identidades, recibos y reconciliación durables. Antes de cerrar G04 se comprobará, contra el código y pruebas ejecutables, cómo un operador autorizado observa capacidad retenida, nodos desconectados y las condiciones necesarias para reanudar una ejecución. Se reutilizarán las consultas y transiciones existentes; solo una ausencia funcional demostrada justificará añadir una proyección diagnóstica acotada. Una expiración de lease nunca sustituye prueba de cese. Una restauración `blocked_restore` nunca se activa automáticamente ni emite credenciales nuevas.

## Autonomous acceptance repair (T058–T062)

The 2026-10-02 autonomous acceptance exposed an OpenCode v2 result-boundary defect and loss of native output observation after process restart. Repair at the owning layers: provider result parsing and dialect restoration; one identity-fenced reconstruction of the existing FIFO/output pipeline; existing generation-bound result settlement and inbox consumers. Never launch or repaste a task to recover observation. Backend errors and quotas retain pending receipts; they do not manufacture completion. Audit these contracts across every registered provider, with actual-runtime validation limited by credentials and quota.

Add failing regressions before fixes. Preserve v1 extraction, historical receipt rejection, Work ownership, incarnation checks and bounded late observations. Use the same external demo for acceptance: repair the missing statistics read under its storage lock and the inconsistent cleanup-control names; test with separate fixture data and actual Chromium. Record the repair as operator implementation, distinct from autonomous model changes in the earlier run. The original notes must retain their hash.

Native acceptance also reproduced an initialization/inbox race: a ready frame allowed a queued peer message to become the first native prompt before the initializer submitted its assignment. Shared terminal-input admission now fences every deferred provider before its row becomes visible; only the initializer holds the opaque first-task capability. Pending inbox rows retain their identity and become eligible after the original task is sent. Regression checks cover every registered provider and real SQLite queue ordering without duplicate delivery.

The native LongCat run also confused the assigning terminal ID with its own identity and treated a receipt sent through send_message as terminal completion. Clarify the shared receipt contract using the server-owned current terminal ID and an explicit terminal-reply requirement. A legacy inbox row carries a declared sender, so its contents alone do not authorize bypassing the existing terminal witness. Preserve cryptographic generation checks, transcript availability, and reconciliation when the model does not provide the required evidence.

Native peer coordination reproduced a further argument-contract mismatch when a randomly generated eight-character hexadecimal terminal ID contained only digits: the model supplied a JSON integer and repeatedly hit FastMCP's string validation. Advertise and accept only lossless eight-digit strict integers in send_message, canonicalizing them to the existing string API identity. Preserve strings including leading zeroes and remote identities; reject booleans, floats, short integers and longer integers before any delivery. Gemini retains provider-specific receipt state but uses the same pure receipt-contract formatter as the other strict adapters.

### Receipt verification admission

Native acceptance exposed an observation race during prepared-to-sent persistence and Codex commentary redraws without their transient spinner. Fence verifier admission while the local dispatch owns preparation through the sent CAS, release in a finally block even on uncertain transport, and retain the receipt identity. For an active Codex receipt, require an extracted current receipt or a native finished-duration marker bound to the current echoed delivery contract. Missing-receipt final evidence must still enter bounded reconciliation; commentary and old completed frames must not spend its budget. No new input or synthetic completion evidence is permitted.

### Native tool vocabulary under soft enforcement

The actual injected developer prompt described fs_* and execute_bash as literal available tools. Native Codex consequently refused its differently named shell/file tools despite those capabilities being granted. The shared soft-provider formatter must identify CAO category permissions and their native equivalents, preserve exact named-tool/MCP scopes and the explicit empty deny-all, and forbid unrelated plugin substitution. Update all six consuming launch-contract tests; no profile permissions or hard provider allowlists change.

### Claude optional feedback and final admission

A native supervisor capture contained its authentic final receipt and Baked duration, followed by the optional two-line rating panel. The extractor selected that panel as the last assistant bullet and discarded the real reply, permanently retaining its receipt and pending inbox. Filter only the exact rating panel between a native final duration and the composer rail, with no intervening response or new user turn. Apply current-receipt/native-final admission in both raw and viewport completion detectors; preserve explicit missing-receipt reconciliation and legacy no-receipt detection.

### Remaining native installations and Copilot sidebar footer

The resumed native check reproduced a Copilot 1.0.91 initialization timeout after the official Linux installation: the ready composer now ends with `← open sidebar · Autopilot · Allow All · / commands · tab next tab`. Extend the existing footer classifier, preserving legacy layouts, unfinished loading chrome, spinner precedence and output trimming. Treat cursor-addressed raw redraws conservatively as processing; erased dialogs must not latch waiting. Opt into the existing rate-limited, generation-fenced recovery from two concordant visible-pane observations. A fixed-size pyte replay was investigated but did not faithfully reproduce the native pane dimensions, so screen detection is not enabled. Verify current startup plus two distinct tasks through the real CAO API on the same demo, with original notes unchanged. Install the remaining vendor CLIs using official distribution channels; installation/version/help checks do not prove authenticated model execution. Record unmet authentication explicitly and preserve the Cursor executable when Grok's installer also publishes an `agent` alias. No permission, receipt, persistence or retry contract changes are needed.
