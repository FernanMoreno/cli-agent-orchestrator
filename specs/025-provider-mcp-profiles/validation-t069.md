# T069 — native Go HTTP boundary

Date: 2026-10-22. Executed source: main `fe35aaed925a5540c31235c1266f14f9626d2be2`.

## R15: observed native HTTP rejection

The pinned native v1 CLI used the selected `opencode-go/longcat-2.5-preview-free` model and an export of the selected active credential from an owned database snapshot. A private native plugin forwarded the original fetch arguments, returned the original response and rethrew the original exception. It recorded only fixed route/status metadata. No headers, bodies, raw URLs, stdout transcripts or auth values were persisted. Native tools were denied and MCP absent.

The single native process observed **HTTP402** on `legacy_go_chat` (`/zen/go/v1/chat/completions`), with no redirect. The plugin clock measured3951ms since installation; native process lifetime was12.647369835991412s. The runner stopped on the first observed HTTP failure. It did not wait for a structured native terminal error and recorded no native JSON events. Exit-15 reflects the diagnostic's owned termination, not a natural native error exit. Admission was9267MiB, above the unchanged6144MiB floor; maximum native lifetime remained75s. Auth export had a separate30s maximum.

[Content-free R15 receipt](t069-native-r15-receipt.json), SHA256 `5ba4c95e278662b0046c930c280739f98bda501318d5f3b7857cab278c18d070`.

All recorded ownership/cleanup and preservation checks passed: owned process session stopped, private root removed, operator auth fingerprints unchanged,505 source/test pins unchanged, binary/plugin/runner unchanged and HEAD still pinned. The execution gates were closed again after the run and executed bytes retained privately.

## Evidence limits

HTTP402 is an observed response, not proof of the provider's account, balance, model availability or root cause. The direct prompt/no-tools configuration differs from R14's managed two-agent journey, so R15 does not attribute R14 or close T060/T062. No API/PostgreSQL service was started or restarted for this diagnostic; it does not assert independent database cleanup checks.

