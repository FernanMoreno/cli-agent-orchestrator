# T007 validation increment — 2026-10-20

## Integrated scope

Main worktree; no project commit/push/publication. T007 remains open for actual per-agent native publication (T022) and expanded-profile live acceptance. This increment implements effective Codex client filtering, profile-aware OpenCode v2 preflight, fail-closed native installation, correct CLI failure exit and a reproduced race in the native catalog fixture.

Private evidence: `/home/felni/.cache/caos-spec025-preflight-r1/`. The prior Spec 025 source receipt remains historical; its 12 selected inputs had zero drift when this increment began. New evidence binds the current selected inputs and does not claim a full-tree freeze.

## Source and reproduced boundaries

- Codex serialized broad/absent enabled_tools independently of the selected policy. It now intersects the protected profile and any explicit client filter, retaining empty/partial restrictions and TOML escaping. No profile retains legacy serialization. Unknown/malformed client lists still fail; legacy marker narrows to three.
- OpenCode v2 preflight required only the historical three tools. It now derives the effective profile from generated environment, preserves marker intersection, and checks every required tool under existing last-rule-wins/resource/Code Mode/collision rules. A selected profile in an ignored env field or malformed environment fails locally. The existing server_name=None no-op remains; T022 must activate validation for an explicit profile even without interaction-source opt-in.
- Native translation discarded cao_tool_profile. Installation now rejects explicit metadata and reserved policy variables in env/environment before changing provider metadata, context or native artifacts. Direct translation has the same guard. The existing grants sidecar is best-effort and is not used as policy authority.
- A service rejection was printed as Error while the CLI exited zero. ClickException now retains Error on stderr and exits nonzero. Other callers of install_agent retain its structured result. The existing regression was strengthened from zero to nonzero failure, not weakened.

## Staging and compatibility limits

The preservation regression covers an existing named source without staging options. URL download, CLI local-file copy into the local store and explicit --env staging already occur before this guard; they are not rolled back. Do not interpret failure as a globally atomic transaction. T022 must design policy/native artifact binding and publication ordering for partial failures. No new field is written into OpenCode's closed native schema and no per-agent policy is placed in the shared MCP environment.

## Native catalog race and retained evidence

Pinned binary: `/home/felni/.opencode/bin/opencode`, version `opencode v2.0.18`, SHA-256 `10d405161d8b9595f4a2ec31254e961969e6ca3f8239ac9bd55b6ff6431dc24f`. Available memory measured 5,575 MiB before preparation; binary-pin metadata records the subsequent launch reading. No model task, operator credential copy, PostgreSQL/Docker/QEMU or historical job was involved.

The first native catalog check passed. A fresh-source repeat then failed: service_ready=true, zero config/agent polls, authentication receipt not initialized. The failed-run private service.json was present after teardown according to metadata-only inspection. The original fixture did one scan immediately after /doc readiness, incorrectly treating a not-yet-readable or partially published receipt as permanent failure.

The bounded helper now polls for a nonempty string password under the same already-created 15-second startup deadline; an exited service or expired deadline prevents acceptance. Sleep uses min(0.1, remaining). Its exact source is injected into the existing bwrap driver. HTTP/catalog assertions, target identity, namespace isolation, 45-second outer bound and finally cleanup remain intact; a new boolean service_auth_ready strengthens evidence. No password enters the report.

`native-catalog.log` preserves the initial pass, `native-catalog-final.log` preserves the failure, and `native-catalog-fixed.log` records the corrected pass. None is used to explain the separate Claude 180-second timeout or to claim expanded-profile acceptance.

## TDD/checks

- Profile/preflight RED: 23 failures, 14 passes before correction; then 38 focused cases pass including the legacy marker.
- Installer RED: five failures showed silent policy loss. After the service guard, four CLI failures exposed exit-zero behavior; the strengthened standalone CLI regression also failed before its fix.
- Reserved environment coverage was added as follow-up validation, not counted as a RED result.
- Catalog helper RED: nine failures before helper implementation. Eleven deterministic tests cover delayed/partial files, malformed values, exact deadline, exited service and transient directory enumeration errors.
- Final combined offline selection: 319 passed, zero skips, with the one native catalog case deselected and run separately. Native corrected check: one pass, zero skips, no model invocation.

