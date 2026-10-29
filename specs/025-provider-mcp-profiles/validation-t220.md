# T220 — native OpenCode concurrent cancellation

Status: harness integrated; actual native acceptance pending. T013/OC-06 remain open.

Scope: two actual pinned OpenCode v2.0.18 CLI processes, one production API and isolated targets on the verified held PostgreSQL service. Synthetic OpenAI-compatible SSE inside a separate loopback-only namespace per attempt; no external inference or operator credential staging. Empty MCP surface does not establish MCP permissions. Built-in offers are counted separately.

Required observations: both turns active; foreign A generation rejected409 without mutation; current A cancellation200 within180s; original A retired and replacement ready with zero model requests; B process/FIFO/generation unchanged and fresh FIFO output; B released deliberately and verified against its own durable receipt; native child join and deletion preserve cancellation verdict; normal API/tmux/process/target cleanup. Admission and pre-native stages retain6144MiB minimum; operation and stream budgets remain180s.

Pre-run checks:47 controls passed34.27s, including existing cancellation negatives for both providers and ambiguous/foreign-role receipt rejection. The native test is disabled without a fresh private source-bound descriptor. Composition5 kept/0 broken. Python compilation, exact imports, call signatures, returned context-manager ownership and two actual private writer/reader observations passed. Black/isort passed.

Graphify's existing index is based on87e2d5f, not current source. Its initialize/readiness/recovery test nodes and40 incident edges were used only as an impact map, then checked against actual initialization, configuration generation and cancel→reset→FIFO replacement→initialize→durable finish→release source. Product behavior is unchanged by this harness; the shared cancellation observer gains a default-compatible provider keyword. Foreign Graphify state is preserved rather than regenerated.

Private receipts belong under `/home/felni/.cache/caos-t220-native-cancel-20261029`. Preflight and concurrent attempts must have separate inventories and immutable executed archives. Every failed attempt remains evidence; preparation is not acceptance.

Retained preflight failures: r1 rejected before API acquisition because the fixed binary prints `opencode v2.0.18` rather than a bare version. r2 failed startup before any model request: binding the executable at `/tmp/opencode` collided with the CLI's runtime directory (`EEXIST`). Source correction mounts it at `/tmp/opencode-native`; failed initialization now archives the raw private capture and verifies wrapper retirement without replacing the original error with an empty-sampler assertion. Both remain harness failures, not concurrency acceptance. r2 returned the verified PostgreSQL parent to0 clients/0 private databases and reclosed its wrapper. After the correction47 controls passed27.29s with coverage enabled.
