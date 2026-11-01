"""Per-process SQL attribution is diagnostic, never session absence authority."""

import importlib
import importlib.util
import json
import os
import select
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.offline_database


def api():
    name = "cli_agent_orchestrator.utils.process_sql_origin"
    assert importlib.util.find_spec(name) is not None, "missing supervised SQL process origin"
    return importlib.import_module(name)


def fresh(monkeypatch, *, enabled=False):
    module = api()
    monkeypatch.setattr(module, "_STATE", module._State(os.getpid()))
    monkeypatch.delenv(module.RESOURCE_BINDING_FD_ENV, raising=False)
    if enabled:
        monkeypatch.setenv(module.RESOURCE_BINDING_FD_ENV, "123")
    return module


def test_legacy_process_keeps_its_original_application_name(monkeypatch):
    module = fresh(monkeypatch)
    assert module.current_sql_application_name() == "caos"
    assert module._STATE.name is None


def test_tag_is_stable_and_latches_after_the_private_fd_is_consumed(monkeypatch):
    module = fresh(monkeypatch, enabled=True)
    first = module.current_sql_application_name()
    assert first != "caos" and first.isascii() and len(first.encode()) < 63
    monkeypatch.delenv(module.RESOURCE_BINDING_FD_ENV)
    assert module.current_sql_application_name() == first
    assert module.current_sql_application_name() == first


def test_cold_threads_publish_one_origin(monkeypatch):
    module = fresh(monkeypatch, enabled=True)
    with ThreadPoolExecutor(max_workers=8) as workers:
        values = list(workers.map(lambda _: module.current_sql_application_name(), range(32)))
    assert len(set(values)) == 1 and values[0] != "caos"


def test_foreign_pid_state_is_replaced_before_its_held_lock(monkeypatch):
    module = fresh(monkeypatch)
    inherited = module._State(os.getpid() + 1, enabled=True)
    monkeypatch.setattr(module, "_STATE", inherited)
    inherited.guard.acquire()
    try:
        assert module.current_sql_application_name() != "caos"
        assert module._STATE is not inherited and module._STATE.pid == os.getpid()
    finally:
        inherited.guard.release()


def test_real_fork_preserves_mode_but_never_the_parents_tag(monkeypatch):
    module = fresh(monkeypatch, enabled=True)
    parent = module.current_sql_application_name()
    monkeypatch.delenv(module.RESOURCE_BINDING_FD_ENV)
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(reader)
        try:
            os.write(writer, module.current_sql_application_name().encode())
            os._exit(0)
        except BaseException:
            os._exit(19)
    os.close(writer)
    pin = os.pidfd_open(child)
    ready = []
    try:
        ready, _, _ = select.select([reader], [], [], 10)
        assert ready, "fork inherited a blocked origin lock"
        value = os.read(reader, 128).decode()
        assert value not in {parent, "caos"}
        assert module.current_sql_application_name() == parent
    finally:
        os.close(reader)
        if not ready:
            import signal

            signal.pidfd_send_signal(pin, signal.SIGKILL)
        os.close(pin)
        assert os.waitpid(child, 0) == (child, 0)


def test_all_database_targets_share_the_process_origin_without_opening_sql(monkeypatch, tmp_path):
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget

    module = fresh(monkeypatch, enabled=True)
    path = tmp_path / "credential.json"
    path.write_text(json.dumps({"password": "test-only-credential"}))
    path.chmod(0o600)
    first = DatabaseTarget(
        "localhost",
        1,
        "one",
        "runtime",
        "private_one",
        "d18bcbac-9c97-4408-baf0-24f7e2331486",
        path,
    )
    second = DatabaseTarget(
        "localhost", 2, "two", "other", "private_two", "d18bcbac-9c97-4408-baf0-24f7e2331487", path
    )
    tag = module.current_sql_application_name()
    assert first.connection_parameters()["application_name"] == tag
    assert second.connection_parameters()["application_name"] == tag
    assert first.connection_parameters()["password"] == "test-only-credential"


def test_supervised_exit_transports_the_exact_sql_origin():
    from test.services.test_resource_process_supervisor import child

    with child() as owner:
        owner.receive_binding(timeout_s=10)
        tag = getattr(owner, "sql_application_name", None)
        assert isinstance(tag, str) and tag.startswith(
            "caos-sql-v1-"
        ), "supervisor lost SQL attribution"
        owner.send_signal(15)
        observation = owner.wait(timeout_s=10)
        assert observation.sql_application_name == tag


@pytest.mark.parametrize(
    "value", [None, "caos", "caos-sql-v1-" + "0" * 32, "caos-sql-v1-" + "A" * 32, [], True]
)
def test_unbound_or_malformed_sql_names_cannot_be_used_in_exit_observations(value):
    from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name

    assert not is_sql_process_application_name(value)


def test_concurrent_native_fork_misses_share_one_new_state(monkeypatch):
    import threading

    module = fresh(monkeypatch)
    state_class = module._State
    monkeypatch.setattr(module, "_STATE", state_class(os.getpid() + 1, enabled=True))
    both = threading.Barrier(2)

    def delayed_state(*args, **kwargs):
        state = state_class(*args, **kwargs)
        both.wait(timeout=10)
        return state

    monkeypatch.setattr(module, "_State", delayed_state)
    with ThreadPoolExecutor(max_workers=2) as workers:
        values = list(workers.map(lambda _: module.current_sql_application_name(), range(2)))
    assert len(set(values)) == 1, "native-fork cold misses published two SQL process identities"


@pytest.mark.parametrize("enabled", [False, True])
def test_legacy_pgappname_environment_stays_strict_for_the_explicit_target(monkeypatch, enabled):
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        UnsupportedLibpqEnvironment,
        _check_libpq_environment,
    )

    module = fresh(monkeypatch, enabled=enabled)
    parameters = dict(
        host="localhost",
        port=1,
        dbname="private",
        user="runtime",
        sslmode="disable",
        connect_timeout=10,
        application_name=module.current_sql_application_name(),
    )
    monkeypatch.setenv("PGAPPNAME", "caos")
    if enabled:
        with pytest.raises(UnsupportedLibpqEnvironment):
            _check_libpq_environment(parameters)
    else:
        assert _check_libpq_environment(parameters) == (("PGAPPNAME", "caos"),)
