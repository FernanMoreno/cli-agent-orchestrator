# Composition review — Docker installation

Changed subsystems: application packaging, container runtime/proxy, installation
and migration lifecycle, external Windows MCP relay, runtime mounts and docs.
Neighbors verified against source: personal deployment `_config`/binding/import
order, SQLite backup, JWT/JWKS, browser-origin checks, FastAPI/static assets,
Work backend image/engine contracts, provider executable and MCP configuration.

Invariants: same UID, private signing key/config, same issuer/principal/scopes,
installation ID, account/session store and absolute state paths; no restore() or
implicit session revocation. Only the localhost application port is published.
Proxy strips forwarded authority headers and preserves browser origin/cookies.
Runtime supervises API/issuer/nginx, rotates internal credentials and stops all
application processes with its container. Work uses its separate immutable rootfs.

Lifecycle: preflight precedes host stop; backup follows stop while the service lock
is free; candidate health and protected JWT access precede host disable. A unique
instance label limits candidate cleanup. Compensation restores metadata/processes
and waits for API health. Upgrade/rollback preserve current data. Atomic private
manifest, install lock, bounded Docker/systemctl calls and signal compensation
cover concurrent installation and partial failure. Windows relay verifies Unix
peer UID and exact registered arguments, and reaps children/process groups after
bounded output draining.

Verification evidence:

- 108 applicable auth/deployment/documentation tests passed before the final relay
  refinement; all 27 installer/runtime/relay tests passed after that refinement.
- Built frontend with TypeScript/Vite and all four MCP static applications in the
  final image; frozen Python dependencies installed and packaged assets verified.
- Real disposable Docker installation test passed on the final image: bootstrap
  with a 10-character password, origin rejection, forwarded-header stripping,
  persistent cookie across stop/start, repeated install, upgrade, rollback and
  injected candidate-health failure restoring the previous usable API.
- Two existing Work compatibility scenarios passed, including real Docker worker
  execution and persisted output while cookie renewal/logout preserved authority.
- Runtime-image preflight verified Work rootfs/engine, Claude/Codex/OpenCode
  executables, Windows MCP initialize and Chromium rendering.
- Import Linter: five existing architecture contracts kept, zero broken.
  `project-composition-check caos` passed after executable acceptance.
- Existing API/auth tests plus real HTTP are the consumer/provider contract checks;
  frontend/backend are packaged together, so no additional independently evolving
  API or new schema requires a separate Pact suite.

Root causes found and fixed: missing build fixture, native TUI accidentally in
context, writable HOME/proxy temporaries, wrong Work backend class, absent Buildx,
foreign glibc libraries, early durable-home selection, anonymous health requests
missing browser origin, compensation returning before readiness, stale rollback
metadata, and MCP process/descendant cleanup. Docker Desktop became unavailable
once during disposable testing; the host service remained active, Desktop was
started and acceptance was repeated successfully before migration.

Deployment constraints: Linux/WSL amd64 owner installation with Docker socket
access, stable UID and localhost origin; Docker/WSL must remain running. Windows
Studio remains an external application via its optional private stdio relay.
Provider versions/MCP startup were checked; no paid model call was added to this
installation task. Arbitrary historical schema downgrades are outside rollback.

Personal acceptance: migrated the real root, verified exact identity/account/session
row digests and all 95 table counts against the pre-migration snapshot, proved
frontend/API unavailable while the container was stopped, and restored healthy
startup. Real rollback to the host service passed; remigration passed and left
the host service inactive/disabled. Final data snapshot remained identical.
Playwright rendered the Spanish login with both tabs; the expected unauthenticated
/auth/session 401 was handled by the login screen. The application container is
healthy and publishes only 127.0.0.1:9889. Its Buildx plugin was verified inside it.

Final diff reviewed. Graphify decision: core auth/API/Work dependency edges are
unchanged; deployment entrypoints and dynamic subprocess boundaries were verified
directly and recorded here. A global generated graph rewrite would overwrite the
owner's pending graph artifacts; those are preserved. No vault transaction is
needed: durable installation contracts are already recorded in repository docs
and research, while raw test logs stay outside the repository.

Verdict: **PASS** within the deployment constraints above.