## Composition review

Boundaries: protected config → client TOML; generated native child env → preflight; installer resolution → native publication; service failure → CLI exit; HTTP readiness → private receipt publication → authenticated catalog queries. Immutable policy vocabulary is shared; empty/partial filters never widen. No production DB/API/event/message schema, transaction, retry, model timeout, resource owner or credential behavior changes. Local native publication is intentionally blocked until T022 can protect partial-install transitions.

Pure tests use actual TOML parsing, CLI runner and private filesystem paths; real FastMCP transport suites preserve the surface/dispatch boundary. Native catalog uses the pinned binary inside the existing private bwrap namespace. PostgreSQL/Testcontainers/Pact are not needed for these local configuration and fixture boundaries; persisted messaging, restart and provider matrix acceptance remain separate gates.

Independent Claude source review verified preflight/serializer behavior and early guard placement; recommendations about reserved env and local-copy staging were incorporated. It confirmed that API/orchestration service callers inspect success=False independently of the CLI. A separate review covers the catalog fixture race. Source/graph limitations are in graphify-impact.md; generated graph refresh is deferred for these existing dependency directions. No durable vault write is needed for transient execution receipts.

## Final source review and evidence limits

Claude approved the bounded catalog helper and integration (job `bb21683822cc46e0a8a10297a729522a`). A minor directory-enumeration recommendation was then reproduced with one failing fake-clock case and corrected by keeping transient rglob OSError inside the same remaining deadline. Each native invocation uses a new pytest tmp_path and starts exactly one private service; the directory is not reused between runs. No extra native repetition is used to discard the preserved failure. A fresh native check after this final helper change is recorded separately.

The exact real interleaving (file creation delay versus partial JSON/readability) was not timestamped and remains unproven. The one-shot-read defect is demonstrated by source and deterministic delayed/partial-publication regressions; the corrected native pass does not retrospectively prove a single OS-level cause or the absence of all intermittent failures.

A final root boundary check reproduced another malformed-shape bypass: env expressed as a YAML list of pairs was accepted by dict(...) after the reserved-key guard, publishing the reserved marker in shared native config. The new regression failed with a successful CLI exit before correction. The validator now rejects non-Mapping env/environment before coercion, preserving None as the legacy unset case. This is required to enforce the same boundary for every accepted input shape; no valid mapping configuration is widened or removed.

## Final result

- **319 offline tests passed**, no failures/skips. One native catalog case was selected separately, not omitted from verification. The selected files include full current configuration utilities, CLI install, profile/transport contracts and deterministic auth-publication tests; this is not the full backend/provider suite.
- **Final pinned native catalog: 1 passed**, zero skips, current source. `native-safe-reports.json` preserves both the earlier failure and final booleans: service_ready, service_auth_ready, service_context_warmed, target_found all true; command_exit=0. No credential fields are retained.
- Black/isort passed for all nine changed Python files. Architecture/composition kept all five contracts, zero broken. Scoped diff and new-file/link validation passed. Existing Pydantic deprecation warnings remain.
- Independent reviews approved the limited T007 corrections and the catalog fixture integration. The final directory-enumeration handler and malformed-list env rejection were root corrections of reviewed boundary concerns, each with a failing regression and a final full selection; their exact final bytes were not independently re-reviewed.
- **Verdict: PASS for this scoped increment; T007/T022 and live provider acceptance remain open.** This record does not close Work/generic messages, real v1, per-agent native publication, source freeze, model capacity/concurrency or the Claude 504 diagnosis.

Selected-source manifest SHA-256: `a149191c34fde003a6792d79114695359a9fb77104eb8ebda770160142eee280` (10 selected inputs; nine changed Python files plus immutable profile vocabulary). Receipt SHA-256: `c8844585c0c174ae591dc92e5a3e8ddcccf9971622cea4313e9baed1e83faec5`. Private bundle `receipt.json` preserves log hashes and safe native report. This is a post-verification selected-source snapshot, not final provider/full-tree acceptance.
