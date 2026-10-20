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


def _dependency_calls(route):
    """Return the exact callables in a route's FastAPI dependency graph."""
    calls = set()
    stack = list(getattr(getattr(route, "dependant", None), "dependencies", []))
    while stack:
        dependency = stack.pop()
        call = getattr(dependency, "call", None)
        if call is not None:
            calls.add(call)
        stack.extend(getattr(dependency, "dependencies", []))
    return calls


def _ascii_token_guard_is_live(function) -> bool:
    """Pin the shared ASCII precheck used before constant-time token comparison."""
    import hmac

    from cli_agent_orchestrator.security import local_token

    helper = local_token.ascii_token_matches
    if getattr(function, "__globals__", {}).get("ascii_token_matches") is not helper:
        return False
    helper_globals = getattr(helper, "__globals__", {})
    helper_hmac = helper_globals.get("hmac")
    if (
        helper_hmac is not hmac
        or getattr(helper_hmac, "compare_digest", None) is not hmac.compare_digest
    ):
        return False
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(helper))).body
    except (OSError, TypeError, IndexError, SyntaxError):
        return False
    if len(tree) != 1 or not isinstance(tree[0], ast.FunctionDef):
        return False
    definition = tree[0]
    if definition.name != "ascii_token_matches" or definition.decorator_list:
        return False
    arguments = definition.args
    if (
        [argument.arg for argument in arguments.args] != ["presented", "expected"]
        or arguments.posonlyargs
        or arguments.vararg is not None
        or arguments.kwonlyargs
        or arguments.kwarg is not None
        or arguments.defaults
        or arguments.kw_defaults
    ):
        return False
    statements = list(definition.body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements.pop(0)
    if len(statements) != 1 or not isinstance(statements[0], ast.Return):
        return False
    expression = statements[0].value
    if not isinstance(expression, ast.BoolOp) or not isinstance(expression.op, ast.And):
        return False
    checks = expression.values
    if len(checks) != 3:
        return False

    def is_isascii_call(node, argument_name):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "isascii"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == argument_name
            and not node.args
            and not node.keywords
        )

    comparison = checks[2]
    return (
        is_isascii_call(checks[0], "presented")
        and is_isascii_call(checks[1], "expected")
        and isinstance(comparison, ast.Call)
        and isinstance(comparison.func, ast.Attribute)
        and comparison.func.attr == "compare_digest"
        and isinstance(comparison.func.value, ast.Name)
        and comparison.func.value.id == "hmac"
        and len(comparison.args) == 2
        and all(isinstance(argument, ast.Name) for argument in comparison.args)
        and [argument.id for argument in comparison.args] == ["presented", "expected"]
        and not comparison.keywords
    )


