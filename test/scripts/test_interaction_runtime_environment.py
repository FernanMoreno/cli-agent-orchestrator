"""Privacy contract for the Questions browser E2E's tmux child process."""

import inspect
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database


def test_tmux_child_environment_is_private_and_allowlisted() -> None:
    from web.e2e.interactions_runtime import _tmux_child_environment

    cao_home = Path("/private/caos-home")
    private_tmp = Path("/private/tmp")
    tmux_tmpdir = Path("/private/tmux")
    parent_environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LC_CTYPE": "C.UTF-8",
        "LANGUAGE": "en_US",
        "TERM": "xterm-256color",
        "OPENAI_API_KEY": "test-provider-key",
        "ANTHROPIC_API_KEY": "test-provider-key",
        "PGPASSWORD": "test-database-password",
        "CAO_AUTH_LOCAL_TOKEN": "test-local-token",
        "CAO_DATABASE_CONFIG_FILE": "/private/config/database-config.json",
        "TMUX": "/host/tmux/socket,1,0",
        "UNRELATED_SETTING": "not-forwarded",
    }

    child_environment = _tmux_child_environment(
        parent_environment,
        cao_home=cao_home,
        private_tmp=private_tmp,
        tmux_tmpdir=tmux_tmpdir,
    )

    assert child_environment == {
        "PATH": "/usr/bin:/bin",
        "CAO_HOME_DIR": str(cao_home),
        "TMPDIR": str(private_tmp),
        "TMUX_TMPDIR": str(tmux_tmpdir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LC_CTYPE": "C.UTF-8",
        "LANGUAGE": "en_US",
        "TERM": "xterm-256color",
    }
    assert "HOME" not in child_environment
    assert "TMUX" not in child_environment
    assert "CAO_DATABASE_CONFIG_FILE" not in child_environment
    assert not any(key.endswith("_API_KEY") for key in child_environment)
    assert "PGPASSWORD" not in child_environment
    assert "CAO_AUTH_LOCAL_TOKEN" not in child_environment


def test_private_path_home_keeps_runtime_paths_and_restores_path_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from web.e2e import interactions_runtime

    original_home = os.environ.get("HOME")
    original_codex_home = os.environ.get("CODEX_HOME")
    original_home_descriptor = inspect.getattr_static(Path, "home")
    originally_owned_home = "home" in Path.__dict__

    with monkeypatch.context() as fixture_patch:
        # Register the raw descriptor so monkeypatch restores it after the
        # helper replaces Path.home with its private runtime path.
        fixture_patch.setattr(Path, "home", original_home_descriptor)
        private_home = interactions_runtime._prepare_private_path_home(tmp_path)

        assert private_home == (tmp_path / "account-home").resolve()
        assert private_home.stat().st_mode & 0o777 == 0o700
        assert Path.home().resolve() == private_home
        assert Path.__dict__["home"] is not original_home_descriptor
        assert os.environ.get("HOME") == original_home
        assert os.environ.get("CODEX_HOME") == original_codex_home

    assert inspect.getattr_static(Path, "home") is original_home_descriptor
    assert ("home" in Path.__dict__) == originally_owned_home
    assert os.environ.get("HOME") == original_home
    assert os.environ.get("CODEX_HOME") == original_codex_home


def test_mcp_child_environment_binds_private_home_and_selected_source_root(
    tmp_path: Path,
) -> None:
    from web.e2e.interactions_runtime import _mcp_child_environment

    source_root = tmp_path / "sealed-source"
    source_root.mkdir()
    private_home = tmp_path / "private-home"
    cao_home = tmp_path / "cao-home"
    config_file = cao_home / "database-config.json"
    tmux_tmpdir = tmp_path / "tmux"
    capability_file = tmp_path / "capability"
    parent_environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONPATH": "/runner/sitecustomize",
        "HOME": "/operator/home",
        "CODEX_HOME": "/operator/codex",
        "OPENAI_API_KEY": "test-provider-key",
        "ANTHROPIC_API_KEY": "test-provider-key",
        "PGPASSWORD": "test-database-password",
        "CAO_AUTH_LOCAL_TOKEN": "test-local-token",
        "UNRELATED_SETTING": "not-forwarded",
    }
    original_parent_environment = dict(parent_environment)

    child_environment = _mcp_child_environment(
        parent_environment,
        private_account_home=private_home,
        source_root=source_root,
        cao_home=cao_home,
        database_config_file=config_file,
        tmux_tmpdir=tmux_tmpdir,
        api_port=12345,
        capability_file=capability_file,
    )

    assert child_environment == {
        "PATH": "/usr/bin:/bin",
        "HOME": str(private_home),
        "PYTHONPATH": os.pathsep.join((str(source_root / "src"), str(source_root))),
        "LANG": "C.UTF-8",
        "CAO_HOME_DIR": str(cao_home),
        "CAO_DATABASE_CONFIG_FILE": str(config_file),
        "TMUX_TMPDIR": str(tmux_tmpdir),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": "12345",
        "CAO_INTERACTION_CAPABILITY_FILE": str(capability_file),
        "CAO_A2A_DISABLED": "true",
        "OTEL_SDK_DISABLED": "true",
    }
    assert parent_environment == original_parent_environment
    assert not {
        "CODEX_HOME",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "PGPASSWORD",
        "CAO_AUTH_LOCAL_TOKEN",
        "UNRELATED_SETTING",
    }.intersection(child_environment)
