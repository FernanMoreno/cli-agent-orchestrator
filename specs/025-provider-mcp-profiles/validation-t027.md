# T027: explicit-profile initialization preflight

## Reproduced boundary

OpenCode v2's protected overlay consumed explicit `cao_tool_profile` metadata and generated the child policy even without interaction opt-in. Initialization nevertheless passed `server_name=None` to the grant validator in that case. The validator returned without inspecting the selected agent's effective grants, and initialization reached the backend launch with absent/partial grants or a denied second child. This is an availability/configuration failure; the server profile middleware continued to restrict its surface.

The corrected original regression was RED with six failures and eight passes. An earlier diagnostic included two incorrect assertions that readiness must not be awaited; current source already waits for active CAO MCP readiness independently of interaction announcement. Those assertions were corrected to preserve the existing behavior. Both logs remain private; no rejected assertion was made into a product requirement.

## Correction and review

Initialization enumerates active policy-marked children from its protected overlay output, then applies the existing generated-config validator to each plus the existing interaction-selected child. It preserves legacy no-profile/no-opt-in behavior, cleanup before failed launch, source config nonmutation, and opt-in-only interaction announcement. It adds no operation authority, capability issuance, global policy or timeout extension.

The first independent Luna review found two actionable issues: only looking in `environment` missed explicit text commands whose marker is in `env`, and the new requirement reused FR-016. Two integrated text-command regressions reproduced that gap (two failures, 18 deselected); the scan now includes both fields so the native v2 validator rejects the invalid command before launch. The new requirement is FR-017. SC-008 is unique.

## Verification status

The initial corrected 18-case variant passed in the eight-module selection: 277 passed, zero failures/skips, eight warnings in 83.55 seconds with unchanged project coverage and marker defaults. That receipt predates the text-command correction and does not validate the final variant. Its JUnit SHA-256 is `eb9e5defba9b34d4a10e850ab592e2738466ba19d760c2a078842ce981e53dcc`.

Final contracts, typing and independent correction review are recorded below when complete. Private logs/JUnit and source seals live in the publication cache; no credentials or provider transcripts are publication inputs.

## Composition and acceptance limits

The changed subsystem is OpenCode provider initialization. Reviewed neighbors are protected MCP overlay, v1-to-v2 translation, generated per-agent grants, initial readiness, interaction capability reporting and owned private-directory cleanup. Tests use actual overlay/translation/validation with fake shell/backend readiness and synthetic credential paths; no model is invoked. The existing PostgreSQL container is reused and identity/image/owner/profile/health verified for older autouse fixtures; this is not evidence of a new persisted-message or live-provider journey.

No schema, database transaction, migration, event, delivery, timeout, cancellation, credential or server operation-authority behavior changes. Source inspection verifies that failure propagates through existing cleanup before `send_keys`. Multiple children, both native layouts, partial/absent/complete grants, missing selected agent, normalized-name collisions, disabled children, legacy unprofiled behavior and text-command rejection are covered.

T027 is a bounded T007 increment. Native policy publication remains rejected by the installer pending artifact-bound per-agent/partial-install design in T022. T007/T022, OpenCode v1 and extended live acceptance remain open. Spec021's loaded strict NFR and global coverage/scalability gates remain open; focused coverage is not a global floor result. Broad Graphify output was truncated and its provider line location was stale; the affected call chain was verified against current source. This local branch change introduces no production symbol requiring a structural graph rebuild. Repository artifacts own the incremental findings; no durable vault write is needed.

## Final contract and composition evidence

The final eight-module selection passed **279 tests, zero failures/errors/skips and eight warnings in 82.64 seconds**, including all **20** new initialization cases. Four workers used the unchanged project coverage and marker defaults, private HOME/XDG/temporaries and the verified existing PostgreSQL container with autostart disabled; initial available memory was 7,526 MiB. This scoped selection is not the complete backend or a global coverage result. Final JUnit SHA-256: `e6f2471133c06560a9e1884402026f7a41119f030637725b9e8329a29aa7e76f`.

Final source SHA-256:

- `providers/opencode_cli.py`: `c88d7e509d3e9e8a9de6c4bbfd37fadf74718f0bc0ee8c082d404eb431b8cc7b`.
- `test/providers/test_opencode_profile_initialization.py`: `1b2ae238f97962860d42070b70ac112f22980f0c7d2698a1601d5333c36f8c87`.

Black/isort and scoped whitespace checks passed. The final project composition check passed with five architecture contracts kept and zero broken. Composition verdict: **PASS WITH RISKS** for this bounded preflight correction; native installation, real-provider acceptance and the pre-existing global NFR/coverage failures remain open. No external HTTP/message contract changed, so the actual in-process MCP transport and configuration contracts are appropriate here; this adds no separate Pact contract.

The canonical CI typing command is `mypy src/`; its first full run passed all 403 source files. An earlier single-entry invocation reported one `work_origin.py:578` inferred-type error; that failure is retained separately and was not suppressed or patched. The unchanged detached validation source passed that single-entry command, so it is not attributed here to a proved pre-existing cause. The final full command is repeated after the last source edit, without typing config or ignore changes.

The final post-edit `mypy src/` invocation passed all 403 source files with exit zero. No source/test edit followed the final contract run or this typing/composition verification.

Independent GPT-6 Luna Max/ECC correction review approved the final delta with no remaining actionable findings, confirming both environment fields, rejection before backend launch, unique FR-017 and unchanged interaction announcement. The reviewer used the lead's executable results and did not claim to run tests. Claude's prior read-mode request was blocked by its advertised session quota; no authentication or repeated pre-reset quota attempts were made. This is not recorded as a completed Claude review of T027.
