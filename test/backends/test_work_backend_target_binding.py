"""Work backend effects must stay inside a durable terminal target binding."""

import sqlite3

import pytest

from cli_agent_orchestrator.backends.base import WorkEffectAuthorizationRequired
from cli_agent_orchestrator.backends.work_backend import WorkBackendView
from cli_agent_orchestrator.constants import FIFO_DIR
from cli_agent_orchestrator.clients.work_repository import WorkConflict
from cli_agent_orchestrator.services.work_admission import WorkAdmission


class RecordingBackend:
    def __init__(self):
        self.effects = []

    def preflight_work(self, _restriction):
        return None

    def _before_work_effect(self, restriction, before_effect):
        self.preflight_work(restriction)
        if before_effect() is not None:
            raise AssertionError("guard must return None")

    def send_work_keys(self, restriction, *args, before_effect, **kwargs):
        self._before_work_effect(restriction, before_effect)
        self.send_keys(*args, **kwargs)

    def create_work_session(self, restriction, *args, before_effect, **kwargs):
        self._before_work_effect(restriction, before_effect)
        self.effects.append(("create_session", args, kwargs))
        return args[1]

    def create_work_window(self, restriction, *args, before_effect, **kwargs):
        self._before_work_effect(restriction, before_effect)
        self.effects.append(("create_window", args, kwargs))
        return args[1]

    def __getattr__(self, name):
        if name in {
            "send_keys",
            "send_special_key",
            "pipe_pane",
            "stop_pipe_pane",
            "kill_session",
            "kill_window",
        }:
            def record(*args, **kwargs):
                self.effects.append((name, args, kwargs))

            return record
        raise AttributeError(name)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("send_keys", ("foreign-session", "foreign-window", "payload")),
        ("send_special_key", ("foreign-session", "foreign-window", "C-c")),
        ("pipe_pane", ("foreign-session", "foreign-window", "/tmp/pane.log")),
        ("stop_pipe_pane", ("foreign-session", "foreign-window")),
        ("kill_window", ("foreign-session", "foreign-window")),
        ("kill_session", ("foreign-session",)),
    ],
)
def test_work_view_rejects_effects_without_attempt_target(method, args):
    backend = RecordingBackend()
    view = WorkBackendView(backend, object(), lambda: None)

    with pytest.raises(WorkEffectAuthorizationRequired):
        getattr(view, method)(*args)

    assert backend.effects == []


@pytest.fixture
def terminal_store(tmp_path):
    path = tmp_path / "target-bindings.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            "CREATE TABLE work_attempts (id TEXT PRIMARY KEY, terminal_id TEXT);"
            "CREATE TABLE terminals (id TEXT PRIMARY KEY, tmux_session TEXT, tmux_window TEXT);"
        )
        connection.executemany(
            "INSERT INTO work_attempts(id,terminal_id) VALUES (?,?)",
            [("attempt-a", "abcd1234"), ("attempt-b", "beef5678")],
        )
        connection.executemany(
            "INSERT INTO terminals(id,tmux_session,tmux_window) VALUES (?,?,?)",
            [
                ("abcd1234", "work-session", "work-window"),
                ("beef5678", "foreign-session", "foreign-window"),
            ],
        )
    return path


def _guard_for(connection_path, *, attempt_id="attempt-a", bound_terminal_id="abcd1234"):
    def guard(effect, terminal_id, session_name=None, window_name=None, file_path=None):
        with sqlite3.connect(connection_path) as connection:
            connection.row_factory = sqlite3.Row
            arguments = dict(
                attempt_id=attempt_id,
                bound_terminal_id=bound_terminal_id,
                effect=effect,
                terminal_id=terminal_id,
                session_name=session_name,
                window_name=window_name,
            )
            try:
                WorkAdmission._check_terminal_effect_target(
                    connection, **arguments, file_path=file_path
                )
            except TypeError as error:
                if "file_path" not in str(error):
                    raise
                WorkAdmission._check_terminal_effect_target(connection, **arguments)

    return guard


