# Independent T062 root follow-up review

Verdict: **PASS** for the root-specified typing hunks. No blocking semantic regression, authorization weakening, new suppression, or invalid cast-to-Any workaround found. This was a read-only source review; only this report was added.

The workspace already contained substantial tmux/MCP/orchestration changes. No before-source snapshot was available, so this review did not attribute the entire dirty Git diff to T062. Root identified the narrow hunks reviewed below.

## Source findings

- **tmux transport/client:** `run_tmux` overloads describe the existing text/byte subprocess modes; call sites request literal `text=True` where string output is consumed. `BoundedTmuxServer.sessions` returns its actual `QueryList[Session]`. Explicit `new_session` keyword arguments match the former temporary dictionary, including environment and dimensions. The window-name assertion remains inside the existing hook-error handler; the later missing-name rejection remains intact. `paste_args` annotation does not change paste flags, retry behavior, or cleanup ordering.
- **MCP:** `Field(None)` → `Field(default=None)` preserves optional HandoffResult fields and JSON-schema requirements. `_AssignmentOptions` contains only the existing optional string `operation_key`; explicit recall keywords preserve values/default normalization. Renaming `context_error` avoids shadowing the subsequently caught exception. The turn-projection TypeGuard retains the runtime dictionary/state/terminal/generation/action checks and exact integer rejection of boolean attempt counts. No tool-allowlist or authorization gate was bypassed.
- **Numeric receiver:** the exported strict integer schema rejects both booleans and retains the eight-digit bound. Thus `isinstance(receiver_id, int)` preserves supported MCP inputs. Out-of-contract direct Python calls with `False` differ from the former exact-type branch; they are rejected by the supported MCP boundary and do not create an authorization bypass.
- **Providers:** nullable OpenCode runtime/path/name annotations match their initialization and guarded uses; non-string MCP names are not valid JSON object keys. Gemini checks that decoded settings are an object before the JSON round-trip, so its detached dictionary annotation has proven shape. MiniMax's explicit `return None` preserves its previous implicit cleanup result; catalog's optional boolean matches its existing no-profile case.
- **Decoder provenance/helpers:** the fixed packaged agent-profile schema and all seven bundled template schemas were parsed independently: all eight roots are dictionaries. Their dictionary casts express the trusted bundled asset contract, while nested `Any` retains heterogeneous JSON-schema values. `frontmatter.dumps` is declared/documented to return `str` in the installed library. MCP readiness narrows decoded JSON to a dictionary before token comparison. No cast to unrestricted `Any` was introduced to conceal a mismatched domain model.
- **Authorization/state:** delegation snapshot `_load` still authorizes before the transactional integrity loader. Approval extraction still verifies the V2 public manifest before accepting a string plan ID; the registry Literal annotation does not change approval settings or precedence. Project-marker resolution retains nonce and physical identity checks. Kiro's compatibility exception alias points to the canonical exception that explicitly preserves the legacy constructor. HandoffContext field typing does not alter supervisor-derived ownership, provider fallback, or allowed-tools handling.

## Independent checks

```bash
COVERAGE_FILE=/home/felni/tmp-cao015-types-work/review.coverage \
TMPDIR=/home/felni/tmp-cao015-types-work .venv/bin/pytest --no-cov -q \
  test/clients/test_tmux_transport.py test/clients/test_tmux_client.py \
  test/providers/test_opencode_launch_contract.py \
  test/providers/test_opencode_cli_unit.py test/providers/test_opencode_v2.py \
  test/providers/test_minimax_code_unit.py \
  -k 'transport or timeout or create_session or create_window or cleanup or launch or disabled_cao_mcp'
```

**36 passed, 198 deselected**, exit 0, in 18.70s. Log: `/home/felni/tmp-cao015-types-work/root-review-tests.txt`. This includes the real temporary tmux suspended-server recovery test; provider/account launches were replaced by the existing test doubles.

A separate check using the actual `NumericTerminalId` alias and Pydantic TypeAdapter rejected `True`/`False`, accepted an eight-digit integer and a string ID, and confirmed HandoffResult only requires `success` and `message`; exit 0. Bundled-schema root check found **8 object roots, 0 nonobjects**. Relevant `git diff --check` passed.

Root's full mypy, formatting, architecture/composition and broad pytest gates remain the overall acceptance authority; this review did not duplicate those large runs or establish coverage percentages.

Root follow-up: restored the original exact `type(receiver_id) is int` branch; the remaining optional string branch is typed with a cast justified by the strict exported MCP schema. This also preserves the unsupported direct Python boolean behavior. The numeric-reference regression suite passed again: 11 passed, exit 0 (`numeric-reference-convergence.log`).
