# Personal localhost deployment

The recommended integrated deployment is now [the automatic Docker installation](docker-installation.md):
`./install.sh` packages the frontend, backend and login in one application container.
Its installer migrates the state described below and disables the previous service
only after successful health checks. Use `./install.sh status`, `start` and `stop`
for the Docker deployment.

The following commands describe the previous host deployment, retained for rollback.
CAO runs on this computer as a persistent user service. Interactive providers
run on the Linux host using the existing subscriptions. Docker Work runs explicit
static/process contracts; its isolated worker has no network or workspace mount.
The API and its local JWKS issuer listen on `127.0.0.1`, ports 9889 and 9890.
Availability requires this computer and WSL to remain running.

## Installed instance

- Runtime: `~/.local/share/cao-personal-runtime/`
- Private configuration, signing key and state: `~/.local/share/cao-personal/`
- tmux sockets: `~/.local/share/cao-personal-sockets/`
- User service: `cao-personal.service`

```bash
systemctl --user status cao-personal.service
systemctl --user start cao-personal.service
systemctl --user restart cao-personal.service
systemctl --user stop cao-personal.service
```

The unit is enabled for the user session, restarts after a failure and rotates
its server process every 12 hours. Normal stops exit cleanly. The internal bearer
is valid for 24 hours; client bearers expire after one hour. An authenticated
server uses grant-authorized knowledge; startup skips local-operator-only legacy
memory repair. No JWT is converted into local-operator authority.

## Authenticate the CLI and TUI

Renew and load the private client environment without displaying its contents:

```bash
~/.local/share/cao-personal-runtime/bin/python \
  ~/.local/share/cao-personal-runtime/personal_deployment.py renew
source ~/.local/share/cao-personal/client.env
export PATH="$HOME/.local/share/cao-personal-runtime/bin:$PATH"
cao status
cao tui
```

`client.env` includes the local bearer. Keep it private and outside Git. The TUI
sends that bearer for ordinary and streaming requests, redacts it from debug
output and does not follow redirects while authenticated. Reading `/health` does
not prove authentication: protected routes must reject requests without a token.

## Open the authenticated web UI

Open the owner's authorized web link using the installed runtime:

```bash
~/.local/share/cao-personal-runtime/bin/python \
  ~/.local/share/cao-personal-runtime/personal_deployment.py web
```

For a private personal installation without an account, the main application
shows **Iniciar sesión** with a **Crear cuenta** tab and a link to create
the first account before mounting the dashboard. Open **Crear cuenta**, then choose a username,
password and confirmation, and optionally remember the browser. The existing
operator JWT authorizes this one-time setup; the browser removes the access link
fragment. Anonymous users and JWTs for another operator cannot create an account.
Setup persists the existing identity/scopes, starts a HttpOnly cookie session,
clears the bootstrap bearer, and opens the dashboard without a service restart.
Later visits to the ordinary URL show the login form or the authenticated dashboard.
There is no public registration or web password recovery.

Terminal account creation remains available as an operator alternative:

```bash
systemctl --user stop cao-personal.service
~/.local/share/cao-personal-runtime/bin/python \
  ~/.local/share/cao-personal-runtime/personal_deployment.py \
  account-create --username operator
systemctl --user start cao-personal.service
```

The command asks for the password twice using a hidden terminal prompt. It
accepts no password argument or environment variable. There is no default account
or public registration. The account keeps the existing JWT operator identity and
configured scopes; it does not create Work grants or provisions. Account creation
verifies the persistent local signing key and claims while the issuer is stopped.

Open the ordinary URL `http://127.0.0.1:9889/`, or run:

```bash
~/.local/share/cao-personal-runtime/bin/python \
  ~/.local/share/cao-personal-runtime/personal_deployment.py web
```

With local login enabled, `web` opens that URL without a bearer fragment. Choose
“Remember this browser” for persistence across browser and server restarts. The
default remembered limits are seven days idle and thirty days total; temporary
sessions have eight hours idle and twenty-four hours total, and a new browser
session without restored temporary state requires login. The browser cookie is
HttpOnly and stays outside JavaScript storage. Renewal occurs automatically.
Logout and logout-all revoke browser access without cancelling agents or Work.

For a forgotten password, run `personal_deployment.py account-reset` as the same
local owner. This prompts twice, preserves identity, and revokes previous browser
sessions; it can run while the service is active. `account-disable` revokes browser
sessions and selects bearer mode, preserving JWKS, CLI/TUI credentials and Work.
Restart the service after enabling or disabling the mode so its startup policy is
reloaded. The browser cannot perform local account creation or recovery.

Without local login enabled, `web` retains the previous private bearer link in
`web-link.txt`. The one-hour bearer travels in a URL fragment, which the UI removes
and stores only in the current tab's sessionStorage. The Connect form accepts a
fresh CAO bearer. Keep these links private. Expired credentials show a connection
prompt; authentication remains enabled. Authenticated redirects and cross-origin
requests are rejected.

