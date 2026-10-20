# T022: managed native OpenCode policy publication

Date: 2026-10-20. Implementation and offline configuration acceptance; no new live-provider or global acceptance.

## Integrated behavior

Native OpenCode installation now consumes valid `cao_tool_profile` selections through a per-agent publication protocol. The agent's Markdown body contains a strictly parsed, versioned envelope; no extra OpenCode schema fields or policy sidecar are used. Before changing the provider record, context or shared MCP configuration, the installer atomically publishes `pending`. The final atomic replacement publishes `ready`, bound to the normalized native metadata/content, selected-agent tool overrides and exact shared declarations for the selected servers. A failed install or plugin refresh leaves pending or the previous complete artifact; reinstall/refresh repairs it. Returning to an unprofiled configuration retains a managed empty envelope.

Raw reserved environment markers, invalid profiles, noncanonical source commands, disabled/non-boolean flags and non-local declarations are refused before publication. Direct native translation still refuses unconsumed policy metadata. The installer captures the exact expected native translations before pending, and checks them against the same completed snapshot used to bind ready. Both foreign local commands and foreign local environments are rejected, as are remote substitutions. Executable basenames alone are not treated as identity evidence.

CAO v2 freezes the selected native payload and validates it against the shared snapshot that will be translated. It pins only bound profiled children to the current approved CAO module in the private copy, then uses the existing protected overlay and T027 grant preflight. Shared persisted launchers retain their original resolution for legacy users. Source registration uses the same validated projection; the historical interaction opt-in still intersects the profile with the three interaction tools. The profile does not itself grant operation authority or an interaction channel.

The envelope is removed from the model prompt. Other managed agents are omitted from that terminal's private catalog; ordinary unmarked agents retain their existing behavior. Managed v1 launches are rejected, including ready empty-profile envelopes. These guarantees require CAO-managed v2 launch: direct external OpenCode invocation does not enforce the envelope. The digest guarantees consistency, not authenticity against an operator deliberately rewriting/removing the envelope and recomputing its digest. Existing CAO v2 terminal policies are frozen; reinstall does not hot-update them. URL/local-import and environment staging remain outside this native publication protocol.

## Reproduction and corrections

- Initial integrated RED: two valid installations were refused and a pending native artifact reached launch (three failures). The first implementation passed those three cases.
- Expanded boundary tests exposed a legacy repair regression (one failure, 56 passes): decoding an old native file prevented replacing invalid UTF-8. Binary marker detection restored legacy repair; the 57-case run passed. Legacy launch still rejects unreadable files, as before.
- A stable launcher was not recognized by interaction source registration (one failing focused regression). Applying the validated private projection fixed registration; the 18-case provider selection passed, including the historical three-tool intersection.
- An initial full typing run reported two new annotation errors; explicit result/container types corrected them without casts, ignores or typing-config changes.
- Independent review identified foreign shared configuration being adopted at ready publication and YAML-dependent hashes. The added review cohort reproduced six failures (publication substitution, four ignored enable-flag shapes and YAML formatting); one CRLF regression already passed. The code now checks the exact expected declaration and hashes metadata/content as canonical JSON. No speculative newline rewrite was introduced.
- Review then identified that a remote-only substitution test could be masked by the local-transport guard. Final tests also substitute a different local command and an unrelated local environment, isolating the exact-declaration check.

All earlier logs/receipts remain private publication-cache evidence and are not retrospectively relabeled green.

## Final executable evidence

The final 17-module selection passed **527 tests, zero failures/errors/skips and eight warnings in 108.09 seconds**, including **72 new native-policy cases**. It used four workers, unchanged project coverage and marker defaults, private HOME/XDG/temporaries and the existing PostgreSQL container with automatic provisioning disabled. Initial available memory was **7,156 MiB**. Container identity, pinned image, owner/profile labels and healthy state were checked before the regression scope. This selection does not establish a global coverage floor.

JUnit SHA-256: `11b21326884124cd0e5581c605cbc7af5efe265878c428c6b81395cdaada5a0a`.

The canonical `mypy src/` check passed **404 source files**. Black/isort and scoped whitespace checks passed. The final project composition gate preserved **five architecture contracts with zero broken**. Final source SHA-256s and runner receipts are retained in the private publication cache, separate from prompts/credentials.

Coverage crosses real installer serialization, native-file publication, profile normalization, actual protected overlay, v2 translation, source registration and grant validation; shell/backend readiness is mocked and no model is invoked. Faults cover pending publication, provider/context writes, a partial grant publication and actual final `os.replace` failure. Tests also cover refresh repair, two agents sharing a server with distinct policies, global-tool changes, stable launcher persistence, v1 refusal, tampered bindings, missing/disabled servers, frozen native input, Unicode, CRLF, parser bounds/duplicates and private catalog/prompt isolation.

## Independent review and composition

Claude completed two read-only design consultations, a product review and a correction review through codex-claude-bridge. The product review found the publication consistency gap; the correction review approved the corrected scope and requested the stronger local-substitution tests, which are included in the final passing run. It reviewed the product boundaries and selected CLI cases/source-registration code, relying on the lead's test results; it did not claim to execute checks or audit every new test body.

The changed composition is native installer → atomic agent artifact → source registration/provider → protected MCP overlay → v2 writer/preflight. Reviewed invariants are no shared policy leakage, no extra operation authority, immutable per-terminal selection, consistent snapshots, bounded metadata, failure-before-backend, cleanup, legacy compatibility and refresh recovery. No database schema/transaction, event, message, model deadline, login or native file/shell permission policy changed. Actual configuration and in-process MCP transport tests are appropriate for this scope; no external HTTP/message contract changed.

Graphify's initial broad query was truncated and its provider positions stale. A deterministic bounded AST refresh of the affected code was merged into a private graph copy, preserving the main graph and unrelated generated work; source locations and file hashes bind that evidence. It uses no semantic model tokens. The first private refresh script assumed `edges` instead of this graph's `links` key and failed before extraction; its failure was retained and corrected. Repository specs own the design and results; no substitute Obsidian vault was accessed.

Composition verdict: **PASS WITH RISKS** for T022's implementation. T007, extended live v2 acceptance, pinned recent v1 acceptance, Claude timeout diagnosis, external Work messaging and Spec021's strict loaded NFR/global coverage gates remain open. No historical reconciliation job or QEMU case was replayed.
