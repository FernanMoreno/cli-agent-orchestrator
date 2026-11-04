"""Actual built-UI policy journey over authenticated HTTP and PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from test.conftest import mint_test_token
from test.fixtures.cao_server import _JWKSServer
from test.postgres.test_admission_bootstrap import resource_target
from typing import Any, NoReturn, cast
from uuid import uuid4

import httpx
import psutil
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from psycopg import sql
from starlette.staticfiles import StaticFiles

from cli_agent_orchestrator.api.admission_routes import router as admission_router
from cli_agent_orchestrator.api.browser_auth_routes import router as browser_auth_router
from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
    ResourceEnvelopeRepository,
)
from cli_agent_orchestrator.clients.admission_policy_repository import (
    AdmissionPolicyRepository,
)
from cli_agent_orchestrator.clients.admission_policy_schema import ADMISSION_POLICY_TABLE
from cli_agent_orchestrator.clients.admission_policy_sdk import AdmissionPolicyClient
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyUpdate,
    AdmissionPolicyValues,
)
from cli_agent_orchestrator.services.admission_bootstrap import (
    ResourceBootstrap,
    capture_server_binding,
)
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy as RuntimePolicy
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.local_peer_identity import LocalProcessIdentity

pytestmark = [pytest.mark.e2e, pytest.mark.postgres]

_BASE_REVISION = 2**53 + 1001
_DRAFT_MEMORY = 2**53 + 1053
_DRAFT_CPU = 2**53 + 1055
_EXTERNAL_CPU = 2**53 + 1005
_ACTOR_ID = "principal-" + "0" * 64
_POLICY_FIELDS = (
    "memory_per_delivery_bytes",
    "cpu_per_delivery_millicores",
    "memory_reserve_percent",
    "cpu_reserve_percent",
    "max_sample_age_s",
    "postgres_reserve_percent",
    "postgres_statement_timeout_ms",
    "sample_interval_s",
    "healthy_samples_before_growth",
)
_REQUIRED_SOURCE_FILES = frozenset(
    {
        "web/src/api.ts",
        "web/src/admissionPolicyApi.ts",
        "web/src/test/admission-policy-api.test.ts",
        "web/src/App.tsx",
        "web/src/components/AdmissionPolicySettings.tsx",
        "web/src/test/admission-policy-settings.test.tsx",
    }
)
_BROWSER_ENV = {
    "dist": "CAO_ADMISSION_BROWSER_DIST",
    "manifest": "CAO_ADMISSION_BROWSER_MANIFEST",
    "node": "CAO_ADMISSION_BROWSER_NODE",
    "playwright": "CAO_ADMISSION_BROWSER_PLAYWRIGHT_MODULE",
    "chromium": "CAO_ADMISSION_BROWSER_CHROMIUM",
}
_BROWSER_EVENT_TIMEOUT_S = 60.0


def _fail_closed(message: str) -> NoReturn:
    pytest.fail(f"Browser acceptance preflight failed: {message}", pytrace=False)
    raise AssertionError("pytest.fail unexpectedly returned")


def _configured_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        _fail_closed(f"{name} is required")
    path = Path(value)
    if not path.is_absolute():
        _fail_closed(f"{name} must be an absolute path")
    return path.resolve(strict=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_paths(value: object, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        _fail_closed(f"build manifest {label} must be a non-empty object")
    result: dict[str, str] = {}
    for name, digest in value.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            _fail_closed(f"build manifest has an invalid {label} entry")
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            _fail_closed(f"build manifest has an unsafe {label} path")
        result[name] = digest
    return result


def _check_file_set(
    root: Path, expected: dict[str, str], *, label: str, require_exact_tree: bool
) -> None:
    observed: set[str] = set()
    for name, digest in expected.items():
        candidate = root / Path(*PurePosixPath(name).parts)
        if candidate.is_symlink():
            _fail_closed(f"{label} file is a symlink")
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            _fail_closed(f"{label} file is missing or escapes its root")
        if _sha256(path) != digest:
            _fail_closed(f"{label} hash changed")
        observed.add(name)
    if require_exact_tree:
        actual: set[str] = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                _fail_closed(f"{label} contains a symlink")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
        if actual != observed:
            _fail_closed(f"{label} files differ from the manifest")


def _validate_browser_inputs() -> (
    tuple[Path, Path, Path, Path, Path, dict[str, str], dict[str, str], str]
):
    dist = _configured_path(_BROWSER_ENV["dist"])
    manifest_path = _configured_path(_BROWSER_ENV["manifest"])
    node = _configured_path(_BROWSER_ENV["node"])
    playwright = _configured_path(_BROWSER_ENV["playwright"])
    chromium = _configured_path(_BROWSER_ENV["chromium"])
    for name, executable in (("Node", node), ("Chromium", chromium)):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            _fail_closed(f"configured {name} executable is not runnable")
    if not playwright.is_file():
        _fail_closed("configured Playwright module is missing")
    runner = Path(__file__).resolve().parents[1] / "fixtures" / "admission_policy_browser.mjs"
    if not runner.is_file():
        _fail_closed("browser acceptance runner is missing")
    if not (dist / "index.html").is_file():
        _fail_closed("built UI index.html is missing")

    try:
        manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail_closed("build manifest cannot be read")
    if not isinstance(manifest, dict):
        _fail_closed("build manifest must be an object")
    if manifest.get("source_preserved") is not True or manifest.get("build_exit_code") != 0:
        _fail_closed("build did not preserve source or did not exit successfully")
    output = manifest.get("output")
    if not isinstance(output, str) or not Path(output).is_absolute():
        _fail_closed("build manifest output must be an absolute path")
    try:
        if Path(output).resolve(strict=True) != dist:
            _fail_closed("build manifest names a different output directory")
    except OSError:
        _fail_closed("build manifest output directory is unavailable")

    source_hashes = _manifest_paths(manifest.get("source_files"), label="source_files")
    artifact_hashes = _manifest_paths(manifest.get("artifacts"), label="artifacts")
    missing_sources = _REQUIRED_SOURCE_FILES.difference(source_hashes)
    if missing_sources:
        _fail_closed("build manifest omits required admission UI sources")
    if "index.html" not in artifact_hashes:
        _fail_closed("build manifest omits index.html")

    repository = Path(__file__).resolve().parents[2]
    _check_file_set(repository, source_hashes, label="source", require_exact_tree=False)
    _check_file_set(dist, artifact_hashes, label="build artifact", require_exact_tree=True)
    tracked_web_files = (
        subprocess.run(
            ["git", "ls-files", "-z", "web"],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    if not {name for name in tracked_web_files if name}.issubset(source_hashes):
        _fail_closed("build manifest omits tracked web source files")
    return (
        dist,
        manifest_path,
        node,
        playwright,
        chromium,
        source_hashes,
        artifact_hashes,
        _sha256(manifest_path),
    )


def _write_private_bearer(directory: Path, token: str) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "browser-bearer.jwt"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(token.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _seed_large_policy_history(target, profile_id: str) -> AdmissionPolicyValues:
    policy = AdmissionPolicyValues(
        memory_per_delivery_bytes=_BASE_REVISION + 1,
        cpu_per_delivery_millicores=_BASE_REVISION + 2,
        memory_reserve_percent=20,
        cpu_reserve_percent=20,
        max_sample_age_s=3.0,
        postgres_reserve_percent=20,
        postgres_statement_timeout_ms=500,
        sample_interval_s=1.0,
        healthy_samples_before_growth=3,
    )
    fields = (
        "profile_id",
        "revision",
        "active",
        *_POLICY_FIELDS,
        "actor_principal_id",
    )
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(target.schema, ADMISSION_POLICY_TABLE),
        sql.SQL(",").join(map(sql.Identifier, fields)),
        sql.SQL(",").join(sql.Placeholder() for _ in fields),
    )
    row_template: tuple[object, ...] = (
        profile_id,
        0,
        True,
        *(getattr(policy, name) for name in _POLICY_FIELDS),
        _ACTOR_ID,
    )
    from cli_agent_orchestrator.clients.postgres import connect

    with connect(target.migration_target()) as owner:
        owner.executemany(
            statement,
            (
                row_template[:1] + (revision,) + row_template[2:]
                for revision in range(_BASE_REVISION - 20, _BASE_REVISION + 1)
            ),
        )
    return policy


def _wait_child_event(
    process: subprocess.Popen[str],
    expected: str,
    descendants: dict[int, tuple[float, int]],
) -> None:
    if process.stdout is None:
        _fail_closed("browser child stdout is unavailable")
    deadline = time.monotonic() + _BROWSER_EVENT_TIMEOUT_S
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if process.poll() is None:
                descendants.update(_capture_owned_descendants(process))
            remaining = max(0.0, deadline - time.monotonic())
            if not selector.select(timeout=min(0.05, remaining)):
                continue
            line = process.stdout.readline()
            event = line.rstrip("\r\n")
            if event == expected:
                return
            if event.startswith("FAIL:"):
                _fail_closed(f"browser journey failed at {event.removeprefix('FAIL:')}")
            _fail_closed("browser child emitted an unexpected protocol event")
    _fail_closed("browser child did not reach the expected journey boundary")


def _wait_child_result(
    process: subprocess.Popen[str], descendants: dict[int, tuple[float, int]]
) -> dict[str, object]:
    if process.stdout is None:
        _fail_closed("browser child stdout is unavailable")
    deadline = time.monotonic() + _BROWSER_EVENT_TIMEOUT_S
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if process.poll() is None:
                descendants.update(_capture_owned_descendants(process))
            remaining = max(0.0, deadline - time.monotonic())
            if not selector.select(timeout=min(0.05, remaining)):
                continue
            line = process.stdout.readline().rstrip("\r\n")
            if line.startswith("FAIL:"):
                _fail_closed(f"browser journey failed at {line.removeprefix('FAIL:')}")
            if line.startswith("RESULT:"):
                try:
                    result = json.loads(line.removeprefix("RESULT:"))
                except json.JSONDecodeError:
                    _fail_closed("browser child returned an invalid acceptance receipt")
                if not isinstance(result, dict):
                    _fail_closed("browser child returned an invalid acceptance receipt")
                return cast(dict[str, object], result)
            _fail_closed("browser child emitted an unexpected protocol event")
    _fail_closed("browser child did not return an acceptance receipt")


def _capture_owned_descendants(process: subprocess.Popen[str]) -> dict[int, tuple[float, int]]:
    if process.poll() is not None:
        return {}
    try:
        root = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        if process.poll() is not None:
            return {}
        _fail_closed("browser child identity could not be inspected")
    except psutil.AccessDenied:
        _fail_closed("browser child identity inspection was denied")
    try:
        descendants = root.children(recursive=True)
    except psutil.NoSuchProcess:
        if process.poll() is not None:
            return {}
        _fail_closed("browser child tree could not be inspected")
    except psutil.AccessDenied:
        _fail_closed("browser child tree inspection was denied")
    captured: dict[int, tuple[float, int]] = {}
    for descendant in descendants:
        try:
            birth_time = descendant.create_time()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            _fail_closed("browser descendant identity inspection was denied")
        try:
            group_id = os.getpgid(descendant.pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            _fail_closed("browser descendant process-group inspection was denied")
        try:
            if descendant.create_time() != birth_time:
                continue
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            _fail_closed("browser descendant identity inspection was denied")
        captured[descendant.pid] = (birth_time, group_id)
    return captured


def _descendant_is_running(pid: int, identity: tuple[float, int]) -> bool:
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        _fail_closed("browser descendant identity inspection was denied")
    try:
        if process.create_time() != identity[0]:
            return False
        return process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        _fail_closed("browser descendant status inspection was denied")


def _descendant_exists(pid: int, identity: tuple[float, int]) -> bool:
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        _fail_closed("browser descendant identity inspection was denied")
    try:
        return process.create_time() == identity[0]
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        _fail_closed("browser descendant identity inspection was denied")


def _current_process_group(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except ProcessLookupError:
        return None
    except PermissionError:
        _fail_closed("browser descendant process-group inspection was denied")


def _verified_current_group(pid: int, identity: tuple[float, int]) -> int | None:
    if not _descendant_is_running(pid, identity):
        return None
    group_id = _current_process_group(pid)
    if group_id is None:
        return None
    try:
        if psutil.Process(pid).create_time() != identity[0]:
            return None
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        _fail_closed("browser descendant identity inspection was denied")
    return group_id


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        _fail_closed("browser process-group state inspection was denied")


def _signal_owned_descendants(captured: dict[int, tuple[float, int]], signum: int) -> None:
    groups: set[int] = set()
    for pid, identity in captured.items():
        current_group = _verified_current_group(pid, identity)
        if current_group is None:
            continue
        if current_group != identity[1]:
            # The exact child is still ours, but its old group id may have been reused.
            try:
                process = psutil.Process(pid)
                if process.create_time() == identity[0]:
                    process.send_signal(signum)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                _fail_closed("browser descendant signal was denied")
            continue
        groups.add(identity[1])
    for group_id in groups:
        member_proven = any(
            identity[1] == group_id and _verified_current_group(pid, identity) == group_id
            for pid, identity in captured.items()
        )
        if not member_proven:
            continue
        try:
            os.killpg(group_id, signum)
        except ProcessLookupError:
            pass
        except PermissionError:
            _fail_closed("browser descendant process-group signal was denied")


def _retire_owned_descendants(captured: dict[int, tuple[float, int]]) -> None:
    _signal_owned_descendants(captured, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        _descendant_exists(pid, identity) for pid, identity in captured.items()
    ):
        time.sleep(0.01)
    if any(_descendant_exists(pid, identity) for pid, identity in captured.items()):
        _signal_owned_descendants(captured, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(
            _descendant_exists(pid, identity) for pid, identity in captured.items()
        ):
            time.sleep(0.01)
    assert not any(
        _descendant_exists(pid, identity) for pid, identity in captured.items()
    ), "owned browser descendants did not exit after cleanup"
    for group_id in {identity[1] for identity in captured.values()}:
        if _process_group_exists(group_id):
            _fail_closed("an observed browser process group remained after retirement")


def _signal_live_browser_group(process: subprocess.Popen[str], signum: int) -> None:
    if process.poll() is not None:
        return
    try:
        group_id = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    except PermissionError:
        _fail_closed("browser process-group identity inspection was denied")
    if group_id != process.pid:
        _fail_closed("browser child left its private process group")
    try:
        os.killpg(group_id, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        _fail_closed("browser private process-group signal was denied")


def _wait_for_browser_exit(
    process: subprocess.Popen[str],
    descendants: dict[int, tuple[float, int]],
    timeout: float,
) -> int | None:
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        descendants.update(_capture_owned_descendants(process))
        time.sleep(0.02)
    return process.poll()


def _run_browser_journey(
    *,
    repository: Path,
    node: Path,
    playwright: Path,
    chromium: Path,
    base_url: str,
    bearer_file: Path,
    private_home: Path,
    private_tmp: Path,
    client: httpx.Client,
    token: str,
) -> dict[str, object]:
    private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_home.chmod(0o700)
    private_tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_tmp.chmod(0o700)
    child_env = {
        "PATH": os.pathsep.join((str(node.parent), "/usr/bin", "/bin")),
        "HOME": str(private_home),
        "TMPDIR": str(private_tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CAO_ADMISSION_BROWSER_APP_URL": base_url,
        "CAO_ADMISSION_BROWSER_BEARER_FILE": str(bearer_file),
        "CAO_ADMISSION_BROWSER_PLAYWRIGHT_MODULE": str(playwright),
        "CAO_ADMISSION_BROWSER_CHROMIUM": str(chromium),
    }
    runner = repository / "test" / "fixtures" / "admission_policy_browser.mjs"
    process: subprocess.Popen[str] | None = None
    descendants: dict[int, tuple[float, int]] = {}
    try:
        process = subprocess.Popen(
            [str(node), str(runner)],
            cwd=repository,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            start_new_session=True,
        )
        _wait_child_event(process, "READY_CONFLICT", descendants)
        descendants.update(_capture_owned_descendants(process))
        if not descendants:
            _fail_closed("browser child had no captured browser descendants")
        values = AdmissionPolicyValues(
            memory_per_delivery_bytes=_DRAFT_MEMORY,
            cpu_per_delivery_millicores=_EXTERNAL_CPU,
            memory_reserve_percent=20,
            cpu_reserve_percent=20,
            max_sample_age_s=3.0,
            postgres_reserve_percent=20,
            postgres_statement_timeout_ms=500,
            sample_interval_s=1.0,
            healthy_samples_before_growth=3,
        )
        client_sdk = AdmissionPolicyClient(client, lambda: token)
        changed = client_sdk.update(
            AdmissionPolicyUpdate(expected_revision=_BASE_REVISION, policy=values)
        )
        assert changed.revision == _BASE_REVISION + 1
        assert changed.policy.cpu_per_delivery_millicores == _EXTERNAL_CPU
        assert process.stdin is not None
        process.stdin.write("continue\n")
        process.stdin.flush()
        receipt = _wait_child_result(process, descendants)
        descendants.update(_capture_owned_descendants(process))
        returncode = _wait_for_browser_exit(process, descendants, 5)
        if returncode is None:
            raise subprocess.TimeoutExpired(process.args, timeout=5)
        assert returncode == 0, "browser child did not exit successfully"
        assert not _process_group_exists(process.pid), "browser process group did not retire"
        return receipt
    except subprocess.TimeoutExpired:
        _fail_closed("browser child did not physically exit")
    finally:
        if process is not None:
            primary_error = sys.exc_info()[1]
            capture_error: BaseException | None = None
            if process.poll() is None:
                try:
                    descendants.update(_capture_owned_descendants(process))
                except BaseException as exc:
                    capture_error = exc
            if process.poll() is None:
                try:
                    _signal_live_browser_group(process, signal.SIGTERM)
                except BaseException as exc:
                    capture_error = capture_error or exc
                    if process.poll() is None:
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                        except BaseException as fallback_exc:
                            capture_error = capture_error or fallback_exc
                try:
                    graceful_returncode = _wait_for_browser_exit(process, descendants, 10)
                except BaseException as exc:
                    capture_error = capture_error or exc
                    graceful_returncode = process.poll()
                if graceful_returncode is None:
                    try:
                        _signal_live_browser_group(process, signal.SIGKILL)
                    except BaseException as exc:
                        capture_error = capture_error or exc
                        if process.poll() is None:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                            except BaseException as fallback_exc:
                                capture_error = capture_error or fallback_exc
                    try:
                        killed_returncode = _wait_for_browser_exit(process, descendants, 10)
                    except BaseException as exc:
                        capture_error = capture_error or exc
                        killed_returncode = process.poll()
                    if killed_returncode is None:
                        try:
                            killed_returncode = process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            capture_error = capture_error or AssertionError(
                                "browser Node process did not exit after SIGKILL"
                            )
            try:
                _retire_owned_descendants(descendants)
            except BaseException as exc:
                capture_error = capture_error or exc
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except BaseException as exc:
                    capture_error = capture_error or exc
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except BaseException as exc:
                    capture_error = capture_error or exc
            if capture_error is not None:
                if primary_error is None:
                    raise capture_error
                primary_error.add_note(f"Browser child cleanup issue: {capture_error}")


@pytest.fixture(autouse=True)
def browser_acceptance_inputs():
    return _validate_browser_inputs()


def test_built_browser_exact_policy_conflict_revoke_and_history(
    resource_target,
    auth_enabled_env,
    rsa_keys,
    monkeypatch,
    request,
    tmp_path,
    tmp_path_factory,
    browser_acceptance_inputs,
):
    target = resource_target
    (
        dist,
        manifest_path,
        node,
        playwright,
        chromium,
        source_hashes,
        artifact_hashes,
        manifest_hash,
    ) = browser_acceptance_inputs
    from cli_agent_orchestrator.clients.admission_policy_schema import (
        ADMISSION_POLICY_MIGRATIONS,
    )

    migrate_component(target, ADMISSION_POLICY_MIGRATIONS)
    profile_id = str(uuid4())
    _seed_large_policy_history(target, profile_id)
    policy_repository = AdmissionPolicyRepository(
        profile_id=profile_id, target_supplier=lambda: target
    )
    identity = LocalProcessIdentity(
        str(uuid4()),
        str(uuid4()),
        os.getpid(),
        psutil.Process().create_time(),
        "owned-browser-policy-acceptance",
        "127.0.0.1",
        0,
        "test-only-public-identity",
    )
    envelope_repository = ResourceEnvelopeRepository(target_supplier=lambda: target)
    bootstrap = ResourceBootstrap(
        repository=envelope_repository,
        binding_supplier=lambda selected: capture_server_binding(
            selected, identity_supplier=lambda: identity
        ),
        instance_supplier=lambda: identity.instance_id,
    )
    owner, storage_control = DeliveryAdmission.with_storage_control(RuntimePolicy())
    monitor = AdmissionMonitor(
        owner,
        storage_control=storage_control,
        policy_loader=AdmissionPolicyLoader(repository=policy_repository),
        bootstrap=bootstrap,
        postgres=PostgresPressureSampler(target_supplier=lambda: target),
        scope_id="owned-browser-policy-acceptance",
        local_storage_capacity=lambda: 4,
    )
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        '{"version":1,"policy":{"cpu_per_delivery_millicores":600}}',
        encoding="utf-8",
    )
    policy_file.chmod(0o600)
    runtime = InboxAdmissionRuntime(
        InboxService(),
        monitor=monitor,
        bootstrap=bootstrap,
        policy_repository=policy_repository,
        policy_file=policy_file,
    )

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.inbox_admission_runtime = runtime
    attempt_counts = {"put": 0, "revoke": 0}
    attempt_guard = threading.Lock()

    @app.middleware("http")
    async def count_policy_mutations(request, call_next):
        if request.url.path in (
            "/admission/policy",
            "/admission/policy/revoke",
        ):
            with attempt_guard:
                if request.method == "PUT":
                    attempt_counts["put"] += 1
                elif request.method == "POST" and request.url.path.endswith("/revoke"):
                    attempt_counts["revoke"] += 1
        return await call_next(request)

    @app.get("/api/fleet")
    async def absent_local_fleet():
        raise HTTPException(status_code=404, detail="No remote fleet is configured")

    app.include_router(browser_auth_router)
    app.include_router(admission_router)
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="built-web")

    jwks = _JWKSServer(rsa_keys[1])
    listener = socket.socket()
    server = None
    server_thread = None
    token = mint_test_token(
        rsa_keys[0], subject="browser-policy-operator", scopes="cao:read cao:write cao:admin"
    )
    bearer_file = _write_private_bearer(tmp_path / "browser-private", token)
    base_url = ""
    process_home = tmp_path / "browser-child-home"
    # Chromium's SingletonSocket must fit the Unix-domain socket path limit.
    # Keep this short directory inside pytest's existing owned temporary root.
    process_tmp = tmp_path_factory.mktemp("cb")
    try:
        jwks.start()
        monkeypatch.setenv("CAO_AUTH_JWKS_URI", jwks.url)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        server_thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]),
            name="owned-browser-policy-api",
            daemon=True,
        )
        server_thread.start()
        deadline = time.monotonic() + 30
        while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "owned browser acceptance API did not start"

        with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
            assert client.get("/auth/config").json() == {"mode": "bearer"}
            policy_response = client.get(
                "/admission/policy",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert policy_response.status_code == 200
            initial_state = policy_response.json()
            assert initial_state["persisted"]["revision"] == _BASE_REVISION
            assert initial_state["persisted"]["policy"]["memory_per_delivery_bytes"] == (
                _BASE_REVISION + 1
            )
            assert initial_state["applied_revision"] is None

            receipt = _run_browser_journey(
                repository=Path(__file__).resolve().parents[2],
                node=node,
                playwright=playwright,
                chromium=chromium,
                base_url=base_url,
                bearer_file=bearer_file,
                private_home=process_home,
                private_tmp=process_tmp,
                client=client,
                token=token,
            )

        with attempt_guard:
            assert attempt_counts == {"put": 3, "revoke": 1}
        assert isinstance(receipt["node"], str) and receipt["node"].startswith("v")
        assert isinstance(receipt["playwright"], str) and receipt["playwright"]
        assert isinstance(receipt["chromium"], str) and receipt["chromium"]
        assert receipt["exactJsonSupport"] is True
        assert receipt["browserPutRequests"] == 2
        assert receipt["exactIntegerTokens"] == 6
        assert receipt["conflictStatus"] == 409
        assert receipt["saveStatus"] == 200
        assert receipt["revokeStatus"] == 200
        assert receipt["appliedRevisionUnknown"] is True
        assert receipt["historyCursor"] == str(_BASE_REVISION - 16)
        assert receipt["historyCursorExact"] is True
        request.node.user_properties.append(
            ("admission_browser_receipt", json.dumps(receipt, sort_keys=True))
        )

        latest = policy_repository.read_latest(target)
        assert latest is not None
        assert latest.revision == _BASE_REVISION + 3
        assert latest.active is False
        assert latest.policy.memory_per_delivery_bytes == _DRAFT_MEMORY
        assert latest.policy.cpu_per_delivery_millicores == _DRAFT_CPU
        assert policy_repository.history(target, limit=100).entries[0].revision == (
            _BASE_REVISION + 3
        )
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_errors: list[BaseException] = []
        try:
            bearer_file.unlink(missing_ok=True)
        except BaseException as exc:
            cleanup_errors.append(exc)
        if server is not None:
            server.should_exit = True
        if server_thread is not None and server_thread.ident is not None:
            try:
                server_thread.join(timeout=60)
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            listener.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if server_thread is not None and server_thread.ident is not None:
            try:
                server_thread.join(timeout=5)
            except BaseException as exc:
                cleanup_errors.append(exc)
            if server_thread.is_alive():
                cleanup_errors.append(AssertionError("owned browser API worker did not retire"))
        try:
            jwks.stop()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            message = "; ".join(str(error) for error in cleanup_errors)
            if primary_error is None:
                raise AssertionError(f"Browser API cleanup failed: {message}")
            primary_error.add_note(f"Browser API cleanup issue: {message}")
    assert runtime.retired and monitor.retired

    # Browser execution must not mutate either the source or the artifact it loaded.
    _check_file_set(
        Path(__file__).resolve().parents[2],
        source_hashes,
        label="source after browser",
        require_exact_tree=False,
    )
    _check_file_set(dist, artifact_hashes, label="build after browser", require_exact_tree=True)
    assert manifest_path.is_file() and _sha256(manifest_path) == manifest_hash
