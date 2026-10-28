"""Unit controls for exact, opt-in MCP owner registration and pre-stop.

The scripted children below stand in for the configured CAO MCP command. These
are process-identity controls, not SDK, provider, or native acceptance.
"""

import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator.utils import mcp_owner_stop as owner_stop
from cli_agent_orchestrator.utils.mcp_readiness import READY_FILE_ENV, READY_TOKEN_ENV

pytestmark = pytest.mark.offline_database

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
_SNAPSHOT = {
    "physical_eof": True,
    "local_input_closed": False,
    "pending_client": 0,
    "pending_server": 0,
    "input_tail_bytes": 0,
    "output_tail_bytes": 0,
    "unflushed_replies": 0,
    "output_busy": 0,
    "unsafe": None,
    "signal_count": 0,
}


def _write_script(root: Path, *, reject_unsettled_on_signal: bool = False) -> Path:
    script = root / "cao-mcp-server"
    publish = ""
    if reject_unsettled_on_signal:
        publish = (
            "        snapshot = {\n"
            "            'physical_eof': False, 'local_input_closed': True,\n"
            "            'pending_client': 1, 'pending_server': 0,\n"
            "            'input_tail_bytes': 0, 'output_tail_bytes': 0,\n"
            "            'unflushed_replies': 0, 'output_busy': 0,\n"
            "            'unsafe': None, 'signal_count': 1,\n"
            "        }\n"
            "        with open(os.environ['TEST_RESULT_FILE'], 'w', encoding='ascii') as f:\n"
            "            f.write(str(publish_owned_mcp_normal_return(registration, snapshot)))\n"
        )
    wait = (
        "        deadline = time.monotonic() + 3.0\n"
        "        while not os.path.exists(os.environ['TEST_RELEASE_FILE']) and time.monotonic() < deadline:\n"
        "            time.sleep(0.01)\n"
    )
    script.write_text(
        "import os, signal, sys, time\n"
        "from cli_agent_orchestrator.utils.mcp_owner_stop import (\n"
        "    publish_owned_mcp_normal_return, register_owned_mcp,\n"
        ")\n"
        "registration = register_owned_mcp()\n"
        "if registration is None:\n"
        "    raise SystemExit(31)\n"
        "def stop(_signum, _frame):\n"
        "    with open(os.environ['TEST_SIGNAL_FILE'], 'w', encoding='ascii') as f:\n"
        "        f.write('seen')\n"
        + publish
        + "    if os.environ.get('TEST_WAIT_ON_SIGNAL') == '1':\n"
        + wait
        + "    raise SystemExit(0)\n"
        + "signal.signal(signal.SIGINT, stop)\n"
        + "while True:\n"
        + "    if os.environ.get('TEST_EXIT_FILE') and os.path.exists(os.environ['TEST_EXIT_FILE']):\n"
        + "        raise SystemExit(0)\n"
        + "    time.sleep(0.02)\n",
        encoding="utf-8",
    )
    return script


def _config_entry(home: Path, terminal_id: str, script: Path, token: str, index: int = 0):
    owner_dir = home / "tmp" / f"{terminal_id}.mcp-owners"
    env = {
        "CAO_TERMINAL_ID": terminal_id,
        READY_FILE_ENV: str(home / "tmp" / f"{terminal_id}.{index}.ready"),
        READY_TOKEN_ENV: token,
        owner_stop.OWNER_DIR_ENV: str(owner_dir),
        owner_stop.SIGNAL_STOP_ENV: "1",
    }
    return {
        "command": sys.executable,
        "args": [str(script)],
        "env": env,
    }, env


def _write_config(home: Path, terminal_id: str, entries: dict[str, dict]) -> Path:
    path = home / "tmp" / f"{terminal_id}.mcp.json"
    path.write_text(json.dumps({"mcpServers": entries}), encoding="utf-8")
    path.chmod(0o600)
    return path


class _FixedPoller:
    def __init__(self, flags: int | None):
        self.flags = flags
        self.fds: list[int] = []

    def register(self, fd: int, _events: int) -> None:
        self.fds.append(fd)

    def poll(self, _timeout: int) -> list[tuple[int, int]]:
        if self.flags is None:
            return []
        return [(fd, self.flags) for fd in self.fds]


