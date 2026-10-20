# C02 independent policy/install/parser review

Verdict: no material blocker found in C02 snapshot reviewed. Read-only product review against /var/tmp/caos-integration-008-cy0jdbdw/files; no product/test edits, launches, commits or children. Read /tmp/cao-integration-policy-providers.md and inspected scoped provider handoff/diffs; concurrent execution C04 work excluded from C02 conclusions.

Checked boundaries against actual source:
- Tool resolution: unknown role fails closed; settings custom role overrides shipped role; explicit allowedTools remains authoritative and skips profile MCP append including []; developer omitted-policy default preserved. Concrete case-sensitive granted_mcp_servers reused for CAO guard globs, excludes @builtin; assign_elastic guard executes before broker placement.
- Persistence: create_terminal serializes [] as JSON "[]" instead of NULL; metadata reader parses it back to [] (nonempty encoded string), so restored caller policy is not widened.
- Kiro install: effective CAO policy mapped to tools availability, [] remains [], harmless @builtin chrome only, privilege-equivalent native aliases included. Explicit profile.tools remains an intentional higher-priority native override consistent with selected upstream design; this does not falsely change it on launch. CLI/server warn wildcard installed agents for restricted requests and say installed policy applies, not launch override. Final enforcement table includes local Gemini classification.
- OpenCode install: @builtin is selector-only and cannot add bash/write; named categories/wildcard remain. Existing hardcoded harmless chrome policy kept (no new widening of writable operations). Current v2 launch/receipt contract remains; v1 marker patch bounded to one rendered line supports spaces, durations.
- Prompt-provider corrections: inspected non-None restriction guards preserving []; no-profile Codex/Kimi/Antigravity paths covered in child tests while profile MCP/config/skills conditional. Advisory classification honest; Hermes/native restriction not claimed.
- Grok: private rules file emitted via existing atomic0600 writer in0700 home; short command substitution after permission flags; dontAsk+native denies, explicit deny-all, disabled web search, existing private cleanup retained. No receipt/private-home mechanisms replaced.
- Kiro2.25 parser/status: optional-word Credits form; current-turn anchors and colored blue assistant bullet extraction; green tool-only colored output rejected; older extractor fallback preserved. Candidate patch follows final a1c3d138 and4b6b5015, not causal branch's older base.
- ShimHTTPError structured detail renders only typed nonempty kind/message; original status/body preserved; malformed JSON/nonobject/nesting fallback remains generic.
- Supervisor prompt no longer requires unavailable shell/write tools: task/feedback inline; persistence/directories delegated to developer; uncertain delivery reconciled rather than blindly retried.

Verification evidence read directly:
- /tmp/spec008-policy-root-green.log:246 passed34.46s (5 warnings).
- /tmp/spec008-policy-parser-final.log:183 passed33.23s (2 warnings).
- /tmp/spec008-opencode-parser-green.log:91 passed11.57s (2 warnings).
- Provider child report:819 passed/4 skipped + subsequent175 prompt/no-profile units and16 receipt/no-profile composition acceptance. Those child results are reported evidence, not independently reexecuted by reviewer; report includes command/log paths and architecture PASS.

Tests inspected include actual bundled Kiro supervisor installation, category/empty/builtin install outputs, stale installed-agent diagnostics, glob/assign_elastic broker guard, policy no-profile controls, spaced v1 markers/new Kiro fixtures. Root may mark T016/T017 accepted within C02 scope, subject to final full integration gates.

Known limits already documented and not new blockers: Kiro/OpenCode installed policy is not rewritten by launch override; explicit native profile.tools can intentionally override translated Kiro policy; prompt providers remain advisory; Hermes/Cursor lack native denial; Grok uses POSIX shell substitution shared with existing Codex mechanism. Future C04 Kimi Code/causal integration must rerun combined empty-policy/provider restoration/status suites; this review does not certify concurrent C04 product changes.
