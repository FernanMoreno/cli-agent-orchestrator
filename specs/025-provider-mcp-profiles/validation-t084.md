# T084 — Native typed-text versus rendered Unicode diagnostic

Date: 2026-10-23. Status: native two-phase acceptance PASS; conditional causal observation not triggered. This diagnostic is not full v2, restart, cao.work.message or global acceptance.

## Why this observation

T082 completed the sender turn but the public LAST marker spanned two lines. T083 compared normal/`tmux -J`/normal on the same stable pane: all kept that line break, so no logical-history parser fix is justified by those results. T084 compares the current native typed assistant text with that rendering; neither representation is normalized for acceptance.

## Boundaries and minimization

Use only the sender's exact-key PrivateConfigOwnership receipt and root marker under the owned run directory. The operator DB is never opened by this probe. No directory-wide history scan or full native trace is added. The private clone may contain operator history; therefore the fresh nonce must match the current PostgreSQL receipt digest (phase result_verified), select exactly one native user message, and restrict assistant messages to the same session after that user and before the next user.

Read only typed assistant text parts; exclude reasoning, tool state and user bodies from returned rows. Verify expected session_message table/columns, query_only mode and a deny-by-default SQLite authorizer. Limit returned parts to 16, combined text to 64 KiB, input buffers to 64 KiB and each private DB/sidecar to 64 MiB. Use a three-second local budget within the unchanged 75-second model deadline; the SQL progress handler and PG connect/statement/lock limits bound reads. Export only counts, shape booleans and classification, never nonce, message/session IDs, raw text, credentials or paths.

## Diagnostic outcome contract

The observer does not change LAST, settle a turn, normalize text, extend a deadline or alter any acceptance helper. Once it has a decisive shape observation, this run may stop with native_text_diagnostic_observed as an explicit FAILED diagnostic, rather than waiting for the known marker failure. Such a stop does not claim receiver, restart or full acceptance.

Exact native text plus different rendering points to the rendering/extraction boundary. A line break already in typed native text only proves its presence in the persisted native message; it does not prove what the raw HTTP model response contained. Unavailable, ambiguous, oversized or mismatched sources fail closed and preserve uncertainty.

## Preparation evidence

31 pure controls passed against fresh disposable SQLite fixtures, including cross-session/sequence boundaries, duplicated or stale nonce, reasoning/tool exclusion, schema mismatch, readonly bytes, symlink rejection, credential-read/write/ATTACH denial, size/deadline bounds and PostgreSQL readonly/phase checks. No provider or operational database was contacted by these controls. The source-bound closed runner passed 274 inherited self-check controls; its acceptance functions remain unchanged. The independent read review and final gate identities are recorded below.

## Actual result

On source HEAD `682797ec6e47aa0845d0153e461a0e415aa81016`, the unchanged two-phase native-v2 acceptance passed in 100.593 seconds: two exact PostgreSQL/API messages, exact Unicode LAST and receiver public ACK in both phases, each receiver turn completed once, AG-UI notification/capture checks, API process replacement and stable terminal/receipt/native identities across restart. Phase model timings sender/receiver were 23.091/6.527 seconds and 11.348/5.383 seconds; minimum admitted memory was 7,305 MiB, above the unchanged 6,144 MiB floor.

The line-break discrepancy did not recur, so the conditional native-text observer was never invoked. No native DB query was performed by this observer in the actual run, and no causal comparison was obtained. This is a current-source native-v2 PASS, not proof that the historical Unicode discrepancy is fixed. T084's causal observation remains unfulfilled; do not repeat unchanged runs solely to force its trigger.

All API/tmux/owned PostgreSQL/private-state cleanup checks passed, and operator auth, source, binary, runner and interpreter remained unchanged. The executed runner bytes were preserved and the gate closed. Executed SHA256 `229fdef1b0959a8b7c3f2c6a6bf90c9323923cc7d5509c7e26178003a1922ac4`; closed runner `60e95ef5174bac791937ac9869a4de45c7d945bc5f43d906d8801f33c76b1c4f`; helper `431bbcd27d2747136c4a81ed99b32f50fb4782c5850ed25ff799ebf8591ee8aa`. Independent Luna source review approved attribution and the revised two-stage size check before execution; root ran the 31 controls and 274 self-checks.

This does not close v1 refresh, human-interaction/profile/model matrices, external Work acceptance, multi-node scale or global gates. [Sanitized receipts](validation-t077-native-receipts.json) preserve both failures and this new PASS.