def _start_child(
    home: Path,
    terminal_id: str,
    script: Path,
    env_values: dict[str, str],
    *,
    wait_on_signal: bool = False,
    expected_records: int = 1,
    child_home: Path | None = None,
    exit_on_file: bool = False,
):
    marker = home / "tmp" / f"{terminal_id}.{env_values[READY_TOKEN_ENV]}.signal"
    env = {
        "PATH": os.defpath,
        "HOME": str(child_home or home),
        "CAO_HOME_DIR": str(child_home or home),
        "PYTHONPATH": str(_SOURCE_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CAO_TERMINAL_ID": terminal_id,
        READY_FILE_ENV: env_values[READY_FILE_ENV],
        READY_TOKEN_ENV: env_values[READY_TOKEN_ENV],
        owner_stop.OWNER_DIR_ENV: env_values[owner_stop.OWNER_DIR_ENV],
        owner_stop.SIGNAL_STOP_ENV: "1",
        "TEST_SIGNAL_FILE": str(marker),
    }
    if wait_on_signal:
        env["TEST_WAIT_ON_SIGNAL"] = "1"
        env["TEST_RELEASE_FILE"] = str(marker.with_suffix(".release"))
    if exit_on_file:
        env["TEST_EXIT_FILE"] = str(marker.with_suffix(".exit"))
    if "TEST_RESULT_FILE" in env_values:
        env["TEST_RESULT_FILE"] = env_values["TEST_RESULT_FILE"]
    stderr_path = home / "tmp" / f"{terminal_id}.{env_values[READY_TOKEN_ENV]}.stderr"
    with stderr_path.open("wb") as child_stderr:
        os.fchmod(child_stderr.fileno(), 0o600)
        process = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=child_stderr,
            env=env,
        )
    owner_dir = home / "tmp" / f"{terminal_id}.mcp-owners"
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if len(_record_files(home, terminal_id)) >= expected_records:
            return process, marker
        if process.poll() is not None:
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
            detail = f"; synthetic child stderr:\n{stderr[-2000:]}" if stderr else ""
            raise AssertionError(
                "controlled MCP child failed to register "
                f"(returncode={process.returncode}){detail}"
            )
        time.sleep(0.01)
    process.kill()
    process.wait(timeout=2)
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
    detail = f"; synthetic child stderr:\n{stderr[-2000:]}" if stderr else ""
    raise AssertionError(
        "controlled MCP child registration timed out " f"(returncode={process.returncode}){detail}"
    )


def _kill_children(children):
    for process, _ in children:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def _private_home(tmp_path: Path) -> Path:
    home = tmp_path / "cao-home"
    home.mkdir(mode=0o700)
    (home / "tmp").mkdir(mode=0o700)
    return home


def _wide_mode_legacy_home(tmp_path: Path) -> Path:
    home = _private_home(tmp_path)
    (home / "tmp").chmod(0o777)
    home.chmod(0o755)
    return home


def _record_files(home: Path, terminal_id: str) -> list[Path]:
    return sorted(
        path
        for path in (home / "tmp" / f"{terminal_id}.mcp-owners").glob("*.json")
        if not path.name.endswith(".normal.json")
    )


def test_exported_contract_and_result_are_content_free():
    assert owner_stop.MCP_STOP_ALLOWANCE_SECONDS == 6.0
    assert owner_stop.SIGNAL_STOP_ENV == "CAO_INTERNAL_MCP_SIGNAL_STOP"
    assert owner_stop.OWNER_DIR_ENV == "CAO_MCP_OWNER_DIR"
    assert owner_stop._valid_terminal_id("claude-one")
    assert not owner_stop._valid_terminal_id("../claude-one")
    assert not owner_stop._valid_terminal_id("claude/one")
    assert not owner_stop._valid_terminal_id("claude\none")
    assert not owner_stop._valid_terminal_id("x" * 245)
    assert {field.name for field in dataclasses.fields(owner_stop.PrestopResult)} == {
        "outcome",
        "identity_count",
        "signaled_count",
        "retired_count",
        "normal_count",
        "unknown_count",
        "still_live_count",
        "elapsed_ms",
    }
    result = owner_stop.PrestopResult(owner_stop.PrestopOutcome.STILL_LIVE, 1, 1, 0, 0, 0, 1, 4)
    rendered = repr(result)
    assert "pid" not in rendered
    assert "token" not in rendered
    assert "ready" not in rendered
    assert "/" not in rendered