The [current official Go documentation](https://opencode.ai/v2/docs/console/go/) names the selected model and a different current chat endpoint. Public model catalogs and documentation differ. Neither catalog absence nor a route difference alone justifies changing a model, login, billing setting or product default.

## Controls and review

Root executed82 JavaScript privacy/pass-through controls and18 ledger/gate controls with no network or auth access. They cover response/error identity, unchanged arguments, sensitive getters, fixed route classes, private file modes, unknown-field rejection and incomplete live ledger handling. They do not prove native transport interception by themselves; the actual HTTP row proves this one request traversed the wrapper.

Claude bridge READ review `bf226277aa36428e833d2d2ae9ced806` identified finalization/route/pin improvements. Root corrected them and reran controls. READ review `af5eeaf7045445f7863ec1d62bde03cc` approved the concrete closed runner before execution. Reviewers did not run tests or recalculate hashes. A read-only Codex Luna/Max ECC reader confirmed that an installed marker without HTTP evidence would remain unattributed. Opening changed only the execution boolean, checked through AST normalization, with updated self hashes.

Private execution artifacts remain under `/home/felni/.cache/caos-git-publish-20261020/native-http-r15-prep`; only the sanitized receipt is checked into the project. No product endpoint or permission change has been justified by R15.

## R16: explicit current-route comparison

Claude READ review `77904f0cf74d4d3e96d944998676c032` approved the concrete comparison. Root executed it once, changing only the private native `provider.opencode-go.options.baseURL` to `https://opencode.ai/inference/go/openai/v1`, plus assertions/report labels. The same binary, model, selected active auth export, prompt, deny-all/no-MCP authority and75s/6144MiB limits remained in place. Root reran82 JS +18 ledger controls and six actual-writer contracts before execution. The diagnostic initializes `error_categories` as a dict, matching the helper's actual returned value; R15's zero is an unpopulated placeholder, not a measured category count.

The actual observed route was **`current_go_chat`, HTTP402**, without redirect. Plugin time was2851ms since installation, native process lifetime11.741213676985353s, admission9182MiB. No native JSON events were recorded before diagnostic termination. Exit-15 again records owned diagnostic termination, not natural native failure. All recorded owned-process/private-root cleanup and auth/source/binary/plugin/runner/HEAD preservation checks passed. Executed bytes were archived privately and execution gates closed again.

[Content-free R16 receipt](t069-native-r16-receipt.json), SHA256 `463485e8fab4a01c3e46cc4f183983b34c6759660ba04113e80683c2d5ea3ace`.

Both the default legacy route and the explicit documented current route rejected their respective native requests with402. This comparison provides no basis for a CAOS endpoint correction. It does not identify the specific rejection reason, prove authentication success, establish a provider outage or establish which response produced R14's rendered error. No account/billing/login settings or product endpoint/permissions were changed. Stop model replays of this unchanged rejection; T069 root-cause attribution, T060/T062 and full native/global acceptance remain open. Further diagnosis requires a distinct bounded error signal or a verified change in provider availability.

Final independent Codex Luna/Max ECC read review confirmed receipt/text agreement and prompted the explicit R16 exit-code clarification. Registered composition check passed:412 files,1834 dependencies,5 contracts kept,0 broken. Product source/tests were unchanged; the controls above validate diagnostic artifacts rather than a product correction or full regression suite.

## Deep reassessment requested by the operator

R15/R16 omitted both the actual request identity and error reason. The pinned upstream [inference proxy](https://raw.githubusercontent.com/anomalyco/opencode/53d1eabb61e21162157817bf677da0a4ad3332e3/packages/console/app/src/lib/inference-proxy.ts) can forward legacy Go to the current service; those routes are not independent service controls. Root investigated the harness rather than assuming payment or provider failure. All subsequent native probes below used main dc6be9a98363c6aaf6ea3b028c9897683f63c882, unchanged75s/6144MiB guards, no MCP/tools, and complete recorded owned-process/root cleanup and operator/source/binary preservation. They remain diagnostics, not collaboration acceptance.

| Run | Changed boundary | Observed result | Limit |
| --- | --- | --- | --- |
| [R17](t069-native-r17-receipt.json) | Request model/key equality booleans, native-header presence, bounded error projection | Original model, HTTP402; all four booleans true; JSON markers endpoint/unsupported;10.541s | Key equality means selected staged key, not proof of entitlement. |
| [R18](t069-native-r18-receipt.json) | Full native-store copy from the same snapshot as the comparison key | Native structured error before any observed HTTP,9.180s; native exit1 | Inconclusive. Does not show credential export is correct or wrong; no provider response captured. |
| [R19](t069-native-r19-receipt.json) | Explicit causal control with current family model longcat-2.0 | Same route/selection method, actual request model/auth/header booleans true; HTTP402,9.538s | No fallback or original-model acceptance. Deprecation alone does not explain both models failing. |
| [R20](t069-native-r20-receipt.json) | Closed-vocabulary error qualifiers | Original model, HTTP402, correct request booleans; permitted tokens: upstream request failed endpoint is unavailable;10.639s | No arbitrary message/body saved; no billing/auth/root-cause conclusion. |

R17 cloned-error observation cost2ms, R19 cost3ms and R20 cost2ms; the cap remained500ms within the existing native deadline. Error bodies were read only in memory with a16KiB cap. R20 emits at most48 tokens from a fixed vocabulary; unknown words become placeholders. All gates were closed again and actual executed bytes archived privately.

Root controls: R17 had34 JavaScript strict-rejection/privacy/pass-through checks,9 schema checks and3 actual gate assertions; R18/R19 had36+9+3; R20 had38+9+3. The first R17 review caught a tuple-key bug introduced while preparing its gate; it was corrected and the actual assertions were tested before execution. The cloned-error task also received an explicit rejection handler. These preparation corrections did not cause R15/R16's HTTP responses. Claude READ reviews df65456b05d44c10876e6afae0893d84, fee3e8ac45de426fa9967636f1a5760f and19f07105f17d45b9808f6bd1c62afb7e completed; the last approved R17. Independent Luna Max ECC readers reviewed and approved the concrete R18/R19/R20 scopes. Reviewers did not run the reported controls.

A native aggregate query against an owned copy returned one active opencode-go credential and no active opencode credential. This disproved the applicability of a proposed Console-OAuth/integration-selection diagnosis for the inspected state; no authentication rewrite followed. Parent DB/inline-auth overrides were absent and XDG data home was the default. A separate in-memory comparison found different active and legacy Go keys, without retaining either values or hashes.

The [read-only usage diagnostic](t069-go-usage-metadata.json) called the documented current Go usage route once for each existing API-key source: active database key HTTP200 with JSON usage, legacy JSON key HTTP401. The old legacy key is therefore not an established working fallback; the active key is accepted by the usage endpoint. This is not proof of model inference entitlement or a reason to alter login. No model requests or account changes were made by these usage GETs; private copies were removed and operator files unchanged.

The current [official catalog](https://models.opencode.ai/api.json) marks longcat-2.5-preview-free deprecated. Longcat-2.0 was present with tool_call true and no deprecated status, but its R19 request also failed. Frozen model metadata was a demonstrated validation gap, addressed separately by [T070](validation-t070.md); it is not established as the sole cause of402. T069/T060/T062 and full native/global acceptance remain open.


## R21: native TUI control against R19 headless

Root challenged the headless-diagnostic method with one native TUI execution using a private PTY and the same explicit `opencode-go/longcat-2.0` control model as R19. This is not the retired original model or original-model acceptance. The binary remained fixed; active-key export, deny-all/no-MCP authority and75s/6144MiB bounds remained unchanged. The public catalog preflight admitted this control before auth staging. The native TUI `--prompt` mode replaced headless `run --format json`; screen bytes were drained and discarded, with the existing1MiB cumulative output cap. No generated text or completion predicate was evaluated.

The [sanitized R21 receipt](t069-native-r21-receipt.json) records **legacy_go_chat HTTP402**, without redirect, and the same permitted error tokens **upstream request failed endpoint is unavailable**. Actual model/auth equality, session/header presence and native UA prefix were true; tools0, streamtrue. The closed client-header category was `cli` despite TUI invocation; this is an observation, not proof of a client-header defect. Native process lifetime13.404471596993972s, observer2ms, admission8702MiB. Exit0 is the diagnostic/native stop result and does not establish generation success. All recorded source507/binary/plugin/runner/HEAD/operator-auth preservation, owned-session stop, PTY stream closure and private-root removal checks passed. No API/PostgreSQL resources were started or restarted.

Independent Luna Max ECC READ review approved the concrete closed runner after correcting inherited manifest labels/hash metadata and adding positive native-UA/client classification controls. Root executed41 strict JS controls,9 schema controls,3 actual gate assertions and7 real private-PTY lifecycle checks, without native/model/auth access in those controls. Opening changed only the execution boolean, verified by normalized AST. Executed bytes were archived privately and the gate reclosed after this one run.

This challenges the hypothesis that headless mode alone caused the Go rejection: the TUI request with matching model/auth received the same route/status/error as the headless control. It does not prove the upstream root cause, model inference entitlement, generation completion or MCP/collaboration acceptance. Do not change account/login, silently substitute models, raise limits or replay unchanged failures. T069/T060/T062 and full native/global acceptance remain open.

Final independent Luna Max ECC READ review confirmed that the private receipt and sanitized copy agree and found no actionable discrepancy in the interpretation. The observed client category remains unclassified as expected or defective; no local upstream source established that convention. No unchanged replay is justified by this result.
