"""Offline contracts for the fixture-only receipt-stage API observer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path
from test.live_provider import receipt_verification_observer as observer

import pytest

REPO = Path(__file__).resolve().parents[2]
SOURCE_FILES = (
    "src/cli_agent_orchestrator/providers/opencode_cli.py",
    "src/cli_agent_orchestrator/services/status_monitor.py",
    "src/cli_agent_orchestrator/services/terminal_service.py",
    "src/cli_agent_orchestrator/utils/receipt_verification_diagnostics.py",
)
SECRET_CANARIES = (
    "receipt-observer-private-body-canary",
    "receipt-observer-terminal-id-canary",
    "receipt-observer-transcript-canary",
)


def _source_hashes(repo: Path = REPO) -> dict[str, str]:
    return {
        relative: hashlib.sha256((repo / relative).read_bytes()).hexdigest()
        for relative in SOURCE_FILES
    }


def test_fixture_pair_allowlist_matches_the_pinned_producer_module() -> None:
    source = REPO / SOURCE_FILES[-1]
    spec = importlib.util.spec_from_file_location("t133_receipt_diagnostics", source)
    assert spec is not None and spec.loader is not None
    diagnostics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostics)
    assert observer._ALLOWED_OUTCOMES == diagnostics._ALLOWED_OUTCOMES


def _make_writer(raw_dir: Path, role: str = "api_a"):
    writer = observer._LedgerWriter(raw_dir, role)
    return writer, writer.emit


def _worker_env(pythonpath: str, home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": os.defpath,
        "PYTHONPATH": pythonpath,
        "PYTHONDONTWRITEBYTECODE": "1",
    }


_API_SERVICE_WORKER = r"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import cli_agent_orchestrator.api.main as api_main
from cli_agent_orchestrator.services import terminal_service as terminal
from cli_agent_orchestrator.utils import receipt_verification_diagnostics as diagnostics

assert api_main.app is not None
assert diagnostics.receipt_verification_diagnostics_enabled()
assert terminal.set_receipt_verification_diagnostic_sink is diagnostics.set_receipt_verification_diagnostic_sink

from cli_agent_orchestrator.models.terminal import TerminalStatus

class FakeReceiptProvider:
    requires_turn_receipt = True
    supports_canonical_turn_receipt_result = False

    def pending_turn_receipt_state(self):
        return {"generation": "generation-canary", "receipt_sha256": "a" * 64}

    def receipt_result_terminal_status(self, _transcript, _result):
        return TerminalStatus.COMPLETED

    def mark_turn_receipt_result_verified(self):
        self.marked = True

provider = FakeReceiptProvider()
provider.marked = False
terminal.settle_terminal_turn_receipt_result = lambda *_args, **_kwargs: True
terminal.status_monitor.publish_observed_status = lambda *_args, **_kwargs: None

transcript = "receipt-observer-transcript-canary — 片"
result = "receipt-observer-private-body-canary — 片\nCAO-TURN-RECEIPT-" + "b" * 32
with diagnostics.receipt_verification_attempt(2):
    public_result = terminal._verify_receipt_bearing_result.__wrapped__(
        "receipt-observer-terminal-id-canary",
        provider,
        transcript,
        result,
    )
assert "receipt-observer-private-body-canary" in public_result
assert provider.marked is True

# Exercise the real Uvicorn serve/_serve shutdown path with a local fake server
# lifecycle. The observer must seal before serve returns, ahead of signal
# re-raise in Uvicorn's outer capture_signals context.
import receipt_verification_observer as fixture_observer
import uvicorn

bootstrap_dir = Path(fixture_observer.__file__).resolve().parent
config = json.loads((bootstrap_dir / fixture_observer.CONFIG_FILENAME).read_text())
assert os.environ["PYTHONPATH"] == os.pathsep.join(
    (str(Path.cwd() / "src"), str(Path.cwd()))
)
server = uvicorn.Server(uvicorn.Config("unused_fixture_app:app", lifespan="off"))
server.config.loaded = True
server.config.lifespan_class = lambda _config: object()
server.should_exit = False
server_calls = []

async def startup(*, sockets=None):
    server_calls.append("startup")

async def main_loop():
    server_calls.append("main_loop")
    if config["role"] == "api_b":
        raise server_error

async def shutdown(*, sockets=None):
    server_calls.append("shutdown")

server.startup = startup
server.main_loop = main_loop
server.shutdown = shutdown
server_error = RuntimeError("server-loop-canary")
if config["role"] == "api_a":
    asyncio.run(server.serve())
    assert server_calls == ["startup", "main_loop", "shutdown"]
else:
    try:
        asyncio.run(server.serve())
    except RuntimeError as error:
        assert error is server_error
    else:
        raise AssertionError("Uvicorn serve failure was not preserved")
    assert server_calls == ["startup", "main_loop"]
assert (
    Path(config["raw_dir"]) / config["role"] / fixture_observer.SEAL_FILENAME
).is_file()
assert diagnostics.receipt_verification_diagnostics_enabled() is False

# A Python descendant inherits the sanitized environment, not the API-only
# bootstrap. It can import the app modules but cannot claim this API ledger.
child_code = r'''
import os
from pathlib import Path
from cli_agent_orchestrator.utils import receipt_verification_diagnostics as child_diagnostics
assert os.environ["PYTHONPATH"] == os.pathsep.join(
    (str(Path.cwd() / "src"), str(Path.cwd()))
)
assert child_diagnostics.receipt_verification_diagnostics_enabled() is False
child_diagnostics.emit_receipt_verification_event("attempt", "started")
print("child-observer-off")
'''
child = subprocess.run(
    [sys.executable, "-c", child_code],
    cwd=Path.cwd(),
    env=os.environ.copy(),
    text=True,
    capture_output=True,
    timeout=15,
    check=True,
)
assert child.stdout.strip() == "child-observer-off"
print("service-observer-ok")
"""


