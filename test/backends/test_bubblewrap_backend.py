"""Bubblewrap Work preflight rejects stale sandboxes before transport effects."""

import importlib.util

import pytest


@pytest.fixture(autouse=True)
def test_backend_features_with_valid_broker(monkeypatch):
    """Isolate sandbox checks from this machine's OS account setup."""
    from cli_agent_orchestrator.backends import bubblewrap_backend

    monkeypatch.setattr(bubblewrap_backend, "_require_work_broker_identity", lambda _name: None)


@pytest.fixture
def attempt_credential_fd():
    """Provide the sealed descriptor required by a bound process launch."""
    import os

    from cli_agent_orchestrator.services.work_attempt_credential import (
        create_attempt_credential_descriptor,
    )

    descriptor = create_attempt_credential_descriptor(b"x" * 32)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


class NoTmuxEffects:
    def __init__(self):
        self.effects = []

    def __getattr__(self, name):
        if name.startswith(("create_", "send_", "kill_")):
            self.effects.append(name)
        raise AssertionError(f"unexpected tmux operation: {name}")


def test_untrusted_bubblewrap_candidate_is_not_run_for_empty_contract(tmp_path, monkeypatch):
    import shlex

    module_name = "cli_agent_orchestrator.backends.bubblewrap_backend"
    spec = importlib.util.find_spec(module_name)
    assert spec is not None, "the unregistered Bubblewrap Work backend is missing"
    module = __import__(module_name, fromlist=["BubblewrapWorkBackend"])

    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    probe_marker = tmp_path / "version-probe-ran"
    fake_bwrap = tmp_path / "bwrap"
    fake_bwrap.write_text(
        "#!/bin/sh\n"
        f"printf 'called' > {shlex.quote(str(probe_marker))}\n"
        "printf 'bubblewrap 0.11.1\\n'\n",
        encoding="utf-8",
    )
    fake_bwrap.chmod(0o700)
    client = NoTmuxEffects()
    backend = module.BubblewrapWorkBackend(client=client, bwrap_executable=str(fake_bwrap))

    contract_module = __import__(
        "cli_agent_orchestrator.backends.base", fromlist=["ProcessRestrictionContract"]
    )
    contract = contract_module.ProcessRestrictionContract(
        paths=(str(tmp_path),), commands=(), network=()
    )

    with pytest.raises(contract_module.UnsupportedWorkEnforcement) as rejected:
        backend.preflight_work(contract)

    assert not probe_marker.exists()
    assert "no command contract is currently supportable" in rejected.value.reason
    assert client.effects == []


def test_preflight_rejects_landlock_abi_below_nine_before_bubblewrap_probe(monkeypatch):
    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import (
        ProcessRestrictionContract,
        UnsupportedWorkEnforcement,
    )
    from cli_agent_orchestrator.models.work_contract import ExecutableIdentity

    identity = ExecutableIdentity(
        command_token="/worker",
        content_reference="sha256:" + "a" * 64,
        sha256_digest="a" * 64,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )
    contract = ProcessRestrictionContract(
        paths=(),
        commands=("/worker",),
        network=(),
        executable_identities=(identity,),
    )
    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        bubblewrap_backend,
        "_probe_bwrap_version",
        lambda _path: pytest.fail("Bubblewrap must not be probed below Landlock ABI 9"),
    )
    monkeypatch.setattr(
        bubblewrap_backend,
        "_query_abi_version",
        lambda: 8,
    )

    with pytest.raises(UnsupportedWorkEnforcement, match="Landlock ABI 9"):
        bubblewrap_backend.BubblewrapWorkBackend().preflight_work(contract)


