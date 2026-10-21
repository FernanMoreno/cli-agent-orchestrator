# T046 — ordered native policy integrity

The reproduced defect crossed two boundaries: default frontmatter serialization reordered overlapping permission rules, and canonical JSON hashing ignored that order. A deny could become allow without changing the old digest. The retained auth-free audit establishes both failures independently of a model.

New managed installs explicitly emit envelope **version 3** at both pending and ready barriers. Native action/resource order is preserved. The digest includes type-tagged ordered representations of native, global and selected-agent permission/tool gates; empty maps and sequences remain distinct. The parser keeps a closed schema and immutable format identity, including empty/partial explicit filters. Launch still validates before applying the private server policy.

Legacy render remains byte-exact for versions 1/2, with two checked-in synthetic golden artifacts. Canonical order-independent legacy artifacts remain accepted. Reordered native metadata and mixed wildcard legacy tool gates require reinstall because their original semantics cannot be attested by the old digest. No operator artifact is rewritten automatically. These envelope versions are unrelated to OpenCode CLI major versions: managed CLI v1 remains refused.

## Verification

The initial 20 regressions failed before implementation. Review added malformed-state/boolean-version and empty-map/sequence collision cases; their failures were retained and corrected. The new module has **35 cases**, including both actual installer barriers and six production provider initialization/child-policy propagation cases with mocked CLI launch.

The combined **19-module selection passed 642 tests**, zero failures/errors/skips, two existing Pydantic deprecation warnings, with 6,449 MiB available at admission. The runner retained normal coverage/markers and used a short owned socket path. Ordinary lifecycle fixtures reused the verified existing PostgreSQL service with isolated schemas; the new module is explicitly database-offline. No native CLI/model was invoked by this selection. Private receipts preserve RED, review RED and GREEN, full command, XML and log.

## Composition review

Changed: native envelope utility and installer. Reviewed boundaries: profile → pending atomic artifact → shared configuration → ready atomic artifact → immutable snapshot → binding validation → private native writer/child profile/filter. Pending remains unlaunchable, stale/order/value drift fails closed, unrelated agents/servers remain outside the selected binding, and legacy hashes are not silently redefined. Model, grants, server operation authority, isolation, cleanup and deadlines are unchanged. No persistence/event schema or remote contract changed; shared regression tests cover the existing lifecycle and harness contracts.

Residual limits: actual CLI v3-envelope installation/operation acceptance, expanded real profiles, v1 managed/model runs, external cao.work.message, concurrency and other authentication modes remain open. The restricted real v2 acceptance on 398bae68 remains historical evidence until a fresh source-bound run. T032 remains explicitly deferred. Codex only.

Canonical mypy passed 405 source files; Black/isort passed all three changed Python files. project-composition-check caos kept five architecture contracts, zero broken. Codex source/diff composition review: PASS WITH RISKS limited to the documented real acceptance gaps. Private bounded Graphify AST refresh inspected 79 nodes/289 edges with zero model tokens and preserved the main generated graph. Durable conclusions live in this Spec Kit increment; no vault copy or operator credential changes.
