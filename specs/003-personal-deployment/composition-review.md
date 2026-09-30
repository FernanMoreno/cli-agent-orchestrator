# Composition review — 2026-09-30

Performed by the primary agent against source, applicable tests and live local
routes. Impact discovery used Graphify before implementation and was checked
against source. Integration preserves the recoverable pre-change tree and the
accepted candidate; Git history/index were not used as an integration shortcut.

## Boundaries reviewed

1. **Persistence and ownership:** existing Work repository/scheduler/services
   remain authoritative. Personal deployment introduces private configuration,
   not another database/state machine. Existing scheduler policy survives restart.
2. **Authentication:** persistent RS256 issuer, bounded bearer lifetime, loopback
   sockets, private files. Actual protected routes return 401/200 without/with
   bearer. JWT mode skips local-operator-only legacy repair; no authority bypass.
3. **Providers and transport:** installed CLI major is probed; v1 and v2 parsers
   are explicit. Real v2 captures cover idle/error/processing/completion and
   receipt line preservation. Profile installation precedes native agent selection.
4. **Input ordering:** the failed tmux cell retained the prompt in its composer.
   V2 waits two seconds for paste settlement, then submits once. The failed cell
   passed twice with fresh sessions/receipts; no payload redelivery was added.
5. **Configuration and MCP:** private v2 JSON/env files preserve installed agent
   rules, global tool denials and explicit grants. V1 MCP servers retain native
   tools through codemode=false. A private live v2 server reported the CAO MCP
   server connected after session initialization. No model call was needed.
6. **Result authority:** authorized provider matrix keeps durable receipt and
   timeout/cancellation/cleanup checks. Static Docker launch produces no model
   result. T020 acceptance separately exercises its actual authenticated worker
   acknowledgement/result protocol and real Docker execution.
7. **Lifecycle:** normal systemd stop exits cleanly; forced parent failure caused
   automatic restart. A successful Codex child receipt and reconciled Docker
   launch persisted. Exact Docker artifacts were observed removed before cleanup
   was recorded; its scheduler reservation was then released through the fenced
   service operation after repeating exact Docker cleanup verification.
8. **Backup/restore:** service lock excludes concurrent startup/backup; immutable
   destination, SQLite backup API/WAL/integrity checks, stable issuer key. Actual
   restoration retained succeeded provider receipt and reconciled Docker receipt.
9. **TUI/API:** configured bearer applies to normal and streamed requests, is
   omitted from Debug and rejects whitespace/control characters before transport.
   Authenticated redirects are disabled. Full Rust suite and live endpoint
   contracts passed against the authenticated personal service.
10. **Compatibility/integration:** current CLI memory logs remains in the TUI
    hidden catalog, restoring CLI/catalog parity after upstream incorporation.
    Pinned plugin schemas/packages and five import contracts passed.

## Review findings and disposition

- Missing native v2 agent installation in the live matrix: corrected through the
  existing profile installation endpoint; no model default substitution.
- V2 paste submission lost under load: conservative version-specific settlement,
  single Enter, retained failed evidence and two passing corrected-cell reruns.
- V1 global MCP tools gate missing from v2 translation: regression and ordered
  deny/grant translation; direct native tool surface preserved.
- Authenticated startup invoking forbidden legacy memory repair: explicit policy
  skip and regression; JWT/local operator identities stay separate.
- Normal SIGTERM marked as service failure: stopped-state handling and regression.
- Scheduler configuration absent/overwritten during provision: initialize only
  when absent; preserve policy on restart and in existing Work provisioning helper.

## Refresh and durable knowledge decisions

Graphify full regeneration is deferred: existing generated graph is discovery
material and cannot certify the changed source. The new provider-to-config-helper
edge and runtime-to-existing-service edges are recorded here; direct source and
Import Linter are authoritative for this acceptance. Keep this review, inventory,
operator guide and public evidence manifest in the repository. Do not persist
credentials or duplicate temporary logs into a knowledge vault.

## Limits

Acceptance covers the authorized three external providers on two terminal
backends and the personal deployment. Ten other external providers lack an
authorized account/model; inventory is not a passed test. Quota exhaustion was
not induced. Historical candidate 13,491-case evidence remains separate from
current main results. No release, publication or Git history merge is inferred.

## Browser follow-up review

Verified Graphify's fetchJSON/terminalSocketUrl/useEventFollow consumers against
source. The first wrong boundary was browser transport: no operator credential
left the browser while the server correctly enforced JWT. The fix preserves
server verification, all database authority and deployment issuers. Browser-owned
state is limited to sessionStorage; the operator link stays private and its fragment
is removed before requests. Invalid input is rejected; 401 clears the matching
credential, with a fence against older responses clearing a replacement. No failed
mutation is automatically replayed. The fetch helper retains abort signals and
SSE Accept headers, rejects external origins and authenticated redirects. Existing
WebSocket token support remains server-owned. Same-document fragment navigation
required an additional hashchange handler; actual browser acceptance reproduced
that failure and then passed. Historical tests without auth retain their transport
shape. Full web regression, build, deployment tests and composition gates pass.
Graph regeneration remains deferred; source edges to auth.ts are documented here.
No private token/log is persisted in the repository or knowledge vault.
