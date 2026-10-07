"""Default-required posture is enabled only with complete prepare/approve/run wiring."""

import json

import pytest

from cli_agent_orchestrator.services import settings_service


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_service, "SETTINGS_FILE", path)
    monkeypatch.delenv("CAO_WORKFLOW_REQUIRE_APPROVAL", raising=False)
    return path


def test_absent_configuration_requires_approval(settings_path):
    assert settings_service.is_workflow_approval_required() is True


@pytest.mark.parametrize(
    "content", ["{broken", "[]", '{"workflow": []}', '{"workflow": {"require_approval": "false"}}']
)
def test_malformed_configuration_cannot_disable_approval(settings_path, content):
    settings_path.write_text(content)
    assert settings_service.is_workflow_approval_required() is True


def test_explicit_operator_boolean_false_is_preserved(settings_path):
    settings_path.write_text(json.dumps({"workflow": {"require_approval": False}}))
    assert settings_service.is_workflow_approval_required() is False


def test_environment_false_cannot_disable_default_gate(settings_path, monkeypatch):
    monkeypatch.setenv("CAO_WORKFLOW_REQUIRE_APPROVAL", "false")
    assert settings_service.is_workflow_approval_required() is True


def test_environment_enable_cannot_be_weakened_by_file(settings_path, monkeypatch):
    settings_path.write_text(json.dumps({"workflow": {"require_approval": False}}))
    monkeypatch.setenv("CAO_WORKFLOW_REQUIRE_APPROVAL", "1")
    assert settings_service.is_workflow_approval_required() is True


def test_malformed_settings_cannot_be_overwritten_by_update(settings_path):
    settings_path.write_text("{broken")
    with pytest.raises(settings_service.SettingsUnreadableError):
        settings_service.set_memory_setting("enabled", True)
    assert settings_path.read_text() == "{broken"


def test_failed_atomic_publish_preserves_operator_optout(settings_path, monkeypatch):
    original = '{"workflow":{"require_approval":false},"other":{"keep":1}}'
    settings_path.write_text(original)

    def refused(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(settings_service.atomic_file, "_atomic_publish", refused)
    with pytest.raises(OSError, match="disk failure"):
        settings_service.set_memory_setting("enabled", True)
    assert settings_path.read_text() == original


def test_concurrent_settings_updates_preserve_unrelated_keys(settings_path):
    from concurrent.futures import ThreadPoolExecutor

    settings_path.write_text('{"workflow":{"require_approval":false},"unrelated":42}')
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(settings_service.set_memory_setting, "enabled", True),
            pool.submit(settings_service.set_extra_agent_dirs, ["/fixture/extra"]),
        ]
        for task in tasks:
            task.result()
    data = json.loads(settings_path.read_text())
    assert data["unrelated"] == 42
    assert data["workflow"]["require_approval"] is False
    assert data["memory"]["enabled"] is True
    assert settings_service.get_extra_agent_dirs() == ["/fixture/extra"]
