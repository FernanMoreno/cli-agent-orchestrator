# Implementation status — 004

Initial phase,2026-09-30: implementation authorized with «empieza», preserving existing work. Later live activation and delivery authorization are recorded below.

Checklist requirements: 16 checked, 0 unchecked. Prerequisites and Graphify impact repeated; graph stale line numbers verified against actual auth.py, main.py and WorkAuthority. No Obsidian duplication.

TDD: backend sessions/passwords, tooling, frontend and API tests precede behavior. Baseline: 58 tests passed for security auth, websocket auth and personal deployment. Parallel ownership: core models/passwords/repository/service; web UI; personal tooling; acceptance fixtures/Playwright; root owns API/security composition and channels. Shared APIs agreed before implementation. All existing changes preserved in place; no clean/stash/commit.

Ruling: continue in current checkout — plan depends on uncommitted 003/runtime and web files; creating a clean worktree would discard the effective source baseline.
Ruling: browser identity preserves sealed jwt kind and issuer/subject/scopes — Work stores full tuple. Cookie method is transport metadata, not new operator authority.
Ruling: canonical temporal_* policy keys from data-model; nested deployment limits are normalized explicitly.

Initial implementation completed in source before the later live activation documented below. All 48 tasks checked after executable verification. Final evidence: 268 broad Python tests; 18 MCP tests; final 79 affected Python tests; 393 Vitest tests; 16 Chromium cases; build/typecheck; five architecture contracts and composition gate. Tests overlap and are not summed into a unique count.

Final review fixed schema startup validation, request activity classification, auth precedence/cache headers, cross-tab response generations, limiter capacity cleanup and initial WebSocket storage outage classification. Work acceptance has preexisting provision/grants/authenticated receipts with real services/SQLite and a test delivery observation; no Docker execution or portable Work restore claimed. Cookie refusal is injected at the browser network boundary.

Graph refresh deferred because the checkout includes hundreds of unrelated changes; existing graph remains stale for 004. Source and Import Linter validate current dependencies. No vault duplication, commits, push, live credentials or live deployment changes.


User refinement: activate against the existing token/runtime and provide configuration in the main frontend, not a terminal. FR-024 and T049–T054 completed. Actual runtime/API/UI updated and verified; owner setup form opened for felni. The user chooses the password in the browser; no password invented, captured or posted by the agent. Current evidence: 398 frontend tests, 17 Chromium cases, 83 Python regressions plus14 setup contracts and composition PASS. No commits/push. Recovery copy recorded in completion-evidence.md.

Owner requested minimum10. T055 completed in source and live runtime/UI.44 Python tests,399 frontendtests, realChromium10-character setup→restart→login and5 compositioncontracts pass. Existingaccounts/session state retained.

Owner confirmed two views: login initial and create account, connected by tabs/links. T056 complete in source/live runtime.402 Vitest and17Chromium pass; activeDOM verified. Backend identity/registration policy unchanged.

Final closure,2026-10-01: Spanish auth UI and public errors complete (404 Vitest/build PASS). Post-rename directory-fsync uncertainty reconciles visible validated state without restart; invalid publication stays unavailable (78 affected backend cases and3 final fault injections PASS). Real Docker/ELF worker acceptance passed with cookie login,3 renewals and logout during execution, verified JWT identity and exactly one work/result/receipt. Five composition contracts PASS. See completion-evidence.md for reproduction and remaining evidence boundaries.

Final frontend405/405 PASS, setup recovery9/9 PASS and backend19/19 PASS. Runtime serves index-1WGJJD8i.js and preserves one account/session and identical JWT/deployment hashes. Isolated Git candidate validated with238 Python tests,19 setup plus1 real Docker case,367 baseline Vitest plus9 final setup tests,build and17 Chromium cases. Delivered Docker test preserves validated durable output with baseline running/sent/unaccepted state; managed completion was proved separately in the earlier full-checkout run, not imported into this commit.

Delivery reviewed and saved as an isolated auth004 commit after candidate verification, as authorized by «hazlo todo». Existing unrelated work remains in the checkout. No push.
