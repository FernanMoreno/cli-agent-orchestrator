# Knowledge graph — the graph layer & how to view it

CAO can project a subsystem's state (memory first) into a **standard, typed
graph** and hand it to whatever engine you want to render or store it. The
thesis is deliberately small:

> **CAO emits a standard `GraphView`; you bring the engine.**

The **primary way to see your graph is the web UI**: the `web/` single-page app
has a **Memory tab** with a List⇄Graph toggle that renders your real memory
graph, lets you drag and click nodes to read memories, and exports to Obsidian —
all against the same `GraphView` contract. Beyond that first-party viewer, the
same shape fans out to a sink you point the graph at: a file you open in
Obsidian/Gephi, or the built-in Sigma renderer inside an MCP-Apps host. This doc
explains the design (for the "how is this built" reader) and then gives
copy-pasteable steps to actually see your graph (for the "just show me" reader).

> **Ports in this doc are examples.** `cao-server` defaults to `127.0.0.1:9889`
> (`CAO_API_PORT`); the web dev server runs on `5173` and proxies to it — match
> your own `CAO_API_PORT`.

## Design in brief

Two pluggable axes sit on either side of the `GraphView` contract:

- **Providers** — project *some subsystem* → a `GraphView`. `memory` is the
  first (and today only real) provider; `stub` exists to prove the seam is
  heterogeneous. Register more with `@register_provider("name")`.
- **Sinks** — consume a `GraphView` → *some target*. Built in: the in-host
  Sigma **renderer** (`ui://cao/graph`), plus three file exporters —
  **OKF**, **Obsidian**, and **GraphML**. Register more with
  `@register_sink("name")`.

```
                         ┌──────────────────────────┐
   subsystem state  ───▶ │  GraphProvider.project()  │ ───▶  GraphView
   (memory wiki, …)      └──────────────────────────┘        {nodes, edges, meta}
                                                                    │
             ┌──────────────────┬──────────────────────────────────┼─────────────────────┐
             ▼                  ▼                     ▼              ▼                     ▼
     web/ Memory tab    render_graph_view       OKF sink      Obsidian sink         GraphML sink
     (Sigma in the      (ui://cao/graph,        (md bundle)   (wiki-linked vault)   (.graphml XML)
      React SPA)         Sigma in MCP host)          │              │                     │
                                                     └──── files under CAO_GRAPH_EXPORT_ROOT ┘
```

*Text description:* a provider projects a subsystem into a single `GraphView`
(nodes + edges + metadata). That one shape then feeds any consumer: the
first-party **web/ Memory tab** (Sigma in the React SPA), the built-in Sigma
renderer (rendered inside an MCP-Apps host), or the OKF / Obsidian / GraphML
file exporters (which write under the graph-export root). There is **zero
branching over provider or sink name** in the API route — names resolve through
registries.

### The `GraphView` contract

Source: `src/cli_agent_orchestrator/graph/models.py`.