@pytest.mark.parametrize(
    "flags,expected",
    [
        (owner_stop.select.POLLIN, True),
        (owner_stop.select.POLLHUP, True),
        (owner_stop.select.POLLERR, None),
        (getattr(owner_stop.select, "POLLNVAL", 0x20), None),
        (0, None),
        (owner_stop.select.POLLIN | owner_stop.select.POLLERR, None),
        (
            owner_stop.select.POLLHUP | getattr(owner_stop.select, "POLLNVAL", 0x20),
            None,
        ),
    ],
)
def test_pidfd_ready_requires_exit_event_without_error_flags(monkeypatch, flags, expected):
    monkeypatch.setattr(
        owner_stop.select,
        "poll",
        lambda: _FixedPoller(flags),
    )
    assert owner_stop._pidfd_ready(12345) is expected


def test_pidfd_poll_exception_is_unknown(monkeypatch):
    class RaisingPoller:
        def register(self, _fd: int, _events: int) -> None:
            raise OSError("poll registration failed")

    monkeypatch.setattr(owner_stop.select, "poll", RaisingPoller)
    assert owner_stop._pidfd_ready(12345) is None


def test_argv_match_is_exact_except_for_a_verified_direct_shebang(tmp_path):
    direct_argv = (sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server")
    direct_config = owner_stop._ConfigEntry(direct_argv, {}, "0" * 64)
    direct_proc = owner_stop._ProcIdentity(
        pid=123,
        uid=os.getuid(),
        start_ticks=1,
        boot_id="test-boot",
        pid_namespace=(1, 2),
        cmdline_sha256="0" * 64,
        exe_path=os.path.realpath(sys.executable),
        argv=tuple(os.fsencode(part) for part in direct_argv),
        relevant_env={},
    )
    assert owner_stop._argv_matches_config(direct_proc, direct_config)

    prefixed_proc = dataclasses.replace(
        direct_proc,
        exe_path="/bin/sh",
        argv=(b"/bin/sh", *direct_proc.argv),
    )
    assert not owner_stop._argv_matches_config(prefixed_proc, direct_config)

    script = tmp_path / "cao-mcp-server"
    script.write_text(f"#!{sys.executable}\npass\n", encoding="utf-8")
    script_config = owner_stop._ConfigEntry((str(script),), {}, "1" * 64)
    shebang_proc = dataclasses.replace(
        direct_proc,
        exe_path=os.path.realpath(sys.executable),
        argv=(os.fsencode(sys.executable), os.fsencode(str(script))),
    )
    assert owner_stop._argv_matches_config(shebang_proc, script_config)
    wrong_prefix = dataclasses.replace(
        shebang_proc,
        exe_path="/bin/sh",
        argv=(b"/bin/sh", os.fsencode(str(script))),
    )
    assert not owner_stop._argv_matches_config(wrong_prefix, script_config)


def test_registry_and_record_have_private_modes_and_exclusive_publication(tmp_path):
    home = _private_home(tmp_path)
    terminal_id = "a01b02c3"
    script = _write_script(tmp_path)
    config, values = _config_entry(home, terminal_id, script, "unit-private-token")
    process, marker = _start_child(home, terminal_id, script, values)
    try:
        _write_config(home, terminal_id, {"cao-mcp-server": config})
        registry = home / "tmp" / f"{terminal_id}.mcp-owners"
        record_path = _record_files(home, terminal_id)[0]
        record_bytes = record_path.read_bytes()
        record = json.loads(record_bytes)
        assert registry.stat().st_mode & 0o777 == 0o700
        assert record_path.stat().st_mode & 0o777 == 0o600
        assert set(record) == {
            "version",
            "terminal_id",
            "pid",
            "uid",
            "start_ticks",
            "boot_id",
            "pid_namespace",
            "ready_token",
            "ready_file",
            "owner_dir",
            "cmdline_sha256",
            "nonce",
        }
        registry_fd = os.open(registry, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert (
                owner_stop._write_exclusive_at(registry_fd, record_path.name, b"replacement")
                is False
            )
        finally:
            os.close(registry_fd)
        assert record_path.read_bytes() == record_bytes
    finally:
        _kill_children([(process, marker)])


def test_registration_is_dormant_without_the_explicit_local_stop(monkeypatch):
    for name in (
        owner_stop.SIGNAL_STOP_ENV,
        owner_stop.PHYSICAL_EOF_ENV,
        owner_stop.OWNER_DIR_ENV,
        "CAO_TERMINAL_ID",
        READY_FILE_ENV,
        READY_TOKEN_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    assert owner_stop.register_owned_mcp() is None


def test_safe_legacy_terminal_id_without_records_is_not_applicable(tmp_path):
    home = _private_home(tmp_path)
    result = owner_stop.prestop_owned_mcp("claude-one", time.monotonic() + 0.1, home=home)
    assert result.outcome is owner_stop.PrestopOutcome.NOT_APPLICABLE
    assert owner_stop.reconcile_owned_mcp("claude-one", home=home) is True


def test_missing_registry_reconcile_is_legacy_noop_with_wide_ancestor_modes(tmp_path):
    home = _wide_mode_legacy_home(tmp_path)
    assert owner_stop.reconcile_owned_mcp("absent-probe", home=home) is True


def test_missing_registry_pre_stop_is_legacy_noop_with_wide_ancestor_modes(tmp_path):
    home = _wide_mode_legacy_home(tmp_path)
    result = owner_stop.prestop_owned_mcp("absent-prestop", time.monotonic() + 0.1, home=home)
    assert result.outcome is owner_stop.PrestopOutcome.NOT_APPLICABLE
    assert result.normal_count == 0


def test_configured_stop_without_registry_stays_unknown_with_wide_ancestor_modes(
    tmp_path,
):
    home = _wide_mode_legacy_home(tmp_path)
    script = _write_script(tmp_path)
    config, _ = _config_entry(home, "configured-absent", script, "unit-configured-absent")
    _write_config(home, "configured-absent", {"cao-mcp-server": config})

    result = owner_stop.prestop_owned_mcp("configured-absent", time.monotonic() + 0.1, home=home)
    assert result.outcome is owner_stop.PrestopOutcome.UNKNOWN
    assert result.normal_count == 0
    assert result.retired_count == 0


@pytest.mark.parametrize("registry_kind", ["bad_mode", "symlink", "inaccessible"])
def test_existing_registry_is_not_absence_when_ancestor_modes_are_wide(tmp_path, registry_kind):
    home = _private_home(tmp_path)
    registry = home / "tmp" / "existing.mcp-owners"
    if registry_kind == "symlink":
        target = home / "tmp" / "registry-target"
        target.mkdir(mode=0o700)
        registry.symlink_to(target, target_is_directory=True)
    else:
        registry.mkdir(mode=0o700)
        if registry_kind == "bad_mode":
            registry.chmod(0o755)
        else:
            registry.chmod(0o000)

    try:
        assert owner_stop.reconcile_owned_mcp("existing", home=home) is False
    finally:
        if registry_kind == "inaccessible":
            registry.chmod(0o700)


def test_existing_registry_with_wide_ancestor_modes_is_not_absence(tmp_path):
    home = _wide_mode_legacy_home(tmp_path)
    registry = home / "tmp" / "existing.mcp-owners"
    registry.mkdir(mode=0o700)
    assert owner_stop.reconcile_owned_mcp("existing", home=home) is False


def test_unsupported_platform_keeps_no_record_legacy_cleanup_a_noop(tmp_path, monkeypatch):
    home = _private_home(tmp_path)
    monkeypatch.setattr(owner_stop.sys, "platform", "darwin")

    absent = owner_stop.prestop_owned_mcp("claude-one", time.monotonic() + 0.1, home=home)
    assert absent.outcome is owner_stop.PrestopOutcome.NOT_APPLICABLE
    assert absent.identity_count == 0
    assert owner_stop.reconcile_owned_mcp("claude-one", home=home) is True

    script = _write_script(tmp_path)
    opted_in, _ = _config_entry(home, "claude-opted-in", script, "unit-opted-in")
    _write_config(home, "claude-opted-in", {"cao-mcp-server": opted_in})
    unregistered = owner_stop.prestop_owned_mcp(
        "claude-opted-in", time.monotonic() + 0.1, home=home
    )
    assert unregistered.outcome is owner_stop.PrestopOutcome.UNKNOWN
    assert unregistered.normal_count == 0
    assert owner_stop.reconcile_owned_mcp("claude-opted-in", home=home) is False

    registry = home / "tmp" / "claude-two.mcp-owners"
    registry.mkdir(mode=0o700)
    record = registry / ("123." + "a" * 32 + ".json")
    record.write_text("{}", encoding="ascii")
    record.chmod(0o600)

    present = owner_stop.prestop_owned_mcp("claude-two", time.monotonic() + 0.1, home=home)
    assert present.outcome is owner_stop.PrestopOutcome.UNSUPPORTED
    assert present.identity_count == 1
    assert record.exists()
    assert owner_stop.reconcile_owned_mcp("claude-two", home=home) is False
    assert record.exists()


@pytest.mark.parametrize(
    "change,expected",
    [
        ("uid", owner_stop.PrestopOutcome.UNKNOWN),
        ("namespace", owner_stop.PrestopOutcome.UNSUPPORTED),
        ("command_digest", owner_stop.PrestopOutcome.UNKNOWN),
        ("birth", owner_stop.PrestopOutcome.RETIRED_UNSEALED),
    ],
)
def test_live_child_identity_mismatch_never_receives_signal(tmp_path, change, expected):
    home = _private_home(tmp_path)
    terminal_id = "a10b20c3"
    script = _write_script(tmp_path)
    config, values = _config_entry(home, terminal_id, script, "unit-token")
    process, marker = _start_child(home, terminal_id, script, values)
    try:
        _write_config(home, terminal_id, {"cao-mcp-server": config})
        record_path = _record_files(home, terminal_id)[0]
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if change == "uid":
            record["uid"] = record["uid"] + 1
        elif change == "namespace":
            record["pid_namespace"]["inode"] += 1
        elif change == "command_digest":
            record["cmdline_sha256"] = "0" * 64
        else:
            record["start_ticks"] += 1
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)

        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 0.1, home=home)
        assert result.outcome is expected
        assert not marker.exists()
        assert process.poll() is None
        if expected is owner_stop.PrestopOutcome.UNKNOWN:
            assert record_path.exists()
        if expected is owner_stop.PrestopOutcome.UNSUPPORTED:
            assert record_path.exists()
    finally:
        _kill_children([(process, marker)])


@pytest.mark.parametrize("mismatch", ["token", "argv", "missing-config"])
def test_old_or_changed_config_cannot_authorize_a_live_record(tmp_path, mismatch):
    home = _private_home(tmp_path)
    terminal_id = "b10c20d3"
    script = _write_script(tmp_path)
    config, values = _config_entry(home, terminal_id, script, "old-unit-token")
    process, marker = _start_child(home, terminal_id, script, values)
    try:
        if mismatch == "token":
            changed, _ = _config_entry(home, terminal_id, script, "new-unit-token")
            _write_config(home, terminal_id, {"cao-mcp-server": changed})
        elif mismatch == "argv":
            changed = dict(config)
            changed["args"] = [str(tmp_path / "not-the-child")]
            _write_config(home, terminal_id, {"cao-mcp-server": changed})
        # The missing-config branch deliberately leaves the private file absent.

        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 0.1, home=home)
        assert result.outcome is owner_stop.PrestopOutcome.STILL_LIVE
        assert result.signaled_count == 0
        assert marker.exists() is False
        assert process.poll() is None
        assert _record_files(home, terminal_id)

        _write_config(home, terminal_id, {"cao-mcp-server": config})
        retired = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 2.0, home=home)
        assert retired.outcome is owner_stop.PrestopOutcome.RETIRED_UNSEALED
        assert retired.signaled_count == 1
        assert retired.retired_count == 1
        assert marker.exists()
        assert process.wait(timeout=1) == 0
        assert _record_files(home, terminal_id) == []
    finally:
        _kill_children([(process, marker)])


