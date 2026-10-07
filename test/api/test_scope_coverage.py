"""H4 — scope coverage across mutating routes.

Two layers of assurance:

* a **guard test** that enumerates the live FastAPI route table and asserts every
  mutating route (POST/PUT/PATCH/DELETE) carries a ``require_any_scope``
  dependency or an explicitly recognized verified-work authority boundary, so a
  future route cannot silently regress the coverage;
* **enforcement tests** that, with auth enabled, a ``cao:read`` token is 403'd on
  a write route and a ``cao:write`` token is 403'd on an admin (delete) route,
  while the matching scope is admitted past the dependency.

Default-off behavior (the dependency returns the full scope set and enforces
nothing) is covered by the existing endpoint suites, which exercise these routes
with no auth configured.
"""

import ast
import inspect
import textwrap

import pytest

from cli_agent_orchestrator.api import browser_auth_routes, work_routes
from cli_agent_orchestrator.api.knowledge_routes import authority as knowledge_authority
from cli_agent_orchestrator.api.main import app, get_work_launch_principal
from cli_agent_orchestrator.security import auth

# Mutating HTTP methods that must be scope-gated when present on a route.
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Routes that use a mutating verb but perform no state change, so they are
# intentionally not scope-gated. ``POST /workflows/validate`` only parses and
# validates a spec file (read-only), mirroring a GET. ``/agents/profiles/templates/validate``
# and ``/agents/profiles/templates/preview`` are POSTs for the same reason — their config
# travels in a JSON body — and mutate nothing (schema check / template render).
# ``/agents/profiles/validate`` is the same shape: the profile content travels in
# a JSON body and is checked against the profile schema without being persisted.
_EXEMPT = {
    ("POST", "/workflows/validate"),
    ("POST", "/agents/profiles/templates/validate"),
    ("POST", "/agents/profiles/templates/preview"),
    ("POST", "/agents/profiles/validate"),
}


def _has_scope_dependency(route) -> bool:
    """True if ``route`` has a ``require_any_scope`` dependency anywhere in its tree."""
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        if call is not None and "require_any_scope" in getattr(call, "__qualname__", ""):
            return True
        stack.extend(getattr(dep, "dependencies", []))
    return False


def _direct_guard_calls(endpoint, name):
    """Read executed top-level guards, excluding nested or conditional markers."""
    function = ast.parse(textwrap.dedent(inspect.getsource(endpoint))).body[0]
    for statement in function.body:
        if not isinstance(statement, (ast.Expr, ast.Assign, ast.AnnAssign, ast.Return)):
            continue
        value = statement.value
        if isinstance(value, ast.Await):
            value = value.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == name
            and value.args
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == "request"
        ):
            yield value


def _has_manual_transport_authority(route) -> bool:
    """Recognize exact live transport handlers and their resource/scope guards.

    These handlers validate identity/scopes manually so single-use tickets can
    authorize a stream without reusable query credentials. Denial tests below
    execute every route; this audit also checks the actual guarded resource and
    required scopes rather than accepting an endpoint name or marker alone.
    """
    from cli_agent_orchestrator.api import main

    handlers = {
        ("POST", "/events/ticket"): (
            main.events_ticket,
            "_issue_transport_ticket",
            '"/events"',
            ("SCOPE_READ", "SCOPE_WRITE", "SCOPE_ADMIN"),
        ),
        ("POST", "/agui/v1/stream/ticket"): (
            main.agui_ticket,
            "_issue_transport_ticket",
            '"/agui/v1/stream"',
            ("SCOPE_READ", "SCOPE_WRITE", "SCOPE_ADMIN"),
        ),
        ("POST", "/terminals/{terminal_id}/ws/ticket"): (
            main.terminal_ticket,
            "_issue_transport_ticket",
            'f"/terminals/{terminal_id}/ws"',
            ("SCOPE_WRITE", "SCOPE_ADMIN"),
        ),
        ("GET", "/events"): (
            main.events_stream,
            "_stream_authorization",
            '"/events"',
            ("SCOPE_READ", "SCOPE_WRITE", "SCOPE_ADMIN"),
        ),
        ("GET", "/agui/v1/stream"): (
            main.agui_stream,
            "_stream_authorization",
            '"/agui/v1/stream"',
            ("SCOPE_READ", "SCOPE_WRITE", "SCOPE_ADMIN"),
        ),
    }
    methods = getattr(route, "methods", None) or set()
    if len(methods) != 1:
        return False
    expected = handlers.get((next(iter(methods)), getattr(route, "path", None)))
    if expected is None or getattr(route, "endpoint", None) is not expected[0]:
        return False
    endpoint, guard, resource, scopes = expected
    for call in _direct_guard_calls(endpoint, guard):
        if len(call.args) != 3 or not isinstance(call.args[2], ast.Tuple):
            continue
        if ast.dump(call.args[1]) != ast.dump(ast.parse(resource, mode="eval").body):
            continue
        if (
            all(isinstance(item, ast.Name) for item in call.args[2].elts)
            and tuple(item.id for item in call.args[2].elts) == scopes
        ):
            return True
    return False