def _interaction_route_contract(route):
    """Recognize only the explicit interaction handlers and their live guards.

    Interaction routes use a sealed human Principal plus an in-handler scope
    check, or a source capability validated by the database service. The route
    table alone is not authority: this map also checks the exact handler,
    executed guard, service method, and service-side source validator.
    """
    from cli_agent_orchestrator.api import interactions
    from cli_agent_orchestrator.services.interactions import service as interaction_service

    human_routes = {
        ("GET", "/interactions"): (interactions.list_interactions, "SCOPE_READ", "list_metadata"),
        (
            "PUT",
            "/interactions/sources/{terminal_id}/content-owner",
        ): (
            interactions.designate_interaction_content_owner,
            "SCOPE_ADMIN",
            "designate_content_owner",
        ),
        (
            "GET",
            "/interactions/{question_id}",
        ): (interactions.get_interaction, "SCOPE_READ", "get_detail"),
        (
            "POST",
            "/interactions/{question_id}/delegation",
        ): (interactions.delegate_interaction_question, "SCOPE_WRITE", "delegate_question"),
        (
            "DELETE",
            "/interactions/{question_id}/delegation",
        ): (interactions.revoke_interaction_delegation, "SCOPE_WRITE", "revoke_delegation"),
        (
            "POST",
            "/interactions/{question_id}/answers",
        ): (interactions.accept_interaction_answer, "SCOPE_WRITE", "accept_answer"),
        (
            "POST",
            "/interactions/{question_id}/cancel",
        ): (interactions.cancel_interaction, "SCOPE_WRITE", "cancel"),
        (
            "POST",
            "/interactions/{question_id}/reconcile",
        ): (interactions.reconcile_interaction, "SCOPE_WRITE", "reconcile"),
        (
            "GET",
            "/interactions/{question_id}/history",
        ): (interactions.interaction_history, "SCOPE_READ", "history"),
        ("GET", "/interaction-wait-graph"): (
            interactions.interaction_wait_graph,
            "SCOPE_READ",
            "wait_graph",
        ),
    }
    agent_routes = {
        ("POST", "/agent/interactions/groups"): (
            interactions.create_interaction_group,
            "create_group",
        ),
        ("GET", "/agent/interactions"): (interactions.agent_list_interactions, "agent_list"),
        (
            "GET",
            "/agent/interactions/{question_id}",
        ): (interactions.agent_get_interaction, "agent_get"),
        (
            "POST",
            "/agent/interactions/{question_id}/answers",
        ): (interactions.agent_answer_interaction, "agent_answer"),
        (
            "POST",
            "/agent/interactions/{question_id}/escalate",
        ): (interactions.escalate_interaction, "escalate"),
        (
            "POST",
            "/agent/interactions/groups/{group_id}/wait",
        ): (interactions.wait_for_interaction_answers, "wait_for_answers"),
        (
            "POST",
            "/agent/interactions/groups/{group_id}/ack",
        ): (interactions.acknowledge_interaction_answers, "acknowledge_answers"),
    }

    methods = getattr(route, "methods", None) or set()
    if len(methods) != 1:
        return False
    method = next(iter(methods))
    key = (method, getattr(route, "path", None))
    function = getattr(route, "endpoint", None)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
    except (OSError, TypeError, IndexError):
        return False
    if not isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    statements = list(tree.body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements.pop(0)

    if key in human_routes:
        expected_function, expected_scope, expected_service_method = human_routes[key]
        if function is not expected_function:
            return False
        dependencies = _dependency_calls(route)
        if (
            interactions._human_principal not in dependencies
            or auth.get_current_principal not in dependencies
            or not _interaction_human_guard_is_live()
        ):
            return False
        if not statements or not isinstance(statements[0], ast.Expr):
            return False
        scope_call = statements[0].value
        if not (
            isinstance(scope_call, ast.Call)
            and isinstance(scope_call.func, ast.Name)
            and scope_call.func.id == "_scope"
            and len(scope_call.args) == 2
            and isinstance(scope_call.args[0], ast.Name)
            and scope_call.args[0].id == "principal"
            and isinstance(scope_call.args[1], ast.Name)
            and scope_call.args[1].id == expected_scope
        ):
            return False
        return _interaction_service_call(tree, expected_service_method, "principal")

    if key == ("GET", "/interaction-capabilities"):
        if function is not interactions.interaction_capabilities:
            return False
        # This read endpoint has two explicit branches: a source capability is
        # validated by the service, while the human path requires a sealed
        # Principal and applies the read/write/admin scope rule.
        source = "".join(inspect.getsource(function).split())
        return (
            all(
                marker in source
                for marker in (
                    "ifcapability:",
                    "service.authenticate_agent,capability",
                    'get_current_principal(request,request.headers.get("Authorization"))',
                    "is_verified_principal(principal)",
                    "_scope(principal,SCOPE_READ)",
                    "get_local_bearer()",
                )
            )
            and "service_token" in source
            and "ascii_token_matches(presented,service_token)" in source
            and _ascii_token_guard_is_live(function)
            and _interaction_service_call(tree, "capabilities", "actor")
        )

    if key in agent_routes:
        expected_function, expected_service_method = agent_routes[key]
        if function is not expected_function:
            return False
        if not _interaction_service_call(tree, expected_service_method, "capability"):
            return False
        # The endpoint must obtain the credential from the dedicated request
        # header itself (directly or in the local `capability` assignment).
        capability_is_bound = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_capability"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "request"
            for statement in statements
            for node in ast.walk(statement)
        )
        if not capability_is_bound:
            return False
        service_method = getattr(interaction_service.InteractionService, expected_service_method)
        service_tree = ast.parse(textwrap.dedent(inspect.getsource(service_method))).body[0]
        validator = (
            "authenticate_agent"
            if expected_service_method == "agent_get"
            else "_current_source_by_capability"
        )
        return (
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == validator
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
                for node in ast.walk(service_tree)
            )
            and _interaction_capability_validators_are_live()
        )

    return False