## Provider profiles

Install a selected OpenCode profile before launching it:

```bash
cao install developer --provider opencode_cli
```

The adapter probes the installed major version. V1 retains its TUI command and
parser. V2 uses `mini --standalone`, a private per-terminal configuration and
translated permissions/MCP declarations. Existing Go authentication is transported
through a private environment file. The adapter retains the explicitly selected
model; it does not substitute another model. For this acceptance, the Go model is
`opencode-go/longcat-2.5-preview-free`; Codex uses `gpt-6-luna` and Claude uses
`claude-haiku-4-5` through the existing subscriptions.

V2 changes documented by OpenCode: [CLI commands](https://opencode.ai/v2/docs/cli/commands/),
[configuration](https://opencode.ai/v2/docs/config/),
[provider configuration](https://opencode.ai/v2/docs/providers).
Run provider acceptance in Linux storage: the current OneDrive checkout adds
substantial latency to imports and repository operations.

## Docker Work provisioning

The service explicitly registers `docker-local` with its immutable image ID.
The default application registry remains empty unless local Docker is enabled.
A verified bearer alone does not authorize a Work launch: a matching provision,
grant, frozen context and executable identity are also required.

The operator tool provides a bounded static launch provision:

```bash
~/.local/share/cao-personal-runtime/bin/python \
  ~/.local/share/cao-personal-runtime/personal_deployment.py provision \
  --worker /absolute/path/to/static-worker \
  --checkout /absolute/path/to/checkout \
  --selection personal-docker --project personal-docker
```

This command requires the running JWKS issuer and a verified admin bearer. It
preserves an existing scheduler policy. Its authority expires: the root grant
lasts 30 minutes and the launch lease lasts 20 minutes. Existing selectors are
not silently overwritten. Use the Work provisioning service with an explicit
expected revision for replacement. This static worker does not acknowledge or
submit a model result; the Docker workflow acceptance uses a separate worker
that implements the authenticated receipt/result protocol.

## Backup and restore

Stop the service before taking a backup. Use a new destination each time.
SQLite's backup API includes WAL contents and validates database integrity.
Private keys remain private. Backups reject symlinks, sockets and devices; tmux
sockets are stored outside the backed-up directory.

```bash
systemctl --user stop cao-personal.service
~/.local/share/cao-personal-runtime/bin/python \
  ~/.local/share/cao-personal-runtime/personal_deployment.py \
  backup /absolute/path/to/new-backup
systemctl --user start cao-personal.service
```

Restore into a new directory; the tool refuses to overwrite an existing instance:

```bash
~/.local/share/cao-personal-runtime/bin/python \
  ~/.local/share/cao-personal-runtime/personal_deployment.py \
  --root /absolute/path/to/new-instance \
  restore /absolute/path/to/backup
```

A restored instance retains its issuer, key, account, operator identity and Work
state. Restore revokes every browser session in the staging copy before publishing
the destination, so the restored browser must log in again. The restored private
`client.env` uses the final destination paths.

A restored instance retains its issuer and key. Its ports match the original;
stop the original before serving the restoration on those ports. Provider
subscription login files belong to the operator's home and are not copied into
this deployment backup. Recheck those logins independently after moving machines.

## Recreate the runtime

Use a Linux directory for the installed runtime. Build the wheel and native TUI
from the selected checkout, install its frozen dependencies, and copy
`scripts/personal_deployment.py` and `scripts/local_work_docker_demo.py` beside
the runtime's `bin/` directory. From the source checkout, run
`scripts/local_work_docker_demo.py` to build and check the local rootfs; pin the
resulting `sha256:` image ID. The optional browser Docker acceptance is described
in `specs/004-browser-login-sessions/completion-evidence.md`.

```bash
uv venv ~/.local/share/cao-personal-runtime
uv export --frozen --all-extras --no-dev --no-emit-project \
  --format requirements-txt > /tmp/cao-runtime-requirements.txt
uv pip install --python ~/.local/share/cao-personal-runtime/bin/python \
  -r /tmp/cao-runtime-requirements.txt
PATH="$HOME/.cargo/bin:$PATH" uv run python scripts/build_tui.py build
uv build --wheel
# Install the exact wheel produced by that build, without resolving dependencies.
uv pip install --python ~/.local/share/cao-personal-runtime/bin/python \
  --no-deps /absolute/path/to/the-built-wheel.whl
cp scripts/personal_deployment.py scripts/local_work_docker_demo.py \
  ~/.local/share/cao-personal-runtime/
```

For a new private instance, run `personal_deployment.py init --image-id SHA256_ID`,
then generate its `unit` output into `~/.config/systemd/user/cao-personal.service`.
Run `systemctl --user daemon-reload` and `systemctl --user enable --now
cao-personal.service`. Initialization refuses insecure paths, changed ports or a
different image ID on an existing instance.