def _has_local_peer_authority(route) -> bool:
    """Exact loopback bootstrap and signed project/action peer boundaries.

    Local discovery and pairing deliberately bootstrap independently of the
    operator JWT. Resource operations require signed, pinned peer grants. Their
    distinct negative contracts are executed below and in test_local_peer_auth.
    """
    from cli_agent_orchestrator.api import local_coordination_routes as local

    handlers = {
        ("GET", "/local-coordination/identity"): (local.local_peer_identity, None),
        ("GET", "/local-coordination/instances"): (local.list_local_peer_instances, None),
        ("GET", "/local-coordination/projects/verify"): (local.verify_local_project, None),
        ("POST", "/local-coordination/pairings"): (local.receive_pairing_invitation, None),
        ("POST", "/local-coordination/pairings/{challenge_id}/accept"): (
            local.accept_pairing_invitation,
            None,
        ),
        ("POST", "/local-coordination/tasks"): (local.submit_local_peer_task, "task:submit"),
        ("GET", "/local-coordination/tasks/{task_id}"): (
            local.inspect_local_peer_task,
            "task:status",
        ),
        ("POST", "/local-coordination/tasks/{task_id}/cancel"): (
            local.cancel_local_peer_task,
            "task:cancel",
        ),
        ("DELETE", "/local-coordination/peers/{peer_id}"): (local.revoke_local_peer, "peer:revoke"),
        ("GET", "/local-coordination/sessions"): (
            local.list_local_project_sessions,
            "session:read",
        ),
    }
    methods = getattr(route, "methods", None) or set()
    if len(methods) != 1:
        return False
    expected = handlers.get((next(iter(methods)), getattr(route, "path", None)))
    if expected is None or getattr(route, "endpoint", None) is not expected[0]:
        return False
    endpoint, scope = expected
    if scope is None:
        return any(_direct_guard_calls(endpoint, "_require_loopback"))
    for call in _direct_guard_calls(endpoint, "_active_peer_headers"):
        keywords = {item.arg: item.value for item in call.keywords}
        required = keywords.get("required_scope")
        if (
            isinstance(required, ast.Constant)
            and required.value == scope
            and "project_id" in keywords
        ):
            return True
    return False


def _has_verified_workflow_capability_authority(route) -> bool:
    """Recognize only the exact composed sealed-capability boundaries.

    Actual signed scoped Work/start/replay/invalid-capability behavior is checked
    by integration_008_coordinator; these routes are not blanket exemptions.
    """
    import inspect

    from cli_agent_orchestrator.api import main, work_coordinator_routes

    if getattr(route, "methods", None) != {"POST"}:
        return False
    if (
        getattr(route, "path", None) == "/terminals/run-step"
        and getattr(route, "endpoint", None) is main.run_step
    ):
        stack = list(getattr(route.dependant, "dependencies", []))
        while stack:
            dependency = stack.pop()
            if getattr(dependency, "call", None) is main._run_step_scopes:
                return True
            stack.extend(getattr(dependency, "dependencies", []))
        return False
    if (
        getattr(route, "path", None) == "/ralph/runs/{run_id}/context"
        and getattr(route, "endpoint", None) is work_coordinator_routes.context
    ):
        source = inspect.getsource(work_coordinator_routes.context)
        compact = "".join(source.split())
        return (
            "X-CAO-Workflow-Run-Credential" in source
            and "authenticate_run_capability,run_id,body.generation,token" in compact
            and "service.context,principal" in compact
        )
    return False


def _has_knowledge_authority(route) -> bool:
    """Versioned knowledge checks scopes plus live grants in its service transaction."""
    calls = set()
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        calls.add(getattr(dep, "call", None))
        stack.extend(getattr(dep, "dependencies", []))
    return {auth.get_current_principal, knowledge_authority}.issubset(calls)


def _has_browser_auth_authority(route) -> bool:
    """Exact browser handlers enforce bootstrap, password, or session authority.

    Login cannot require an existing scope-bearing session. Setup verifies the
    configured operator token; login checks credentials; the remaining handlers
    verify the browser session. Their origin and denial contracts are exercised
    in test_browser_auth.py and test_browser_setup.py.
    """
    handlers = {
        ("POST", "/auth/setup"): browser_auth_routes.setup,
        ("POST", "/auth/login"): browser_auth_routes.login,
        ("POST", "/auth/renew"): browser_auth_routes.renew,
        ("POST", "/auth/logout"): browser_auth_routes.logout,
        ("POST", "/auth/logout-all"): browser_auth_routes.logout_all,
        ("POST", "/auth/password"): browser_auth_routes.password,
        ("GET", "/auth/session"): browser_auth_routes.session,
    }
    methods = getattr(route, "methods", None) or set()
    if len(methods) != 1:
        return False
    expected = handlers.get((next(iter(methods)), getattr(route, "path", None)))
    return expected is not None and expected is getattr(route, "endpoint", None)


def _has_verified_work_launch_authority(route) -> bool:
    """Only the durable launch ingress may use verified work identity without legacy scopes."""
    if getattr(route, "path", None) != "/work-launches" or getattr(route, "methods", None) != {
        "POST"
    }:
        return False
    calls = set()
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        calls.add(getattr(dep, "call", None))
        stack.extend(getattr(dep, "dependencies", []))
    return get_work_launch_principal in calls