def test_work_backend_view_routes_initial_and_later_windows_through_protected_methods(tmp_path):
    from cli_agent_orchestrator.backends.base import (
        ProcessRestrictionContract,
        WorkEffectAuthorizationRequired,
    )
    from cli_agent_orchestrator.backends.work_backend import WorkBackendView

    class ProtectedBackend:
        def __init__(self):
            self.calls = []

        def create_work_session(self, restriction, *args, before_effect):
            self.calls.append(("session", restriction, args))
            before_effect()
            return args[1]

        def create_work_window(self, restriction, *args, before_effect, **kwargs):
            self.calls.append(("window", restriction, args))
            before_effect()
            return "1"

        def send_work_keys(self, restriction, *args, before_effect, **kwargs):
            self.calls.append(("send", restriction, args))
            before_effect()

        def create_session(self, *args, **kwargs):
            raise AssertionError("legacy create_session bypassed the protected effect")

        def create_window(self, *args, **kwargs):
            raise AssertionError("legacy create_window bypassed the protected effect")

        def send_keys(self, *args, **kwargs):
            raise AssertionError("legacy send_keys bypassed the protected effect")

    root = tmp_path.resolve()
    backend = ProtectedBackend()
    contract = ProcessRestrictionContract(
        paths=(), commands=("bash",), network=(), read_paths=(str(root),), checkout_root=str(root)
    )
    events = []
    view = WorkBackendView(
        backend,
        contract,
        lambda *_args: events.append("guard"),
        terminal_id="terminal",
        expected_target=("s", "initial"),
    )
    assert view.create_session("s", "initial", "terminal", str(root)) == "initial"
    with pytest.raises(WorkEffectAuthorizationRequired, match="durable Work window reservation"):
        view.create_window("s", "later", "terminal", str(root))
    view.send_keys("s", "initial", "input")

    assert [kind for kind, _, _ in backend.calls] == ["session", "send"]
    assert all(restriction is contract for _, restriction, _ in backend.calls)
    assert events == ["guard", "guard"]


def _current_bubblewrap(tmp_path):
    fake_bwrap = tmp_path / "bwrap"
    fake_bwrap.write_text("#!/bin/sh\nprintf 'bubblewrap 0.13.0\\n'\n", encoding="utf-8")
    fake_bwrap.chmod(0o700)
    return fake_bwrap


def _command_contract(tmp_path, *, commands=("bash",)):
    import hashlib

    from cli_agent_orchestrator.backends.base import ProcessRestrictionContract
    from cli_agent_orchestrator.models.work_contract import ExecutableIdentity

    root = tmp_path.resolve()
    identities = []
    for command in commands:
        digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
        try:
            identities.append(
                ExecutableIdentity(
                    command_token=command,
                    content_reference=f"sha256:{digest}",
                    sha256_digest=digest,
                    elf_machine="x86_64",
                    elf_class="ELF64",
                    endianness="little",
                    static=True,
                )
            )
        except ValueError:
            identities.clear()
            break
    return ProcessRestrictionContract(
        paths=(),
        commands=commands,
        network=(),
        read_paths=(str(root),),
        checkout_root=str(root),
        executable_identities=tuple(identities),
    )


def test_shell_command_entry_is_rejected_before_candidate_execution(tmp_path, monkeypatch):
    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    backend = bubblewrap_backend.BubblewrapWorkBackend(bwrap_executable=tmp_path / "unused-bwrap")

    with pytest.raises(UnsupportedWorkEnforcement, match="canonical absolute executable token"):
        backend.preflight_work(_command_contract(tmp_path, commands=("/bin/sh -c id",)))


def test_preflight_accepts_one_command_after_the_host_bwrap_probe(tmp_path, monkeypatch):
    from pathlib import Path

    from cli_agent_orchestrator.backends import bubblewrap_backend

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bubblewrap_backend, "_query_abi_version", lambda: 9)
    probes = []
    monkeypatch.setattr(
        bubblewrap_backend,
        "_probe_bwrap_version",
        lambda path: probes.append(path) or (0, 13, 0),
    )
    backend = bubblewrap_backend.BubblewrapWorkBackend(bwrap_executable="/usr/bin/bwrap")

    assert backend.preflight_work(_command_contract(tmp_path, commands=("/worker",))) is None
    assert probes == [Path("/usr/bin/bwrap")]


def test_preflight_rejects_multiple_commands_before_host_probe(tmp_path, monkeypatch):
    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        bubblewrap_backend,
        "_probe_bwrap_version",
        lambda _path: pytest.fail("multiple command mappings must fail before host probing"),
    )
    backend = bubblewrap_backend.BubblewrapWorkBackend(bwrap_executable="/usr/bin/bwrap")

    with pytest.raises(UnsupportedWorkEnforcement, match="exactly one executable mapping"):
        backend.preflight_work(_command_contract(tmp_path, commands=("/worker", "/other-worker")))


