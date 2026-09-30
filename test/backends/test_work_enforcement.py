"""Protected work must not mistake terminal transport for process isolation."""

import importlib

import pytest

from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend


class NoEffects:
    def __getattr__(self, name):
        raise AssertionError(f"backend effect attempted: {name}")


def contract_module():
    module = importlib.import_module("cli_agent_orchestrator.backends.base")
    assert hasattr(module, "ProcessRestrictionContract"), "explicit restriction contract missing"
    return module


@pytest.mark.parametrize("backend_type", [TmuxBackend, HerdrBackend])
@pytest.mark.parametrize("effect", ["preflight", "session", "window", "send"])
@pytest.mark.parametrize(
    "paths,commands,network", [((), (), ()), (("/checkout",), ("git",), ("example.org",))]
)
def test_existing_backends_reject_before_any_effect(backend_type, effect, paths, commands, network):
    module = contract_module()
    backend = object.__new__(backend_type)
    backend._client = NoEffects()
    # Herdr transport also fails loudly if the gate accidentally dispatches.
    backend._run_herdr = NoEffects()
    contract = module.ProcessRestrictionContract(paths=paths, commands=commands, network=network)
    operations = {
        "preflight": lambda: backend.preflight_work(contract),
        "session": lambda: backend.create_work_session(contract, "s", "w", "t"),
        "window": lambda: backend.create_work_window(contract, "s", "w", "t"),
        "send": lambda: backend.send_work_keys(contract, "s", "w", "hello"),
    }
    with pytest.raises(module.UnsupportedWorkEnforcement) as error:
        operations[effect]()
    assert error.value.error_kind == "unsupported_enforcement"
    assert error.value.required_level == "process_boundary"


@pytest.mark.parametrize("field", ["paths", "commands", "network"])
@pytest.mark.parametrize("value", [None, "*", [], True, (True,), ("",), ("  ",), ("safe", None)])
def test_contract_rejects_implicit_or_malformed_authority(field, value):
    module = contract_module()
    arguments = dict(paths=(), commands=(), network=())
    arguments[field] = value
    with pytest.raises(ValueError):
        module.ProcessRestrictionContract(**arguments)


def test_contract_is_explicit_immutable_and_has_no_cooperative_override():
    module = contract_module()
    with pytest.raises(TypeError):
        module.ProcessRestrictionContract()
    with pytest.raises(TypeError):
        module.ProcessRestrictionContract(
            paths=(), commands=(), network=(), enforcement_level="control_plane"
        )
    contract = module.ProcessRestrictionContract(paths=(), commands=(), network=())
    assert contract.enforcement_level == "process_boundary"
    with pytest.raises(AttributeError):
        contract.paths = ("/",)


def test_contract_rejects_relative_checkout_root():
    module = contract_module()
    with pytest.raises(ValueError, match="absolute path"):
        module.ProcessRestrictionContract(
            paths=(), commands=(), network=(), checkout_root="relative/checkout"
        )

    assert (
        module.ProcessRestrictionContract(
            paths=(), commands=(), network=(), checkout_root="/checkout"
        ).checkout_root
        == "/checkout"
    )


@pytest.mark.parametrize("forged", [None, {}, {"process_boundary": True}, True])
def test_capability_dictionary_or_missing_contract_cannot_authorize_work(forged):
    module = contract_module()
    backend = TmuxBackend(client=NoEffects())
    with pytest.raises(module.UnsupportedWorkEnforcement):
        backend.create_work_session(forged, "s", "w", "t")


def test_legacy_tmux_create_still_delegates_without_claiming_enforcement():
    calls = []

    class ExistingClient:
        def create_session(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "actual-window"

    backend = TmuxBackend(client=ExistingClient())
    assert backend.create_session("s", "w", "t") == "actual-window"
    assert calls == [(("s", "w", "t", None), {"extra_env": None})]


class GuardedTestBackend(TmuxBackend):
    """Test-only enforcing boundary; no actual tmux transport or sandbox claim."""

    def __init__(self):
        self.order = []

    def preflight_work(self, contract):
        self.order.append("preflight")

    def create_session(self, *args, **kwargs):
        self.order.append(("effect", args, kwargs))
        return "created"

    create_window = create_session
    send_keys = create_session


@pytest.mark.parametrize("method", ["create_work_session", "create_work_window", "send_work_keys"])
def test_protected_effect_runs_guard_after_preflight_and_never_forwards_it(method):
    module = contract_module()
    backend = GuardedTestBackend()
    contract = module.ProcessRestrictionContract(paths=(), commands=(), network=())

    def guard():
        backend.order.append("authorized")

    value = getattr(backend, method)(contract, "session", "window", "argument", before_effect=guard)
    assert value == "created"
    assert backend.order == [
        "preflight",
        "authorized",
        (
            "effect",
            ("session", "window", "argument"),
            {"work_safe": True} if method == "create_work_session" else {},
        ),
    ]


@pytest.mark.parametrize("method", ["create_work_session", "create_work_window", "send_work_keys"])
@pytest.mark.parametrize(
    "guard", [None, True, "authorized", lambda: True, lambda: False, lambda: {}]
)
def test_missing_or_client_style_guard_cannot_authorize_effect(method, guard):
    module = contract_module()
    assert hasattr(
        module, "WorkEffectAuthorizationRequired"
    ), "typed effect authorization error missing"
    backend = GuardedTestBackend()
    contract = module.ProcessRestrictionContract(paths=(), commands=(), network=())
    kwargs = {} if guard is None else {"before_effect": guard}
    with pytest.raises(module.WorkEffectAuthorizationRequired) as error:
        getattr(backend, method)(contract, "session", "window", "argument", **kwargs)
    assert error.value.error_kind == "work_effect_authorization_required"
    assert backend.order == ["preflight"]


def test_revocation_during_preflight_is_seen_by_guard_before_effect():
    module = contract_module()
    backend = GuardedTestBackend()
    authority = {"revoked": False}

    def preflight(contract):
        backend.order.append("preflight")
        authority["revoked"] = True

    def guard():
        backend.order.append("guard")
        if authority["revoked"]:
            raise PermissionError("grant revoked")

    backend.preflight_work = preflight
    with pytest.raises(PermissionError, match="revoked"):
        backend.send_work_keys(
            module.ProcessRestrictionContract(paths=(), commands=(), network=()),
            "s",
            "w",
            "input",
            before_effect=guard,
        )
    assert backend.order == ["preflight", "guard"]
