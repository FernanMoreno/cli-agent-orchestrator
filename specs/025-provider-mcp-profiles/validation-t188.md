# T188: Claude native MCP catalog

Preparation and native matrix are separate gates. The current execution base is
`389805b6e410b92ba2e447b940360a2ab3532343`; the private manifests additionally
pin the473 actual source files, including the new reader and tests. No accepted
result is inferred from configuration or from another provider.

## Protocol and offline controls

The [official Agent SDK control implementation](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/query.py)
defines correlated `control_request`/`control_response` envelopes for
`initialize` and `mcp_status`. This reader sends only those methods. It ignores
startup inventory as evidence, refuses callbacks and model execution events,
and requires an explicit connected server and exact, unique tool names.
Duplicate JSON keys, non-finite constants, invalid UTF8, malformed frames,
unexpected IDs, EOF, the4MiB cumulative input limit and one absolute180s deadline
fail closed. The caller owns process retirement and private stderr capture.

53 tests pass in81.59s across the new reader, existing Codex catalog and Claude
server-filter contracts. They use owned real child pipes and real MCP stdio
controls, without a database or provider/model execution. The first new-suite
run failed collection before the reader existed; it is not a reproduced product
defect. The final new suite contains23 controls.

## Native feasibility

Pinned Claude2.1.303 has executable SHA256
`375988f457756524234695cf2d8f64ace99c5f68682e47ea24862a843c191450`.
The first production-builder `interaction/full` control passes in28.735s,
with exactly the three expected bare tool names and the actual ready signal.
Admission memory10717MiB exceeds the unchanged6144MiB floor. The actual sandbox
probe verifies masked operator credential/config paths and a different network
namespace. No user message or model turn is sent; the native process group is
physically retired and operator/source hashes are unchanged.

Read-only Claude review found no reader blockers. Its launcher review caught
an incorrect mask mount order before execution; the corrected launcher tests
the actual namespace before starting Claude. The unexecuted draft and exact
executed archive remain private and distinct. The operational descriptor is
reclosed after retirement. No operator login is copied, renewed or modified.

## Matrix status

All21 cases pass: full counts3/8/12/11/10/12/9 for interaction,
collaboration, coordination, supervision, knowledge, knowledge_writer and
elastic_worker; every empty filter returns0 and every partial filter returns
only `cao_ask_user`. The initial interaction/full feasibility is reused under
the same source pins, not rerun or counted twice. Matrix native elapsed time
sums540.881s; this excludes parent import/startup overhead.

Two additional live native sessions prove isolation: the interaction catalog
remains3 while a coordination catalog is12; the first is re-read while the
second remains alive. Both catalogs stay unchanged. Across23 native sessions,
all owned process groups physically retire, source/operator hashes stay
unchanged and operational descriptors are reclosed. Exact executed archives
remain intact. Minimum pre-start admission memory is10481MiB; this is admission
sampling, not a continuous memory benchmark. Every sandbox verifies actual
credential masking/network namespace isolation before starting Claude.

T188 is complete at FR166/SC150 catalog/isolation scope. CL-05/06/07 remain
open for their broader invocation/authorization/effect criteria. Public
sanitized results and file bindings: [validation-t188.json](validation-t188.json).
Private receipts/archives remain under the three `caos-claude-catalog-20261028`
roots in the operator cache; no credential contents are published.

## Composition review

Changed surface: diagnostic reader/test contracts only; production provider,
MCP permissions, APIs, PostgreSQL state/events and model prompts are unchanged.
Graphify located the Claude builder-to-profile/resolution/tool-mapping edges;
the relevant MCP writer/filter/readiness paths were verified against source.
The generated graph has stale line positions and was neither rewritten nor
used as acceptance evidence. Import Linter:5 contracts kept,0 broken.

Framing, correlation, failure propagation, deadlines and selector lifecycle are
tested through real process pipes; the native feasibility composes the actual
builder, CLI, stdio server, readiness signal and sandbox/retirement paths.
No database transactions, HTTP/event schema changes or migrations are involved.
Verdict: PASS at native catalog/filter/isolation scope; no call/effect/global credit.

Catalog visibility does not establish a model offer, per-operation native call
denial, authorized effects, resources/prompts absent from this protocol, or
global provider acceptance. T032 remains deferred; historical jobs untouched.
