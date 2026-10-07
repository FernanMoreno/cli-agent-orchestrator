# Final frontend fixture and formatting evidence — 2026-10-07

Bounded, parent-authorized changes: one Web test fixture and the twelve files identified by the existing MCP Apps Prettier report. No product behavior, dependency policy, expiry guard, assertion removal, commit or remote operation. Candidate edits were synchronized to the original shared workspace.

## Expiry fixture root cause and correction

The original full Web run, `/home/felni/cao015-final/web-tests.log`, failed the expiry-after-approval test: **468 passed, 1 failed**. The first incorrect boundary was test-fixture time: `mocks(Date.now()/1000+.3)` constructed the review expiry before mount, preparation, review and approval. Under parallel load that 300ms window had already elapsed when approval was attempted. `BeadsWorkControl` correctly refused the expired plan; the test could not reach its intended approved-then-expired state. This was fixture nondeterminism, not an invalid product guard.

A focused original-file rerun passed **11 tests**, demonstrating load sensitivity rather than deterministic product failure (`web-expiry-baseline.log`). The corrected `web/src/test/beads-work-reviewer.test.tsx` scopes fake timers/system time to this one test with `try/finally` restoration. Preparation, review and approval flush under React `act`, and an explicit assertion confirms starting is enabled before advancing the clock. Advancing 400ms executes the component's actual 300ms expiry timer. Existing assertions still require starting to become disabled and `api.startBeadAssignment` never to be called. No wall-clock timeout was increased.

Validation in the native candidate:

- `npm test -- src/test/beads-work-reviewer.test.tsx`: **11 passed**, exit 0 (`web-expiry-fixed.log`).
- Full `npm test`: **42 files / 469 tests passed**, exit 0, 7.27s (`web-tests-fixed.log`). The suite emits existing diagnostic stderr from intentional failure-path scenarios; the final test result is passing.

## MCP Apps formatting

Used the installed existing Prettier version on exactly the twelve reported paths: AgentView, Dashboard, EventStreamView, GraphView and its test, shared mcpApp/TaskControl/types, and integration-008-recovery-order/integration/mockHost/turn-recovery tests. No unlisted generated files were rewritten. Turn-recovery required a second formatting pass before the full check stabilized.

`prettier --check 'src/**/*.{ts,tsx}' 'e2e/**/*.{js,mjs,ts}'` passed, exit 0 (`mcp-prettier-fixed.log`). All twelve files also preserve their TypeScript-emitted JavaScript ASTs compared with the exact original preformat baseline; comparison ignores comments/source positions and redundant parentheses while preserving identifiers, literals and executable structure. The original baseline transpilation hashes matched before synchronization. Proof: `/home/felni/cao015-final/mcp-format-proof.json`.

Scoped `git diff --check` passed. The parent owns MCP tests/type/build and browser/E2E gates, dependency patch updates, exact staged selection and final publication checks.

## Fresh gates after the compatible source-map-js patch

The parent installed both locked npm environments with the approved `source-map-js` override (`^1.2.2`). This reviewer then reran gates against those patched candidate dependencies, after formatting the changed Web files. No dependency edits were made by this reviewer.

Staged-style anticipation derived **35 changed/new Web JS/TS paths** from the original captured Git status. Existing Prettier reported **34 warnings**; only those 34 files were formatted and synchronized to the original workspace. The final check of all 35 paths passed (exit 0). Proof compares canonical emitted JavaScript ASTs with the original workspace source: ignores comments/positions/redundant parentheses and merges adjacent literal string children introduced by JSX whitespace formatting. All 34 matched; identifiers, literals, rendered text and executable structure are retained. Proper source filenames were supplied to TypeScript so `.ts` generic syntax is parsed correctly. Evidence: `web-format-proof.json`, `web-prettier-initial.log`, `web-prettier-final.log` in the private final evidence directory. Byte comparison after synchronization confirmed the owned files match across workspaces.

Fresh native results, all exit 0:

