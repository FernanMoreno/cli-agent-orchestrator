# T085 — Fresh native-v1 generation and managed refresh

Date: 2026-10-23. Current source HEAD: `682797ec6e47aa0845d0153e461a0e415aa81016`. Status: fresh headless generation PASS; managed acceptance FAILED (`receiver_ack_not_observed`). No global or original-model acceptance is claimed.

## Current-source generation

The exact T077 closed generation runner was rebound only to the current HEAD and a fresh T085 output-receipt name. Root verified whole-file equality after those substitutions, all 507 current source hashes and unchanged plugin/schema bytes, then reran 30 pure controls (15 completion predicates, eight shape/prompt controls, seven static gates). Native v1 binary SHA remains `77b2cfe4b97df6f15c3673b22100b9f79c711f25ecb9bf513bb82526b15d24fa`, model `opencode-go/glm-5.3-flash`, tools/MCP disabled and the original 75-second/6,144-MiB limits intact.

Generation passed in 16.499 seconds with natural exit 0, exact Unicode sentinel, step_finish, successful HTTP observation and expected model/auth matching. Memory admission was 7,931 MiB. Operator auth, source, binary, plugin/schema, runner and source HEAD remained unchanged; the owned process stopped and all private state was removed. The new generation receipt SHA is `f68704a5cb0f50201e6475704be1f01de2d41ba7f6d1fc0a958431b8907280ad`; executed runner SHA `664a8408763e3dcb2267b1d1939bb09c86c1c6344b1e6e092a590655ae96df31`. Executed bytes were retained separately and the gate reclosed.

Independent read review identified that this older headless harness fixes the default operator credential-store location while the product supports overrides. Root confirmed, without printing paths or values, that OPENCODE_DB and XDG_DATA_HOME are absent from the inherited launcher environment and the effective source is the default for this run. The invocation added only virtualenv/test/source bindings. This verification does not claim support for custom operator-store overrides in the diagnostic harness.

## Managed refresh gate

Only this freshly successful same-source generation receipt may unlock the managed runner. Historical T078 acceptance remains preserved with its original source and cannot replace the current prerequisite. The managed candidate must retain the existing Unicode, two-message/API-restart/AG-UI, identity and cleanup checks and satisfy the additional mandatory ACK gate described below; generation alone does not satisfy them.

## Acceptance gap found before managed execution

The T078 baseline _final_success predicate can return true without receiver ACK evidence, and even when a synthetic receipt explicitly sets that evidence false. It also did not require exactly one completed receiver turn. Root reproduced the pure predicate gap; this is a validation-harness issue and does not prove a product delivery failure. No new weaker managed run was executed.

T086 adds a mandatory supplementary gate while preserving all 17 original helper bodies and their checks. The receiver profile distinguishes public ACK from required receipt metadata. Completion must advance exactly once from the pre-input baseline; LAST is read only after the expected receiver turn completed and the metadata is checked again afterward. ACK and metadata must be observed before the unchanged original 75-second sender-phase deadline, with continuity across restart. Missing, false, truthy nonboolean, duplicate or stale evidence fails the new final conjunction.

The whole managed module is therefore no longer binding-only: the initial reanchored scaffold remains preserved separately, and the deliberate strengthening overlay is recorded independently. Sixty pure controls passed, including the inherited gap reproduction. Current-source LAST shape controls also remain applicable; actual managed acceptance and independent review are still pending.

Independent read review approved the supplementary gate with one hardening suggestion: the public-output helper should not remove arbitrary receipt-shaped strings or trim whitespace. Root confirmed that terminal_service already verifies/removes the active receipt for the public result, then made the new helper require the exact API string ACK. The negative controls now reject whitespace and leaked protocol metadata too; no acceptance condition was relaxed.


## Actual managed result and separate recovery

Executed on the same pinned HEAD with the mandatory T086 overlay: FAILED in 135.192 s, phase one `receiver_ack_not_observed`. The sender completed its Unicode result in 11.843 s and one message persisted. Receiver MCP catalog was empty, sender catalog exact; the observed calls completed without MCP error. There was no second phase or API-restart acceptance. The lowest model admission was 6,711 MiB; 75 s and 6,144 MiB criteria were unchanged. Executed runner SHA `b216a942063ab1e542d7a25c2464f56466f2d13c3c160fcce9a2c7e5d1f4bdee`; its bytes are preserved and execution is closed.

The original receipt records API/capture shutdown and zero provider roots after session deletion, but tmux cleanup was unconfirmed, PostgreSQL cleanup deferred and private state retained. Operator auth, source, binary and runner remained unchanged. This is a failed run, including its cleanup. No acceptance is claimed from its sender-only success.

A separately reproduced cleanup fault (T087) came from newer unrelated Chromium zombies: `/proc` environment reads raised PermissionError. Both ownership scanners now revalidate identity and state before omitting a zombie environment. Eight new regressions failed before the correction; all 123 module tests passed afterward, including existing live unreadable-process checks. Independent read review found no blocking issue. Composition: 412 files, 1,834 dependencies, five contracts kept, zero broken. This changes the live-test harness only.

Separate explicit recovery verified the current run-owned server/socket identity, then the corrected strict cleanup helper confirmed shutdown with no owned descendants. The exact three temporary databases and six roles were verified and removed from the existing identified PostgreSQL service; private root removed. The original failed receipt remains immutable. Recovery is not original-run cleanup acceptance.

The ACK cause is still unproved: the initial harness did not retain content-free receiver output samples. The private HTTP log confirms 196 output reads, so a hypothesis that the receiver never reached the completed-output branch is contradicted. A next diagnostic must retain bounded HTTP/status/counter/type/length/exact-ACK observations, preserve literal ACK and all acceptance gates, and bind fresh source/generation; do not change provider behavior based on this incomplete evidence.

Composition verdict: PASS for the scoped zombie-inventory correction and its separate real cleanup recovery; FAIL for managed-v1 acceptance. Product parser, permission policy, model and API contracts are unchanged. No full native acceptance rerun after the harness mutation is claimed.