def _make_view(backend, guard, *, terminal_id, expected_target=None):
    return WorkBackendView(
        backend,
        object(),
        guard,
        terminal_id=terminal_id,
        expected_target=expected_target,
    )


@pytest.mark.parametrize(
    ("session_name", "window_name"),
    [
        ("foreign-session", "foreign-window"),
        ("work-session", "other-window"),
    ],
)
def test_work_view_rejects_foreign_session_or_window_before_transport(
    terminal_store, session_name, window_name
):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")

    with pytest.raises((WorkEffectAuthorizationRequired, WorkConflict)):
        view.send_keys(session_name, window_name, "payload")

    assert backend.effects == []


@pytest.mark.parametrize(
    ("durable_session", "durable_window"),
    [
        ("foreign-session", "foreign-window"),
        ("work-session", "other-window"),
    ],
)
def test_work_view_rejects_a_target_when_its_durable_row_has_changed(
    terminal_store, durable_session, durable_window
):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    with sqlite3.connect(terminal_store) as connection:
        connection.execute(
            "UPDATE terminals SET tmux_session=?,tmux_window=? WHERE id='abcd1234'",
            (durable_session, durable_window),
        )

    with pytest.raises(WorkConflict, match="does not match the durable terminal row"):
        view.bind_terminal_target("abcd1234", "work-session", "work-window")

    assert backend.effects == []


