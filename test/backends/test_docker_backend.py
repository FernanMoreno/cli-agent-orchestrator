"""Docker Work accepts only the profile its container boundary can prove."""

import io
import hashlib
import importlib.util
import json
import tarfile
from types import SimpleNamespace

import pytest


def _restriction(module, *, paths=(), read_paths=(), network=(), tools=()):
    from cli_agent_orchestrator.models.work_contract import ExecutableIdentity

    from cli_agent_orchestrator.backends.base import ProcessRestrictionContract

    identity = ExecutableIdentity(
        command_token="/worker",
        content_reference="sha256:" + "a" * 64,
        sha256_digest="a" * 64,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )
    return ProcessRestrictionContract(
        paths=tuple(paths),
        commands=("/worker",),
        network=tuple(network),
        read_paths=tuple(read_paths),
        checkout_root="/workspace",
        executable_identities=(identity,),
        tools=tuple(tools),
    )


def test_docker_work_rejects_contract_dimensions_it_cannot_enforce(monkeypatch):
    spec = importlib.util.find_spec("cli_agent_orchestrator.backends.docker_backend")
    assert spec is not None, "the explicit Docker Work backend is missing"
    module = __import__(
        "cli_agent_orchestrator.backends.docker_backend", fromlist=["DockerWorkBackend"]
    )
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    backend = module.DockerWorkBackend(image_ref="sha256:" + "b" * 64)
    probes = []
    monkeypatch.setattr(backend, "_validate_engine_and_image", lambda: probes.append("probe"))
    for restriction in (
        _restriction(module, paths=("/workspace/out",)),
        _restriction(module, network=("https://example.test",)),
        _restriction(module, tools=("cao.work.child",)),
    ):
        with pytest.raises(UnsupportedWorkEnforcement):
            backend.preflight_work(restriction)
    backend.preflight_work(_restriction(module, read_paths=("/workspace",)))
    assert probes == ["probe"]


def test_docker_work_maps_only_managed_tools_when_a_server_proxy_is_bound(monkeypatch):
    module = __import__(
        "cli_agent_orchestrator.backends.docker_backend", fromlist=["DockerWorkBackend"]
    )
    backend = module.DockerWorkBackend(
        image_ref="sha256:" + "b" * 64,
        mcp_proxy_factory=lambda *_args: object(),
    )
    probes = []
    monkeypatch.setattr(backend, "_validate_engine_and_image", lambda: probes.append("probe"))

    backend.preflight_work(_restriction(module, tools=("cao.work.child",)))
    with pytest.raises(module.UnsupportedWorkEnforcement):
        backend.preflight_work(_restriction(module, tools=("filesystem.read",)))
    assert probes == ["probe"]


def test_docker_work_requires_an_immutable_image_id_before_any_probe():
    spec = importlib.util.find_spec("cli_agent_orchestrator.backends.docker_backend")
    assert spec is not None, "the explicit Docker Work backend is missing"
    module = __import__(
        "cli_agent_orchestrator.backends.docker_backend", fromlist=["DockerWorkBackend"]
    )

    with pytest.raises(ValueError, match="immutable image ID"):
        module.DockerWorkBackend(image_ref="cao-work-rootfs:latest")


def test_docker_worker_transfer_is_one_readonly_fixed_name_tar_member():
    spec = importlib.util.find_spec("cli_agent_orchestrator.backends.docker_backend")
    assert spec is not None, "the explicit Docker Work backend is missing"
    module = __import__(
        "cli_agent_orchestrator.backends.docker_backend", fromlist=["DockerWorkBackend"]
    )

    archive = module.DockerWorkBackend._worker_archive(b"\x7fELFworker")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        members = stream.getmembers()
        assert [member.name for member in members] == ["worker"]
        assert members[0].isfile()
        assert members[0].mode == 0o555
        assert stream.extractfile(members[0]).read() == b"\x7fELFworker"


def test_docker_attempt_build_context_uses_pinned_base_and_attempt_bound_labels():
    module = __import__(
        "cli_agent_orchestrator.backends.docker_backend", fromlist=["DockerWorkBackend"]
    )
    executable = b"\x7fELFworker"
    archive = module.DockerWorkBackend._attempt_image_archive(
        "cao-work-rootfs:" + "b" * 64, "attempt-1", 7, executable
    )

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        members = stream.getmembers()
        assert [member.name for member in members] == ["Dockerfile", "cao-work-worker"]
        dockerfile = stream.extractfile(members[0]).read().decode("ascii")
        assert "FROM cao-work-rootfs:" + "b" * 64 in dockerfile
        assert f'org.cao.work.attempt_sha256="{hashlib.sha256(b"attempt-1").hexdigest()}"' in dockerfile
        assert 'org.cao.work.generation="7"' in dockerfile
        worker = members[1]
        assert worker.mode == 0o555
        assert stream.extractfile(worker).read() == executable


def test_docker_reconcile_removes_only_artifacts_with_exact_attempt_labels(monkeypatch):
    module = __import__(
        "cli_agent_orchestrator.backends.docker_backend", fromlist=["DockerWorkBackend"]
    )
    backend = module.DockerWorkBackend(image_ref="sha256:" + "b" * 64)
    attempt_id = "attempt-reconcile"
    generation = 4
    attempt_hash = hashlib.sha256(attempt_id.encode()).hexdigest()
    removed = []
    image_removed = []

    def run(arguments, **_kwargs):
        if arguments[:3] == ["container", "inspect", "--format"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "Id": "c" * 64,
                        "Config": {
                            "Labels": {
                                "cao.work.backend": "docker",
                                "cao.work.attempt_sha256": attempt_hash,
                                "cao.work.generation": str(generation),
                            }
                        },
                    }
                ),
            )
        if arguments[:3] == ["image", "inspect", "--format"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "Config": {
                            "Labels": {
                                "org.cao.work.backend": "docker",
                                "org.cao.work.attempt_sha256": attempt_hash,
                                "org.cao.work.generation": str(generation),
                            }
                        }
                    }
                ),
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(backend, "_run", run)
    monkeypatch.setattr(
        backend, "_cleanup_container", lambda container_id, *, kill: removed.append((container_id, kill)) or True
    )
    monkeypatch.setattr(
        backend, "_cleanup_attempt_image", lambda image_tag: image_removed.append(image_tag) or True
    )

    assert backend.reconcile_attempt(attempt_id, generation) == {
        "container_removed": True,
        "image_removed": True,
    }
    assert removed == [("c" * 64, True)]
    assert image_removed == [backend._attempt_image_tag(attempt_id, generation)]