@pytest.mark.parametrize(
    "version_output",
    ("bubblewrap 0.13.0", "unknown version output"),
    ids=("spoofed-supported-version", "unknown-version"),
)
def test_absolute_executable_token_still_fails_closed_after_version_validation(
    tmp_path, monkeypatch, version_output
):
    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bubblewrap_backend, "_query_abi_version", lambda: 9)
    monkeypatch.setattr(
        bubblewrap_backend,
        "_probe_bwrap_version",
        lambda _path: bubblewrap_backend._parse_bwrap_version(version_output, 0),
    )
    backend = bubblewrap_backend.BubblewrapWorkBackend(bwrap_executable=tmp_path / "unused-bwrap")

    if version_output == "unknown version output":
        with pytest.raises(UnsupportedWorkEnforcement) as rejected:
            backend.preflight_work(_command_contract(tmp_path, commands=("/usr/bin/bash",)))
        assert "malformed or unknown" in rejected.value.reason
    else:
        assert (
            backend.preflight_work(_command_contract(tmp_path, commands=("/usr/bin/bash",))) is None
        )


@pytest.mark.parametrize(
    ("version_output", "returncode", "expected_version", "reason"),
    [
        ("bubblewrap 0.13.0", 0, (0, 13, 0), None),
        ("bubblewrap 0.12.0", 0, None, "does not match the required version 0.13.0"),
        ("bubblewrap 0.14.0", 0, None, "does not match the required version 0.13.0"),
        ("unknown version output", 0, None, "malformed or unknown"),
    ],
    ids=("required-version", "below-required", "above-required", "unknown-version"),
)
def test_bubblewrap_version_probe_requires_exact_shared_version_and_unknown_fails_closed(
    monkeypatch, version_output, returncode, expected_version, reason
):
    import hashlib
    import os
    import subprocess
    from pathlib import Path

    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    descriptor = os.open(__file__, os.O_RDONLY | os.O_CLOEXEC)
    monkeypatch.setattr(bubblewrap_backend, "_verified_bwrap_descriptor", lambda _path: descriptor)
    monkeypatch.setattr(
        bubblewrap_backend,
        "_BWRAP_SHA256_ALLOWLIST",
        frozenset({hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}),
    )
    probe_calls = []

    def fake_run(args, **kwargs):
        probe_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, returncode, stdout=version_output, stderr="")

    monkeypatch.setattr(bubblewrap_backend.subprocess, "run", fake_run)

    if expected_version is not None:
        assert bubblewrap_backend._probe_bwrap_version(__file__) == expected_version
    else:
        with pytest.raises(UnsupportedWorkEnforcement, match=reason):
            bubblewrap_backend._probe_bwrap_version(__file__)

    assert len(probe_calls) == 1
    assert probe_calls[0][0][1:] == ["--version"]


def test_bubblewrap_probe_rejects_digest_outside_empty_allowlist_before_version_run(
    tmp_path, monkeypatch
):
    import os

    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    candidate = tmp_path / "bwrap"
    candidate.write_bytes(b"untrusted candidate")
    descriptor = os.open(candidate, os.O_RDONLY | os.O_CLOEXEC)
    monkeypatch.setattr(bubblewrap_backend, "_verified_bwrap_descriptor", lambda _path: descriptor)
    monkeypatch.setattr(bubblewrap_backend, "_BWRAP_SHA256_ALLOWLIST", frozenset(), raising=False)
    version_runs = []
    monkeypatch.setattr(
        bubblewrap_backend.subprocess,
        "run",
        lambda *args, **kwargs: version_runs.append((args, kwargs)),
    )

    with pytest.raises(UnsupportedWorkEnforcement, match="SHA-256 digest is not allowlisted"):
        bubblewrap_backend._probe_bwrap_version(candidate)

    assert version_runs == []


def test_nonempty_network_contract_is_rejected_without_running_candidate(tmp_path, monkeypatch):
    import shlex

    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import (
        ProcessRestrictionContract,
        UnsupportedWorkEnforcement,
    )

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    probe_marker = tmp_path / "version-probe-ran"
    bwrap = tmp_path / "bwrap"
    bwrap.write_text(
        "#!/bin/sh\n"
        f"printf 'called' > {shlex.quote(str(probe_marker))}\n"
        "printf 'bubblewrap 0.13.0\\n'\n",
        encoding="utf-8",
    )
    bwrap.chmod(0o700)
    backend = bubblewrap_backend.BubblewrapWorkBackend(bwrap_executable=bwrap)
    root = tmp_path.resolve()
    contract = ProcessRestrictionContract(
        paths=(),
        commands=("/usr/bin/bash",),
        network=("all",),
        read_paths=(str(root),),
        checkout_root=str(root),
    )

    with pytest.raises(
        UnsupportedWorkEnforcement,
        match="non-empty network contracts have no supported grammar",
    ):
        backend.preflight_work(contract)

    assert not probe_marker.exists()


