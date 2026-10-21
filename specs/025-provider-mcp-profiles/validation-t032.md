# T032 — Claude explicit tool filter propagation

This record describes the T032 increment published as `8f951775`. Subsequent
[T033 verification](validation-t033.md) covers an additional native-routing
refusal; it does not resume or close T032's deferred expanded checks.

## Implemented boundary

For a CAO-managed Claude MCP entry with `cao_tool_profile`, the builder now consumes a non-null `enabled_tools` list and emits only its intersection with the selected profile through a protected child variable. An empty list denies every tool. Unknown input names disappear; malformed non-null input fails before a command is returned. Null/absent filters retain the profile and do not reach Claude as client metadata. Unprofiled entries retain their previous serialization.

Server startup requires a profile when the internal filter is present and validates a JSON list of exact in-profile names, bounded to 4,096 characters. The middleware snapshots an immutable subset for both catalog and direct dispatch. Resources/templates/prompts remain denied. The shared overlay strips forged copies from both environment fields on every entry; OpenCode native translation refuses the reserved marker. Existing capability paths, operation authority, native permissions and the historical three-tool restriction remain independent. This increment does not implement native-wrapper or OpenCode native `enabled_tools` propagation.

The implementation uses the ordinary child environment transport described in [Claude's MCP configuration documentation](https://code.claude.com/docs/en/mcp); it does not rely on an undocumented vendor interpretation of the copied `enabled_tools` field. No authenticated vendor CLI invocation is claimed by the in-process tests.

## Evidence so far

The first new selection reproduced **25 failures and two passes** before implementation. In particular, the actual Claude builder's emitted configuration still produced the full eight-tool server catalog for empty and partial lists. The first corrected selection passed **66 tests** in **47.67 seconds**, including real in-process MCP catalog/call transport, profile guards, protected overlays and existing policy regressions. These runs began with 7,523 and 7,621 MiB available respectively. RED logs remain preserved; the old result is not relabeled green.

Canonical mypy passed **404 source files**. Composition preserved **five architecture contracts, zero broken**. Black/isort passed on the changed Python files; all current changed code/test files also passed a syntax check. An independent read-only Claude review approved the six product hunks with no blocking findings; the reviewer did not execute checks or review the test bodies. The initial broader consultation timed out without a diagnosis and does not count as a completed review.

Two additional tests now prepare an actual stdio server child from the generated Claude configuration, with a private HOME, current-main PYTHONPATH, no model invocation and exact denied-call checks. A larger 16-module regression selection is also prepared. Its launch was **refused before pytest started** because available memory fell below the unchanged 6,144 MiB floor; later observation was 4,139 MiB. The deferred selection has no passing receipt. No unrelated process was stopped or reconfigured to obtain memory.

A second scoped read-only Claude review approved both test files without executing them. Its optional improvements were integrated: recursive checking that malformed filters emitted no MCP file, explicit canonical interpreter/module assertions, and HOME/TMPDIR/PYTHONPATH pinned after merging the generated environment. Those final test-only improvements remain unexecuted while the memory preflight is blocked. Later measured available-memory values fell to **1,025 MiB** and then **591 MiB**; no floor was lowered.

A deterministic bounded Graphify AST refresh captured 440 nodes and 1,160 edges from the eight affected files into a private merged graph, with no semantic-model tokens or rewrite of the main generated graph. The six product-file hashes still match that receipt; the later test-only assertion changes have separate current syntax/hash proof and are not falsely bound to the earlier test AST snapshot.

Composition review: builder configuration → protected environment → startup parsing → immutable middleware → exact catalog/dispatch. Existing handler authority, native grants and the three-tool intersection remain downstream checks. No database schema, transaction, retry, timeout, cache or migration changed. In-process positive/sentinel-negative transport verifies the changed dispatch boundary without a real database; the prepared stdio negatives deliberately invoke no allowed production handler. No model, authentication, AG-UI/restart or Work-message acceptance is inferred. The internal environment protocol is covered by producer/parser tests rather than a new external Pact contract.

Verdict: **verification incomplete** for the final T032 selection. T032 remains open pending the final required checks; T007 and expanded real-provider acceptance remain open. At T032 publication, product code was unchanged since its preliminary 66-test pass and review. The user explicitly deferred the expanded validation after the memory refusal. Publishing that reviewed increment did not close its remaining checks. Subsequent work is Codex-only at the user's request; all recorded Claude consultations preceded that instruction.

## Resumed and completed verification, 2026-10-21

The user explicitly requested finishing postponed work, superseding the T032 validation hold. Codex-only remains in force. A new explicit current-source 16-module selection was used because the original refused full command was not retained. Its first run reproduced one stale native list-command assertion after T035 consumed enabled_tools into the protected child filter: **604 passed, one failed**, including both actual Python stdio children passing. This is a test expectation mismatch with the documented protected transport, not a reason to restore ineffective client metadata.

T047 strengthens that test with exact partial/empty protected filters, independent capability paths and input nonmutation. The **identical 16-module selection then passed 605 tests**, no failures/errors/skips, eight existing Pydantic warnings, with four loadfile workers, original coverage/markers and 6,274 MiB available. Both real stdio cases passed in the final selection; no Claude executable, model, login or authentication was used. All pinned tracked source/test hashes remained unchanged during the retest. Original memory refusal, preliminary results and the newly reproduced failed run remain preserved.

This closes T032's expanded regression/stdio verification only, subject to the accompanying final style/composition review. It does not prove authenticated Claude behavior, full T007, model operations, AG-UI/restart or external Work message acceptance.

Final T047 review: mypy passed **405 source files**, Black/isort passed the changed regression module, and project-composition-check caos kept **five contracts, zero broken**. Codex inspected producer → protected env → strict startup parser → immutable catalog/dispatch, canonical command pinning, null/empty/partial behavior, malformed refusal, capability and per-instance separation. Both actual stdio children terminated under keep_alive=False and existing 45-second bounds. Final verdict: **PASS for T032/T047**, with the remaining provider/live/global limits above. No production code was changed for this closure.
