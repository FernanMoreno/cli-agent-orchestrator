# Current-main closure checklist — 2026-10-18

This checklist tracks the user's requested correction order. Historical receipts
remain valid only for their recorded source; isolated or offline passes do not
close full-suite or live acceptance. Work stays in the existing `main` worktree,
with no commit, push or publication.

| Order | Item | Current evidence / required closure |
| --- | --- | --- |
| 1 | Provider execution driver | Root reproduced the private driver's unconditional Claude auth predicate and verified the provider-specific correction. Six generated children parse. Synthetic cleanup checks preserve source sentinels and retain uncertain homes. Main's existing journey already has the correct provider-specific predicate. Related main offline tests: 75 passed, zero failed/skipped. The host-bound driver is an execution artifact, not a delivered product feature; fresh per-provider source/runtime plans and live acceptance remain pending under item 7. |
| 2 | Strict memory round-trip | OPEN under full load: the current-source R6 unfiltered eight-worker run failed the unchanged `<1 s` assertion at **1.9527287899982184s**. The separately sealed current-source cold cohort passed all three no-retry covered processes at **0.771994 / 0.776891 / 0.817269s**, and the current focused selector remains below one second outside suite contention; this closes the strict isolated cohort, not the loaded failure. |
| 3 | Interaction visibility under load | OPEN under the current full backend: `test_concurrent_capacity_profile_records_lock_wait_and_visible_latency` failed the unchanged `<2000 ms` assertion with `confirmed_registration_to_visible` p95 **3226.5ms**. Subsequent isolated runs of that exact selector passed 1/1 both without coverage (p95 **244.3ms**, lock-wait p95 571.1ms) and with the same coverage option (p95 **270.3ms**, lock-wait p95 691.5ms). The evidence points to full-suite PostgreSQL/host contention but does not prove a single root cause or close the loaded gate. No product change is accepted from this run yet. |
| 4 | Complete backend, omissions and coverage | OPEN: the current-source R6 unfiltered eight-worker run selected **18,463** cases; **18,223 passed, 4 failed, 236 skipped, 0 errors, 71 subtests**. Statement coverage was **68,548/78,568 = 87.2467%**, above the exact 87% floor, and before/after source guards matched. The four loaded failures were the memory `<1s` case, FIFO child bound, one profile-drift target revalidation, and a tmux replacement-shell probe; the exact affected selectors passed **7/7** in an isolated current PostgreSQL run. Final uninstrumented full-green acceptance remains required. |
| 5 | Docker | Current-source technical gates passed with zero failures/errors/skips: installation lifecycle 1/1, PostgreSQL restore 1/1 and browser cookie renewal/logout 1/1 (18 Oct. receipt `/tmp/caos-docker-current-main-20261018-r4/gates-receipt.json`, newly sealed app image `sha256:f1e3b76334c0af8a886be8a90c376fc7a8ab123aa1e6db4369d8c36141037fd9`). The complete installer unit module then passed **70/70 in 24.31s** on 19 Oct. The later current-source T019/T020/restart packet passed **16/16** (receipt `/home/felni/.cache/caos-docker-extra16-current-main-20261019-t131-r1/gates-receipt.json`); the existing PostgreSQL container stayed healthy with restart0 and was not replaced. The real pinned-entrypoint reproduction also passed, recording socket rc=0/loopback rc=2 during init and loopback rc=0 after init, with target identity unchanged (receipt `/home/felni/.cache/caos-t068-entrypoint-readiness-20261019/receipt.json`). T068 is closed; the full backend gate remains open separately. |
| 6 | QEMU | The historical R2 failure remains preserved (9 passed,1 SIGKILL after_upstream case failed,81 not executed). T131's bounded correction is integrated in main with15 new regressions passing and broader supervisor/gateway checks green. The requested current-main final-group run then passed **exactly the four remaining T098 selectors, 4/4, zero skips/failures**, with owned marker removal and no cleanup error; receipt `/tmp/caos-qemu-t098-final-group-current-main-20261019-t131-racefix-r2/runner/runs/run-79652b7293dbae38-eiau03id/run-receipt.json`. This is the four-case scope requested by the user, not a full QEMU91 claim; the broader gate remains open unless explicitly requested. |
| 7 | Real Codex, Claude and OpenCode, serially | OPEN: fresh per-provider source/runtime binding; two journeys per provider (questions/human answers/exact ACK and generic agent messages), redacted AG-UI observations and API restart. The current worktree's Unicode/receipt and interaction transport regressions pass offline (124 focused interaction/AG-UI cases, 56 OpenCode observer/message cases, and a clean 135-case message/AG-UI/observer/archive selection); Work message PostgreSQL/API checks pass 17/17 and 10/10. The fixes remain local worktree changes because commits are prohibited. T122 fixes four separately reproduced non-ASCII token comparison boundaries; no historical attribution without traceback. A fresh R7 Claude Unicode `run-step` cell reached the provider boundary but returned HTTP 504 after 222.63s; it did not reproduce the old non-ASCII comparison exception. A new R10 OpenCode lane started both native processes but ended in phase A with zero MCP calls/events/rows and clean cleanup; the stale-hash preflight and 120-second initialization timeout were not observed. Claude, OpenCode and provider-backed Work-message acceptance remain open. Preserve operator Claude login. |
| 8 | Historical CAO reconciliation | OPEN: two external `RECONCILE_REQUIRED` records lack a linked verified resolution. Inspect only through an available manager interface; no global reinstall, invented closure or automatic replay. |
| 9 | Final PostgreSQL-only and composition review | Current scoped checks pass: architecture/PostgreSQL-only12, the extended PostgreSQL role/registry/column-grant selection41/41, `uv.lock` verification, configured mypy400, Black/isort1342, composition5 contracts kept/0 broken, Markdown links and both plugin contract checks. Work regression modules146 passed after the typing fixes. The final private source seal records unchanged HEAD, index and inventory. Aggregate OPEN: accepted reusable changes must be in main, required architecture/contracts/integration checks pass, final diff reviewed, and limitations reported precisely. No aggregate completion before the preceding gates. |

### Additional current evidence — 2026-10-19

The current Codex AG-UI/restart diagnostic completed two questions and two
human answers, performed one real API worker restart, observed three redacted
snapshots with stable identity, acknowledged both answers, and cleaned up
without failures. The private selected-source manifest matched before and
after, and operator authentication remained unchanged. This is current
diagnostic evidence for Spec022; it does not replace the immutable full-tree
source freeze or the provider-backed Work-message lane still open in items 7
and 9. The four-case QEMU result in item 6 remains the requested scope; no
QEMU91 rerun was launched.

## Driver correction verification

The corrected temporary host adapter's SHA-256 is
`e9dfee3b0580a7ff539c2882866ae236cedea363db666c8e6d7722e05d6edec7`.
Root checked the old and corrected generated predicates for all three providers,
parsed all six generated children, and ran synthetic cleanup checks without
reading real authentication bytes or starting a provider, API, Docker or database.

The main offline selection was:

```text
test/live_provider/test_interaction_cli_journey.py
test/live_provider/test_claude_auth_archive.py
test/live_provider/test_message_cleanup_auth_archive.py
75 passed in 14.98s; zero failures and skips
```

JUnit: `/tmp/caos-provider-runner-offline-root-20261018.junit.xml`.
Synthetic root receipt: `/tmp/caos-provider-runner-offline-root-20261018.json`.
These are offline results; no real-provider acceptance is claimed.