| Gate | Result | Private log |
|---|---|---|
| Web full unit suite | 42 files / 469 tests passed | `web-tests-patched-final.log` |
| Web TypeScript + Vite build | PASS; existing 500kB chunk-size warning remains | `web-build-patched-final.log` |
| MCP full unit suite | 12 files / 92 tests passed | `mcp-tests-patched-final.log` |
| MCP TypeScript | PASS | `mcp-types-patched-final.log` |
| MCP four bundles | PASS | `mcp-build-patched-final.log` |
| MCP JIT scan | Four files clean | `mcp-jit-patched-final.log` |
| MCP gzip size budgets | 49.4/49.4/48.5/86.6KB; all within limits | `mcp-size-patched-final.log` |
| Web production npm audit | Zero vulnerabilities at every severity | `web-audit-final.json` |
| MCP production npm audit | Zero vulnerabilities at every severity | `mcp-audit-final.json` |
| MCP Chromium E2E | Seven passed, 5.2s | `mcp-browser-patched-retry.log` |

Production audit commands were exactly `npm audit --omit=dev --json`; these results do not claim a development-dependency audit.

Initial browser attempts did not pass: MCP lacked its pinned Chromium revision 1228; Web revision 1243 failed before browser startup because `libasound.so.2` was unavailable. Installed the matching MCP browser cache, then set process-scoped `LD_LIBRARY_PATH=/home/felni/.local/share/playwright-mcp/lib` to the retained private audio library. `ldd` on both headless browser binaries reported no missing dependencies. No host packages or product configuration were changed. Both browser reruns use one worker; MCP uses a fresh free loopback port, and Web's real Python authentication runtime binds its own free ports and private databases. The failing startup attempts are not acceptance evidence.

## Additional confirmed composition defect — empty 204 body adaptation

The fresh Web browser suite first reached **16 passed / 1 failed** (`web-browser-patched-retry.log`), and the setup/restart case failed again in isolation. After a real server restart, POST `/auth/logout` returned **204** but the browser stayed in pending logout. A bounded, temporary test-only trace printed HTTP paths/status/error codes and a boolean describing the response body, never credentials, cookies or response content. It showed `/auth/session` 200 before logout; logout 204; `response.body === null` was **false** in real Chromium; no subsequent session confirmation request was issued. Temporary diagnostics were then removed byte-for-byte.

Graphify query ran before mutation (`browser-logout-graph-query.log`); its local structural context was checked against `browser_auth_routes`, browser-auth service/repository, the real restart fixture and Web auth transport. Server logout commits revocation and returns 204 correctly. The first incorrect boundary is `web/src/auth.ts` rebuilding a buffered zero-byte stream as `new Response(Uint8Array(0), {status: 204})`: Fetch's Response constructor forbids a non-null body for that status and raises TypeError before cookie confirmation. The existing unit response `new Response(null,{status:204})` had hidden the real browser composition.

A regression in `web/src/test/browser-auth.test.ts` supplies the real observed response shape: 204 plus an empty ReadableStream. Before the fix it failed with **TypeError: Invalid response status code 204**, while the other eleven tests passed (`web-logout-stream-red.log`). The one-line transport correction reconstructs zero-byte buffered responses with a null body; nonempty body buffering, deadline, abort and auth-state fences remain unchanged. The regression requires a subsequent session confirmation, anonymous state and cleared pending logout. Existing offline pending-logout, body-read deadline/cancellation and concurrent-session behavior remain covered.

Source and regression were synchronized to both workspaces. Focused auth/session checks: **27 passed**, exit 0 (`web-logout-stream-green.log`). Fresh full Web suite after the fix: **42 files / 470 passed**, exit 0, 8.26s (`web-tests-logout-final.log`). Fresh TypeScript/Vite build passed, exit 0 (`web-build-logout-final.log`); existing chunk-size warning remains. Parent owns final exact-index selection checks. MCP source/dependencies were unchanged by this correction, so its preceding 92-unit/seven-browser/typing/build/JIT/size evidence remains applicable.

