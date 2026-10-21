# T068 validation: additional-terminal discovery fields

Current status: HTTP correction integrated in the working MAIN checkout; offline regression, type/style and registered composition passed. Native acceptance remains open.

## Actual current-source failure

Fresh native-v2 R9b on published `c8951659ecc199bf812e48b93a8c5449377123f1` failed with `terminal_metadata_contract_mismatch` after 45.834 s, before either message phase. Both terminal creation requests returned 201. The receiver had its exact requested ordered group; the additional sender returned group null, although its coarse and native tool permissions matched the planned profile. The receiver reached the pinned Go/LongCat readiness within 8.793 s. This establishes a creation/metadata fault, not a cause for earlier provider timeouts.

Private receipt SHA256: `6bf788f5b039f66b654ec90744b66bba103e4577873e7fd7cb1da21556d1e4f5`. Failed acceptance remains failed. Its cleanup passed: session DELETE 200; immediate provider roots 0/capability files 0; API/tmux/PostgreSQL cleanup true; operator auth, source, binary, runner and interpreter unchanged; private state removed; no cleanup failures. Raw terminal/create evidence remains private and is not published.

## First incorrect boundary and correction

The runner sends the same group JSON to initial-session and additional-terminal creation. `CreateTerminalBody` lacks group/metadata and Pydantic silently ignores those extra keys, so data is lost before the handler. The additional-terminal handler also omits both service kwargs. `CreateSessionBody` already declares and validates them, and its initial-session route forwards them. `terminal_service.create_terminal`, database persistence and sibling discovery already support the fields. Claude independently confirmed both omissions in read mode. Its mention of a200 success response was imprecise: this API and the actual run return201.

FR052/SC036/T068 and plan were recorded before code. Bounded private Graphify extraction covered API, terminal/session services, database and terminal model (5 inputs, 1037 nodes, 2278 edges; SHA256 `48bcd5f48cdc51bfebf35bd5e0db8d50ad622a6fc187897219d20c620712811b`, source unchanged). Source verification traced HTTP DTO→route→service persistence→group-based discovery. The orchestration caller currently sends no group, so omitted-body behavior stays unchanged.

Root added 12 actual TestClient regressions before changing production: synchronous/deferred creation with explicit, empty, null and absent fields; invalid group/metadata types and over-cap values rejected before service execution. **RED: 12 failed**, 50.90 s pytest time (62.65 s process). Failures show missing kwargs for valid bodies and silently dropped invalid fields while returning201. The production correction moves the existing fields and exact validators to the common body, keeps session-only environment validation on the subclass, and forwards the two kwargs in the additional-terminal route. No unknown-field forbid policy, inherited labels, model/tools/authorization, timeout, retry or schema change.

Group/metadata remain intentionally excluded from the existing creation idempotency fingerprint because they are mutable discovery labels; this change does not redefine replay or use them as an authority grant. Existing permission scopes, caller/session identity and initial-message/deferred-init rules remain enforced.

## Verification

- **471 passed** in ten API/service/orchestration modules, 130.41 s pytest time (142.08 s process), including initial session creation, sibling discovery, group/metadata updates/caps, deferred-init, idempotency, terminal limits, scopes and authentication. Changed source/test pins were unchanged during execution; memory admission was 6,888 MiB, above the unchanged 6,144 MiB floor, with the exact existing PostgreSQL container verified and autostart disabled.
- Claude final read review approved the source and HTTP tests, confirmed inherited validators/flat wire shape and intentional label idempotency, and ran no commands. Existing helper tests cover cap validation; the new endpoint cases cover invalid types, too many group elements and oversized metadata.
- Style **passed: Black/isort on both changed Python files**. Whole-source typing **passed: 409 files**; registered MAIN composition **passed: five contracts kept, zero broken**; native-v2 acceptance remains separate until a fresh repinned run after publication.

Private RED/regression evidence is under `t068-root-validation/`; the failed native receipt is under `v2-messages-r9b-prep/`. Work T064–T067 acceptance remains complete on its recorded source. Native generic-message, scale/control-latency and global NFR/coverage claims are separate.

## Composition review

**PASS WITH RISKS** for this bounded HTTP correction. Changed owner: creation request DTO and additional-terminal forwarding. Reviewed neighbors: initial-session subclass/route, caller/session identity, scope middleware, terminal persistence, sibling discovery, orchestration caller, deferred initial-message handling and idempotent replay. Ordered labels and metadata use the existing validators and persistence; no new state owner, transaction, schema, event type, cache, retry or lifecycle is introduced. Existing mutable-label idempotency exclusions, native permissions and session-only environment handling are unchanged.

Actual HTTP tests exercise the serialization/validation/forwarding boundary; existing service, namespace, discovery, scopes and orchestration cases passed in the471-test selection. Native persistence/discovery/restart acceptance is still pending and must use a new source-bound run. No new separately deployed protocol/consumer was introduced, so a new Pact contract is not applicable. The prior failed real run used the exact existing PostgreSQL service and fully cleaned only its owned state. Registered architecture contracts remained unchanged and passed5/0.

Final diff/source review and Claude read approval passed. A private post-change AST refresh retains5 inputs/1037 nodes/2278 edges, source unchanged; SHA256 `0ba30296f3e57adcc0e6be23010afcb14bb8146bad1a871c8ada70d111de7a5f`. Shared generated Graphify artifacts are preserved without staging. Repository specs keep the durable first-boundary conclusion; no alternate vault was used.