def test_one_deadline_signals_every_matching_owner_before_wait(tmp_path):
    home = _private_home(tmp_path)
    terminal_id = "c10d20e3"
    script = _write_script(tmp_path)
    children = []
    entries = {}
    try:
        for index in range(2):
            name = f"owner-{index}"
            config, values = _config_entry(home, terminal_id, script, f"unit-token-{index}", index)
            children.append(
                _start_child(
                    home,
                    terminal_id,
                    script,
                    values,
                    wait_on_signal=True,
                    expected_records=index + 1,
                )
            )
            entries[name] = config
        _write_config(home, terminal_id, entries)

        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 0.25, home=home)
        assert result.outcome is owner_stop.PrestopOutcome.STILL_LIVE
        assert result.identity_count == 2
        assert result.signaled_count == 2
        assert result.still_live_count == 2
        assert all(marker.exists() for _, marker in children)
        for _, marker in children:
            marker.with_suffix(".release").write_text("release", encoding="ascii")
        assert all(process.wait(timeout=1) == 0 for process, _ in children)
        assert owner_stop.reconcile_owned_mcp(terminal_id, home=home) is True
        assert _record_files(home, terminal_id) == []
    finally:
        _kill_children(children)


def test_wait_loop_poll_error_is_unknown_not_retired(tmp_path, monkeypatch):
    home = _private_home(tmp_path)
    terminal_id = "poll-error-one"
    script = _write_script(tmp_path)
    config, values = _config_entry(home, terminal_id, script, "unit-poll-error")
    process, marker = _start_child(home, terminal_id, script, values)

    class ErrorPoller:
        def __init__(self):
            self.fd = -1

        def register(self, fd: int, _events: int) -> None:
            self.fd = fd

        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            return [(self.fd, owner_stop.select.POLLERR)]

    try:
        _write_config(home, terminal_id, {"cao-mcp-server": config})
        monkeypatch.setattr(owner_stop, "_pidfd_ready", lambda _fd: False)
        monkeypatch.setattr(owner_stop.signal, "pidfd_send_signal", lambda *_args: None)
        monkeypatch.setattr(owner_stop.select, "poll", ErrorPoller)

        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 2.0, home=home)
        assert result.outcome is owner_stop.PrestopOutcome.UNKNOWN
        assert result.signaled_count == 1
        assert result.retired_count == 0
        assert result.unknown_count == 1
        assert _record_files(home, terminal_id)
        assert process.poll() is None and not marker.exists()
    finally:
        _kill_children([(process, marker)])


