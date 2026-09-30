# Completion evidence — 2026-09-30

Scope: current main checkout and this computer's authenticated personal service.
Detailed report paths and hashes: [acceptance-evidence.json](acceptance-evidence.json).
No count here substitutes for an unexecuted default/full-suite scenario.

| Gate | Evidence |
|---|---|
| Accepted upstream incorporation | 505 paths applied, 25 already identical; 13 conflicts reviewed; current work preserved; HEAD unchanged |
| Joint affected Python regressions | 663 passed, 4 honest environment skips; all four accepted separately with their actual dependencies |
| Final v2/deployment/MCP/install regressions | 191 passed, 2 environment skips; Docker dispatch and native v1 list each accepted separately |
| Rust/TUI full suite | 255 passed: 216 unit and 39 integration, including four live endpoint cases with personal bearer |
| Authorized matrix with OpenCode v2 | 18 combinations accepted: 9 Herdr; 8 tmux retained plus corrected Claude→OpenCode cell passing twice |
| Claude renewed-login workflows | 2 passed; real YAML and script workflows using subscription authentication |
| Docker T019/T020 worker route | 14 passed on actual Docker; local static dispatch separately passed |
| OpenCode v1 native install | 1 passed against installed 1.18.32; v1 adapter behavior covered by unit regressions |
| V2 MCP | Private 2.0.18 server reports CAO MCP connected after session initialization |
| Personal provider work | Codex subscription turn returned 200, correct marker and completion receipt; session cleanup accepted |
| Personal Docker Work | Verified admin provision, endpoint 202, durable launch; exact artifact reconciliation and cleanup complete; scheduler reservation released |
| Automatic restart | Forced service-parent exit restarted automatically; succeeded provider/reconciled Work receipts retained |
| Backup/restore | Two actual restorations; SQLite integrity ok, signing key/scheduler/state/receipts preserved |
| Architecture/composition | 5 Import Linter contracts kept, 0 broken; project composition check passed |
| Package/config gates | Wheel installed; pinned Agent Plugins schemas/packages passed; mypy on v2 modules passed; formatting/import/diff checks passed |

## Remaining boundaries

[Inventory](provider-inventory.json) records the ten external providers without
an authorized account/model. They remain unaccepted. mock_cli is a local test
provider. Quota continuation is not_applicable in the real matrix because no
quota exhaustion was induced. A sleeping/stopped computer cannot serve requests.
Static Work authority is finite and is not a model workload inside Docker.

The checkout contains the accepted upstream code, but no new commit, push,
release or history merge is part of this work. Earlier candidate-only acceptance
and unchanged historical reports are not recounted as fresh main results.

## Browser correction after operator report

The first closure missed authenticated browser acceptance: public static assets
loaded, while `/sessions` returned 401 and the UI displayed Offline. CLI/TUI
checks did not establish browser connectivity. The correction adds tab-scoped
bearer transport for REST, SSE and the existing terminal WebSocket handshake,
a connection form and the private operator `web` command. The real browser
acceptance covers initial missing bearer, fragment consumption, Live status,
protected sessions/profiles 200, rejected credentials and fresh-link reconnection
in the same tab. Web suite: **372 passed**; deployment suite: **9 passed**.
TypeScript/Vite build and five import/composition contracts pass. This correction
does not claim every dashboard operation or every Work policy is accepted.