def _interaction_service_call(tree, expected_method: str, actor_name: str) -> bool:
    """Check the endpoint passes the expected trusted actor to `_invoke`."""
    for statement in tree.body:
        for node in ast.walk(statement):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_invoke"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == expected_method
            ):
                continue
            actor = (
                node.args[2]
                if len(node.args) >= 3
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "actor"),
                    None,
                )
            )
            if isinstance(actor, ast.Name) and actor.id == actor_name:
                return True
            if (
                actor_name == "capability"
                and isinstance(actor, ast.Call)
                and isinstance(actor.func, ast.Name)
                and actor.func.id == "_capability"
                and len(actor.args) == 1
                and isinstance(actor.args[0], ast.Name)
                and actor.args[0].id == "request"
            ):
                return True
    if actor_name == "capability":
        # `create_interaction_group` assigns the header value once, then passes
        # that local into the service invocation.
        assigned = any(
            isinstance(statement, (ast.Assign, ast.AnnAssign))
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "_capability"
            for statement in tree.body
        )
        if assigned:
            return any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_invoke"
                and len(node.args) >= 3
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == expected_method
                and isinstance(node.args[2], ast.Name)
                and node.args[2].id == "capability"
                for statement in tree.body
                for node in ast.walk(statement)
            )
    return False


def _interaction_capability_validators_are_live() -> bool:
    """Pin capability hashing, revocation, source binding, and install checks."""
    from cli_agent_orchestrator.api import interactions
    from cli_agent_orchestrator.services.interactions import service as interaction_service

    header_extractor = "".join(inspect.getsource(interactions._capability).split())
    current_source = "".join(
        inspect.getsource(
            interaction_service.InteractionService._current_source_by_capability
        ).split()
    )
    validate_source = "".join(
        inspect.getsource(interaction_service.InteractionService._validate_source_row).split()
    )
    return (
        all(
            marker in header_extractor
            for marker in (
                "request.headers.get(_AGENT_CAPABILITY_HEADER)",
                "ifnotcapability:raiseHTTPException(status_code=401",
                "iflen(capability)>512:raiseHTTPException(status_code=401",
            )
        )
        and all(
            marker in current_source
            for marker in (
                "capability_hash(token)",
                "WHEREcapability_hash=%s",
                "require_capability=True",
            )
        )
        and all(
            marker in validate_source
            for marker in (
                'source["revoked_at"]',
                'source["capability_available"]',
                'source["incarnation_id"]',
                'source["allowed_tool_fingerprint"]',
                "self._validate_work(connection,source)",
            )
        )
    )


def _interaction_human_guard_is_live() -> bool:
    """Pin the sealed-Principal dependency and prohibit either bearer as identity."""
    from cli_agent_orchestrator.api import interactions

    dependency = inspect.signature(interactions._human_principal).parameters["principal"].default
    source = "".join(inspect.getsource(interactions._human_principal).split())
    return (
        getattr(dependency, "dependency", None) is auth.get_current_principal
        and "is_verified_principal(principal)" in source
        and "request.headers.get(_AGENT_CAPABILITY_HEADER)" in source
        and "get_local_bearer()" in source
        and "ascii_token_matches(presented,service_token)" in source
        and _ascii_token_guard_is_live(interactions._human_principal)
    )


def _has_interaction_authority(route) -> bool:
    return bool(_interaction_route_contract(route))


def _has_verified_work_read_authority(route) -> bool:
    """These exact handlers enforce read scope and owner inside WorkQueries."""
    if getattr(route, "endpoint", None) not in {
        work_routes.get_work,
        work_routes.get_work_events,
        work_routes.stream_work_messages,
    }:
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
            or _has_interaction_authority(route)
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
            or _has_interaction_authority(route)
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


def test_interaction_authority_audit_covers_exact_live_routes():
    """Every interaction read/mutation is recognized by its executed authority path."""
    interaction_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "").startswith("/interactions")
        or getattr(route, "path", "").startswith("/agent/interactions")
        or getattr(route, "path", None) in {"/interaction-capabilities", "/interaction-wait-graph"}
    ]
    assert len(interaction_routes) == 18
    assert all(_has_interaction_authority(route) for route in interaction_routes)
    for route in interaction_routes:
        if "GET" in (getattr(route, "methods", None) or set()):
            assert route.path not in _OPEN_READS