def test_batch_pre_stop_uses_persisted_config_for_multiple_terminals(tmp_path):
    home = _private_home(tmp_path)
    script = _write_script(tmp_path)
    terminals = ["f10a20b3", "f20a30c4"]
    children = []
    try:
        for index, terminal_id in enumerate(terminals):
            config, values = _config_entry(
                home, terminal_id, script, f"unit-batch-token-{index}", index
            )
            children.append(_start_child(home, terminal_id, script, values))
            _write_config(home, terminal_id, {"cao-mcp-server": config})

        results = owner_stop.prestop_owned_mcp_many(terminals, time.monotonic() + 2.0, home=home)
        assert set(results) == set(terminals)
        assert all(
            results[terminal_id].outcome is owner_stop.PrestopOutcome.RETIRED_UNSEALED
            for terminal_id in terminals
        )
        assert all(results[terminal_id].signaled_count == 1 for terminal_id in terminals)
        assert all(process.wait(timeout=1) == 0 for process, _ in children)
    finally:
        _kill_children(children)


def test_child_private_home_does_not_redirect_owner_record_from_api_home(tmp_path):
    api_root = tmp_path / "api"
    child_root = tmp_path / "child"
    api_root.mkdir(mode=0o700)
    child_root.mkdir(mode=0o700)
    api_home = _private_home(api_root)
    child_home = _private_home(child_root)
    terminal_id = "claude-one"
    script = _write_script(tmp_path)
    config, values = _config_entry(api_home, terminal_id, script, "unit-owner-token")
    process, marker = _start_child(
        api_home,
        terminal_id,
        script,
        values,
        child_home=child_home,
    )
    try:
        _write_config(api_home, terminal_id, {"cao-mcp-server": config})
        api_registry = api_home / "tmp" / f"{terminal_id}.mcp-owners"
        child_registry = child_home / "tmp" / f"{terminal_id}.mcp-owners"
        assert len(_record_files(api_home, terminal_id)) == 1
        assert not child_registry.exists()

        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 2.0, home=api_home)
        assert result.outcome is owner_stop.PrestopOutcome.RETIRED_UNSEALED
        assert result.signaled_count == 1
        assert process.wait(timeout=1) == 0
        assert not list(api_registry.glob("*.json"))
    finally:
        _kill_children([(process, marker)])


