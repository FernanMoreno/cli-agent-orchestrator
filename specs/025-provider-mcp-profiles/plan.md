# Plan: provider MCP profiles

## Technical context and constitution

Python 3.12, FastMCP middleware, copied provider MCP config maps, existing stdio children. No new dependencies, schema or persistence migrations. Preserve unrelated main changes. Constitution I–V: source and executable evidence; no cleanup of existing work; spec → plan → tasks → Graphify → tests → implementation; composition and independent review before closure.

## Initial design

Use one pure policy module for immutable profile sets and strict resolution. Accept CAO-only `cao_tool_profile` metadata on a canonical bundled MCP declaration. The shared overlay consumes that metadata, rejects profile metadata on unrecognized server commands, strips profile-supplied internal policy environment entries, and installs the resolved internal marker only on the canonical CAO child. Do not change `enabled_tools` or native permissions: they remain an additional restriction. Unknown metadata must fail before launch; the server must independently reject unknown internal selections.

Generalize the existing restrictive middleware using a supplied immutable set, retaining its three-tool class and historical behavior. Install profile middleware in `server.main` alongside (intersecting) the historical OpenCode marker. Every restricted policy hides resources/templates/prompts and rejects direct access. Do not alter `_tool_denied_reason` or operation handlers; surface restrictions are an additional boundary.

The shared overlay already serves Codex and Claude through BaseProvider. OpenCode v2 calls it over its installed MCP config. OpenCode v1 does not yet have equivalent private overlay propagation and remains explicitly pending. Expanded client grants and v2 profile-aware preflight must precede real expanded-profile acceptance.

## Validation and TDD

First add failing transport/config tests, then implement. Use actual in-process FastMCP transport for list/call/resource/prompt boundaries; no DB mocks are used to claim persisted behavior. Audit current real catalog names through the registered server and verify every excluded tool against middleware before dispatch. Pure configuration tests cover canonical command pinning, stripped overrides, invalid metadata, caller nonmutation and isolation. Run existing restrictive middleware/provider overlay suites, targeted lint, architecture/composition gate and peer review. Live/provider/PostgreSQL acceptance is a subsequent serial task, not needed to prove this isolated policy boundary.

## Risks and evidence limits

An environment variable is a launch-time restriction, not a sandbox against arbitrary shell execution. Native tool authority, terminal/project checks and private capability files remain necessary. No per-call mutable global policy or DB/catalog cache. Constant-time set membership; one linear filter of each catalog response. No retry, timeout or event behavior changes. Graphify gaps are recorded separately and verified with source.

## Knowledge decision

Repository specs and provider todo documents own this work. No durable Obsidian write is needed for an incremental policy primitive or transient receipts; do not copy generated graph facts to the vault.

## Timeout observation design after independent diagnosis

Claude read-only review identifies a real information-loss boundary: `_wait_for_completion` retains pickup/candidate/probe status locally but its final StepExecutionError loses those facts, and the run-step API cannot report them. Add a frozen typed diagnostic record, optional on StepExecutionError, populated only at completion deadline. Capture counters/booleans at the existing probe/extraction points; serialize only the explicit fields at API error mapping. Do not infer auth/provider/monitor causes or alter successful settling, redelivery, quota handling or timeouts. No database/event schema changes. TDD uses timeout=0 for one factual observation with no real wait, and an actual FastAPI handler boundary with fake substrate. Persistence/provider diagnostics still need source-bound real evidence.

Source/Graphify impact: graph vocabulary includes server/provider but lacks the current observation helper. Source path `_wait_for_completion` → StepExecutionError → `api.main` run-step exception mapping verified directly. Review native-child receipt and script/job settlement neighbors; keep current semantics intact. No new retry, resource or lifecycle branch.

The real-provider matrix now retains only a closed projection of timeout observations in its existing failure evidence before asserting HTTP success. Unknown fields/values, prompts, messages, identifiers and receipt contents are excluded; malformed projections are omitted. This addresses the demonstrated harness information loss without a new live run.

Profile-bearing text commands with an `environment` map are rejected explicitly: Codex/Claude use `env`; silently selecting a field they ignore could drop enforcement. Array-command OpenCode keeps `environment`. This opt-in validation does not rewrite legacy unprofiled environment handling.