def test_interaction_authority_audit_rejects_wrong_live_guard(monkeypatch):
    """The scanner follows a changed guard instead of blessing a route name."""
    from cli_agent_orchestrator.api import interactions

    human_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/interactions/{question_id}/cancel"
    )
    agent_route = next(
        route for route in app.routes if getattr(route, "path", None) == "/agent/interactions"
    )
    assert _has_interaction_authority(human_route)
    assert _has_interaction_authority(agent_route)
    original_getsource = inspect.getsource
    human_source = original_getsource(interactions.cancel_interaction)
    agent_source = original_getsource(interactions.agent_list_interactions)

    monkeypatch.setattr(
        inspect,
        "getsource",
        lambda endpoint: (
            human_source.replace("_scope(principal, SCOPE_WRITE)", "_scope(principal, SCOPE_READ)")
            if endpoint is interactions.cancel_interaction
            else (
                agent_source.replace("_capability(request)", "request.headers.get('Authorization')")
                if endpoint is interactions.agent_list_interactions
                else original_getsource(endpoint)
            )
        ),
    )
    assert not _has_interaction_authority(human_route)
    assert not _has_interaction_authority(agent_route)


def test_interaction_authority_audit_rejects_unsafe_ascii_token_helper(monkeypatch):
    """The source audit must reject a helper that compares Unicode directly."""
    from cli_agent_orchestrator.api import interactions
    from cli_agent_orchestrator.security import local_token

    human_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/interactions/{question_id}/cancel"
    )
    capabilities_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/interaction-capabilities"
        and "GET" in (getattr(route, "methods", None) or set())
    )
    assert _has_interaction_authority(human_route)
    assert _has_interaction_authority(capabilities_route)

    original_getsource = inspect.getsource
    weakened_helper = """\
def ascii_token_matches(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented, expected)
"""
    monkeypatch.setattr(
        inspect,
        "getsource",
        lambda target: (
            weakened_helper
            if target is local_token.ascii_token_matches
            else original_getsource(target)
        ),
    )
    assert not _has_interaction_authority(human_route)
    assert not _has_interaction_authority(capabilities_route)


def test_interaction_authority_audit_rejects_rebound_ascii_token_helper(monkeypatch):
    """Live routes must stay bound to the canonical token guard."""
    from cli_agent_orchestrator.api import interactions

    human_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/interactions/{question_id}/cancel"
    )
    capabilities_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/interaction-capabilities"
        and "GET" in (getattr(route, "methods", None) or set())
    )
    assert _has_interaction_authority(human_route)
    assert _has_interaction_authority(capabilities_route)

    monkeypatch.setattr(interactions, "ascii_token_matches", lambda presented, expected: False)
    assert not _has_interaction_authority(human_route)
    assert not _has_interaction_authority(capabilities_route)


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


def _enable_signed_interaction_jwts(monkeypatch, jwt_factory):
    """Enable the real Principal/JWT path with this test's local signing key."""
    from cryptography.hazmat.primitives import serialization

    monkeypatch.setenv("AUTH0_DOMAIN", jwt_factory.domain)
    monkeypatch.setenv("AUTH0_AUDIENCE", jwt_factory.audience)
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    monkeypatch.delenv("CAO_AUTH_LOCAL_TOKEN_FILE", raising=False)
    public_key = serialization.load_pem_private_key(
        jwt_factory.private_pem, password=None
    ).public_key()

    class _SigningKey:
        key = public_key

    class _JwksClient:
        def get_signing_key_from_jwt(self, _token):
            return _SigningKey()

    auth.get_jwks_cache().clear()
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda _uri: _JwksClient())


def test_interaction_human_routes_enforce_verified_jwt_scopes_and_reject_machine_bearer(
    client, monkeypatch, jwt_factory
):
    """Real JWT identities get only their explicit interaction read/write scopes."""
    _enable_signed_interaction_jwts(monkeypatch, jwt_factory)
    monkeypatch.delenv("CAO_AUTH_LOCAL_TOKEN", raising=False)

    assert client.get("/interactions").status_code == 401

    no_interaction_scope = jwt_factory.mint(
        scopes="cao:metrics", extra_claims={"sub": "scope-coverage-human"}
    )
    response = client.get(
        "/interactions", headers={"Authorization": f"Bearer {no_interaction_scope}"}
    )
    assert response.status_code == 403

    read_token = jwt_factory.mint(
        scopes=auth.SCOPE_READ, extra_claims={"sub": "scope-coverage-human"}
    )
    read_headers = {"Authorization": f"Bearer {read_token}"}
    read_response = client.get("/interactions", headers=read_headers)
    assert read_response.status_code not in (401, 403)

    read_cannot_write = client.post(
        "/interactions/d97001ba-d4ed-4c78-b3b9-22fa8c77631d/cancel",
        headers={**read_headers, "If-Match": '"1"'},
        json={"reason": "scope enforcement check"},
    )
    assert read_cannot_write.status_code == 403

    read_cannot_designate_owner = client.put(
        "/interactions/sources/ac010001/content-owner",
        headers=read_headers,
        json={
            "expected_incarnation_id": "scope-check-incarnation",
            "reason": "scope enforcement check",
        },
    )
    assert read_cannot_designate_owner.status_code == 403

    write_token = jwt_factory.mint(
        scopes=auth.SCOPE_WRITE, extra_claims={"sub": "scope-coverage-human"}
    )
    write_headers = {"Authorization": f"Bearer {write_token}"}
    write_can_read = client.get("/interactions", headers=write_headers)
    assert write_can_read.status_code not in (401, 403)
    write_can_cancel = client.post(
        "/interactions/d97001ba-d4ed-4c78-b3b9-22fa8c77631d/cancel",
        headers={**write_headers, "If-Match": '"1"'},
        json={"reason": "scope enforcement check"},
    )
    assert write_can_cancel.status_code != 403

    # The configured MCP bearer is a transport credential, even when it is a
    # syntactically valid JWT with read scope. It is not a human Principal and
    # cannot replace the source-bound interaction capability on agent routes.
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN", read_token)
    machine_on_human_route = client.get("/interactions", headers=read_headers)
    assert machine_on_human_route.status_code == 403
    machine_on_agent_route = client.get("/agent/interactions", headers=read_headers)
    assert machine_on_agent_route.status_code == 401


