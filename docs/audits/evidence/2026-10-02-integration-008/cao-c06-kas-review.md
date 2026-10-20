# C06 KAS independent review

Read-only product review plus isolated reviewer regressions in `test/providers/test_integration_008_kas_launch_review.py`. No provider CLI, operator home, external service, commit or child agent used. Artifacts are private files in pytest temporary directories and the backend is a fake.

## Findings resolved during review

1. KAS proof was checked before awaiting shell readiness but not before the launch write. Reviewer changed the artifact during that actual await; initialization still launched the changed grant. RED recorded in `/tmp/spec008-kas-review-launch-red.log` (1 failed, 5 passed). Root added a proof recheck immediately before launch/status notification.
2. An explicit provider model override could differ from the frozen model yet still launch. RED recorded in `/tmp/spec008-kas-review-launch-v2.log` (2 failed, 5 passed, including prior race). Root now refuses model/tool override differences with canonical typed `policy-drift` before effects. New creation legitimately compiles its requested override into the private snapshot first.
3. Generic Kiro trust-all-tools consent could send Down/Enter for KAS. Reviewer actual consent helper reproduced this in `/tmp/spec008-kas-review-launch-v3.log` (1 failed, 7 passed). Root now refuses the verified KAS dialog as `native-policy-unproven`; Cedar admission cannot be broadened through generic approval. Valid and drifted snapshots are both tested for zero special keys.

4. Retention cleanup bypassed native stop proof and reached registry deletion for an UNKNOWN KAS runtime. RED in `/tmp/spec008-kas-review-retention-red.log`. Root added the early exact cleanup branch before reader/status/provider mutations and the defensive row-delete stop fence.

## Current proof

Final expanded reviewer run `/tmp/spec008-kas-review-composition-final.log`: **14 passed in 5.42s**. Earlier focused run `/tmp/spec008-kas-review-final.log`: **11 passed in 4.87s**. Strengthened UNKNOWN retention assertions (no reader/status/provider mutations) passed separately: **1 passed in 7.14s**, `/tmp/spec008-kas-review-retention-green.log`. Tests exercise actual `KiroCliProvider.initialize()` and its consent helper, not a mocked launch guard. Coverage includes exact private runtime alias/frozen model/explicit empty tools, missing persisted digest, missing artifact, changed content, public permissions, asynchronous artifact drift, explicit model/tool conflicts and both consent cases. Initial invalid proof performs no backend launch or shell-baseline read. Scoped test diff check passed.

Source review confirms:

- Default-off is checked at creation, reconstruction, direct provider initialization and reused dispatch. No profileless opt-in bypass. Canonical exception aliases preserve legacy catch compatibility while new codes identify refusal.
- Compiler rejects unproven aliases/settings and invalid selectors; raw profile hooks/config remain explicit frozen material. MCP plugin/config normalization happens before digest/rendering, and explicit empty native grant is retained. KAS uses `kiro-cli --v3 chat` and a terminal-specific alias without v2 trust-all flags.
- Source snapshot and rendered artifact use a shared bounded no-follow/nonblocking private regular-file reader. Publication is exclusive hardlink plus directory fsync; competing different material cannot replace a winning grant. Restoration consults private admitted snapshot rather than mutable operator profile.
- Frozen Work launch material captures effective plugin MCP configuration, complete profile, model, allowed policy and KAS digest along with authoritative Work contract/delivery/executable closure. Dependency checks rebuild configuration only for equality, not authority. Broader active-scope/admission validation belongs to C05 and is not newly certified here.

## Lifecycle, HTTP and recovery follow-up

Source now routes KAS runtime dismantling through exact identity proof. UNKNOWN retains runtime resources and row/private proof. Row deletion independently requires confirmed absence before it releases the two exact per-terminal artifact names. Session false stop-proof outcomes enter deferred IDs and cannot be erased by bulk sweep. Failed/cancelled initializer cleanup now uses the guarded row helper. Typed HTTP refusal is ordered ahead of generic ValueError handling, retains legacy string diagnostics for kas-unavailable and emits new code/profile_field/message at HTTP400.

Read actual root evidence: compiler/install `/tmp/spec008-kas-install-final.log` **75 passed**; `/tmp/spec008-kas-lifecycle-final.log` **88 passed, 3 deselected**; `/tmp/spec008-kas-production-final.log` **299 passed**; `/tmp/spec008-kas-http-green.log` **2 passed, 86 deselected**; `/tmp/spec008-recovery-bundle-final.log` **85 passed**. The earlier recovery log had 3 stale-profile failures and is not relabeled green. V33 is additive to frozen v32; current tests explicitly reject new plan tables under the historical v32 catalog.

The session bulk fallback residual is fixed: successful sweep releases the exact KAS IDs' proof before owed events; failed sweep retains both private files. Reviewer added two regressions using the actual session deletion coordinator with isolated backend/row-outage doubles. Both pass.

Reviewer also added a complete actual terminal creation→SQLite row→ProviderManager→actual Kiro initialization test. It starts from a broader mutable profile, supplies explicit empty tools and a model override, and proves the exact narrowed digest is persisted with the session incarnation, the private artifact verifies, the launch uses the private alias/override, and the original profile remains unchanged. SQLite uses an isolated shared in-memory engine, artifacts use a temporary directory and backend is fake. This closes the otherwise untested fresh creation composition seam.

No outstanding material finding remains in C06. T040 is review-ready, subject to root's final overall composition gate. Broader active-scope/admission validation stays outside this review. Existing owner compiler/install/guard/race/restoration gates are reused; no redundant broad provider suite was run.