def test_double_leading_slash_command_is_rejected_before_candidate_execution(tmp_path, monkeypatch):
    import shlex

    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    probe_marker = tmp_path / "version-probe-ran"
    bwrap = tmp_path / "bwrap"
    bwrap.write_text(
        "#!/bin/sh\n"
        f"printf 'called' > {shlex.quote(str(probe_marker))}\n"
        "printf 'bubblewrap 0.13.0\\n'\n",
        encoding="utf-8",
    )
    bwrap.chmod(0o700)
    backend = bubblewrap_backend.BubblewrapWorkBackend(bwrap_executable=bwrap)

    try:
        backend.preflight_work(_command_contract(tmp_path, commands=("//usr/bin/bash",)))
    except UnsupportedWorkEnforcement as rejected:
        assert not probe_marker.exists()
        assert "canonical absolute executable token" in rejected.reason
    else:
        pytest.fail("double-leading-slash command token unexpectedly passed preflight")


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("create_session", ("s", "w", "t")),
        ("create_window", ("s", "w", "t")),
        ("send_keys", ("s", "w", "input")),
        ("send_special_key", ("s", "w", "C-c")),
        ("kill_session", ("s",)),
        ("kill_window", ("s", "w")),
        ("pipe_pane", ("s", "w", "/tmp/pane.log")),
        ("stop_pipe_pane", ("s", "w")),
    ],
)
def test_legacy_mutators_reject_without_touching_tmux_client(tmp_path, method, args):
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement
    from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend

    class RecordingClient:
        def __init__(self):
            self.effects = []

        def __getattr__(self, name):
            def record(*call_args, **kwargs):
                self.effects.append((name, call_args, kwargs))

            return record

    client = RecordingClient()
    backend = BubblewrapWorkBackend(client=client, bwrap_executable=_current_bubblewrap(tmp_path))

    with pytest.raises(UnsupportedWorkEnforcement):
        getattr(backend, method)(*args)

    assert client.effects == []


def test_bound_process_executor_forwards_exact_work_fences_to_recorded_bubblewrap_launch(
    tmp_path, monkeypatch, attempt_credential_fd
):
    from types import SimpleNamespace

    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import ProcessRestrictionContract
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContractV2,
        ExecutableIdentity,
    )
    from cli_agent_orchestrator.services import work_bubblewrap_composition

    repository = WorkRepository(tmp_path / "bubblewrap-bound.sqlite3")
    repository.initialize()
    digest = "b" * 64
    executable = ExecutableIdentity(
        command_token="/worker",
        content_reference=f"sha256:{digest}",
        sha256_digest=digest,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )
    contract = EffectiveWorkContractV2(
        id="bubblewrap-bound-contract",
        operation_kind="launch",
        provider="scratch",
        backend="bubblewrap",
        permissions=ContractPermissions(commands=("/worker",)),
        resources=ContractResources(
            checkout_root=str(tmp_path), write_paths=(str(tmp_path / "out"),), units=1
        ),
        snapshot=ContractSnapshot(state="absent", absence_reason="legacy_parent_has_no_snapshot"),
        executable_identities=(executable,),
    )
    binding = SimpleNamespace(
        attempt_id="bubblewrap-attempt",
        generation=7,
        contract=contract,
        contract_hash=contract.canonical_hash(),
    )
    restriction = ProcessRestrictionContract(
        paths=(),
        commands=("/worker",),
        network=(),
        checkout_root=str(tmp_path),
        executable_identities=(executable,),
    )
    calls = []
    supervisor = object()
    before_effect = lambda: None
    authorize_setup = lambda _connection: None
    authorize_go = lambda _connection: None

    def launch(*args, **kwargs):
        calls.append((args, kwargs))
        return "scratch result"

    monkeypatch.setattr(
        work_bubblewrap_composition, "launch_recorded_bound_work_static_elf", launch
    )
    backend = bubblewrap_backend.BubblewrapWorkBackend(
        repository=repository,
        bwrap_executable="/tmp/accepted-scratch-bwrap",
        bwrap_sha256_digest="c" * 64,
        supervisor_factory=lambda: supervisor,
    )

    result = backend.execute_bound_process(
        restriction,
        binding=binding,
        command_token="/worker",
        worker_input=b"frozen task",
        expected_attempt_revision=19,
        attempt_credential_fd=attempt_credential_fd,
        before_effect=before_effect,
        authorize_setup=authorize_setup,
        authorize_go=authorize_go,
    )

    assert result == "scratch result"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (repository, "bubblewrap-attempt", 7)
    assert kwargs["expected_attempt_revision"] == 19
    assert kwargs["command_token"] == "/worker"
    assert kwargs["worker_input"] == b"frozen task"
    assert kwargs["bwrap_sha256_digest"] == "c" * 64
    assert kwargs["process_supervisor"] is supervisor
    assert kwargs["before_start"] is before_effect
    assert kwargs["authorize_setup"] is authorize_setup
    assert kwargs["authorize_go"] is authorize_go