@pytest.mark.parametrize("role", ["api_a", "api_b"])
def test_real_api_import_service_stages_write_private_role_ledger(
    tmp_path: Path, role: str
) -> None:
    bootstrap = tmp_path / f"bootstrap-{role}"
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    pythonpath = observer.prepare_api_observer(
        bootstrap,
        raw,
        REPO,
        _source_hashes(),
        role,
    )
    result = subprocess.run(
        [sys.executable, "-c", _API_SERVICE_WORKER],
        cwd=REPO,
        env=_worker_env(pythonpath, tmp_path / f"home-{role}"),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )

    assert result.stdout.strip() == "service-observer-ok"
    assert not any(canary in result.stdout + result.stderr for canary in SECRET_CANARIES)
    summary = observer.read_api_observation(raw, role=role, writers_dead=True)
    assert summary["observer_status"] == "complete"
    assert summary["role"] == role
    assert summary["event_count"] >= 6
    assert summary["failed_event_count"] == 0
    assert summary["stage_outcome_counts"]["receipt_witness"]["passed"] == 1
    assert summary["stage_outcome_counts"]["durable_cas"]["settled"] == 1

    role_dir = raw / role
    events_path = role_dir / observer.EVENT_FILENAME
    seal_path = role_dir / observer.SEAL_FILENAME
    assert stat.S_IMODE(role_dir.stat().st_mode) == 0o700
    for path in (events_path, seal_path):
        info = path.lstat()
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_nlink == 1
        assert info.st_uid == os.getuid()
    assert stat.S_IMODE(bootstrap.stat().st_mode) == 0o700
    for name in (
        observer.HELPER_FILENAME,
        observer.CONFIG_FILENAME,
        observer.BOOTSTRAP_FILENAME,
    ):
        private_file = bootstrap / name
        private_info = private_file.lstat()
        assert stat.S_IMODE(private_info.st_mode) == 0o600
        assert private_info.st_nlink == 1
        assert private_info.st_uid == os.getuid()
    raw_events = events_path.read_bytes()
    summary_json = json.dumps(summary, ensure_ascii=False)
    assert not any(canary.encode() in raw_events for canary in SECRET_CANARIES)
    assert not any(canary in summary_json for canary in SECRET_CANARIES)
    rows = [json.loads(line) for line in raw_events.splitlines()]
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert all("thread_id" not in row and "terminal_id" not in row for row in rows)
    assert {row["attempt_ordinal"] for row in rows} == {2}


