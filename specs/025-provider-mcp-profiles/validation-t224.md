# T224 — positive native tool availability

**PASS for the scoped built-in availability correction; native MCP/profile matrix remains separate.** [Structured receipt](validation-t224.json).

Actual Claude2.1.303 offered18 unmapped built-ins under `allowedTools:[]`. CAO previously generated only a finite `--disallowedTools` list, so new names escaped the compiler's inventory. Restricted profiles now use the existing common `get_allowed_tools` mapping to select available built-ins through native `--tools`. An empty mapped grant is passed as a real empty argument. Unknown categories fail closed; `None`/`*` retain their existing behavior and known execution-denial flags remain.

This tightens restricted profiles: ungranted planning, cron, skill and task chrome is excluded alongside execution tools. MCP server configuration, profile/filter compilation and per-operation authorization remain separate. No ToolSearch or other new native capability was granted speculatively.

The native empty-grant preflight-r3 confirms offer0, one input and its own verified final receipt with clean owned resources/PostgreSQL.442 expanded tests pass, including positive categories, empty/unknown grants, None/* compatibility, existing provider/MCP/native-agent cases and shell escaping. Mypy1source/0issues and composition5/0 pass; independent Claude review gives GO. The original RED includes8 genuine command-contract failures and one incorrect test import, corrected without weakening its assertions.

This does not accept MCP-bearing native tool offers or all CLI versions/profiles, nor repair T223's separate long-output final-receipt attribution failure. T223's native concurrent run remains failed. Actual pinned CLI help documents the built-in selection flag; no package reinstall or operator login change was used.
