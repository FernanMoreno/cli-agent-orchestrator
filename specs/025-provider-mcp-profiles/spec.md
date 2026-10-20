# Provider MCP profiles and current acceptance

Date: 2026-10-20. Status: incremental implementation in main; no live acceptance inferred.

## User scenarios

1. **P1: Diagnose current failures.** An operator can distinguish a provider timeout from an authentication dialog, monitor failure, tool denial, or missing receipt using sanitized evidence. A historical record remains unresolved until causal evidence exists.
2. **P1: Restrict agent collaboration.** An operator selects an explicit interaction or collaboration profile. The agent sees and can invoke only the selected operations; direct calls to other operations are rejected before dispatch. Human answers still require their existing authority.
3. **P2: Extend authority deliberately.** Coordinators, knowledge writers and elastic workers receive separately reviewed tool sets; the basic collaboration profile never implicitly gains those operations.
4. **P2: Accept current binaries and source.** Each provider/version is validated with isolated configuration, persistent messages/interactions, AG-UI projection, restart and owned cleanup. Offline results and historical receipts remain distinct from final acceptance.

## Requirements

- FR-001: Retain the exact restricted three-tool interaction profile and existing unmarked behavior for compatibility.
- FR-002: The initial collaboration profile adds only `send_message`, `list_siblings`, `cao_list_interactions`, `cao_get_interaction`, and `cao_escalate_interaction`. It excludes answering human questions, delegation, supervision and memory writes.
- FR-003: A selected profile must filter both catalog and direct dispatch, including unknown names and collisions. Resources, resource templates and prompts must remain inaccessible in restricted profiles.
- FR-004: Profiles restrict surface; they do not grant operation authority, broaden provider-native permissions or replace existing interaction, identity, project and server-selector checks.
- FR-005: Unknown or malformed explicit profile values fail closed before provider launch or server execution. Selection is per MCP child. Configuration supplied for another server must not receive CAO's protected policy or capability.
- FR-006: The historical OpenCode child marker always preserves the exact three-tool restriction, even when combined with a broader profile. Existing profiles are never silently widened.
- FR-007: Evidence must cover permitted/denied calls, empty/partial client permissions, cross-instance isolation and the entire registered CAO tool catalog. Final live acceptance must bind the source and runner actually executed.
- FR-008: Current Claude failures are diagnosed without changing the operator login or extending the 180-second task deadline. Intensive live tests are serialized after a fresh memory check. Reuse PostgreSQL only after identity verification.
- FR-009: Work messaging (`cao.work.message`) and generic `send_message` have separate acceptance. Preserve historical `RECONCILE_REQUIRED` records. OpenCode v1 requires its own pinned real binary. QEMU is limited to the four remaining cases.

## Success criteria

- SC-001: In offline transport tests, both profile catalogs and direct invocation match their exact sets, and every excluded registered operation is denied before its handler executes.
- SC-002: Independent child instances cannot affect each other's policy. Malformed selections do not reach unrestricted startup.
- SC-003: Existing interaction contracts and affected configuration tests continue passing without changed gates.
- SC-004: Each numbered provider pending item has an explicit execution task or deferred acceptance task below; completed means evidence exists, not merely a proposal.

## Assumptions and scope

The first implementation adds an opt-in server policy primitive through CAO's existing protected MCP configuration overlay. It does not yet enable expanded provider permissions, change OpenCode's legacy three-tool marker, implement coordination profiles, or claim a real provider acceptance. Those are dependent tasks. The user has authorized ongoing implementation in main without commits or publication.

## Timeout observability increment (CL-01, 2026-10-20)

- FR-010: A completion timeout must expose only factual, closed observations: completion phase, final terminal status, whether working was observed, receipt-probe error count, whether a receipt candidate was seen, current stable receipt observation count and whether idle extraction was attempted. It must retain kind/status, delivery uncertainty, existing deadlines, quota/error precedence and no-retry semantics.
- SC-005: Offline timeout tests must distinguish no-pickup, observed work, candidate receipt and probe error without retaining Unicode prompt/receipt/error content. An API-boundary test must propagate those observations without changing the existing 504 or non-timeout responses. These observations are diagnostic, not a demonstrated historical cause or a new completion signal.

## T007 increment: effective client filters and profile-aware preflight

- FR-011: OpenCode v2 preflight must require every tool in the effective selected profile, reading the generated child's actual `environment` field. The historical marker intersects that profile with the three interaction tools; absent profile metadata retains the existing three-tool preflight. Invalid profile/environment shape or a policy in an ignored environment field must fail before launch. Existing last-rule-wins/resource/name-collision and Code Mode checks remain unchanged.
- FR-012: With an explicit profile, Codex's advertised `enabled_tools` must be the intersection of the selected server policy and any explicit client allowlist. An absent/None client filter becomes the profile's set; an empty filter stays empty; partial filters are never expanded. Unknown/banned names are removed, malformed lists fail before launch, and the legacy marker can only narrow the policy. Profiles without an explicit policy retain existing behavior. No operation authority or native file/shell permissions are granted by this transformation.
- SC-006: Offline tests must demonstrate v2 rejection when any collaboration tool lacks its effective client grant, exact legacy-marker intersection, invalid selections/environment rejection, and Codex's emitted TOML for empty/partial/absent filters and special quoted tool names without broadening.

- FR-013: Until per-agent native installation propagation is implemented, OpenCode installation must reject an explicit `cao_tool_profile` before writing native agent/config/context artifacts or changing recorded provider metadata. Native config translation must also reject the unsupported key instead of silently discarding it. Existing profiles without that key retain installation support. This is an interim fail-closed guard, not completion of per-agent policy installation.

- FR-014: A rejected installation must exit the CLI with a nonzero status while retaining the existing sanitized `Error:` message. A service result with success=false must not be reported to shell callers as successful exit 0. Successful installations remain exit 0.

- FR-015: OpenCode installation must also reject either reserved policy marker supplied in env/environment, before a per-agent selection can leak into a shared MCP declaration. This guard does not claim atomic rollback of existing URL download or --env staging behavior; it guarantees the native/provider-record/context boundary after profile resolution.

## Reproduced native catalog startup race

- FR-016: The isolated OpenCode v2 catalog test must not treat a ready /doc endpoint as proof that its private service.json authentication receipt is already readable and complete. It must wait for a nonempty string password only within the existing shared 15-second startup deadline, stop when the owned service exits, and retain the existing HTTP/catalog assertions, process cleanup and 45-second outer bound. No operator credential path or login is used.
- SC-007: Offline regression tests cover delayed receipt creation, partial JSON, invalid/missing password, deadline expiry and exited service. A single corrected native catalog execution must bind the existing pinned binary; preserve the prior failed receipt instead of overwriting it.

## T007: explicit-policy preflight without interaction opt-in

- FR-017: OpenCode v2 initialization must validate the effective generated grants for every enabled canonical CAO MCP child carrying an explicit tool profile, even when the terminal has not requested the interaction channel. Multiple configured children must each pass. The existing interaction request still validates its selected child. Unprofiled launches without interaction opt-in preserve their legacy behavior; successful profile validation alone never advertises an interaction capability.
- SC-008: Initialization regressions must reject absent/partial grants and a denied second profiled server before backend launch, clean owned temporary configuration on failure, and accept complete grants in both native MCP layouts without mutating the source config or enabling an unrequested interaction channel. Disabled children remain inactive. This does not enable native policy installation or close T022.