def test_tool_contract_gets_fresh_attempt_bound_proxy_before_recorded_launch(
    tmp_path, monkeypatch, attempt_credential_fd
):
    from types import SimpleNamespace

    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import ProcessRestrictionContract
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContractV2,
        ExecutableIdentity,
    )
    from cli_agent_orchestrator.services import work_bubblewrap_composition
    from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy

    repository = WorkRepository(tmp_path / "bubblewrap-proxy.sqlite3")
    repository.initialize()
    digest = "d" * 64
    executable = ExecutableIdentity(
        command_token="/worker",
        content_reference=f"sha256:{digest}",
        sha256_digest=digest,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )
    contract = EffectiveWorkContractV2(
        id="bubblewrap-proxy-contract",
        operation_kind="launch",
        provider="scratch",
        backend="bubblewrap",
        permissions=ContractPermissions(commands=("/worker",), tools=("test.echo",)),
        resources=ContractResources(checkout_root=str(tmp_path), write_paths=(), units=1),
        snapshot=ContractSnapshot(state="absent", absence_reason="legacy_parent_has_no_snapshot"),
        executable_identities=(executable,),
    )
    binding = SimpleNamespace(
        attempt_id="bubblewrap-proxy-attempt",
        generation=3,
        contract=contract,
        contract_hash=contract.canonical_hash(),
    )
    restriction = ProcessRestrictionContract(
        paths=(),
        commands=("/worker",),
        network=(),
        tools=("test.echo",),
        executable_identities=(executable,),
    )
    proxies = []

    def make_proxy(attempt_id, generation):
        assert (attempt_id, generation) == ("bubblewrap-proxy-attempt", 3)
        proxy = WorkMcpProxy(
            repository,
            server_secret_factory=lambda: b"attempt-secret",
            upstream=lambda request, _secret: {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"ok": True},
            },
        )
        proxies.append(proxy)
        return proxy

    calls = []
    monkeypatch.setattr(
        work_bubblewrap_composition,
        "launch_recorded_bound_work_static_elf",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "scratch result",
    )
    supervisor = object()
    backend = bubblewrap_backend.BubblewrapWorkBackend(
        repository=repository,
        bwrap_executable="/tmp/accepted-scratch-bwrap",
        bwrap_sha256_digest="e" * 64,
        supervisor_factory=lambda: supervisor,
        mcp_proxy_factory=make_proxy,
    )

    result = backend.execute_bound_process(
        restriction,
        binding=binding,
        command_token="/worker",
        worker_input=b"frozen task",
        expected_attempt_revision=8,
        attempt_credential_fd=attempt_credential_fd,
        before_effect=lambda: None,
        authorize_setup=lambda _connection: None,
        authorize_go=lambda _connection: None,
    )

    assert result == "scratch result"
    assert len(proxies) == 1
    assert len(calls) == 1
    assert calls[0][1]["mcp_proxy"] is proxies[0]
    assert calls[0][1]["process_supervisor"] is supervisor