Final Web Chromium run after rebuilding the corrected transport: **17 passed, exit 0, 55.7s** (`web-browser-logout-final.log`). This includes the original setup → real interpreter/server restart → retained remembered session → confirmed logout → login scenario, as well as real SQLite contention, cookie refusal, offline/reconnect, SSE/WebSocket revocation and cross-tab cases. One worker and the existing private process-scoped audio library were used. The whole 35-path changed-Web formatter check also passed after the fix (`web-prettier-logout-final.log`), and scoped diff whitespace checks passed.

An index/worktree comparison before the parent restaged showed the two new logout-fix files differed from the current index; the restored E2E setup file matched it. Consequently these executable results certify the checked candidate source bytes, and do not yet certify the parent's final index. Both updated source/test files and this report are synchronized to the original workspace for final selection. **Final frontend verdict: PASS for the verified candidate; exact staged-tree selection remains parent-owned.**

## Fresh post-format coverage

The parent repeated the entire MCP suite with `npm run test -- --coverage --maxWorkers=1` after all formatting and locked dependency updates: exit 0, **92 passed**. Fresh line coverage is **478/531 = 90.01%**, above the unchanged 90.0% frontend floor. Statements remain 517/594 (87.03%), branches 419/528 (79.35%) and functions 108/124 (87.09%). The earlier 90.64% line report predates formatting and is historical; it does not certify the current line layout. Log: `/home/felni/cao015-final/mcp-coverage-final.log`. The complete backend/frontend ratchet awaits the final backend report.

## Real browser confirmation after T080

The changed browser SQLite reference/setup ownership was exercised again
against the frozen final backend after T080. All **17 Chromium cases passed**,
exit 0, **57.5 seconds**, with the same private process-scoped audio library,
one worker and real isolated authentication runtime. Login, restart, logout,
remembered/temporary cookies, contention, offline reconnect and SSE/WebSocket
revocation remained accepted. Frontend source/build bytes were unchanged.
The final source manifest SHA-256 is
`fd1d17a9bd9159a2d6f59ce7342a60dd16408f3fb21d322036d75b2a7398a21a`.
Private log `web-browser-t080.log`: SHA-256 `00dd09d6314314d1fe89067d18bcccd571b46b997395cd4dfbe89f433c298f7c`.

## Revalidación tras reconstruir el entorno temporal

La fuente frontend permanece en el manifiesto final T077–T087. Las 470 pruebas Web de 42 archivos vuelven a pasar (15.92 s), junto con types/build. Las 92 pruebas MCP vuelven a pasar y producen cobertura de líneas 478/531 = 90.01%; sus types/build/JIT/size pasan. Los logs privados anteriores fueron eliminados durante la limpieza del entorno; los resultados anteriores conservan su identidad histórica. El ratchet conjunto espera la cobertura Python 3.12 de la matriz nueva.

El barrido Chromium completo sobre la fuente actual T077–T087 aprobó **17 casos, exit 0, 56.1 s**. Se ejercitaron login, cookies, revocación SSE/WebSocket, logout, actividad/deadline absoluto y setup con reinicio real. El SHA-256 del log privado es `0ba9427553eb6f35c41b87769a558ef0aba2a5ef8b22a47d616e2cb3cfa9a3cc`; la fuente final está ligada al manifiesto `74c64da609f1c71cae85c9b95e9ae1269b6c1a7de2039eb3129652ea22de243c`. Los logs nuevos se retirarán tras registrar los controles finales.

## Cobertura conjunta aceptada de la fuente final

La selección completa Python 3.12 terminó con 16604 PASS, 100 SKIP y cero fallos. Su informe individual produce 87.1310926894555% de cobertura; SHA-256 del JSON `b7fc06fd1e8e47b8228cef3bcf2a5478763cee86a148c0ec70405b9a8ddb5590`. El ratchet real terminó con exit 0: Python **87.13% ≥87.00%** y MCP Apps **90.01% ≥90.00%**, usando ambos informes presentes y sin cambiar mínimos. El posthash verifica 1361 fuentes/configs sin diferencias. Este éxito de 3.12 no sustituye las otras cuatro versiones pendientes/completadas de la matriz; commits y publicación siguen pendientes.