@pytest.mark.postgres
def test_interaction_agent_capability_is_source_bound_through_real_service(
    client, monkeypatch, postgres_interaction_store
):
    """A live source capability cannot read another source's private question."""
    from uuid import uuid4

    from cli_agent_orchestrator.api.main import app as main_app
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    for name in (
        "AUTH0_DOMAIN",
        "AUTH0_AUDIENCE",
        "CAO_AUTH_AUDIENCE",
        "CAO_AUTH_JWKS_URI",
        "CAO_AUTH_LOCAL_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    # Bind the same fully migrated PostgreSQL service used to seed these rows.
    assert client.get("/health").status_code == 200
    service = postgres_interaction_store.service
    monkeypatch.setattr(main_app.state, "interaction_service", service, raising=False)

    owner = auth._verified_principal(
        "https://scope-coverage.test", "interaction-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    source_ids = []
    for suffix in ("first", "second"):
        terminal_id = uuid4().hex[:8]
        session_name = f"scope-interaction-{suffix}-{uuid4().hex[:8]}"
        incarnation_id = f"scope-interaction-{suffix}-{uuid4().hex}"
        database.create_terminal(
            terminal_id,
            session_name,
            "worker",
            "codex",
            allowed_tools=["@cao-mcp-server"],
            session_incarnation_id=incarnation_id,
            new_session_incarnation=True,
        )
        issued = service.register_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="codex",
            principal=owner,
        )
        source_ids.append(issued)

    question_group = service.create_group(
        source_ids[1].token,
        CreateGroupRequest(
            questions=[
                QuestionInput(
                    kind="text",
                    prompt="private source-bound question",
                    required=True,
                )
            ]
        ),
        "scope-coverage-source-bound-question-0001",
    )
    question_id = question_group.questions[0]["question_id"]

    missing_capability = client.get(f"/agent/interactions/{question_id}")
    assert missing_capability.status_code == 401
    unrelated_capability = client.get(
        f"/agent/interactions/{question_id}",
        headers={"X-CAO-Interaction-Capability": source_ids[0].token},
    )
    assert unrelated_capability.status_code == 403
    assert "private source-bound question" not in unrelated_capability.text


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
    for path in (
        "/work-items/{work_item_id}",
        "/jobs/{job_id}/events",
        "/jobs/{job_id}/messages/stream",
    ):
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


@pytest.mark.postgres
def test_registered_local_peer_with_pinned_grant_rejects_invalid_signature(
    monkeypatch, tmp_path, postgres_targets
):
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
    from cli_agent_orchestrator.clients.database import core_migrations
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.services import local_peer_registry
    from cli_agent_orchestrator.services.local_peer_identity import (
        LocalProcessIdentity,
        _signature_material,
        public_key_text,
    )

    migrate_component(postgres_targets.main, core_migrations())
    migrate_component(postgres_targets.peer, local_peer_registry.LOCAL_PEER_MIGRATIONS)

    directory = tmp_path / "local-peers"
    monkeypatch.setattr(local_peer_registry, "LOCAL_PEER_DIR", directory)
    monkeypatch.setattr(local_peer_registry, "_database_target", lambda: postgres_targets.peer)
    local_peer_registry._verify_target.cache_clear()
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
