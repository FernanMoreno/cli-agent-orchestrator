# T210 — owned MCP shutdown before Claude retirement

Status: source implementation and the scoped native zero-turn control passed;
T209 is complete in that scope. T210 and full T197 remain open. The fresh r5
full-journey attempt stopped at expired OAuth admission before native startup.
No default activation or full-journey acceptance is claimed.

## Reproduced cause and changed boundary

T208 native r2 observed SIGINT, SIGTERM at about100ms and nonzero kernel wait9
at about509ms, with no physical input EOF. Static inspection of the frozen
Claude2.1.303 executable independently found connected stdio cleanup escalation
before transport close. This matches the sequence but does not identify the
runtime signal sender PID. Direct vendor500ms shutdown remains FAILED.

The new explicit local-stop guard closes its genuine input resource only after
an actual signal, quiescent wire state and no already-readable input. It raises
the installed SDK's supported ClosedResourceError; it does not manufacture EOF.
CAOS now requests that stop before ordinary Claude `/exit`, terminal writer
retirement and session writer retirement. The parent remains alive while exact
MCP identities retire under one shared original6s budget.

Per-terminal private immutable process-control records bind UID, boot, PID
namespace, birth, command and persisted launch environment. pidfds fence each
signal. Poll errors are unknown, never exit proof. A separate witness is written
only after genuine complete guarded SDK return. That witness plus identity
retirement does not independently establish a kernel process exit status0;
the real-SDK direct-child control additionally requires actual wait0, and native
acceptance retains its separate original audit, CLI, lifetime and cleanup gates.

The builder owns the registry path under the API home even when the child uses
a different private CAO_HOME_DIR. Parent environment alone does not activate
this mode. Remote/KAS/tombstone routes retain their existing authority checks;
API restart does not signal the child. Passive cleanup retains unknown/live
owners before deleting terminal resources, rows or worktrees.

## Validation history

- First canonical utility selection:15 passed,15 failed. All affected synthetic
  children exited before registration because a returned registry path was
  discarded and later referenced, raising NameError. The unpacking was fixed;
  no startup bypass or broader exception swallowing was added.
- Private isolated diagnostic after that fix:39/39 passed, without coverage.
  This is agent diagnostic evidence, not the canonical completion gate.
- First canonical integration selection:52 passed,7 failed. Two were deliberate
  RED controls for missing builder scope and a WAIT transition during Work
  lookup. Five exposed fixture defects: a non-context-manager Mock and a patch
  applied to the defining module instead of its imported lock alias. Fixes
  address those exact boundaries; no assertion or deadline was weakened.
- Canonical joint component/installed-SDK selection:108 passed,3 existing
  FastAPI deprecation warnings,493.80s. This includes genuine canonical MCP
  process0/audit seal before the parent closes input, with shared and separate
  API/MCP private homes, plus the explicitly failing vendor500ms behavior
  asserted by its negative control.
- Independent Claude implementation review found two blockers: authorized
  WorkBackendView exits incorrectly used the public denial path, and a second
  directory listing could silently absorb a newly registered live MCP into
  the scan baseline. Both were reproduced and fixed. The same scan race was
  also reproduced and fixed in passive reconciliation before resource cleanup.
- Canonical post-correction selection:68 passed,3 existing FastAPI warnings,
  135.41s, with unraisable exceptions promoted to errors. This includes the
  three new regressions and both actual canonical MCP/SDK process0 controls.
- Mypy:2 production modules pass. Composition/Import Linter:5 contracts kept,
  0 broken. Claude read-only correction review6f2f330ea10d47e3becb996348ab037f
  returns GO for the corrected implementation, excluding native/default/full
  T197 acceptance. Its earlier NO-GO remains in the private review history.
- The source-bound message/restart harness now includes the new transport,
  owner utility, session and cleanup modules in its mandatory source inventory;
  missing/mutated/symlink gates remain unchanged.
- Existing API/service/real PostgreSQL regression:245 passed,27 failed,
  5 warnings,156.86s under a held identity-verified owner. The shared failure
  is new passive reconciliation rejecting a legacy absent registry because
  the ordinary DrvFS home/tmp report mode777. Root reproduced this outside
  pytest with a positively absent exact registry. A scoped compatibility fix
  and negative controls are in progress: absence must preserve legacy cleanup,
  while an existing unsafe/unknown/live registry stays fenced. No operator
  permissions are changed and private record checks remain strict.
- The compatibility fix passed48isolated utility controls. The full canonical
  PostgreSQL/API/service/message-source-binding/component/actual-SDK selection
  then passed347cases,5 existing deprecation warnings,210.51s. The separately
  added real-utility/real-service bad-config-mode control passed1case in41.17s:
  UNKNOWN authorizes no signal/normal witness and does not block legacy cleanup
  when the exact owner registry is absent. Existing records remain fenced.
- Final compatibility review27d7be6339dd4fe3b0def4d8d1a37086 returns GO after
  verifying those actual call sites and the new composition control. Types2
  and architecture5/0 pass again on the corrected production bytes.
- Durable conclusions saved through Obsidian operation
  save-20261028-caos-t209-t210-owner-order; strict vault lint0issues. This is
  knowledge persistence, not native acceptance.
- Current source-bound native zero-turn is still pending. No global/default or
  fullT197 completion is inferred from the source/SDK/PostgreSQL checks.

