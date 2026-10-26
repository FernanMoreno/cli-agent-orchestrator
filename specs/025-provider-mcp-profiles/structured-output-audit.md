# Structured-output boundary audit

2026-10-26. Two independent Luna READ audits, checked by Codex against source. Initial snapshot a7659dd3; T169 is complete (4f25382f); T170 remains a proposal. Audits are source evidence, not provider acceptance.

The reusable rule is **JSON plus a versioned operation schema for machine results**. CLI commands opt in with --json, emit one object on stdout and keep prompts/progress on stderr; failures also retain nonzero exits. Export JSON Schema from shared models so consumers in other languages can validate the same contract. Preserve each transport: MCP tool results/structuredContent, HTTP status/response DTOs, AG-UI SSE and private native lifecycle receipts have different semantics.

| Priority | Boundary | Evidence and action | Status |
| --- | --- | --- | --- |
| Current | peer pair/accept → native controller | Actual non-TTY confirmation joined the Spanish prompt and broke text parsing. T169 adds strict JSON schemas and peer/project binding, without bypassing confirmation or exporting the private one-use code. | Implemented T169; real pairing also passed in R6 |
| Next | cao agent non-TTY output | _machine_mode promises JSON for pipes, but only handoff consults it for progress; every final _emit, including handoff, still receives as_json directly. Explicit --json already works. Reproduce and make the promised machine mode consistent while retaining TTY output and exit semantics. | Proposed T170; not implemented |
| Existing contract | MCP outcomes | isError False is tool-transport completion; individual success/ok/state determines operation outcome. Human interaction already has closed Pydantic outputs; Work has ok/code/retryable/required_action. T168 validates the coordination outcome. | Preserve operation-specific contracts; no blanket wrapper |
| Existing contract | HTTP/AG-UI/native receipts | Work response models are versioned; AG-UI validates typed SSE events; native receipts carry private lifecycle evidence. | Preserve existing schemas/authority; migration needs a concrete consumer |
| Optional | Git worktree inventory | T168 counts a porcelain header substring; a path containing that substring could alter the count. Current private path does not contain it. | Source-only hardening candidate, no reproduced current failure |

Root verification: agent.py::_machine_mode/_emit/handoff_cmd; models/work.py::VersionedModel; mcp_server/server.py interaction outputs and local-coordination handlers; protocol/helper result parsing. Native TUI markers, Git porcelain/cat-file, tmux explicit formats and pinned provider --version output are intentional adapter inputs. Converting these external protocols to a CAOS JSON envelope would not address their actual contract.

The audits did not run services/tests/models or read credentials. Private reports remain in t168-native-paired/structured-protocol-audit.md and the agent messages. No other provider operation is declared accepted from this audit.

User scope confirmed: enforce structured operation results where automation could otherwise be fragile. Do not force JSON final model responses without a task requirement. R6 verifies exact Unicode text as the worker public body. T168 diagnostic snapshots are versioned private evidence; they do not authorize operations or replace the existing admission predicate.