def _has_verified_work_read_authority(route) -> bool:
    """These exact handlers enforce read scope and owner inside WorkQueries."""
    if getattr(route, "endpoint", None) not in {work_routes.get_work, work_routes.get_work_events}:
        return False
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        if getattr(dep, "call", None) is auth.get_current_principal:
            return True
        stack.extend(getattr(dep, "dependencies", []))
    return False


def _mutating_routes():
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        mutating = methods & _MUTATING_METHODS
        if not mutating:
            continue
        yield route, mutating


def test_every_mutating_route_is_scope_or_verified_work_authority_gated():
    """No mutating route may bypass scopes or an explicit verified-work authority boundary."""
    missing = []
    for route, mutating in _mutating_routes():
        if any((m, route.path) in _EXEMPT for m in mutating):
            continue
        if not (
            _has_scope_dependency(route)
            or _has_knowledge_authority(route)
            or _has_browser_auth_authority(route)
            or _has_verified_work_launch_authority(route)
            or _has_verified_workflow_capability_authority(route)
            or _has_manual_transport_authority(route)
            or _has_local_peer_authority(route)
        ):
            missing.append(f"{sorted(mutating)} {route.path}")
    assert not missing, (
        "mutating routes missing scope, verified knowledge authority, or verified work authority: "
        + ", ".join(missing)
    )


# --------------------------------------------------------------------------- #
# Disclosure-bearing GET routes.
#
# The mutating-route guard above cannot see the failure mode the agent-plugins
# adoption audit found (R2): `GET /plugins` shipped with no scope dependency while
# disclosing every plugin's source path plus the terminal IDs, session names,
# profile names and skill names of running work. Nothing enumerated GETs, so
# nothing caught it.
#
# Gating all 28 pre-existing ungated reads is NOT the fix — it would change the
# auth posture of shipped routes and could break existing unauthenticated readers,
# the same trade-off recorded for the `/workflows` reads below. So the guard
# inverts the default for GETs and pins today's state as data: a GET route must
# either carry a scope dependency or appear in `_OPEN_READS`. A new route is
# gated by default, and opening one becomes a visible, reviewable diff to this
# list rather than an omission nobody sees.
#
# `/plugins` is deliberately ABSENT from this list: it is gated.
# --------------------------------------------------------------------------- #
_OPEN_READS = {
    # Protocol/discovery surfaces that must answer before a caller can hold a
    # token at all, and CAO's liveness probe.
    "/.well-known/oauth-protected-resource",
    "/health",
    # Public login mode, timeout policy, and setup availability; no account or
    # session identity.
    "/auth/config",
    # Agent profile and provider catalogs. Schema/search/template discovery plus
    # which provider binaries are present. The profile *content* routes
    # (`/agents/profiles`, `/agents/profiles/{name}`) are gated upstream and so
    # are deliberately absent.
    "/agents/profiles/schema",
    "/agents/profiles/search",
    "/agents/profiles/templates",
    "/agents/profiles/templates/{category}/{name}/schema",
    "/agents/providers",
    # Settings reads.
    "/settings/memory",
    "/settings/skill-dirs",
    # Live session and terminal state that remains ungated upstream. The rest of
    # this surface — `/sessions`, `/terminals/{terminal_id}` and its inbox,
    # memory-context and output reads — is now scope-gated, which is the
    # direction that motivated gating `/plugins`.
    "/sessions/{session_name}/terminals",
    "/terminals/{terminal_id}/working-directory",
}


def _has_verified_work_read_authority(route) -> bool:
    """These exact handlers enforce read scope and owner inside WorkQueries."""
    if getattr(route, "endpoint", None) not in {work_routes.get_work, work_routes.get_work_events}:
        return False
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        if getattr(dep, "call", None) is auth.get_current_principal:
            return True
        stack.extend(getattr(dep, "dependencies", []))
    return False


def _api_get_routes():
    """Every GET route that FastAPI resolved a dependency tree for.

    Skips the routes Starlette mounts itself — ``/docs``, ``/redoc``,
    ``/openapi.json``, ``/docs/oauth2-redirect`` — which have no ``dependant`` and
    are not application endpoints.
    """
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "GET" not in methods:
            continue
        if getattr(route, "dependant", None) is None:
            continue
        yield route


def test_every_disclosure_bearing_get_route_is_gated_or_explicitly_open():
    """A GET route is scope-gated unless it is listed as deliberately open.

    The assertion is one-directional on purpose: it fails for a *new* ungated GET,
    not for one that becomes gated. Tightening a route should never require
    editing a test to permit it.
    """
    unlisted = [
        route.path
        for route in _api_get_routes()
        if not (
            _has_scope_dependency(route)
            or _has_knowledge_authority(route)
            or _has_browser_auth_authority(route)
            or _has_verified_work_read_authority(route)
            or _has_manual_transport_authority(route)
            or _has_local_peer_authority(route)
        )
        and route.path not in _OPEN_READS
    ]
    assert not unlisted, (
        "ungated GET route(s) not listed in _OPEN_READS: "
        + ", ".join(sorted(unlisted))
        + ". Add a scope dependency, or add the path to _OPEN_READS with a comment "
        "saying what it discloses and why that is acceptable."
    )


