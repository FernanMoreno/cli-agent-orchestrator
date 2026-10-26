# Test API Worker Contract

This is an internal test-harness contract, not a new product API.

## Launch

The parent launches one owned worker process using the current interpreter and the candidate source root, with a minimal explicit environment. The child command is argument-vector based (no shell). The worker loads `cli_agent_orchestrator.api.main.app`, constructs and initializes a fresh `InteractionService` from the private main database target, sets that service on the app state, and serves the production application routes with lifespan disabled.

Required process inputs:

- `CAO_HOME_DIR`: private run-local application home;
- `CAO_DATABASE_CONFIG_FILE`: private configuration written by the isolated PostgreSQL fixture;
- `CAO_API_HOST`: loopback only;
- `CAO_API_PORT`: one validated integer port, unchanged across worker replacement;
- `CAO_AUTH_MODE`: test-local auth-off mode for loopback calls;
- `CAO_AGUI_ENABLED`: enables the real production AG-UI stream for this run;
- `PYTHONPATH`: run-derived candidate `src/` and repository root;
- `HOME` and `TMPDIR`: run-derived private paths where required;
- `TMUX_TMPDIR`: a run-derived private socket directory so the production
  stream's fleet snapshot cannot inspect the operator's default tmux server;
- `PYTHONDONTWRITEBYTECODE=1`.

The environment must omit provider API key variables, operator provider-home variables, and unrelated inherited secrets.

## Authentication boundary

The worker serves the production app and keeps its production middleware and route dependencies enabled. For this isolated acceptance it uses `CAO_AUTH_MODE=off` and a loopback-only listener. Agent interaction routes still validate the per-incarnation interaction capability; human answer routes use the production local-operator principal derived from the loopback ASGI peer; and AG-UI streaming uses the production scope authorization helper in local mode. This validates local auth-off route composition, not browser-session login or cookie authentication.

## Readiness and restart

Readiness is established by polling a real production route for the already registered terminal; no test-only health endpoint is added. The parent must confirm worker A exited before starting worker B on the same port. Worker B reads the same private target config, creates a new process/service instance, and must report the existing terminal/source and durable pending questions through production routes and AG-UI state.

## Stream framing

The reader consumes actual `text/event-stream` frames:

- `event: STATE_SNAPSHOT` carries a JSON `snapshot`;
- `event: STATE_DELTA` carries JSON patch operations;
- comments/keep-alive lines are ignored;
- frame boundaries are blank lines;
- data lines are joined per SSE framing rules before JSON decoding.

Only production frame names and payload fields are accepted. A parser error, unavailable projection, missing expected interaction, worker exit, or timeout fails the acceptance.

## Ownership and logging

CX-07 adds a private, closed `wait_started` observation with only `event`,
`group_id`, `worker_pid` and integer `wait_seconds` (0–30), synchronously before
awaiting the service. It is an entry observation, not proof of successful
capability validation. Pair it with the matching owned Popen PID, database group,
an outstanding native wait and no successful return. The opt-in crash method
kills/reaps only that live Popen child; default stop/restart remain unchanged.
After reaping, re-read observations to reject a response racing with the crash.
Recovery requires original/replacement entries, different PIDs, a decreasing
wait budget, one ready return and one native wait invocation. Public receipts
retain only interruption/recovery booleans; private observations contain no
capability, question/answer content or opaque receipt.

The parent records exact child PIDs/process groups at launch and signals only those owned children. It confirms process exit before cleanup. The child writes no prompt, response, database credential, source capability, receipt token, or raw provider output to stdout/stderr. The parent retains detailed logs only under the private run root; sanitized evidence contains bounded status, IDs/identity booleans under the existing receipt policy, and timing/cleanup categories.