The text-command BaseProvider path also rejects profile-bearing list commands before serialization. OpenCode v2 calls the native overlay directly and retains list-command support. Tests cover rejection and successful text-command policy serialization in both Codex and Claude.

## T007 execution increment

Implement profile-aware tool requirements at `utils/opencode_v2.py::validate_v2_mcp_configuration` using the actual generated MCP `environment`, not arbitrary raw metadata. Preserve the existing no-server no-op, collision check, last matching permission rule and `codemode=false` requirement. An ignored `env` field containing the internal policy is rejected rather than mistaken for enforcement. A malformed native environment fails locally.

In Codex's final config serializer, after protected overlay and canonical command resolution, derive the profile's effective tool set from its `env` (and intersect the existing marker when present). Validate the explicit enabled_tools shape, then intersect it, or supply the exact profile set when unspecified. Keep escaping through the existing TOML scalar writer. Do not add Codex-only fields to Claude/OpenCode configs and do not alter native permissions or operation handlers.

Knowledge remains in this feature; no durable vault write. TDD first proves three-tool preflight incorrectly accepts a partially granted collaboration profile, and proves the current Codex command leaves broad/absent enabled_tools unbounded. The separate installer design remains under independent read review and is not represented as solved by this increment. No persistence, network, provider, cleanup, timeout or event behavior changes.

## Installer information-loss guard

Independent source diagnosis confirms native translation discards cao_tool_profile and the shared grant sidecar is best-effort, unsuitable for security policy. A strict per-agent sidecar needs transaction/order and artifact-binding design so a partial installation cannot widen an existing agent after an error. Do not add fields to OpenCode's closed schema or put per-agent policy into the shared server environment.

For this increment, reject explicit profile metadata early in install_agent after provider/plugin resolution and before provider-record/context/native writes. Reuse the same validation in native translation to prevent direct silent loss. Unit/CLI tests preserve existing artifact bytes on rejection and prove legacy entries still translate. Implement full per-agent propagation as a subsequent T007 task with fail-closed partial-failure tests; do not close T007 on this guard.

## CLI failure boundary found by installer regression

After the service guard rejected the unsupported policy, four CLI regressions still observed exit 0. Source confirms cli/commands/install.py prints the service failure then returns normally; the existing test explicitly preserved that behavior. Strengthen that regression to require nonzero failure and map the existing service failure to ClickException, retaining the same Error message. This fixes the first incorrect service→shell boundary; successful CLI calls and service error categories are unchanged. The changed failure exit code is intentional and must be documented/tested, not hidden by weakening the new rejection tests.

Reserved profile and pull-flow environment keys are rejected by the same native installation validator: neither can become shared policy through raw env/environment. Existing URL profile download and --env staging happen before the guard by design in the current installer. They remain explicit staging side effects, not an atomic installation transaction; artifact-preservation tests cover an existing named profile without those staging options. Do not claim rollback of a downloaded source or requested env staging. T022 must bind policy/native artifact publication and partial failures. When propagation is added, provider preflight must also receive server_name for an explicitly selected profile without interaction-source opt-in; today's None no-op is retained for legacy callers.

## Native catalog race — reproduced during final verification

First native catalog execution passed; the fresh-source repeat failed with service_ready=true, zero API polls and 'service authentication was not initialized'. The private failed-run service.json existed after process teardown (metadata-only inspection; no password read). Source checks /doc readiness then scans authentication only once, so publication after HTTP readiness or a partial JSON write is incorrectly treated as permanent absence. This is the first incorrect startup boundary, unrelated to operator OAuth or provider model response.

Extract a standard-library-only bounded password-read helper in the catalog test module. Inject its exact source into the existing namespace-isolated driver and call it with the already-created startup deadline. Poll at the existing 0.1-second cadence using only remaining time; do not reset/extend deadlines or change HTTP, target-found, cleanup or timeout gates. Offline deterministic file/clock tests go RED before the helper exists. Preserve the failed native artifact and use a distinct corrected-run log. No production/auth behavior changes.

CLI local-file imports also copy into the local store before install_agent, like URL staging. The interim guard does not roll back that explicit copy. The failure message already used stderr; the corrected behavior changes its exit status, retaining the existing stream/prefix.
