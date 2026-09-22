<!-- An absolute raw URL, not a repo-relative path: this file is also the PyPI
     long description (see `readme` in pyproject.toml) and PyPI cannot resolve
     relative paths.

     The banner is used rather than the bare mark because it supplies its own
     background, so the mark's navy never has to survive a backdrop it cannot
     see. The bare mark would need a <picture>/prefers-color-scheme swap, since
     that navy contrasts at only 1.77:1 on GitHub's dark theme, and PyPI allows
     <img> but not <picture>, so that markup would either be dropped or escaped
     into visible tag soup depending on how it sanitizes.

     The same constraint means the banner cannot be swapped per theme either.
     It is light, matching PyPI and GitHub's light theme, and it shows as a
     bright panel on GitHub's dark theme. That is a deliberate trade, not an
     oversight. Keep in sync with README.zh-CN.md. -->
<p align="center">
  <img src="https://raw.githubusercontent.com/awslabs/cli-agent-orchestrator/main/docusaurus/static/img/cao-social-card.png"
       alt="" width="640">
</p>

# CLI Agent Orchestrator (CAO)

[English](README.md) | [简体中文](README.zh-CN.md)

[![PyPI version](https://img.shields.io/pypi/v/cli-agent-orchestrator.svg)](https://pypi.org/project/cli-agent-orchestrator/)
[![Python versions](https://img.shields.io/pypi/pyversions/cli-agent-orchestrator.svg)](https://pypi.org/project/cli-agent-orchestrator/)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/awslabs/cli-agent-orchestrator)

**CLI Agent Orchestrator (CAO)** coordinates multiple AI coding CLIs so a
supervisor can delegate work to specialist agents in parallel or sequence.

📚 **[Documentation](https://awslabs.github.io/cli-agent-orchestrator/)** —
guides, reference, and two interactive courses.

## What CAO does

CAO runs a local `cao-server`, starts provider CLIs in isolated terminal
sessions, and gives a supervisor tools for coordinating workers. The agents
remain full CLI processes with their native authentication and capabilities.
See [CODEBASE.md](CODEBASE.md) for the runtime architecture and package layout.

## Prerequisites

Install:

- Python 3.10 or later
- tmux 3.3 or later
- [uv](https://docs.astral.sh/uv/)
- At least one supported provider CLI, authenticated before you launch CAO:
  [Kiro CLI](docs/kiro-cli.md), [Claude Code](docs/claude-code.md),
  [Codex CLI](docs/codex-cli.md), [Antigravity CLI](docs/antigravity-cli.md),
  [Gemini CLI](docs/gemini-cli.md),
  [Hermes](docs/hermes.md), [Kimi CLI](docs/kimi-cli.md),
  [MiniMax Code](docs/minimax-code.md),
  [GitHub Copilot CLI](docs/copilot-cli.md),
  [OpenCode CLI](docs/opencode-cli.md), [Oh My Pi(OMP) CLI](docs/omp-cli.md),
  [Cursor CLI](docs/cursor-cli.md), or
  [Grok Build CLI](docs/grok-cli.md)

The focused provider guides contain installation, authentication, and
provider-specific behavior.

## Install CAO

Install the current `main` branch as a uv tool:

```bash
uv tool install git+https://github.com/awslabs/cli-agent-orchestrator.git@main --upgrade
cao --help
```

For a tagged release, install
[`cli-agent-orchestrator` from PyPI](https://pypi.org/project/cli-agent-orchestrator/).
See [DEVELOPMENT.md](DEVELOPMENT.md) for a source checkout.
For container-based installation, see the
[devcontainer feature](docs/devcontainer-feature.md).

To update an existing CAO installation:

```bash
cao update
```

See [Updating CAO](docs/updating.md) for source-aware behavior and edge cases.

## First supervisor launch

The unqualified commands below use CAO's default Kiro CLI provider. If you
installed a different provider, follow its focused guide above for the
provider override while keeping the same sequence.

1. Install the built-in supervisor profile:

   ```bash
   cao install code_supervisor
   ```

2. In terminal A, start the local server and leave it running:

   ```bash
   cao-server
   ```

3. In terminal B, change to the project directory the agents should work in,
   then launch the supervisor:

   ```bash
   cd /path/to/your/project
   cao launch --agents code_supervisor
   ```

4. Observe the supervisor in the attached launch terminal, open the
   [Web UI](docs/web-ui.md) at `http://localhost:9889`, or follow the
   [tmux guide](docs/tmux.md) to attach to its session.

5. Stop the named session when finished:

   ```bash
   cao shutdown --session {session-name}
   ```

   To stop every CAO session instead, run `cao shutdown --all`.

## Where to go next

### Operate CAO

- [Control-plane selection](docs/control-planes.md): choose the Web UI, shell
  CLI, operations MCP server, or plugins.
- [Web UI](docs/web-ui.md) and [MCP Apps](docs/mcp-apps.md): browser and
  host-rendered fleet interfaces.
- [Flows](docs/flows.md) and [workflows](docs/workflows.md): scheduled runs and
  multi-step pipelines.
- [Skills](docs/skills.md): install, scope, and author reusable agent guidance.
- [Memory](docs/memory.md) and [self-learning](docs/self-learning.md):
  persistent cross-session memory, and the opt-in loop that turns workflow
  outcomes into lessons and promoted instructions.
- [AI-DLC portfolio example](examples/aidlc-portfolio/README.md): coordinate
  parallel AI-DLC intents across repositories and isolated worktrees.
- [Tool restrictions](docs/tool-restrictions.md): roles, allowlists, and
  provider enforcement.
- [Kubernetes deployment](examples/cao-clusters/kubernetes/eks/README.md): run a supervisor and worker fleet on
  Amazon EKS, with shared workspace, per-pod state, and credential delivery.
- [Updating CAO](docs/updating.md): update an installed uv tool.

### Configure and integrate

- [Agent profiles](docs/agent-profile.md): profile schema, discovery, provider
  selection, and overrides.
- [HTTP API and PTY WebSocket](docs/api.md): route-family overview and terminal
  streaming contract.
- [Plugins](docs/plugins.md): outbound events, installation, and authoring.
- Provider behavior:
  [Kiro CLI](docs/kiro-cli.md), [Claude Code](docs/claude-code.md),
  [Codex CLI](docs/codex-cli.md), [Antigravity CLI](docs/antigravity-cli.md),
  [Gemini CLI](docs/gemini-cli.md),
  [Hermes](docs/hermes.md), [Kimi CLI](docs/kimi-cli.md),
  [MiniMax Code](docs/minimax-code.md),
  [GitHub Copilot CLI](docs/copilot-cli.md),
  [OpenCode CLI](docs/opencode-cli.md), [Oh My Pi(OMP) CLI](docs/omp-cli.md),
  [Cursor CLI](docs/cursor-cli.md), and
  [Grok Build CLI](docs/grok-cli.md).
- [Security policy](SECURITY.md): vulnerability reporting and deployment
  guidance.

### Contribute

- [Codebase guide](CODEBASE.md): runtime surfaces, package ownership, and data
  flow.
- [Development guide](DEVELOPMENT.md): local setup, testing, and verification.
- [Release guide](docs/RELEASING.md): maintainer release process.

## Architecture roadmap: verifiable collective work

This fork's direction is to evolve CAO from a terminal orchestrator into an
orchestrator of **verifiable collective work**. CAO already has important
foundations: persistent scoped memory, a SQLite metadata index, native-child
receipts and leases, workflow journaling, memory relationships, and
provider-neutral first-message injection.

Those foundations do not yet constitute a complete collective-work contract.
The following gaps are intentional roadmap items, not guarantees made by the
current runtime.

1. **One durable work model for every operation.** Native children have a
   durable lifecycle, and workflows have durable runs, but ordinary terminals,
   inbox messages, and ad-hoc delegation do not all share a single work-item
   and work-attempt model. The target model separates terminal liveness, turn
   delivery, task state, and job state; every work item must retain its parent,
   provider, lease, result, evidence, and reconciliation status.

2. **Memory must be trusted knowledge, not just stored text.** A collective
   memory record needs producer identity, task/run identity, source artifact
   and revision hashes, evidence references, confidence, freshness, and an
   approval or rejection state. The target lifecycle is proposed, verified,
   approved, rejected, or superseded. Unreviewed statements must not silently
   become shared instructions.

3. **Freeze shared context for all delegated work.** Workflows already freeze
   their curated memory to make resumes reproducible. The same snapshot
   contract must extend to ordinary launches, native children, handoffs, and
   cross-provider continuations, so sibling agents do not receive different
   knowledge merely because they started at different times.

4. **Make memory authorisation server-derived.** A terminal may be permitted
   to use the memory tool without being entitled to publish to global or shared
   scopes. Scope authority must derive from the durable job and terminal
   capability grant, not an optional caller-supplied context. Shared-memory
   publication and instruction promotion require explicit policy and audit.

5. **Use durable events for the whole fleet.** The live event bus and UI
   timeline are observability aids, not sufficient evidence of work. Every
   task, message, delivery receipt, reconciliation decision, and state
   transition needs an append-only, replayable event record with declared gaps.

6. **Support collective memory across nodes deliberately.** A remote memory
   endpoint alone is not replication or multi-writer consistency. A
   multi-node deployment needs a versioned authoritative store, conflict
   semantics, identity and ACLs per project/job, retention rules, and a
   recovery protocol.

7. **Make provider capabilities declarative and uniform.** Provider discovery,
   manager creation, memory integration, status evidence, safe continuation,
   and tool delivery must use one capability contract. A provider should be
   admitted because it satisfies the requested operation, not because it is in
   a fixed list of compatible pairs.

8. **Continue safely after quotas or provider failure.** A quota pause must
   produce a portable continuation package: frozen task contract, memory
   snapshot, completed evidence, artifact hashes, and outstanding work. A
   replacement provider can then continue without redoing or fabricating
   completed work.

9. **Protect memory as sensitive data.** Scope isolation, secret detection,
   redaction, retention, tombstones, and access audit must apply consistently
   to global, project, session, agent, and federated memory. Default-off
   authentication remains suitable only for a strictly local loopback
   deployment; shared or remote deployments require explicit authentication
   and authorisation.

10. **Reduce change coupling.** The API, terminal, database, and memory
    services are large integration points. Extracting domain reducers,
    persistence repositories, provider adapters, and read projections will
    make lifecycle changes testable without duplicating state logic.

The recommended implementation order is: (1) universal work items and
attempts; (2) evidence-backed memory records; (3) frozen context plus portable
continuation; (4) durable fleet events and reconciliation; then (5) distributed
ACLs and provider capability consolidation.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [DEVELOPMENT.md](DEVELOPMENT.md)
before submitting changes. Documentation changes must also follow the
[documentation maintenance rule](CODEBASE.md#documentation-maintenance).

## License

This project is licensed under the Apache License 2.0. See
[LICENSE](LICENSE).