def test_tool_contract_fails_preflight_without_attempt_bound_proxy_factory(monkeypatch):
    from cli_agent_orchestrator.backends import bubblewrap_backend
    from cli_agent_orchestrator.backends.base import (
        ProcessRestrictionContract,
        UnsupportedWorkEnforcement,
    )
    from cli_agent_orchestrator.models.work_contract import ExecutableIdentity

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        bubblewrap_backend,
        "_query_abi_version",
        lambda: pytest.fail("missing MCP proxy factory must reject before host probing"),
    )
    backend = bubblewrap_backend.BubblewrapWorkBackend()
    executable = ExecutableIdentity(
        command_token="/worker",
        content_reference="sha256:" + "f" * 64,
        sha256_digest="f" * 64,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )
    restriction = ProcessRestrictionContract(
        paths=(),
        commands=("/worker",),
        network=(),
        tools=("test.echo",),
        executable_identities=(executable,),
    )

    with pytest.raises(UnsupportedWorkEnforcement, match="bound MCP proxy factory"):
        backend.preflight_work(restriction)


@pytest.mark.parametrize(
    ("commands", "reason"),
    [
        pytest.param((), "no command contract is currently supportable", id="empty"),
        pytest.param(("bash",), "canonical absolute executable tokens", id="relative-bash"),
        pytest.param(("python3",), "canonical absolute executable tokens", id="relative-python"),
        pytest.param(
            ("/usr/bin/bash",),
            "canonical trusted path",
            id="absolute-bash",
        ),
    ],
)
def test_work_backend_view_rejects_unenforceable_commands_before_tmux_effects(
    tmp_path, commands, reason, monkeypatch
):
    from cli_agent_orchestrator.backends.base import (
        UnsupportedWorkEnforcement,
        WorkEffectAuthorizationRequired,
    )
    from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend
    from cli_agent_orchestrator.backends.work_backend import WorkBackendView
    from cli_agent_orchestrator.constants import FIFO_DIR
    from cli_agent_orchestrator.backends import bubblewrap_backend

    monkeypatch.setattr(bubblewrap_backend, "_query_abi_version", lambda: 9)
    client = NoTmuxEffects()
    backend = BubblewrapWorkBackend(client=client, bwrap_executable=_current_bubblewrap(tmp_path))
    contract = _command_contract(tmp_path, commands=commands)
    effects = []
    view = WorkBackendView(
        backend,
        contract,
        lambda *_args: effects.append("authorized"),
        terminal_id="t",
        expected_target=("s", "w"),
    )
    view._authorized_targets.add(("s", "w"))

    for operation in (
        lambda: view.create_session("s", "w", "t", str(tmp_path)),
        lambda: view.send_keys("s", "w", "input"),
        lambda: view.send_special_key("s", "w", "C-c"),
        lambda: view.kill_session("s"),
        lambda: view.kill_window("s", "w"),
        lambda: view.pipe_pane("s", "w", str(FIFO_DIR / "t.fifo")),
        lambda: view.stop_pipe_pane("s", "w"),
    ):
        with pytest.raises(UnsupportedWorkEnforcement, match=reason):
            operation()

    with pytest.raises(WorkEffectAuthorizationRequired, match="durable Work window reservation"):
        view.create_window("s", "w", "t", str(tmp_path))

    assert client.effects == []
    assert effects == []


@pytest.mark.parametrize(
    ("commands", "reason"),
    [
        pytest.param((), "no command contract is currently supportable", id="empty"),
        pytest.param(("bash",), "canonical absolute executable tokens", id="relative-bash"),
        pytest.param(
            ("/usr/bin/bash",),
            "exactly one immutable static ELF executable mapping is required",
            id="absolute-bash",
        ),
    ],
)
def test_work_admission_preflight_rejects_unenforceable_commands(tmp_path, commands, reason):
    from types import SimpleNamespace

    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement
    from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend
    from cli_agent_orchestrator.services.work_admission import WorkAdmission

    client = NoTmuxEffects()
    backend = BubblewrapWorkBackend(client=client, bwrap_executable=_current_bubblewrap(tmp_path))
    admission = object.__new__(WorkAdmission)
    admission.backends = {"bubblewrap": backend}
    contract = SimpleNamespace(
        backend="bubblewrap",
        permissions=SimpleNamespace(
            paths=(str(tmp_path.resolve()),), commands=commands, network=(), tools=()
        ),
        resources=SimpleNamespace(checkout_root=str(tmp_path.resolve()), write_paths=()),
    )

    with pytest.raises(UnsupportedWorkEnforcement, match=reason):
        admission._preflight(contract)

    assert client.effects == []