def test_work_view_rechecks_the_durable_attempt_terminal_before_each_send(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")
    with sqlite3.connect(terminal_store) as connection:
        connection.execute(
            "UPDATE work_attempts SET terminal_id='beef5678' WHERE id='attempt-a'"
        )

    with pytest.raises(WorkConflict, match="durable terminal binding changed"):
        view.send_keys("work-session", "work-window", "payload")

    assert backend.effects == []


def test_work_view_allows_its_bound_terminal_target_for_input_key_and_pane_ops(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")

    view.send_keys("work-session", "work-window", "payload")
    view.send_special_key("work-session", "work-window", "C-c")
    view.pipe_pane("work-session", "work-window", str(FIFO_DIR / "abcd1234.fifo"))
    view.stop_pipe_pane("work-session", "work-window")

    assert [effect[0] for effect in backend.effects] == [
        "send_keys",
        "send_special_key",
        "pipe_pane",
        "stop_pipe_pane",
    ]


def test_work_create_session_requires_exact_durable_attempt_id(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("planned-session", "planned-window"),
    )

    with pytest.raises(WorkEffectAuthorizationRequired, match="differs"):
        view.create_session("new-session", "new-window", "beef5678")

    assert backend.effects == []


def test_work_create_window_rejects_a_spoofed_attempt_terminal_id(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")
    backend.effects.clear()

    with pytest.raises(WorkEffectAuthorizationRequired, match="differs"):
        view.create_window("work-session", "work-window", "beef5678")

    assert backend.effects == []


def test_work_create_session_binds_its_durable_terminal_for_later_input(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("new-session", "new-window"),
    )
    with sqlite3.connect(terminal_store) as connection:
        connection.execute("DELETE FROM terminals WHERE id='abcd1234'")

    assert view.create_session("new-session", "new-window", "abcd1234") == "new-window"
    with sqlite3.connect(terminal_store) as connection:
        connection.execute(
            "INSERT INTO terminals(id,tmux_session,tmux_window) VALUES (?,?,?)",
            ("abcd1234", "new-session", "new-window"),
        )
    view.send_keys("new-session", "new-window", "task")

    assert [effect[0] for effect in backend.effects] == ["create_session", "send_keys"]


def test_work_view_cannot_kill_a_session_shared_with_another_terminal(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    # A successful view-owned create is the only way to acquire session kill
    # authority. Remove the old row so the fresh-session precondition is true.
    with sqlite3.connect(terminal_store) as connection:
        connection.execute("DELETE FROM terminals WHERE id='abcd1234'")
    view.create_session("work-session", "work-window", "abcd1234")
    backend.effects.clear()
    with sqlite3.connect(terminal_store) as connection:
        connection.execute(
            "INSERT INTO terminals(id,tmux_session,tmux_window) VALUES (?,?,?)",
            ("abcd1234", "work-session", "work-window"),
        )
        connection.execute(
            "UPDATE terminals SET tmux_session='work-session' WHERE id='beef5678'"
        )

    with pytest.raises(WorkConflict, match="server-instance-fenced"):
        view.kill_session("work-session")

    assert backend.effects == []


def test_work_create_session_rejects_unplanned_names_before_backend(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        lambda *_args: None,
        terminal_id="abcd1234",
        expected_target=("planned-session", "planned-window"),
    )

    with pytest.raises(WorkEffectAuthorizationRequired, match="planned target"):
        view.create_session("caller-session", "caller-window", "abcd1234")

    assert backend.effects == []


def test_work_create_window_fails_closed_before_backend(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        lambda *_args: None,
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")
    backend.effects.clear()

    with pytest.raises(WorkEffectAuthorizationRequired, match="durable Work window reservation"):
        view.create_window("work-session", "work-window", "abcd1234")

    assert backend.effects == []


def test_work_pipe_pane_rejects_unbound_path_before_backend(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        lambda *_args: None,
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")
    backend.effects.clear()

    with pytest.raises(WorkEffectAuthorizationRequired, match="FIFO path"):
        view.pipe_pane("work-session", "work-window", "/tmp/foreign.fifo")

    assert backend.effects == []


def test_work_view_cannot_kill_a_session_without_a_server_instance_fence(terminal_store):
    with sqlite3.connect(terminal_store) as connection:
        connection.row_factory = sqlite3.Row
        with pytest.raises(WorkConflict, match="server-instance-fenced"):
            WorkAdmission._check_terminal_effect_target(
                connection,
                attempt_id="attempt-a",
                bound_terminal_id="abcd1234",
                effect="kill_session",
                terminal_id="abcd1234",
                session_name="orphan-session",
                window_name=None,
            )


def test_work_view_refuses_kill_even_when_durable_terminal_row_owns_session(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")
    backend.effects.clear()

    with pytest.raises(WorkConflict, match="server-instance-fenced"):
        view.kill_session("work-session")

    assert backend.effects == []


def test_work_view_refuses_kill_window_even_when_durable_owner_matches(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("work-session", "work-window"),
    )
    view.bind_terminal_target("abcd1234", "work-session", "work-window")
    backend.effects.clear()

    with pytest.raises(WorkConflict, match="server-instance-fenced"):
        view.kill_window("work-session", "work-window")

    assert backend.effects == []


def test_work_view_does_not_kill_created_session_until_durable_row_exists(terminal_store):
    backend = RecordingBackend()
    view = _make_view(
        backend,
        _guard_for(terminal_store),
        terminal_id="abcd1234",
        expected_target=("new-session", "new-window"),
    )
    with sqlite3.connect(terminal_store) as connection:
        connection.execute("DELETE FROM terminals WHERE id='abcd1234'")
    view.create_session("new-session", "new-window", "abcd1234")
    backend.effects.clear()

    with pytest.raises(WorkConflict, match="server-instance-fenced"):
        view.kill_session("new-session")

    assert backend.effects == []


def test_work_guard_refuses_name_only_session_rollback(terminal_store):
    with sqlite3.connect(terminal_store) as connection:
        connection.row_factory = sqlite3.Row
        for session_name, window_name in (
            ("new-session", "new-window"),
            ("work-session", "work-window"),
            ("foreign-session", "foreign-window"),
        ):
            with pytest.raises(WorkConflict, match="server-instance-fenced"):
                WorkAdmission._check_terminal_effect_target(
                    connection,
                    attempt_id="attempt-a",
                    bound_terminal_id="abcd1234",
                    effect="rollback_created_session",
                    terminal_id="abcd1234",
                    session_name=session_name,
                    window_name=window_name,
                )