def test_reconcile_retires_owned_zombie_before_parent_wait(tmp_path):
    home = _private_home(tmp_path)
    terminal_id = "zombie-one"
    script = _write_script(tmp_path)
    config, values = _config_entry(home, terminal_id, script, "unit-zombie-token")
    process, marker = _start_child(home, terminal_id, script, values, exit_on_file=True)
    try:
        _write_config(home, terminal_id, {"cao-mcp-server": config})
        marker.with_suffix(".exit").write_text("exit", encoding="ascii")
        deadline = time.monotonic() + 2.0
        zombie = False
        while time.monotonic() < deadline:
            try:
                stat_fields = (
                    Path(f"/proc/{process.pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
                )
            except FileNotFoundError:
                break
            if stat_fields and stat_fields[0] == b"Z":
                zombie = True
                break
            time.sleep(0.01)
        assert zombie

        # Do not call Popen.poll()/wait() until the owner helper has observed
        # and retired this exact child through its already-readable pidfd.
        assert owner_stop.reconcile_owned_mcp(terminal_id, home=home) is True
        assert _record_files(home, terminal_id) == []
        assert process.wait(timeout=1) == 0
    finally:
        _kill_children([(process, marker)])


def test_pre_stop_baseline_uses_the_same_names_as_record_scan(tmp_path, monkeypatch):
    home = _private_home(tmp_path)
    terminal_id = "scan-race-one"
    script = _write_script(tmp_path)
    old_config, old_values = _config_entry(home, terminal_id, script, "unit-scan-old", index=0)
    new_config, new_values = _config_entry(home, terminal_id, script, "unit-scan-new", index=1)
    old_process, old_marker = _start_child(home, terminal_id, script, old_values, exit_on_file=True)
    children = [(old_process, old_marker)]
    _write_config(
        home,
        terminal_id,
        {"old-owner": old_config, "new-owner": new_config},
    )
    old_marker.with_suffix(".exit").write_text("exit", encoding="ascii")
    deadline = time.monotonic() + 2.0
    zombie = False
    while time.monotonic() < deadline:
        try:
            stat_fields = (
                Path(f"/proc/{old_process.pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
            )
        except FileNotFoundError:
            break
        if stat_fields and stat_fields[0] == b"Z":
            zombie = True
            break
        time.sleep(0.01)
    assert zombie

    original_scan = owner_stop._scan_records
    new_child: tuple[subprocess.Popen, Path] | None = None

    def scan_then_publish(*args):
        nonlocal new_child
        scanned = original_scan(*args)
        if new_child is None:
            new_child = _start_child(
                home,
                terminal_id,
                script,
                new_values,
                expected_records=2,
            )
            children.append(new_child)
        return scanned

    monkeypatch.setattr(owner_stop, "_scan_records", scan_then_publish)
    try:
        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 2.0, home=home)
        assert new_child is not None
        assert result.outcome is owner_stop.PrestopOutcome.STILL_LIVE
        assert result.identity_count == 2
        assert result.retired_count == 1
        assert result.still_live_count == 1
        assert len(_record_files(home, terminal_id)) == 1
    finally:
        _kill_children(children)


def test_reconcile_snapshot_does_not_hide_a_later_owner(tmp_path, monkeypatch):
    home = _private_home(tmp_path)
    terminal_id = "reconcile-race-one"
    script = _write_script(tmp_path)
    _, old_values = _config_entry(home, terminal_id, script, "unit-reconcile-old", index=0)
    _, new_values = _config_entry(home, terminal_id, script, "unit-reconcile-new", index=1)
    old_process, old_marker = _start_child(home, terminal_id, script, old_values, exit_on_file=True)
    children = [(old_process, old_marker)]
    try:
        old_marker.with_suffix(".exit").write_text("exit", encoding="ascii")
        deadline = time.monotonic() + 2.0
        zombie = False
        while time.monotonic() < deadline:
            try:
                stat_fields = (
                    Path(f"/proc/{old_process.pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
                )
            except FileNotFoundError:
                break
            if stat_fields and stat_fields[0] == b"Z":
                zombie = True
                break
            time.sleep(0.01)
        assert zombie

        original_scan = owner_stop._scan_records
        new_child: tuple[subprocess.Popen, Path] | None = None

        def scan_then_publish(*args):
            nonlocal new_child
            scanned = original_scan(*args)
            if new_child is None:
                new_child = _start_child(
                    home,
                    terminal_id,
                    script,
                    new_values,
                    expected_records=2,
                )
                children.append(new_child)
            return scanned

        monkeypatch.setattr(owner_stop, "_scan_records", scan_then_publish)
        assert owner_stop.reconcile_owned_mcp(terminal_id, home=home) is False
        assert new_child is not None
        assert len(_record_files(home, terminal_id)) == 1
        assert new_child[0].poll() is None
    finally:
        _kill_children(children)


def test_malformed_record_is_retained_and_reconcile_stays_false(tmp_path):
    home = _private_home(tmp_path)
    terminal_id = "f30b40d5"
    script = _write_script(tmp_path)
    config, values = _config_entry(home, terminal_id, script, "unit-token")
    process, marker = _start_child(home, terminal_id, script, values)
    try:
        _write_config(home, terminal_id, {"cao-mcp-server": config})
        record_path = _record_files(home, terminal_id)[0]
        record_path.write_text('{"version":1,"token":"DO_NOT_ECHO"}', encoding="utf-8")
        record_path.chmod(0o600)

        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 0.1, home=home)
        assert result.outcome is owner_stop.PrestopOutcome.UNKNOWN
        assert result.signaled_count == 0
        assert result.unknown_count == 1
        assert process.poll() is None and not marker.exists()
        assert record_path.exists()
        assert owner_stop.reconcile_owned_mcp(terminal_id, home=home) is False
        assert "DO_NOT_ECHO" not in repr(result)
    finally:
        _kill_children([(process, marker)])


def test_expired_deadline_does_not_signal_and_reconcile_only_prunes_after_exit(tmp_path):
    home = _private_home(tmp_path)
    terminal_id = "d10e20f3"
    script = _write_script(tmp_path)
    config, values = _config_entry(home, terminal_id, script, "unit-token")
    process, marker = _start_child(home, terminal_id, script, values)
    try:
        _write_config(home, terminal_id, {"cao-mcp-server": config})
        expired = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() - 0.1, home=home)
        assert expired.outcome is owner_stop.PrestopOutcome.STILL_LIVE
        assert expired.signaled_count == 0
        assert process.poll() is None and not marker.exists()
        assert owner_stop.reconcile_owned_mcp(terminal_id, home=home) is False

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=2) != 0
        assert owner_stop.reconcile_owned_mcp(terminal_id, home=home) is True
        assert owner_stop.reconcile_owned_mcp(terminal_id, home=home) is True
        assert _record_files(home, terminal_id) == []
    finally:
        _kill_children([(process, marker)])


