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