def test_the_open_reads_list_has_no_stale_entries():
    """Keeps `_OPEN_READS` honest in the other direction.

    Without this, a path that was gated (or deleted) would linger in the list and
    silently pre-authorize a *future* route that happened to reuse the path. This
    test is why gating a route requires removing it from the list — which is the
    reviewable diff the list exists to produce.
    """
    registered_ungated = {
        route.path for route in _api_get_routes() if not _has_scope_dependency(route)
    }
    stale = sorted(_OPEN_READS - registered_ungated)
    assert not stale, (
        "_OPEN_READS lists path(s) that are no longer ungated GET routes: "
        + ", ".join(stale)
        + ". Remove them — a stale entry would pre-authorize a future route reusing the path."
    )


def test_plugins_list_is_gated_and_not_exempted():
    """`GET /plugins` specifically — the route the audit found ungated (R2).

    Named rather than left to the generic guard because the generic guard would
    also pass if someone added `/plugins` to `_OPEN_READS`, and that would be
    exactly the regression. This asserts the route carries the dependency AND that
    the exemption list does not mention it.
    """
    matches = [route for route in _api_get_routes() if route.path == "/plugins"]
    assert matches, "GET /plugins is not registered"
    assert _has_scope_dependency(matches[0]), "GET /plugins lost its scope dependency"
    assert "/plugins" not in _OPEN_READS, "GET /plugins must not be exempted from the read floor"


def _override_scopes(scopes):
    async def _dep():
        return list(scopes)

    return _dep


@pytest.fixture
def auth_on(monkeypatch):
    """Enable the auth layer for enforcement tests."""
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://idp.example/jwks")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(auth.get_current_scopes, None)
    app.dependency_overrides.pop(auth.get_current_principal, None)


def test_read_token_forbidden_on_write_route(client, auth_on):
    """A cao:read token is 403'd on a write-gated route (POST /settings/skill-dirs)."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.post("/settings/skill-dirs", json={"extra_dirs": []})
    assert resp.status_code == 403


def test_write_token_admitted_on_write_route(client, auth_on):
    """A cao:write token passes the dependency on a write-gated route (not 403)."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_WRITE])
    resp = client.post("/settings/skill-dirs", json={"extra_dirs": []})
    assert resp.status_code != 403


def test_write_token_forbidden_on_admin_route(client, auth_on):
    """A cao:write token is 403'd on an admin (delete) route (DELETE /memory/{key})."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_WRITE])
    app.dependency_overrides[auth.get_current_principal] = lambda: auth._verified_principal(
        "https://issuer.test", "worker", [auth.SCOPE_WRITE], "jwt"
    )
    resp = client.delete("/memory/some-key")
    assert resp.status_code == 403


def test_admin_token_cannot_delete_local_legacy_memory(client, auth_on):
    """Even admin scopes cannot bypass the local legacy authority boundary."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_ADMIN])
    app.dependency_overrides[auth.get_current_principal] = lambda: auth._verified_principal(
        "https://issuer.test", "worker", [auth.SCOPE_ADMIN], "jwt"
    )
    resp = client.delete("/memory/some-key")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "legacy_memory_local_only"


# ---------------------------------------------------------------------------
# PR 526 review — SHOULD-FIX: the diagnostics bundle must be scope-gated.
#
# GET /workflows/runs/{id}/diagnostics returns the run's `inputs` (its raw
# inputs_json, passed through sanitize_output — which is transport hygiene, NOT
# secret redaction, so a credential passed as a workflow input comes back
# verbatim) plus capture-gated output excerpts. It had NO require_any_scope
# dependency, so with auth enabled ANY valid token could export it.
# ---------------------------------------------------------------------------
def test_diagnostics_route_is_scope_gated():
    """The wiring guard: the diagnostics route carries a require_any_scope dep."""
    routes = [
        r for r in app.routes if getattr(r, "path", None) == "/workflows/runs/{run_id}/diagnostics"
    ]
    assert routes, "diagnostics route not found in the route table"
    for route in routes:
        assert _has_scope_dependency(route), "diagnostics route lost its scope gate"


def test_unscoped_token_forbidden_on_diagnostics(client, auth_on):
    """A token carrying NO recognized scope is 403'd on the diagnostics export."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
    resp = client.get("/workflows/runs/r1/diagnostics")
    assert resp.status_code == 403


def test_read_token_admitted_on_diagnostics(client, auth_on):
    """A cao:read token passes the dependency (404 for an unknown run, not 403)."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.get("/workflows/runs/r1/diagnostics")
    assert resp.status_code != 403


def test_unscoped_token_forbidden_on_vault_status(client, auth_on):
    """Vault operational status is read-scoped even though it is content-free."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
    resp = client.get("/memory/vault/status")
    assert resp.status_code == 403


def test_read_token_admitted_on_vault_status(client, auth_on):
    """A read token reaches the status handler rather than being scope-rejected."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.get("/memory/vault/status")
    assert resp.status_code != 403