@pytest.mark.parametrize(
    "snapshot",
    [
        {**_SNAPSHOT, "pending_client": 1},
        {**_SNAPSHOT, "pending_server": 1},
        {**_SNAPSHOT, "input_tail_bytes": 1},
        {**_SNAPSHOT, "output_tail_bytes": 1},
        {**_SNAPSHOT, "unflushed_replies": 1},
        {**_SNAPSHOT, "output_busy": 1},
        {**_SNAPSHOT, "unsafe": "frame_observer_failed"},
        {key: value for key, value in _SNAPSHOT.items() if key != "physical_eof"},
        {key: value for key, value in _SNAPSHOT.items() if key != "unsafe"},
    ],
)
def test_normal_return_requires_complete_settled_transport_snapshot(snapshot):
    assert owner_stop._settled_snapshot(snapshot) is None


def test_settled_snapshot_has_only_the_two_admitted_origins():
    assert owner_stop._settled_snapshot(_SNAPSHOT)[0] == "peer_eof"
    local_stop = {
        **_SNAPSHOT,
        "physical_eof": False,
        "local_input_closed": True,
        "signal_count": 1,
    }
    assert owner_stop._settled_snapshot(local_stop)[0] == "local_stop_after_signal"
    assert owner_stop._settled_snapshot({**local_stop, "signal_count": 0}) is None
    assert owner_stop._settled_snapshot({**local_stop, "physical_eof": True}) is None


def test_invalid_post_signal_snapshot_does_not_publish_a_normal_receipt(tmp_path):
    home = _private_home(tmp_path)
    terminal_id = "e10f20a3"
    script = _write_script(tmp_path, reject_unsettled_on_signal=True)
    config, values = _config_entry(home, terminal_id, script, "unit-token")
    result_file = home / "tmp" / "publish-result"
    values["TEST_RESULT_FILE"] = str(result_file)
    process, marker = _start_child(home, terminal_id, script, values)
    try:
        _write_config(home, terminal_id, {"cao-mcp-server": config})
        result = owner_stop.prestop_owned_mcp(terminal_id, time.monotonic() + 2.0, home=home)
        assert result.outcome is owner_stop.PrestopOutcome.RETIRED_UNSEALED
        assert process.wait(timeout=1) == 0
        assert result_file.read_text(encoding="ascii") == "False"
        assert not list((home / "tmp" / f"{terminal_id}.mcp-owners").glob("*.normal.json"))
    finally:
        _kill_children([(process, marker)])
