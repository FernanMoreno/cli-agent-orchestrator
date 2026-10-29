# T214 · observer adapter preparation

2026-10-29 · **Adapter preparation only; native T214 is pending.**
Base: `69bd219e47c37a3e494518bedc8e86b253fbdf3d`.

The native-v2 observer derivation rejected `get_local_cao_task` and reconstructed
an installer call that omitted the new task-binding keywords. Its regression
test reproduced that boundary: **1 failed, 4 passed**. The earlier test-module
registration error is also retained separately; it was a test-loader defect,
not a native/provider failure.

The adapter now accepts only the two supported safe projections and uses AST
to change the ledger path and its explicitly selected projection/message probe.
It preserves the production installer's task path and protected requester
keywords. Existing environment validation still executes; a task case must
explicitly supply both its selector and binding-path environment variables.
It does not invent a binding path, relax ownership or activate capture by default.

The test executes the generated module with the actual unregistered native
observer-loader protocol, installs FastMCP, issues the metadata after startup,
calls the handler and reads the terminal-specific ledger. It checks all task
match flags and absence of task/caller/output in that ledger. Controls reject
unsupported selectors and missing task binding, and execute the prior disabled
and peer-list capture modes.

Initial focused verification: **5 passed, 41.93 s**. Combined regression:
**311 passed, 20 subtests passed, 3 existing warnings, 88.30 s**. Final added
compatibility controls and exact final source identities are recorded in the
[structured receipt](validation-t214-preparation.json). The generated code runs
outside coverage's production-source selection; its isolated suite reports no
production coverage. The combined run retains the existing coverage settings.
No coverage exclusions, deadlines or acceptance predicates changed.

Claude read review `fc8a129717f942388a1bf4562992b3f0`: GO for the adapter;
the lead ran the checks. Architecture/composition: 5 contracts kept, 0 broken.
Black/isort pass. Production grants, task schemas and provider behavior do not
change. No new durable knowledge entry is needed beyond T213's existing frozen
loader/AST keyword preservation note in Obsidian.

## Composition scope and remaining work

Reviewed boundaries: production environment installer → generated installer →
FastMCP handler → bounded writer → strict reader; default, malformed startup,
late issuance and legacy peer-list controls. Source-backed generated modules
are sufficient for this adapter; no database or model is involved. The held
PostgreSQL service was independently reverified with 0 client borrowers and
0 private databases and remains available for the serial native gate. Foreign
Graphify files are preserved; no graph rebuild.

**Verdict: adapter preparation PASS, native acceptance NOT RUN.** The current
T166 case still requires two messages and a restart. T214 needs a distinct
one-call case/prompt/fixture and acceptance branch, current runtime/source pins,
actual HTTP offer and successful native query, plus normal owned cleanup. A
coherent synthetic completed receipt must bind the actual API identity,
requester terminal, principal and project. It proves query behavior, not native
task execution. No old launcher was armed and no accepted v2 journey repeated.