# ---------------------------------------------------------------------------
# PR 526 human review — BLOCKING: every payload-bearing run READ route must be
# scope-gated, not just /diagnostics.
#
# The review's point: GET /workflows/runs/{run_id} (inspect) returns every step's
# full `output_json` and `error` text — strictly MORE payload than /diagnostics,
# whose excerpts are capture-gated — and GET .../events and GET .../compare carry
# error_kind / reason / validation_result / output_ref and terminal-offset
# coordinates. All three shipped with no require_any_scope dependency while
# /diagnostics was deliberately gated, so the most payload-bearing read route
# escaped the PR's own scope model.
#
# Table-driven (not one test per route) so a future run read route added without
# a gate fails here. The expected paths are hard-coded literals rather than
# derived from the route table — a fixture sourced from the value under test
# would stay green if a route were renamed or dropped.
#
# SCOPE OF THIS CLAIM (PR #526 review fix cycle 1): this list is the set of
# payload-bearing read routes #504 OWNS, not an exhaustive inventory of every
# route that can return captured content. Specifically it does NOT include
# ``GET /terminals/{id}/output`` — that route predates #504 and the wider
# ``/terminals/*`` surface is uniformly ungated, so gating one pre-existing member
# of it is a separate, deliberate decision about that whole surface rather than
# part of this PR. ``GET /terminals/{id}/output/range`` IS included: #504 added it.
# Do not read a passing run here as "every content-returning route is gated."
# ---------------------------------------------------------------------------
_GATED_RUN_READ_ROUTES = [
    "/workflows/runs/{run_id}",
    "/workflows/runs/{run_id}/events",
    "/workflows/runs/{run_id}/compare",
    "/workflows/runs/{run_id}/diagnostics",
    "/terminals/{terminal_id}/output/range",
]


def _get_routes_for_path(path: str):
    """Every GET route registered at exactly ``path``."""
    return [
        r
        for r in app.routes
        if getattr(r, "path", None) == path and "GET" in (getattr(r, "methods", None) or set())
    ]


@pytest.mark.parametrize("path", _GATED_RUN_READ_ROUTES)
def test_payload_bearing_run_read_route_is_scope_gated(path):
    """Each payload-bearing run read route carries a require_any_scope dependency."""
    routes = _get_routes_for_path(path)
    assert routes, f"GET {path} not found in the route table"
    for route in routes:
        assert _has_scope_dependency(route), f"GET {path} is missing its scope gate"


@pytest.mark.parametrize("url", ["/workflows/runs/r1", "/workflows/runs/r1/events"])
def test_unscoped_token_forbidden_on_run_read_routes(client, auth_on, url):
    """A token carrying NO recognized scope is 403'd on inspect and events."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
    resp = client.get(url)
    assert resp.status_code == 403


def test_unscoped_token_forbidden_on_compare(client, auth_on):
    """A token carrying NO recognized scope is 403'd on compare.

    Separate from the parametrized pair because ``?against=`` is required: without
    it FastAPI would 422 on validation and never reach the scope dependency, so
    the 403 would prove nothing about the gate.
    """
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
    resp = client.get("/workflows/runs/r1/compare", params={"against": "r2"})
    assert resp.status_code == 403


@pytest.mark.parametrize("url", ["/workflows/runs/r1", "/workflows/runs/r1/events"])
def test_read_token_admitted_on_run_read_routes(client, auth_on, url):
    """A cao:read token passes the gate on inspect and events (404/200, never 403)."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.get(url)
    assert resp.status_code != 403


# --------------------------------------------------------------------------- #
# PR #525 review — the two NEW #505 run-read routes carry a read-scope gate.
#
# Scoped deliberately to the two routes issue #505 ADDED. The two pre-existing
# sibling reads (``GET /workflows``, ``GET /workflows/{name}``) are equally ungated
# and are left alone: gating them would change the auth posture of shipped routes
# and could break an existing unauthenticated reader, which is a bigger risk than
# the residual asymmetry.
#
# INTEGRATION NOTE (#504 merge): ``GET /workflows/runs/{run_id}`` was listed here
# as a third ungated sibling. It is NO LONGER ungated — issue #504 enriched that
# handler in place to the payload-bearing ``RunInspection`` shape and gated it with
# ``require_any_scope(READ, WRITE, ADMIN)``. Its gate is asserted above, in the
# PR 526 block (``test_read_token_admitted_on_run_read_routes``).
# --------------------------------------------------------------------------- #
_NEW_505_READ_ROUTES = [
    ("GET", "/workflows/runs"),
    ("GET", "/workflows/runs/{run_id}/result"),
]


@pytest.mark.parametrize("method,path", _NEW_505_READ_ROUTES)
def test_new_505_read_routes_declare_a_scope_dependency(method, path):
    """Structural guard: the dependency is present on the route object.

    This is the half that CANNOT be faked by ambient config. ``is_auth_enabled()`` is
    default-off (true only when ``AUTH0_DOMAIN`` or ``CAO_AUTH_JWKS_URI`` is set) and
    ``require_any_scope`` hands back the full scope set when auth is off — so a plain
    "the route still returns 200" test passes whether or not the dependency exists at
    all. Asserting on the route table instead makes the guard real.
    """
    matches = [
        r
        for r in app.routes
        if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set())
    ]
    assert matches, f"{method} {path} is not registered"
    assert _has_scope_dependency(matches[0]), f"{method} {path} has no require_any_scope dependency"


