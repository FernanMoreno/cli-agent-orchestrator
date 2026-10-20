# Validation — 2026-10-20

## Evidence and distinction

Integrated in main, without commit/push/publication. Implementation is an opt-in surface restriction and diagnostic observation increment. It does not close the historical Claude timeout, real expanded-profile acceptance, v1 acceptance, source freezes, provider concurrency or Work-message gates.

Private evidence: `/home/felni/.cache/caos-spec025-policy-r1/` (logs, selected source hashes and receipt). No operator credentials or real transcripts were read by the lead or copied to the repository. No provider acceptance, database run, Docker or QEMU test was launched. Measured available memory was 4,832 MiB initially and 5,515 MiB later, below the documented 6,144 MiB heavy-validation floor.

## TDD and checks

- Policy RED: 19 failures before implementation. Additional Codex override regression failed before the new fence.
- Diagnostic RED: 8 failures before adding timeout observations; matrix evidence preservation failed with KeyError before the harness fix.
- Final focused selection: 127 passed, no failures/skips. Includes real in-process MCP transport, all 79 registered operations classified, protected overlay, command building, timeout/HTTP projection, malformed privacy sentinels and existing harness credential-boundary contracts.
- Additional provider/configuration selection: 237 passed, 36 deselected initialization/dialog cases (outside the config impact). This is not full provider acceptance.
- An earlier broad provider-unit invocation was interrupted after four passing tests; it is not a completed suite and no success is inferred. Final selections target affected configuration boundaries and do not modify existing tests or criteria.
- Black/isort: all twelve changed Python files pass. Deprecation warnings remain in existing dependencies; no check is weakened.
- Architecture/composition: five contracts kept, zero broken (406 files, 1,798 dependencies); rerun after the final B1 correction: five kept, zero broken.
- Peer read review found silent policy loss on unrecognized commands, misleading mode text and the Codex alternate environment guard. These have corrections and negative tests. Final independent review approved the offline scope with B1 conditional; B1 was then closed by the separate scoped review.

## Composition review

Changed boundaries: copied MCP config → pinned child environment → startup middleware → exact catalog/dispatch; and completion wait facts → StepExecutionError → HTTP 504 → closed matrix evidence. Existing human/delegated authority and server-selector/project checks remain in handlers. Native client permissions are an additional restriction, not broadened by the policy. Legacy marker intersects; unmarked startup remains compatible.

Policy state is immutable per middleware instance, with no global mutable selection or shared cache. Tests prove instance isolation, not real child-process credential/concurrency isolation. The catalog inventory covers current registration with MCP Apps disabled; optional plugin registration is not accepted here and new names default to denied. Resources/templates/prompts are hidden and rejected through actual transport. Per-operation authority and persistence remain separate pending integration work.

Diagnostics add only factual counters/booleans/enum states at the existing deadline, without changing success signals, quota/error precedence, timeout, redelivery, cancellation or child lifecycle. API serialization is additive only for an error carrying observations. Harness allowlists reject malformed/unknown fields and never retain messages, terminal identifiers, prompt or receipt contents. No persistence/schema/event/transaction changes, new locks, cache, subprocess or lifecycle effects. Real PostgreSQL/Pact is not needed to prove these in-process configuration/observation boundaries; real provider/restart/persistence gates remain open.

Graphify lacks the current overlay/policy symbols; the impact document records source-verified paths and defers generated-graph refresh until broader integration. No Obsidian write is needed for these transient validation records. Final actual diff reviewed; peer findings were corrected and rechecked before T005/T017 completion.

## Final review corrections

The final independent review approved the offline scope conditionally on B1: a string-command entry containing an `environment` map could place policy in the field Claude/Codex ignore. Both real command builders reproduced the missing rejection in RED. The overlay now rejects that explicit mixed configuration before launch; array-command OpenCode and profiles using `env` retain their intended behavior. Existing unprofiled capability behavior is unchanged. Final B1 review is recorded separately.

`stable_receipt_observations` is the current candidate streak at the deadline, not a historical maximum. It resets to zero after candidate invalidation or unsuccessful settling/CAS. `receipt_candidate_seen=true` with streak zero therefore remains a valid factual observation and is not proof of completion.

The opt-in metadata must reach the protected overlay. Native OpenCode installation/translation and expanded client grants still require T007; the initial overlay primitive is not an accepted end-to-end installer contract.

**Final independent review:** Bridge jobs `ea2aaff970c143f9a05d05c33b1c077f` and `293c08ca982148c5adeffe660af06c6b` approved the limited offline scope and closed B1 respectively. The latter requested checking array commands too: root reproduced both builders in RED (Codex rejected it with a generic scalar TypeError; Claude returned a command without the policy), then made BaseProvider reject profile-bearing array commands before text-adapter serialization. Four regressions cover both rejections and both successful text-command policy serializations. OpenCode's direct native overlay retains its list-command tests. This final residual correction was root-reviewed and rechecked; the peer did not review its final bytes. No historical or live acceptance is inferred.

Final evidence uses `selected-source-manifest-final.json` and `receipt-final.json` in the private bundle; the earlier selected manifest and pending-review receipt are preserved as superseded intermediate records.

Final selected-source SHA-256: `c7cb7c36ec162d0104afac5d9b6fafe4ce645775e12b03d76b06870ae7abc4ae` (12 Python files). Final receipt SHA-256: `7e15236d74f84db5c0e60133e66a80edb8558e08601f08f8a4555ed46de624d1`. Both source/configuration selections, lint and composition passed on the final code; selected-source drift was false. This binds only the listed files and is not a full-tree live-source freeze.
