# T056 — native-v1 current credential-error classification

Classification: bug-significant (provider status → monitor/API/task failure). Existing Spec025 FR043/SC027 and incremental plan/task define the bounded change; requirements checklist6/6, no extension hook. Repository specs own the evidence. The existing registered-vault identity conflict remains untouched; no vault was substituted. Codex only, no peer process or credential/login changes.

## Reproduction and first incorrect boundary

T054's actual invalid-key diagnostic on4f032e95 used the requested LongCat/OpenCode Go model and displayed `Upstream request failed: Invalid credential`, but CAOS returned idle/LAST202. The generic error headings omit that native rendering, and the separate auth detector applies only to v2. The first incorrect boundary is v1 viewport/raw status classification, not model availability, authentication selection or final-receipt authorization. The sanitized retained49-row viewport removes prompts, receipts, identifiers, timestamps and private paths while preserving the error body/toast and native chrome.

Before implementation, bounded Graphify inspection found both OpenCode status paths and the v2 helper (three direct nodes/17 edges); source verified their order plus monitor, receipt/extraction and agent-step error consumers. Spec → plan → T056 preceded implementation. RED: **five failures/28 controls passed** on both raw/screen paths, including an older completion followed by a new actual failure. Initial GREEN:33 passed. Codex review reproduced an active footer containing both `esc interrupt` and ordinary commands (**two failures/33 controls**); the helper now explicitly excludes that processing footer. Final **35 passed**,44.16 s; original RED/GREEN/review receipts retained.

## Implementation and limits

A pure v1-only helper shared by raw and screen classification requires a current native version1 idle footer, current Go model chrome, exact standalone credential-error body and separately framed toast. Earlier-frame errors and a later completed turn are refused. Active vendor retries keep processing semantics. Quota precedence, v2 detection, native backend status authority, operation permissions, task delivery, retry/deadline policy and receipt gates are unchanged. No DB/API/event schema, migration, mutable global cache or new subprocess. Detection is deliberately bounded to the observed pinned native presentation; different provider wording/layout needs separate evidence.

Expanded/style/type/composition and committed-source actual diagnostics are recorded below as they finish. T054 timeout/cancellation and full OC1/global acceptance remain open.

## Offline/composition verification

Expanded serial regression passed **565 tests**, two existing warnings, **212.42 s**, admission **7,944 MiB** against6,144. The previous17-module provider/native-policy/installer/preallocation/MCP-readiness/PostgreSQL-only-guard selection is preserved, with the new35 classifier cases and three existing agent-step contracts that stop on ERROR without extracting output. Markers and configured coverage unchanged; focused22% coverage is not the global gate. Typing: **405 source files**, no issues; Black/isort: both changed Python files pass. Project architecture/composition: **five kept/zero broken**.

Composition review: inspected raw/screen classifier → monitor/API status → task error propagation and receipt/extraction neighbors. Active retries, quota precedence, historical frames, later completion and v1/v2 isolation are checked. State/transaction/message/migration boundaries are unchanged; no independently evolving wire shape requires a new Pact contract. The existing real MCP stdio cases remain in the selection; actual PostgreSQL/API/provider boundaries still require the diagnostics below. Codex review corrected the active-footer false positive before final verification. Final diff reviewed; bounded AST refresh **93 nodes/209 edges**, no model tokens, main generated graph preserved. No durable vault write is needed for this incremental classifier/evidence.

Verdict: **PASS WITH RISKS** for the offline implementation candidate. Actual invalid-key refusal and successful authenticated control on committed source remain required. Broader presentation/authentication support, timeout/cancellation, expanded profiles/messages/installer and global NFR/coverage remain open.