| Field | Type | Notes |
|---|---|---|
| `Node.id` | `str` | Unique within a view (duplicate ids are rejected). For memory, this is the topic key. |
| `Node.kind` | `str` | Lowercase snake-case (`^[a-z][a-z0-9_]*$`). `"topic"` for memory, `"stub"` for the stub provider. |
| `Node.label` | `str` | Display text. **Untrusted** — may be an LLM summary; sinks and the web UI escape/render as plain text on output. |
| `Node.status` | `NodeStatus` | `active` (default), `proposal`, `observation`, `superseded`. |
| `Node.attrs` | `dict` | Free-form. Memory sets `is_hub` and/or `is_orphan` (see below). |
| `Edge.source` / `Edge.target` | `str` | Must reference known node ids (validated). |
| `Edge.type` | `EdgeType` | Closed taxonomy — see below. |
| `Edge.attrs` | `dict` | Free-form (e.g. `{"source": "related_keys"}`). |
| `GraphView.meta` | `dict` | Provider metadata (`provider`, `scope`, `scope_id`, …), plus cache provenance (`cached`, `as_of`) — see [Caching](#caching--staleness). |

The **edge-type taxonomy** (`EdgeType`) is a closed enum organized by family:

| Value | Family | Populated by |
|---|---|---|
| `relates_to` | topical | memory provider (active relationship store) |
| `contradiction` | lint-derived | memory provider (active relationship store, including persisted lint findings) |
| `supersedes` | lifecycle | memory provider (active relationship store) |

> `GraphView` enforces referential integrity (every edge endpoint is a known
> node) but imposes **no size cap** on node/edge counts or payloads — providers
> are trusted local code today.

## What the memory provider projects

Source: `src/cli_agent_orchestrator/graph/providers/memory.py`.

`MemoryGraphProvider` projects **one memory scope's wiki** into a graph, keyed
by `(scope, scope_id)`:

- **Nodes** — one `kind="topic"` node per key in that scope's index, plus one
  extra node per `orphan_page` lint finding (orphans are absent from the index
  by definition, so they need a synthesized node to carry the attribute).
- **Edges** —
  - Active `relates_to`, `contradiction` and `supersedes` relationships from
    the relationship store, read after lint persists any new contradictions.
    Both endpoints must belong to this scope's key set; provenance stays in attrs.
- **Node attributes** —
  - `is_hub: true` — from a `graph_density` lint finding (highly-connected topic);
  - `is_orphan: true` — from an `orphan_page` lint finding (unreferenced topic).
- **Scope-bounded** — edges never cross the `(scope, scope_id)` boundary.
  `stale_claim` and `poison_frequency` do not add rendered nodes or edges;
  `lint_error` findings mark degraded enrichment in metadata.

A valid scope with no wiki on disk is an empty graph. Unknown scopes are
rejected. Optional lint failure preserves topic nodes and available stored
relationships, marking degraded enrichment in metadata. Policy/audit failures
propagate instead of being treated as optional degradation.

> Graphs can be **sparse**. A scope with topics but no `related_keys` and no
> contradiction findings yields disconnected nodes; `global` today often has
> few or no edges. That's data, not a bug.

## Caching & staleness

Memory graph builds can include expensive lint detectors. The cache owns each
build independently of the HTTP request: the API waits up to 90 seconds, and a
request timeout or disconnect does not cancel that shared memory build. A retry
joins the same work. Providers without the optional `project_inflight` hook keep
request-owned cancellation; `projection_status` supplies optional build metadata.

- Completed views have a 300-second TTL and an LRU cap of 64 entries.
- At most two builds run concurrently and two additional keys wait in the queue.
  A full queue returns the same retryable 504 contract with
  `build_state=rejected_queue_full`; no extra build task is created.
- Each active build has a 600-second coroutine deadline. A deadline is reported
  as `failed_deadline`, with a 600-second retry suggestion; normal request
  timeouts suggest five seconds. Both include the `Retry-After` header and
  `detail.retryable`, `retry_after_s`, plus available content-free build state,
  elapsed time and start timestamp. The timeout kind remains
  `graph_projection_timeout`.
- Keys retain the storage owner, repository, canonical scope, lint mode and
  resolved binding policy fingerprint. Global/federated `scope_id` is ignored
  and reported in `meta.ignored_filters`; project/session/agent IDs remain
  significant. Unknown memory scopes are rejected.
- `meta.cached` and `meta.as_of` describe the resulting view. Lint mode,
  returned `lint_error_count`, and independent relationship errors describe
  degraded builds; vault metadata-only projections keep their own unavailable
  enrichment/boundary diagnostics. Authorization and audit failures propagate.

Invalidation remains TTL-based: graph Refresh can serve a view up to 300 seconds
old after a memory edit. `invalidate()` is available for future write hooks.
`clear()` removes completed entries and preserves active single-flight work;
explicit cache shutdown cancels and drains owned coroutines. The API drains its
tracked projections during shutdown.

Cancellation cannot forcibly stop a blocking body already running in
`asyncio.to_thread`. Such work can outlive the coroutine deadline and occupy an
executor worker. The limits bound admitted graph coroutines and cached entries,
not the size of each view or all residual executor work. Historical scope timings
are workload-specific; this integration does not establish new production timings.

## The API

Three routes in `src/cli_agent_orchestrator/api/main.py` expose the registered
providers and their projections. Projection/export retain knowledge authorization,
private-tier handling and secret/export-path gates; catalog discovery exposes
provider names only.

### `GET /graph/providers` — discover providers

Requires the same `cao:read`/`cao:write`/`cao:admin` scope floor as graph reads
when authentication is enabled. Returns `{"providers":["memory","stub"]}`
for the shipped registry, plus any additionally registered provider names. The
static catalog route takes precedence over `/graph/{provider}`.

### `GET /graph/{provider}` — project and return the wire shape

| Param | In | Notes |
|---|---|---|
| `provider` | path | `memory` or `stub`. Unregistered → **404**. |
| `scope` | query | `global` (default) or `project`. `session` / `agent` → **400** (private, refused). |
| `scope_id` | query | Required for `project` (the canonical project id). Omit for `global`. |

Returns the `GraphView` wire shape: `{"nodes": [...], "edges": [...], "meta": {...}}`.

```bash
# Global scope (no scope_id)
curl -s "http://127.0.0.1:9889/graph/memory?scope=global" | jq

# A specific project scope
curl -s "http://127.0.0.1:9889/graph/memory?scope=project&scope_id=github-com-awslabs-cli-agent-orchestrator" | jq
```

#### Finding your `scope_id`

`scope_id` is the **canonical project id** resolved by
`resolve_project_id` (`services/memory_service.py`): the git-remote-derived,
auth-stripped identity — e.g. `github-com-awslabs-cli-agent-orchestrator` — or a
`sha256(realpath(cwd))[:12]` fallback for a repo with no remote. In practice the
scope_ids on your machine are the **directory names** under the memory root:

```bash
ls ~/.aws/cli-agent-orchestrator/memory/
# → global  github-com-awslabs-cli-agent-orchestrator  <12-hex-hash>  …
```

`global` has **no** `scope_id`. `project` scopes use the canonical id / dir
name. `session` and `agent` are private tiers and are **not** exposed through
this API at all (see [Secure access](#secure-access)).

### `POST /graph/{provider}/export` — project, then write through a sink

Request body (`GraphExportRequest`):

| Field | Type | Notes |
|---|---|---|
| `sink` | `str` | `okf`, `obsidian`, or `graphml`. Unregistered → **404**. |
| `dest` | `str` | Destination **relative to** `CAO_GRAPH_EXPORT_ROOT` (a directory for `okf`/`obsidian`, a filename for `graphml`). Traversal/escape → **400**. |
| `options` | `dict` | Opaque, forwarded per-sink; the route never inspects it. |

Query params (`scope`, `scope_id`) are still forwarded to the provider, exactly
as for `GET`. Response: `{"written_files": [...], "sink": "...", "dest": "..."}`.

```bash
# Export the global memory graph as an Obsidian vault named "global-vault"
curl -s -X POST \
  "http://127.0.0.1:9889/graph/memory/export?scope=global" \
  -H 'Content-Type: application/json' \
  -d '{"sink":"obsidian","dest":"global-vault","options":{}}' | jq

# Export a project scope to a single GraphML file
curl -s -X POST \
  "http://127.0.0.1:9889/graph/memory/export?scope=project&scope_id=github-com-awslabs-cli-agent-orchestrator" \
  -H 'Content-Type: application/json' \
  -d '{"sink":"graphml","dest":"cao.graphml","options":{}}' | jq
```

The built sinks:

| Sink | `dest` is | Output |
|---|---|---|
| `okf` | a directory | Markdown bundle: one `.md` per node + a generated `index.md` + a `manifest.md` provenance note. |
| `obsidian` | a directory | Obsidian vault: one wiki-linked note per node (`[[target]]` per outgoing edge; contradiction edges suffixed). No `.obsidian/` config written. |
| `graphml` | a filename | A single `.graphml` XML file (stdlib only, deterministic key order). |

## Secure access

The graph carries **summaries of memory content** (notably contradiction-edge
summaries), so access is gated. The behaviors below are enforced in code, not
aspirational.

- **Reads are scope-gated (D5).** `GET /graph/{provider}` requires any of
  `cao:read` / `cao:write` / `cao:admin` (read is the floor), identical to
  `/events`. This **supersedes** the earlier "ungated by design" (FR-12)
  wording — an unauthenticated caller must not read the graph.
- **Private tiers are refused outright.** `scope=session` or `scope=agent` is
  rejected with **400** even for an authed `cao:read` caller (case-insensitive
  check). The graph API never exposes private tiers — mirrors `/memory/export`.
- **Exports are write-scoped.** `POST /graph/{provider}/export` requires
  `cao:write` / `cao:admin`.
- **Secret gate runs before any write.** The serialized view is scanned by
  `secret_gate` **before** the sink is invoked. On a hit the export is rejected
  with **422**, the sink's `export()` is never called, and **nothing is
  written**. The 422 detail names only the matched **pattern**, never the
  matched bytes.
- **Exports are confined under a root.** `dest` is confined **under**
  `CAO_GRAPH_EXPORT_ROOT` (default `<CAO_HOME_DIR>/graph-exports`, i.e.
  `~/.aws/cli-agent-orchestrator/graph-exports`) via `safe_join_under_base` —
  per-segment validation + realpath containment. An absolute `dest` is accepted
  **only** if it already resolves under the root; `..` traversal, absolute-path
  escape, and symlink escape are all rejected with **400**. There is no
  arbitrary server-side write; **each sink owns confinement** before its first
  write.
- **Auth-off default: localhost trust.** With no IdP configured
  (`AUTH0_DOMAIN` / `CAO_AUTH_JWKS_URI` unset), every request is granted the
  full scope set and nothing is enforced — this is CAO's unauthenticated,
  **localhost-only** trust model. Keep the server on a trusted loopback host in
  this state. To enforce scopes, configure an IdP (`AUTH0_DOMAIN` /
  `CAO_AUTH_JWKS_URI`, plus `CAO_AUTH_AUDIENCE` / `CAO_AUTH_ISSUER`); see
  [`mcp-apps.md`](mcp-apps.md#security) for the full auth layer.

## Viewing option A — the web UI Memory tab (recommended)

**This is the primary way to view the graph.** The `web/` single-page app has a
**Memory** tab (the Brain icon) with a **List ⇄ Graph toggle**. Flip it to
**Graph** and CAO renders your real memory graph with Sigma.js — no MCP host, no
export step, no separate page.

What the Graph view gives you (`web/src/components/MemoryGraphView.tsx`,
`web/src/components/MemoryPanel.tsx`):

- **Sigma render** with the same visual constants as the MCP renderer
  (`GraphView.tsx`): a **hub** node is larger, an **orphan** node is dimmed grey,
  a **contradiction** edge is red, ordinary topics/edges are blue/slate.
- **Node dragging** — grab a node and drag to reposition it. A drag does **not**
  trigger click-to-read (the handler distinguishes a moved pointer from a click).
- **Click a node → read that memory** — clicking (without dragging) opens a side
  panel showing that topic's content as **plain text** (memory bodies are
  untrusted agent output, so they are never rendered as HTML/markdown).
- **Export to Obsidian** button — exports the currently-loaded scope to an
  Obsidian vault named `<scope>-vault` via `POST /graph/memory/export`, and
  toasts the resulting path under `CAO_GRAPH_EXPORT_ROOT`. Enabled once a graph
  with ≥1 node is loaded.
- **Shared scope selector** — the scope dropdown is shared with the List view.
  Pick **global** (sends no `scope_id`) or **project** (a `scope_id` text input
  appears; it's pre-filled from the listed memories when discoverable). The
  **All scopes / session / agent** tiers can't be projected as a single graph
  and show a friendly "pick global or project" guard instead of firing a doomed
  request.

### Run recipe (dev)

The web app is **same-origin** with the API (`api.ts` uses `BASE = ''`). In dev,
the Vite dev server proxies `/graph`, `/memory`, `/sessions`, `/terminals`,
`/agents`, `/settings`, `/flows`, and `/health` to `cao-server` on `:9889`
(`web/vite.config.ts`). So there is **no CORS setup for this path** — do **not**
set `CAO_CORS_ORIGINS` for the web UI.

```bash
# Terminal 1 — from the worktree root, start the API server (default :9889).
uv run cao-server

# Terminal 2 — start the web dev server (proxies to :9889).
cd web
npm install
npm run dev
# → open http://localhost:5173
```

Then in the browser: **Memory** tab → **Graph** toggle → pick **global** (or
**project** + a `scope_id`). Expect a slow first (cold) load — the server runs
`wiki_lint`; see [Caching & staleness](#caching--staleness). Subsequent views
within the 300s TTL are near-instant.

> **If `/graph` 404s**, the running server is stale or not this worktree's code.
> Stop any other server occupying `:9889` first, then from the worktree root run
> `uv sync` and `uv run cao-server` again. Confirm the OpenAPI schema lists the
> route: `curl -s http://127.0.0.1:9889/openapi.json | jq '.paths | keys'`
> should include `/graph/{provider}`.

### Run recipe (production)

Build the SPA into the package and let `cao-server` serve it directly — still
same-origin, no proxy:

```bash
cd web
npm run build          # emits static files into src/cli_agent_orchestrator/web_ui/
```

`cao-server` then serves the bundled UI at `http://localhost:9889`. Open that,
go to the Memory tab, and use the Graph toggle exactly as in dev.

## Viewing option B — file exports (Obsidian / GraphML)

If you'd rather see the graph in a dedicated graph tool, export it and open the
file. No web build, no MCP host.

**Obsidian** — export the `obsidian` sink, then open the folder as a vault:

```bash
curl -s -X POST "http://127.0.0.1:9889/graph/memory/export?scope=global" \
  -H 'Content-Type: application/json' \
  -d '{"sink":"obsidian","dest":"global-vault","options":{}}' | jq -r '.written_files[0]'
# → ~/.aws/cli-agent-orchestrator/graph-exports/global-vault/<topic>.md
```

Then in Obsidian: **Open folder as vault** → point at
`~/.aws/cli-agent-orchestrator/graph-exports/global-vault` → open the **Graph
view**. Each node is a note; each `relates_to` / `contradiction` edge is a
`[[wikilink]]`. (This is exactly what the web UI's **Export to Obsidian** button
produces.)

**GraphML** — export the `graphml` sink and open the `.graphml` in **Gephi**,
**yEd**, **Cytoscape**, or load it with **networkx**:

```bash
curl -s -X POST "http://127.0.0.1:9889/graph/memory/export?scope=global" \
  -H 'Content-Type: application/json' \
  -d '{"sink":"graphml","dest":"global.graphml","options":{}}' | jq -r '.written_files[0]'
# → ~/.aws/cli-agent-orchestrator/graph-exports/global.graphml
```

```python
import networkx as nx
g = nx.read_graphml("~/.aws/cli-agent-orchestrator/graph-exports/global.graphml")
print(g.number_of_nodes(), g.number_of_edges())
```

Everything lands **under** `CAO_GRAPH_EXPORT_ROOT`; the response's
`written_files` gives you the exact paths.

## Viewing option C — the built-in Sigma renderer (`ui://cao/graph`)

CAO ships a Sigma.js renderer as an **MCP App** (SEP-1865). It polls
`render_graph_view` every 30s and mounts a Sigma canvas over a graphology graph.
Node styling mirrors the contract: hubs are larger, orphans are grey,
contradiction edges are red.

**This needs a real MCP-Apps UI host** — it does not run as a standalone page.
Hosts that implement the SEP-1865 UI capability include the Claude Desktop
consumer app, Cursor, VS Code Insiders, and Goose (see the
[client support matrix](https://modelcontextprotocol.io/extensions/client-matrix)).

Steps:

```bash
# 1. Build the view bundles (emits graph.html into apps_static/)
cd cao_mcp_apps && npm ci && npm run build:all

# 2. Enable the surface and run both servers
export CAO_MCP_APPS_ENABLED=true
uv run cao-server        # FastAPI + /events on :9889
uv run cao-mcp-server    # registers render_graph_view + ui://cao/graph
```

Then, from an MCP-Apps-capable host connected to `cao-mcp-server`, ask in
**Agent-mode chat** to render the graph — the host renders the `ui://cao/graph`
view. See [`mcp-apps.md`](mcp-apps.md#enabling) for the full enable-and-drive
flow.

> **Caveat — not every host renders the canvas.** Enterprise/managed hosts and
> some third-party builds (e.g. Claude "cowork"-style deployments) may **not**
> implement the MCP-Apps UI capability. In that case you get the tool's JSON
> output (or a mermaid fallback), **not** the Sigma canvas. That's a host
> limitation, not a CAO bug — use option A or B instead.

> **Node clicks are host-mediated.** Clicking a node calls `onOpenTopic`, which
> in this build calls `app.silentlyNoteToModel(...)` — it *tells the model* a
> node was opened. It is **not** a standalone "open this memory" action; without
> a host driving the model, a click does nothing visible. (The web UI's Graph
> view, option A, gives you a real click-to-read side panel instead.)

## Choosing a path

| I want to… | Use |
|---|---|
| Just view / explore the graph, click a node to read a memory | **The web UI Memory tab** (option A) — the recommended default |
| A portable artifact for Gephi / yEd / networkx | **GraphML** export (option B) |
| The graph in Obsidian's own graph view | **Obsidian** export (option B) |
| Script against the graph data | `GET /graph/memory` via `curl` (the API) |
| The graph rendered **inside my agent host** | The built-in **Sigma renderer** (option C) — needs a UI-capable host |

## Issue #348 delivery and extension points

> **Status:** issue #348's implemented graph scope shipped across merged PRs
> #402 (`84d79ff`, contract and registries), #416 (`98443e3`, providers), #424
> (`67f8e4b`, API and three file sinks), and #442 (`d7c1cd0`, Sigma renderer,
> web view, and cache). The ideas below are possible extensions; where issue
> #348 still lists an unchecked follow-up, that status is called out explicitly.

The thesis this doc opens with — **CAO emits a standard typed `GraphView`; it
does not own the engine** — is what makes growth cheap. Memory is the *first*
provider, not the only intended one. Any future provider that projects its
subsystem into the same `{nodes, edges, meta}` shape inherits the renderer and
**every** sink (Obsidian / OKF / GraphML, and any future sink) for free — no new
engine work. Current behavior is authoritative in the source paths listed under
[See also](#see-also). Any future provider or sink should be tracked in its own
issue rather than inferred from this historical roadmap.

### 1. New CAO-subsystem providers

The epic anticipates three more providers, each a new `GraphProvider` projecting
a different CAO subsystem into the same `GraphView`:

- an **orchestration provider** — live fleet topology fed by the SSE event
  stream; the epic notes it would **replace the topology placeholder** currently
  stubbed in `ext_apps`;
- a **workflow DAG provider** — a run's task graph as nodes and edges;
- an **audit / lineage provider** — where a piece of knowledge came from and
  what it derived.

Each would light up in the web UI, the Sigma renderer, and every file export
with no changes to the engine. One design tension is called out as an **open
question** in the epic and is worth restating here: **snapshot vs. live.** The
memory provider projects a *bounded snapshot* (`meta` carries `live: false` and
an `as_of` timestamp — see [Caching & staleness](#caching--staleness)), whereas
an orchestration provider would project a *live event stream* (a rolling SSE
ring buffer). The contract is meant to serve both without a redesign; proving
that out is future work, not a shipped guarantee.

### 2. A broader knowledge-base provider

Beyond CAO's own subsystems, the same seam could project **non-memory knowledge**
— project docs, decisions/ADRs, or an external KB — as just another provider
emitting the same `GraphView` shape. What lets
heterogeneous sources *converge* rather than each inventing its own semantics is
the **typed `EdgeType` taxonomy** already baked into the contract (see
[The `GraphView` contract](#the-graphview-contract)). This is deliberate: the
epic's maintainer field note argues for keeping **lifecycle edges separate from
topical edges** (`relates_to` aids navigation, but a `supersedes`-style edge
changes whether older knowledge may still govern behavior) and for carrying
**provenance / authority in node metadata** — is this accepted guidance, a
proposal, a session observation, or superseded history? The shipped contract
already reserves that room: `Node.status` (`active` / `proposal` / `observation`
/ `superseded`) and the lifecycle edge family exist today precisely so future
providers converge on one shared vocabulary instead of drifting apart.

### 3. A server-side, query-capable knowledge store

Today's sinks are **export-only** — they serialize a `GraphView` to a file or
render it, and that's the end of the line. The epic sketches a **second sink
tier**: a *query-capable* store that a graph could be loaded into and then
**traversed** — the kind of cross-scope, multi-hop traversal and graph analytics
that flat SQLite can't do. Issue #348 names **AWS Neptune** as the exemplar of
this tier in its architecture and sink comparison and still lists an unchecked
**AWS Neptune sink (export + query; infra)** follow-up. It was not delivered by
PRs #402, #416, #424, or #442. Its differentiating capability depends on the
issue's first **open question — cross-scope edges.** Memory edges never cross
the `(scope, scope_id)` boundary today (see
[What the memory provider projects](#what-the-memory-provider-projects)), so a
whole-knowledge-base view currently fragments per scope. A query-capable store
is only *useful* once cross-scope edges exist — and that design isn't settled.
So this tier is explicitly a **future / optional adapter, not core**: it is the
heaviest tier (infrastructure, IAM, bulk-load), gated behind an open question,
and would ship — if ever — as an optional event plugin, never as part of the base
engine.

> **Tracking rule:** this section preserves possible extension directions. It is
> not an authoritative backlog. Use source for current behavior and dedicated
> issues for accepted future work.

## See also

- [`mcp-apps.md`](mcp-apps.md) — the MCP Apps surface, enabling
  `CAO_MCP_APPS_ENABLED`, the auth layer, and which hosts render MCP UI.
- [`web/README.md`](../web/README.md) — the web UI's architecture, the canonical
  dev-vs-production run story, and the Vite proxy config.
- **Source of truth:**
  - Contract: `src/cli_agent_orchestrator/graph/models.py`
  - Providers: `src/cli_agent_orchestrator/graph/providers/`
  - Cache: `src/cli_agent_orchestrator/graph/cache.py`
  - Sinks: `src/cli_agent_orchestrator/graph/sinks/`
  - API routes: `src/cli_agent_orchestrator/api/main.py` (`/graph/{provider}`)
  - Web UI Graph view: `web/src/components/MemoryGraphView.tsx`,
    `web/src/components/MemoryPanel.tsx`
  - MCP renderer: `cao_mcp_apps/src/graph/GraphView.tsx`,
    `mcp_server/app_tools.py` (`render_graph_view`), `ext_apps/apps.py`
    (`ui://cao/graph`)
</content>
</invoke>