def test_scopeless_token_forbidden_on_run_list(client, auth_on):
    """Enforcement: a token holding none of read/write/admin is 403'd on the run list.

    403 (not 401) is the correct expectation here: ``require_any_scope`` itself raises
    403 for a token that authenticated but lacks the scope, while 401 comes from
    ``get_current_scopes`` upstream on a missing/invalid token.
    """
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
    resp = client.get("/workflows/runs")
    assert resp.status_code == 403


def test_scopeless_token_forbidden_on_run_result(client, auth_on):
    """Enforcement: same for the result route, which exposes per-step output blobs."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
    resp = client.get("/workflows/runs/whatever/result")
    assert resp.status_code == 403


def test_read_token_admitted_on_run_list(client, auth_on):
    """A cao:read token PASSES the gate (not 403) — the point of including SCOPE_READ.

    Guards the over-restriction failure mode: gating these reads on write/admin only
    would lock out exactly the read-only callers they exist for.
    """
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.get("/workflows/runs")
    assert resp.status_code != 403


def test_write_token_still_admitted_on_run_list(client, auth_on):
    """A cao:write token keeps working — existing write-scoped callers must not break."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_WRITE])
    resp = client.get("/workflows/runs")
    assert resp.status_code != 403


# --------------------------------------------------------------------------
# Profile write routes (#510 PR B)
#
# All three mutating routes accept cao:write or cao:admin, so a single credential
# covers the create/edit/delete cycle #510 specifies. Scopes are a flat set, not
# a hierarchy — ``require_any_scope`` tests membership — so gating DELETE on
# admin alone would 403 a caller holding exactly cao:write and leave a client
# able to create and edit a profile unable to remove it. Most other DELETE
# routes here are admin-only, but they remove running or generated state
# (sessions, terminals, workflows, flows, bulk memory); a profile is an authored
# document, like DELETE /memory/relationships/{id}, which is also write-or-admin.
# --------------------------------------------------------------------------


def test_read_token_forbidden_on_profile_create(client, auth_on):
    """A cao:read token cannot create a profile."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.post("/agents/profiles", json={"name": "x", "content": "---\nname: x\n---\n"})
    assert resp.status_code == 403


def test_write_token_admitted_on_profile_create(client, auth_on):
    """A cao:write token passes the create dependency."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_WRITE])
    resp = client.post("/agents/profiles", json={"name": "x", "content": "---\nname: x\n---\n"})
    assert resp.status_code != 403


def test_write_token_admitted_on_profile_replace(client, auth_on):
    """A cao:write token passes the replace dependency."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_WRITE])
    resp = client.put("/agents/profiles/x", json={"content": "---\nname: x\n---\n"})
    assert resp.status_code != 403


def test_read_token_forbidden_on_profile_delete(client, auth_on):
    """A cao:read token cannot delete a profile — deletion is still a mutation."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.delete("/agents/profiles/x")
    assert resp.status_code == 403


def test_write_token_admitted_on_profile_delete(client, auth_on):
    """A cao:write token passes the deletion dependency.

    Changed during the PR #585 review. DELETE was briefly admin-only, which
    contradicted the contract published in #510 and would have broken the
    documented create/edit/delete workflow for a write-scoped client, since
    holding cao:write grants no admin privilege under a flat scope set.
    """
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_WRITE])
    resp = client.delete("/agents/profiles/x")
    assert resp.status_code != 403


def test_admin_token_admitted_on_profile_delete(client, auth_on):
    """A cao:admin token passes the deletion dependency."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_ADMIN])
    resp = client.delete("/agents/profiles/x")
    assert resp.status_code != 403


# --------------------------------------------------------------------------
# PR #585 review — the NEW #510 read route carries a read-scope gate.
#
# Scoped deliberately to the one route this PR ADDED, following the precedent
# the #505 block above set. The pre-existing profile reads (``GET
# /agents/profiles``, ``/search``, ``/templates``, ``/schema``, ``/{name}``) are
# equally ungated and are left alone: tightening shipped routes could break an
# existing unauthenticated reader.
#
# The gate matters more on this route than on those siblings because
# ``_read_agent_profile_source`` returns the stored bytes verbatim, across the
# local, provider, extra and built-in stores, including documents that fail to
# parse. The parsed route can only return what the model accepts.
# --------------------------------------------------------------------------
_NEW_510_READ_ROUTES = [
    ("GET", "/agents/profiles/{name}/source"),
]


@pytest.mark.parametrize("method,path", _NEW_510_READ_ROUTES)
def test_new_510_read_routes_declare_a_scope_dependency(method, path):
    """Structural guard: the dependency is present on the route object.

    Asserting on the route table rather than on a status code, for the reason the
    #505 version of this test spells out: ``is_auth_enabled()`` is default-off and
    ``require_any_scope`` hands back the full scope set when auth is off, so a
    "the route still returns 200" test passes whether or not the dependency
    exists at all. That is exactly how this route shipped ungated.
    """
    matches = [
        r
        for r in app.routes
        if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set())
    ]
    assert matches, f"{method} {path} is not registered"
    assert _has_scope_dependency(matches[0]), f"{method} {path} has no require_any_scope dependency"


def test_scopeless_token_forbidden_on_profile_source(client, auth_on):
    """Enforcement: a token holding none of read/write/admin is 403'd on the source read."""
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([])
    resp = client.get("/agents/profiles/x/source")
    assert resp.status_code == 403


