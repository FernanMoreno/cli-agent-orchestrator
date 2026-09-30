from __future__ import annotations

import pytest

from cli_agent_orchestrator.backends import work_registry
from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository


def _repository(tmp_path):
    repository = WorkRepository(tmp_path / "work.sqlite3")
    repository.initialize()
    return repository


def test_local_work_backends_default_to_an_instance_copy_without_global_registration(
    tmp_path,
):
    repository = _repository(tmp_path)

    configured = work_registry.local_work_backends_for(repository, environ={})

    assert configured == {}
    assert configured is not work_registry.WORK_BACKENDS
    assert work_registry.WORK_BACKENDS == {}


def test_local_work_backends_require_explicit_enablement_and_immutable_image(
    tmp_path,
):
    repository = _repository(tmp_path)

    assert (
        work_registry.local_work_backends_for(repository, environ={"CAO_WORK_DOCKER_LOCAL": "0"})
        == {}
    )
    with pytest.raises(ValueError, match="CAO_WORK_DOCKER_LOCAL"):
        work_registry.local_work_backends_for(repository, environ={"CAO_WORK_DOCKER_LOCAL": "true"})
    with pytest.raises(ValueError, match="CAO_WORK_DOCKER_IMAGE_ID"):
        work_registry.local_work_backends_for(repository, environ={"CAO_WORK_DOCKER_LOCAL": "1"})
    with pytest.raises(ValueError, match="immutable image ID"):
        work_registry.local_work_backends_for(
            repository,
            environ={
                "CAO_WORK_DOCKER_LOCAL": "1",
                "CAO_WORK_DOCKER_IMAGE_ID": "cao-work-rootfs:latest",
            },
        )


def test_local_work_backends_bind_docker_to_the_exact_runtime_repository(tmp_path):
    repository = _repository(tmp_path)
    image_id = "sha256:" + "a" * 64

    configured = work_registry.local_work_backends_for(
        repository,
        environ={
            "CAO_WORK_DOCKER_LOCAL": "1",
            "CAO_WORK_DOCKER_IMAGE_ID": image_id,
        },
    )

    assert set(configured) == {"docker-local"}
    backend = configured["docker-local"]
    assert isinstance(backend, DockerWorkBackend)
    assert backend.repository is repository
    assert backend.image_ref == image_id
    assert work_registry.WORK_BACKENDS == {}


def test_local_work_backend_binds_the_same_work_origins_owner_as_its_runtime(tmp_path):
    from cli_agent_orchestrator.services.work_launch_runtime import LaunchRuntime

    repository = _repository(tmp_path)
    backends = work_registry.local_work_backends_for(
        repository,
        environ={
            "CAO_WORK_DOCKER_LOCAL": "1",
            "CAO_WORK_DOCKER_IMAGE_ID": "sha256:" + "d" * 64,
        },
    )

    runtime = LaunchRuntime(repository, backends=backends, delivery_adapters={})

    backend = runtime._admission.backends["docker-local"]
    assert backend.repository is repository
    assert backend._work_origins is runtime.origins
    assert runtime.origins.repository is repository


def test_local_work_backends_reject_a_non_repository_before_registration():
    with pytest.raises(ValueError, match="verified work repository"):
        work_registry.local_work_backends_for(
            object(),
            environ={
                "CAO_WORK_DOCKER_LOCAL": "1",
                "CAO_WORK_DOCKER_IMAGE_ID": "sha256:" + "b" * 64,
            },
        )


def test_local_work_backends_reject_conflicting_local_key(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setitem(work_registry.WORK_BACKENDS, "docker-local", object())

    with pytest.raises(ValueError, match="docker-local"):
        work_registry.local_work_backends_for(
            repository,
            environ={
                "CAO_WORK_DOCKER_LOCAL": "1",
                "CAO_WORK_DOCKER_IMAGE_ID": "sha256:" + "c" * 64,
            },
        )
