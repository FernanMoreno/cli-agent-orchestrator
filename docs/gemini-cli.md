# Gemini CLI Provider

## Overview

The `gemini_cli` provider runs the official [Gemini CLI](https://github.com/google-gemini/gemini-cli)
as a long-lived, interactive CAO worker. It is a distinct adapter from
`antigravity_cli`: choose `gemini_cli` when the `gemini` executable and its
authentication method are the ones available to the operator.

CAO deliberately does not install Gemini CLI, sign in on the operator's
behalf, or copy credentials into a terminal. Install and authenticate the CLI
once in a normal terminal before creating Gemini workers.

## Prerequisites

- tmux 3.3 or later
- The official `gemini` executable on `PATH`
- An authentication method supported by Gemini CLI: an existing interactive
  login, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, or the configured Vertex AI
  environment

For example, install and inspect the official CLI outside CAO:

```bash
npm install -g @google/gemini-cli
gemini --version
gemini
```

Complete any initial authentication and workspace-trust dialogs in that
normal terminal. Do not put API keys in CAO profiles, project files, or a
command line.

## Launch

Start the CAO server, then select the provider normally:

```bash
cao install reviewer --provider gemini_cli
cao launch --agents reviewer --provider gemini_cli
```

An explicit model is preserved for a child task and takes precedence over the
profile's `model:` value:

```bash
cao launch --agents reviewer --provider gemini_cli --model gemini-2.5-pro
```

CAO launches an interactive session in this form:

```text
env -u GEMINI_CLI_TRUST_WORKSPACE CAO_TERMINAL_ID=<terminal> \
  GEMINI_CLI_SYSTEM_SETTINGS_PATH=<private-settings> \
  gemini --approval-mode=yolo [--model MODEL] -i <role-bootstrap>
```

`-i` keeps Gemini interactive after the role bootstrap, so CAO can deliver
later tasks, receive a result, and use the normal `assign`, `handoff`, and
`run-step` paths. CAO intentionally does **not** use `-p` / `--prompt`,
`--skip-trust`, the deprecated `--yolo` spelling, or a sandbox-bypass flag.
If Gemini presents a trust, authentication, or other choice dialog, CAO
surfaces it as an operator-answerable state instead of auto-accepting it.
The actual launch also uses `env -u GEMINI_CLI_TRUST_WORKSPACE` before setting
its CAO-owned variables, so an inherited headless trust override cannot hide
that prompt; normal Gemini/Google authentication variables remain inherited.

## MCP and configuration isolation

Gemini merges configuration from several scopes. To prevent one CAO child
from inheriting another child's orchestration identity, the adapter writes a
terminal-private `settings.json` below CAO's state directory and passes it
only through `GEMINI_CLI_SYSTEM_SETTINGS_PATH` to that one process.

- The directory is deterministic per terminal, mode `0700`; the file is mode
  `0600` and removed during terminal cleanup, including cleanup after a CAO
  server restart.
- CAO never runs `gemini mcp add`, writes `~/.gemini`, creates `GEMINI.md`, or
  changes project-local `.gemini` configuration.
- Every worker receives a private `mcp.allowed` list containing exactly its
  profile-owned aliases. A profile must declare at least one `mcpServers`
  entry. CAO currently rejects an empty profile rather than pretending that an
  empty `mcp.allowed` is a proven deny-all across Gemini's user/workspace
  configuration scopes. This requirement will be relaxed only after a
  version-pinned live test proves a real deny-all grammar.
- Profile MCP aliases must contain only letters, digits, and hyphens. CAO
  resolves each profile server, rejects aliases that collide under
  case-insensitive matching, then supplies its own `$CAO_TERMINAL_ID`
  reference in the server environment. The identity is not serialized as a
  literal in settings or placed in the role prompt. A profile cannot shadow a
  system-policy alias under case-insensitive matching. When a system policy
  declares `mcp.allowed`, it must contain the exact emitted spelling of every
  profile alias; CAO rejects case-only near-matches instead of assuming Gemini
  treats that allowlist case-insensitively.
- The supported profile MCP schema is deliberately narrow: `command`, `args`,
  `cwd`, `env`, `url`, and `httpUrl`. Arbitrary `headers`, credential-like
  command arguments (for example `--api-key`, `--header`, or `-H…`), URL user
  info, query strings, and fragments are rejected before CAO writes the
  private file. Put credentials only in validated `$VAR`/`${VAR}` entries of
  `env`.
- CAO parses the **entire** Gemini profile without normal `cao env`
  interpolation before any part of it reaches Gemini: role prompt, model,
  MCP configuration, container paths, and tool configuration therefore cannot
  turn a managed `.env` value into command-line text or private runtime JSON.
  The only exceptions are a small typed operational set
  (`provider_init_timeout`, `engine`, `useLegacyMcpJson`, and
  `grokNativeWorkflows`), resolved locally before schema validation and never
  serialized into Gemini's settings or prompt.
- For other MCP environment values, use Gemini's documented reference syntax
  (`$TOKEN` or `${TOKEN}`), not a literal credential. Gemini receives that
  reference unchanged and expands it in the worker's own environment. CAO
  rejects every `$` in `command`, `args`, `cwd`, `url`, and `httpUrl`, including
  braced defaults, so a reference cannot silently become a command argument or
  URL-path exfiltration channel. If the raw profile source cannot be read
  consistently, launch fails rather than falling back to a resolved value.
  Ensure the referenced variable is actually present in the worker shell or
  container; an unresolved Gemini reference expands to an empty value.
- A Gemini profile `model:` may not contain `$` interpolation. Pass a literal
  `--model` override for a dynamic selection instead; CAO refuses the profile
  form before it can enter process arguments.
- Existing system policy is an administrator-owned, trusted input and is
  copied into the private overlay because Gemini accepts one effective system
  settings path. CAO's no-secret guarantee applies to CAO-managed `.env`
  values and profile MCP configuration, not arbitrary literals an
  administrator deliberately places in that system policy. Keep that source
  protected and prefer `$VAR`/`${VAR}` references for its credentials.
- If policy forbids unattended mode or MCP, launch fails closed. Policies with
  `mcp.serverCommand`, `admin.mcp.config`, or `admin.mcp.requiredConfig` are
  also rejected for now: CAO cannot yet expose those administrator-injected
  servers in its effective-MCP contract while preserving the worker's profile
  isolation.

## Tools and lifecycle

Gemini receives the same profile prompt, CAO skill catalog, cross-provider
child model, task receipts, and cleanup lifecycle as the other registered
providers. Therefore a Codex, Claude, OpenCode, or Gemini parent can request a
Gemini child when `gemini_cli` is present in that job's provider allowlist.

Gemini's documented `tools.core` control is an allowlist for every built-in
tool. CAO does **not** guess the provider-specific identifiers for an
unverified Gemini CLI release and then label a worker as constrained. A Gemini
worker with a restrictive `allowed_tools` list (including an empty list) is
therefore rejected before launch. Use `allowed_tools: ["*"]` only when the
worker is intentionally unrestricted, or choose a provider with a verified
native restriction mechanism. A future release can enable restrictive Gemini
profiles only alongside a version-pinned live matrix that proves the exact
`tools.core` mapping and denial behavior.

The adapter is deliberately conservative about terminal rendering. Its
screen-based monitor and stale-processing self-healing remain disabled until
the exact installed Gemini version has captured TUI fixtures and passes the
live lifecycle matrix. This does not change the durable task/turn receipts;
it avoids promoting an unverified transient footer to a terminal or task
completion. A visual Gemini completion marker starts a background result
verification only; the API/UI and a native child become `COMPLETED`/succeeded
after CAO extracts the answer and atomically records its receipt digest. The
shared verifier makes at most three automatic, exponentially backed-off
attempts for the same opaque receipt; after that it leaves the task in
reconciliation rather than creating an unbounded capture loop or guessing a
completion. Result settlement and native-child success are one conditional
database transaction, so a concurrent cancellation, failure, lease expiry, or
terminal deletion cannot be overwritten by a stale completed marker. If a
launch or task paste has an ambiguous transport outcome, CAO retains the
terminal and private settings for reconciliation instead of retrying or
deleting possible live work.

## Validation before production use

Unit and API contracts verify registration, command construction, private
configuration, no credential serialization, model precedence, and generic
cross-provider child routing. A production rollout should additionally run the
real-provider matrix with an authenticated `gemini` executable for the chosen
version: bootstrap, a second turn, MCP identity, cross-provider child, sibling
message, cancellation, timeout/reconciliation, cleanup, and a restricted-tool
denial probe. Add the provider to the protected dynamic matrix manifest rather
than hard-coding a fixed provider combination.