def test_api_ledgers_are_role_separated_and_unavailable_while_writer_is_live(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    writer_a, sink_a = _make_writer(raw, "api_a")
    writer_b, sink_b = _make_writer(raw, "api_b")
    sink_a(0, "attempt", "started")
    sink_b(3, "durable_cas", "settled")

    unavailable = observer.read_api_observation(raw, role="api_a", writers_dead=False)
    assert unavailable["observer_status"] == "unavailable"
    writer_a.close()
    writer_b.close()

    summary_a = observer.read_api_observation(raw, role="api_a", writers_dead=True)
    summary_b = observer.read_api_observation(raw, role="api_b", writers_dead=True)
    assert summary_a["observer_status"] == "complete"
    assert summary_b["observer_status"] == "complete"
    assert summary_a["event_count"] == summary_b["event_count"] == 1
    assert summary_a["stage_outcome_counts"]["attempt"]["started"] == 1
    assert summary_b["stage_outcome_counts"]["durable_cas"]["settled"] == 1
    assert summary_a["role"] != summary_b["role"]


def test_private_bootstrap_is_not_prepared_for_wrong_source_or_symlink(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    hashes = _source_hashes()
    hashes[SOURCE_FILES[0]] = "0" * 64
    with pytest.raises(RuntimeError):
        observer.prepare_api_observer(tmp_path / "bad-source-bootstrap", raw, REPO, hashes, "api_a")
    assert not (tmp_path / "bad-source-bootstrap").exists()

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    os.chmod(target, 0o700)
    link = tmp_path / "bootstrap-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError):
        observer.prepare_api_observer(link, raw, REPO, _source_hashes(), "api_a")
    assert not list(target.iterdir())


def test_child_pythonpath_is_exact_and_ignores_environment_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    bootstrap = tmp_path / "bootstrap"
    monkeypatch.setenv("PYTHONPATH", "/untrusted/pythonpath-canary")

    child_pythonpath = observer.prepare_api_observer(
        bootstrap, raw, REPO, _source_hashes(), "api_a"
    )

    assert child_pythonpath == os.pathsep.join((str(bootstrap), str(REPO / "src"), str(REPO)))
    assert os.environ["PYTHONPATH"] == "/untrusted/pythonpath-canary"


def test_default_api_import_has_no_diagnostic_sink_or_ledger(tmp_path: Path) -> None:
    raw = tmp_path / "must-not-exist"
    code = r"""
import cli_agent_orchestrator.api.main as api_main
from cli_agent_orchestrator.utils import receipt_verification_diagnostics as diagnostics
assert api_main.app is not None
assert diagnostics.receipt_verification_diagnostics_enabled() is False
diagnostics.emit_receipt_verification_event("attempt", "started")
print("default-off")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=_worker_env(str(REPO / "src"), tmp_path / "ordinary-home"),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "default-off"
    assert not raw.exists()


def test_bootstrap_source_mismatch_aborts_before_user_code_without_details(
    tmp_path: Path,
) -> None:
    bootstrap = tmp_path / "bootstrap"
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    observer.prepare_api_observer(bootstrap, raw, REPO, _source_hashes(), "api_a")
    config_path = bootstrap / observer.CONFIG_FILENAME
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["source_sha256"][SOURCE_FILES[0]] = "0" * 64
    config_path.write_text(json.dumps(config), encoding="utf-8")
    os.chmod(config_path, 0o600)

    marker = tmp_path / "user-code-ran"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=_worker_env(
            f"{bootstrap}{os.pathsep}{REPO / 'src'}{os.pathsep}{REPO}",
            tmp_path / "home",
        ),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert not marker.exists()
    assert "private_observer_bootstrap_failed" in result.stderr
    assert "receipt-observer-private-body-canary" not in result.stderr
    assert (
        observer.read_api_observation(raw, role="api_a", writers_dead=True)["observer_status"]
        == "unavailable"
    )


def test_atexit_seals_api_epoch_when_uvicorn_serve_never_starts(tmp_path: Path) -> None:
    bootstrap = tmp_path / "bootstrap"
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    pythonpath = observer.prepare_api_observer(bootstrap, raw, REPO, _source_hashes(), "api_b")
    code = r"""
import cli_agent_orchestrator.api.main as api_main
from cli_agent_orchestrator.utils import receipt_verification_diagnostics as diagnostics
assert api_main.app is not None
assert diagnostics.receipt_verification_diagnostics_enabled()
with diagnostics.receipt_verification_attempt(3):
    diagnostics.emit_receipt_verification_event("attempt", "started")
print("atexit-only")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=_worker_env(pythonpath, tmp_path / "home"),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "atexit-only"
    summary = observer.read_api_observation(raw, role="api_b", writers_dead=True)
    assert summary["observer_status"] == "complete"
    assert summary["event_count"] == 1
    assert summary["stage_outcome_counts"]["attempt"]["started"] == 1


def test_core_emitter_swallows_baseexception_from_diagnostic_sink() -> None:
    code = r"""
from cli_agent_orchestrator.utils import receipt_verification_diagnostics as diagnostics
previous = diagnostics.set_receipt_verification_diagnostic_sink(
    lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt())
)
try:
    diagnostics.emit_receipt_verification_event("attempt", "started")
    with diagnostics.receipt_verification_diagnostic_stage("durable_cas") as finish:
        finish("settled")
finally:
    diagnostics.set_receipt_verification_diagnostic_sink(previous)
print("baseexception-contained")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=_worker_env(str(REPO / "src"), Path("/tmp")),
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert result.stdout.strip() == "baseexception-contained"


def test_concurrent_writer_orders_fixed_events_and_exports_no_thread_ids(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    writer, sink = _make_writer(raw)
    workers = 12
    per_worker = 30
    threads = [
        threading.Thread(target=lambda: [sink(1, "attempt", "started") for _ in range(per_worker)])
        for _ in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    writer.close()

    role_dir = raw / "api_a"
    events = (role_dir / observer.EVENT_FILENAME).read_bytes().splitlines()
    rows = [json.loads(line) for line in events]
    assert len(rows) == workers * per_worker
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert [row["monotonic_ms"] for row in rows] == sorted(row["monotonic_ms"] for row in rows)
    assert len({row["thread_ordinal"] for row in rows}) == workers
    assert all("thread_id" not in row and "ident" not in row for row in rows)

    summary = observer.read_api_observation(raw, role="api_a", writers_dead=True)
    assert summary["observer_status"] == "complete"
    assert summary["event_count"] == workers * per_worker


@pytest.mark.parametrize(
    "tamper",
    [
        "unknown_field",
        "duplicate_key",
        "sequence_gap",
        "bool_attempt",
        "unknown_pair",
        "seal_count",
    ],
)
def test_reader_rejects_tampered_event_or_seal(tmp_path: Path, tamper: str) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    writer, sink = _make_writer(raw)
    sink(1, "attempt", "started")
    writer.close()

    role_dir = raw / "api_a"
    events_path = role_dir / observer.EVENT_FILENAME
    seal_path = role_dir / observer.SEAL_FILENAME
    rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if tamper == "unknown_field":
        rows[0]["terminal_id"] = "must-not-be-accepted"
        events_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    elif tamper == "duplicate_key":
        original_line = events_path.read_bytes().splitlines()[0]
        duplicate_line = original_line.replace(b'"version":1', b'"version":1,"version":1', 1)
        events_path.write_bytes(duplicate_line + b"\n")
        seal["byte_count"] = len(duplicate_line) + 1
        seal_path.write_text(json.dumps(seal), encoding="utf-8")
    elif tamper == "sequence_gap":
        rows[0]["sequence"] = 2
        events_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    elif tamper == "bool_attempt":
        rows[0]["attempt_ordinal"] = True
        events_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    elif tamper == "unknown_pair":
        rows[0]["outcome"] = "unlisted-outcome"
        events_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    else:
        seal["event_count"] = 2
        seal_path.write_text(json.dumps(seal), encoding="utf-8")
    os.chmod(events_path, 0o600)
    os.chmod(seal_path, 0o600)

    assert (
        observer.read_api_observation(raw, role="api_a", writers_dead=True)["observer_status"]
        == "unavailable"
    )


@pytest.mark.parametrize("tamper", ["file_mode", "hardlink", "symlink", "directory_mode"])
def test_reader_rejects_non_private_ledger_paths(tmp_path: Path, tamper: str) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    writer, sink = _make_writer(raw)
    sink(0, "attempt", "started")
    writer.close()
    role_dir = raw / "api_a"
    events_path = role_dir / observer.EVENT_FILENAME

    if tamper == "file_mode":
        os.chmod(events_path, 0o640)
    elif tamper == "hardlink":
        os.link(events_path, tmp_path / "outside-link")
    elif tamper == "symlink":
        external = tmp_path / "external-events"
        events_path.rename(external)
        events_path.symlink_to(external)
    else:
        os.chmod(role_dir, 0o755)

    assert (
        observer.read_api_observation(raw, role="api_a", writers_dead=True)["observer_status"]
        == "unavailable"
    )


def test_reader_rejects_missing_seal_and_late_callback(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    writer, sink = _make_writer(raw)
    sink(0, "admission", "current_capture")
    writer.close()
    seal_path = raw / "api_a" / observer.SEAL_FILENAME
    seal_path.unlink()
    assert (
        observer.read_api_observation(raw, role="api_a", writers_dead=True)["observer_status"]
        == "unavailable"
    )

    late_raw = tmp_path / "late-raw"
    late_raw.mkdir(mode=0o700)
    os.chmod(late_raw, 0o700)
    late_writer, late_sink = _make_writer(late_raw)
    late_sink(0, "admission", "current_capture")
    late_writer.close()
    late_sink(0, "attempt", "started")
    assert (
        observer.read_api_observation(late_raw, role="api_a", writers_dead=True)["observer_status"]
        == "unavailable"
    )


def test_writer_overflow_never_projects_as_complete(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    writer, sink = _make_writer(raw)
    for _ in range(observer.MAX_EVENTS + 1):
        sink(0, "attempt", "started")
    writer.close()
    assert (
        observer.read_api_observation(raw, role="api_a", writers_dead=True)["observer_status"]
        == "unavailable"
    )