def test_read_token_admitted_on_profile_source(client, auth_on):
    """A cao:read token PASSES the gate — this is an authoring read, not a mutation.

    Guards the over-restriction failure mode: gating on write/admin only would
    lock out a read-only client that legitimately needs the unresolved document,
    which is the safe one to read since it never returns substituted secrets.
    """
    app.dependency_overrides[auth.get_current_scopes] = _override_scopes([auth.SCOPE_READ])
    resp = client.get("/agents/profiles/x/source")
    assert resp.status_code != 403


def test_owned_reads_require_verified_identity_and_are_never_open():
    from types import SimpleNamespace

    routes = {route.path: route for route in _api_get_routes()}
    for path in ("/work-items/{work_item_id}", "/jobs/{job_id}/events"):
        route = routes[path]
        assert path not in _OPEN_READS
        assert _has_verified_work_read_authority(route)
        without_identity = SimpleNamespace(
            endpoint=route.endpoint, dependant=SimpleNamespace(dependencies=[])
        )
        assert not _has_verified_work_read_authority(without_identity)
    for path, route in routes.items():
        if path.startswith("/v1/knowledge/"):
            assert path not in _OPEN_READS
            assert _has_knowledge_authority(route)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/local-coordination/identity", None),
        ("GET", "/local-coordination/instances", None),
        (
            "GET",
            "/local-coordination/projects/verify?project_path=/tmp&expected_project_id=" + "a" * 64,
            None,
        ),
        (
            "POST",
            "/local-coordination/pairings",
            {
                "challenge_id": "a" * 36,
                "code": "c" * 32,
                "expires_at": 1,
                "initiator_instance_id": "i" * 36,
                "initiator_process_generation": "g" * 36,
                "initiator_display_name": "initiator",
                "initiator_public_key": "k" * 44,
                "initiator_loopback_port": 9889,
                "candidate_instance_id": "d" * 36,
                "candidate_process_generation": "e" * 36,
                "candidate_display_name": "candidate",
                "candidate_public_key": "l" * 44,
                "project_id": "a" * 64,
                "canonical_root": "/tmp",
                "requested_scopes": ["task:submit"],
            },
        ),
        (
            "POST",
            "/local-coordination/pairings/challenge/accept",
            {
                "code": "c" * 32,
                "candidate_instance_id": "d" * 36,
                "candidate_process_generation": "e" * 36,
                "candidate_display_name": "candidate",
                "candidate_public_key": "l" * 44,
                "project_id": "a" * 64,
            },
        ),
    ],
)
def test_local_discovery_and_pairing_reject_non_loopback(method, path, body):
    """Bootstrap discovery is local authority, never an open network disclosure."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import local_coordination_routes

    application = FastAPI()
    application.include_router(local_coordination_routes.router)
    with TestClient(
        application, base_url="http://127.0.0.1:9889", client=("192.0.2.1", 50000)
    ) as local_client:
        response = local_client.request(method, path, json=body)
    assert response.status_code == 403
    assert response.json()["detail"]["kind"] == "not_local"


@pytest.mark.parametrize(
    "method,path,body",
    [
        (
            "POST",
            "/tasks",
            {
                "task_id": "t" * 36,
                "source_instance_id": "i" * 36,
                "source_process_generation": "g" * 36,
                "requester_terminal_id": "abcdef01",
                "project_id": "a" * 64,
                "operation_key": "operation",
                "request_hash": "b" * 64,
                "agent_profile": "profile",
                "message": "task",
                "use_worktree": True,
            },
        ),
        ("GET", "/tasks/task?project_id=" + "a" * 64 + "&requester_terminal_id=abcdef01", None),
        (
            "POST",
            "/tasks/task/cancel?project_id=" + "a" * 64 + "&requester_terminal_id=abcdef01",
            None,
        ),
        ("DELETE", "/peers/peer?project_id=" + "a" * 64, None),
        ("GET", "/sessions?project_id=" + "a" * 64, None),
    ],
)
def test_local_peer_operations_reject_unsigned_loopback_request(monkeypatch, method, path, body):
    """Being on loopback does not replace signed peer grants for resource operations."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import local_coordination_routes

    monkeypatch.setattr(local_coordination_routes, "get_instance", lambda _: None)
    application = FastAPI()
    application.include_router(local_coordination_routes.router)
    with TestClient(
        application, base_url="http://127.0.0.1:9889", client=("127.0.0.1", 50000)
    ) as local_client:
        response = local_client.request(method, "/local-coordination" + path, json=body)
    assert response.status_code == 403
    expected = "scope_denied" if method == "DELETE" else "not_local"
    assert response.json()["detail"]["kind"] == expected


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/events/ticket"),
        ("POST", "/agui/v1/stream/ticket"),
        ("POST", "/terminals/abcdef01/ws/ticket"),
        ("GET", "/events"),
        ("GET", "/agui/v1/stream"),
    ],
)
def test_manual_transport_guards_reject_unscoped_identity(client, monkeypatch, method, path):
    """The live manual scope boundary runs before ticket issuance or stream disclosure."""
    from cli_agent_orchestrator.api import main

    monkeypatch.setattr(main, "_require_mcp_apps_enabled", lambda: None)
    monkeypatch.setattr(main, "_require_agui_enabled", lambda: None)
    monkeypatch.setattr(main, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(
        main,
        "principal_from_token",
        lambda _: auth._verified_principal("urn:test", "unscoped", [], "jwt"),
    )

    async def no_scopes(*args):
        return []

    monkeypatch.setattr(main, "get_current_scopes", no_scopes)
    response = client.request(method, path, headers={"Authorization": "Bearer unscoped"})
    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient transport scope"


@pytest.mark.parametrize(
    "path,recognize,before,after",
    [
        (
            "/events/ticket",
            _has_manual_transport_authority,
            "_issue_transport_ticket",
            "_unguarded_marker",
        ),
        (
            "/terminals/{terminal_id}/ws/ticket",
            _has_manual_transport_authority,
            "SCOPE_WRITE, SCOPE_ADMIN",
            "SCOPE_READ, SCOPE_ADMIN",
        ),
        (
            "/local-coordination/identity",
            _has_local_peer_authority,
            "_require_loopback",
            "_unguarded_marker",
        ),
        ("/local-coordination/tasks", _has_local_peer_authority, "task:submit", "task:status"),
    ],
)
def test_manual_authority_audit_rejects_missing_or_wrong_guard(
    monkeypatch, path, recognize, before, after
):
    """Recognition follows the actual guard and exact scope, not a path exemption."""
    route = next(route for route in app.routes if getattr(route, "path", None) == path)
    assert recognize(route)
    original = inspect.getsource
    source = original(route.endpoint)
    assert before in source
    monkeypatch.setattr(
        inspect,
        "getsource",
        lambda endpoint: (
            source.replace(before, after) if endpoint is route.endpoint else original(endpoint)
        ),
    )
    assert not recognize(route)


def test_manual_authority_routes_are_never_open_read_exemptions():
    for route in _api_get_routes():
        if _has_manual_transport_authority(route) or _has_local_peer_authority(route):
            assert route.path not in _OPEN_READS


def test_registered_local_peer_with_pinned_grant_rejects_invalid_signature(monkeypatch, tmp_path):
    """Matching registry identity and action grants cannot bypass signature verification."""
    import base64
    import hashlib
    import json
    import os
    import time
    from uuid import uuid4

    import psutil
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import local_coordination_routes
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.services import local_peer_registry
    from cli_agent_orchestrator.services.local_peer_identity import (
        LocalProcessIdentity,
        _signature_material,
        public_key_text,
    )

    directory = tmp_path / "local-peers"
    monkeypatch.setattr(local_peer_registry, "LOCAL_PEER_DIR", directory)
    monkeypatch.setattr(
        local_peer_registry, "LOCAL_PEER_REGISTRY_FILE", directory / "registry.sqlite3"
    )
    identity = LocalProcessIdentity(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        pid=os.getpid(),
        process_started_at=float(psutil.Process().create_time()),
        display_name="scope audit peer",
        loopback_host="127.0.0.1",
        loopback_port=19889,
        public_key=public_key_text(Ed25519PrivateKey.generate()),
    )
    local_peer_registry.register_instance(identity)
    registered = local_peer_registry.get_instance(identity.instance_id)
    assert registered is not None
    assert registered.process_generation == identity.process_generation
    project_id = "a" * 64
    with database.SessionLocal() as session:
        session.add(
            database.LocalPeerGrantModel(
                grant_id=str(uuid4()),
                peer_instance_id=identity.instance_id,
                project_id=project_id,
                peer_display_name=identity.display_name,
                peer_public_key=identity.public_key,
                scopes_json=json.dumps(["session:read"]),
            )
        )
        session.commit()
    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    body_sha256 = hashlib.sha256(b"").hexdigest()
    # A normal pinned key and a different signer exercise signature rejection,
    # independently of the separate weak-public-key admission regressions.
    invalid_signature = Ed25519PrivateKey.generate().sign(
        _signature_material(
            "GET",
            "/local-coordination/sessions",
            "project_id=" + project_id,
            project_id,
            identity.instance_id,
            identity.process_generation,
            timestamp,
            nonce,
            body_sha256,
        )
    )
    application = FastAPI()
    application.include_router(local_coordination_routes.router)
    with TestClient(
        application, base_url="http://127.0.0.1:9889", client=("127.0.0.1", 50000)
    ) as local_client:
        response = local_client.get(
            "/local-coordination/sessions",
            params={"project_id": project_id},
            headers={
                "X-CAO-Peer-Instance": identity.instance_id,
                "X-CAO-Peer-Generation": identity.process_generation,
                "X-CAO-Peer-Signature": base64.urlsafe_b64encode(invalid_signature)
                .decode()
                .rstrip("="),
                "X-CAO-Peer-Timestamp": timestamp,
                "X-CAO-Peer-Nonce": nonce,
                "X-CAO-Peer-Body-SHA256": body_sha256,
            },
        )
    assert response.status_code == 403
    assert response.json()["detail"]["kind"] == "scope_denied"