The initial physical-EOF failures and direct500ms failure remain authoritative.
A successful component suite alone cannot close full T197 messages/API restart
acceptance. The next native control must use the canonical production module,
production ownership helper and genuine audit seal, with origin `caos_prestop`,
without a proxy, vendor patch, synthetic EOF, operator login copy or model turn.

## Composition review

Changed boundaries: MCP input resource/lifespan, provider configuration, exact
child ownership and terminal/session/GC retirement. Upstream API authorization,
WorkBackendView, PostgreSQL advisory dispatch locks and interaction WAIT checks
retain their authority before effects. Downstream FIFO/status/inbox/provider/
worktree/row cleanup remains gated by writer retirement and known-owner
reconciliation. No business schema, migration, public HTTP body or permission
expansion was introduced; business state stays PostgreSQL.

Scenarios include normal/failed/late/partial closure, real process0 and normal
SDK seal, same/different homes, new-owner races, pidfd errors, uncertain retirement,
public denial/managed Work admission, WAIT during lookup, remote/tombstone paths,
concurrent sessions/healthy siblings and actual PostgreSQL teardown retries.
Existing API and source-binding contract tests cover current consumers; no new
independently deployed wire contract requires a new Pact schema.

Architecture:5 kept/0broken. Source/SDK/PostgreSQL verdict:PASS, with native
zero-turn, default activation and fullT197 still explicitly pending. Graphify
was consulted and actual call paths verified against source. Refresh is deferred
to preserve the unrelated dirty generated graph; no generated facts were copied
to Obsidian. Source and executable checks are authoritative for the new utility.

## Extended typing check

The broader five-module wiring check initially found20errors, including three
introduced by the generic exit sender and Optional metadata access. The sender
now uses separate typed key/text call paths, and metadata is explicitly narrowed.
Fresh targeted exit/service/API regression:29 passed,5 existing warnings,49.03s.
A full HEAD shadow of all five wiring modules establishes17existing errors;
the corrected tree has the exact same17error multiset, with no additions.
Therefore the two new owner/transport modules pass mypy, while the broader
legacy wiring check remains failing at its unchanged baseline. This is not a
global type-check PASS. The initial hybrid one-file shadow added two artificial
missing-helper diagnostics; the complete five-file baseline replaces that
comparison and its failed receipt is preserved. No ignores or limits changed.

## Current native admission

T210 r4 on source6d443125d9f6f8e9e9abf8cf964cf20cd4e9e477 passes the explicit
interaction-profile zero-turn control:544 Git-blob-verified source bindings,
Claude2.1.303, two stable catalog observations of3tools,0 dispatched calls,
1exact production SIGINT/normal-return witness/retired identity, genuine SDK
completion seal and CLIexit0. The explicit post-wait observation closes within
494.424ms of the original shared6s deadline. Total58.549s; minimum9642MiB
against6144MiB. No operator login copied, real provider inference, proxy,
harness-requested MCP EOF, API or PostgreSQL usage. The existing PostgreSQL
owner merely remains alive outside this control for the next gate.

All15preexisting container and19network identities remain present. Owned group
and exact MCP identity retired; source/HEAD/archive checks pass and operational
descriptors are RECLOSED. The MCP kernel wait status was not observed; do not
infer MCPwait0 from its normal SDK witness. Direct-child installed-SDK tests
separately established actual MCPprocess0. Public projection:
[validation-t210-native-r4.json](validation-t210-native-r4.json).

Preserved preparation history: r1's empty-JSON text comparison was rejected
before any CLI; r2's namespace probe inherited the observer and timed out
importing the SDK before any CLI. Actual masks/network namespace were exercised
before the corrected run; only the auxiliary stdlib probe disables that hook,
while the actual MCP retains the real source-bound observer. Actual r3 then
passed its bounded-wait diagnostic in57.712s/min9645MiB. A deterministic offline
late-return control exposed the missing explicit post-wait clock assertion;
r4 adds that assertion and records the shared duration without changing any
wait timeout or acceptance limit. These receipts/archives are retained.

T209 is complete at this explicit local-stop/source/SDK/native-zero scope.
T210 and full T197 remain open until the original messages/API-restart journey
passes using production `/exit` pre-stop. No global/default/all-profile or
intrinsic vendor500ms cleanup acceptance is inferred.

## Original full-journey attempt r5

The source-bound r5 controller on fef8462e retained the original 180s per-phase
and shared6s cleanup criteria, production MCP module and exact two-message,
row/event/ACK/restart predicates. Closed preflight passed, including synthetic
default-auth-path, owner-context, actual-import and uncertain-retirement controls.
Claude's read review found an unnecessary explicit config-directory requirement;
the controller restored the default path without changing the operator login.

The actual attempt returned FAILED at preflight after9.964s/min10023MiB.
Read-only OAuth metadata confirmed that the selected access credential was
expired under the existing fixture admission rule. No API, native CLI, model
turn, MCP call, message row or restart occurred. No refresh was attempted.
The borrowed PostgreSQL owner survived, all preexisting containers/networks
were preserved, and the operator credential was unchanged.

The original receipt also reports staged-credential cleanup false: its strict
resolver encountered an already-removed private HOME. Preserve that false
result; independent post-run closure verified zero private credential files,
no borrowers and no auth-source directory. An absent, never-created owner
registry likewise does not establish normal MCP shutdown. Operational descriptors
were reclosed and executed archives preserved. T197/T210 remain open; do not
repeat the authenticated run against the same expired stage or infer a product
shutdown regression from this preflight failure. See the sanitized
[r5 projection](validation-t197-r5.json).
