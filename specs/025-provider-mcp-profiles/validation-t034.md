# T034 — refuse discarded native installation filters

Historical snapshot at `158904ae`: valid-list refusal was subsequently superseded by [T035 bound filter propagation](validation-t035.md). Its original failure/pass evidence remains unchanged; malformed filters still fail before publication.

## Reproduced boundary

OpenCode installation's `_prepare_native_profiles` retained `enabled_tools` while consuming `cao_tool_profile`. `translate_mcp_server_config` then dropped the filter because native shared MCP entries have a closed schema. The version-one native envelope records profile names only. Installation could therefore return success and publish a ready full-profile artifact for an empty, partial, unknown or malformed client filter. This is an installation configuration defect, independent of a model response or the historical Claude timeout.

The first unchanged-product run reproduced **24 failures and 28 passing controls in 54.48 seconds**. Failure messages were exactly failure-to-raise in preparation and exit zero in CLI installation, not dependency or infrastructure failures. Private RED logs/JUnit/receipt are retained.

## Integrated correction and compatibility

Preparation now refuses a non-null `enabled_tools` on an explicitly selected native profile, with a fixed error that excludes supplied names/values. This occurs before the pending publication barrier, provider recording, context, shared MCP or ready writes. It checks field value rather than truthiness, so empty lists/maps cannot bypass it. The original input remains unchanged. Null/absent filters still mean the full selected profile; unprofiled preparation retains its previous behavior. This guard does not implement per-agent filter propagation or reinterpret client filters as operation grants.

Existing managed installation files and file modes, source and context/configuration bytes are preserved on rejection. A fresh rejected install creates no native agent/config/context artifact. These claims apply after the existing profile import/download/explicit `--env` staging boundary; no global rollback is claimed. Previously discarded filters cannot be recovered from an existing native artifact. Review the original source before reinstalling; the guard does not rewrite active terminals or old artifacts.

## Fresh verification

The final serial configuration/filesystem unit module `test/cli/commands/test_opencode_native_policy.py` passed **52 tests, zero failures/errors/skips in 40.91 seconds**, including **29 new cases**. Tests cover both selected profiles, six non-null filter forms, direct preparation nonmutation and error privacy, fresh and managed CLI rejection, null/absent installation with actual native binding validation, and legacy preparation. Existing publication, repair, partial-failure and multi-agent tests remain in the module. Private HOME/XDG/temp and plugin stores were used; PostgreSQL auto-start was disabled. No vendor CLI, MCP server child, database or authentication operation ran.

The runs started with 7,893 MiB (RED) and 7,637 MiB (GREEN) available; execution was serial. Project coverage and marker options were retained. Two existing Pydantic class-config deprecation warnings remain. Scoped coverage is not global floor acceptance. Canonical `mypy src/` passed **404 source files**; Black/isort passed for both Python files. `project-composition-check caos` preserved **five contracts, zero broken**. Current product/test SHA-256 values match the GREEN receipt. Evidence lives in the existing private publication cache under `t034-red`, `t034-green`, typing/style/composition and final publication receipts.

## Codex-only composition review

Changed subsystem: native installation policy preparation. Reviewed neighbors: source parsing, plugin merge, persisted command normalization, direct translation guards, pending/ready envelope and binding, provider recording/context, shared MCP/tool publication, CLI error status and refresh. The added branch precedes all native publication and leaves the accepted installation sequence unchanged. No protocol/version, database, message/event, retry, timeout, cancellation, cache, locking or concurrent-installer behavior changes. The actual isolated filesystem/service/CLI path is exercised rather than mocking publication; no external dependency is needed to establish refusal. No independently deployed wire consumer changed, so additional Pact contracts are not applicable.

Graphify's pre-implementation query returned 301 nodes but truncated its display and did not establish the current policy-preparation path. Direct source inspection verified preparation → translation/envelope → publication. This six-line existing-function branch adds no new structural dependency or production symbol, so no graph refresh is required. Main generated graph changes were preserved and excluded. Repository specs own the incremental decision; no durable vault access/write was needed. Review and implementation used Codex only, without Claude or delegated agents.

Verdict: **PASS WITH RISKS** for T034's refusal. Actual native per-agent filter propagation, full T007, live provider acceptance and global NFR/coverage gates stay open. T032's deferred 16-module selection and two real-stdio cases were not resumed. T034 neither changes their criteria nor accepts previously filtered native artifacts.
