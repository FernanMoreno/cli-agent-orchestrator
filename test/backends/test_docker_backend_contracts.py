"""Docker contracts exercised through native CLI seams, without a daemon claim."""

import hashlib
import io
import json
import select
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from test.backends.test_docker_backend import _restriction
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.backends import docker_backend as docker

BASE = "sha256:" + "b" * 64
IMAGE = "sha256:" + "c" * 64
CONTAINER = "d" * 64
ATTEMPT = "attempt-contract"
GENERATION = 3


def result(stdout="", *, code=0, stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def image_metadata(*, attempt=False):
    labels = {
        "org.cao.work.supervisor.protocol": "1",
        "org.cao.work.supervisor.source_sha256": docker._SUPERVISOR_SOURCE_SHA256,
    }
    if attempt:
        labels.update(
            {
                "org.cao.work.backend": "docker",
                "org.cao.work.attempt_sha256": hashlib.sha256(ATTEMPT.encode()).hexdigest(),
                "org.cao.work.generation": str(GENERATION),
                "org.cao.work.worker_sha256": hashlib.sha256(b"worker").hexdigest(),
            }
        )
    return {
        "Id": IMAGE if attempt else BASE,
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {"Labels": labels},
        "RootFS": {"Type": "layers", "Layers": ["base", "worker"] if attempt else ["base"]},
    }


def container_metadata(*, running=True):
    return {
        "Id": CONTAINER,
        "Image": IMAGE,
        "Mounts": [],
        "State": {"Running": running, "StartedAt": "2026-10-07T01:00:00Z", "ExitCode": 0},
        "Config": {
            "User": "0:0",
            "Entrypoint": ["/cao-work-supervisor"],
            "Cmd": ["--protocol=1"],
            "AttachStdin": True,
            "OpenStdin": True,
            "Env": ["LC_ALL=C"],
            "Labels": {
                "cao.work.backend": "docker",
                "cao.work.attempt_sha256": hashlib.sha256(ATTEMPT.encode()).hexdigest(),
                "cao.work.generation": str(GENERATION),
                "cao.work.image_sha256": IMAGE.removeprefix("sha256:"),
                "cao.work.mcp_enabled": "false",
            },
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "IpcMode": "private",
            "PidMode": "",
            "CapDrop": ["ALL"],
            "CapAdd": ["CAP_SETUID", "CAP_SETGID"],
            "SecurityOpt": ["no-new-privileges"],
            "Privileged": False,
            "PidsLimit": 2,
            "Memory": 256 * 1024 * 1024,
            "MemorySwap": 256 * 1024 * 1024,
            "NanoCpus": 1_000_000_000,
        },
    }


class NativeDocker:
    """Record actual subprocess arguments; serve controlled Docker response bytes."""

    def __init__(self):
        self.calls = []
        self.response = lambda args, options: result()

    def __call__(self, command, **options):
        self.calls.append((command, options))
        assert command[1] == "--host"
        assert command[2].startswith("unix://")
        assert "DOCKER_HOST" not in options["env"]
        assert "DOCKER_CONTEXT" not in options["env"]
        return self.response(command[3:], options)


@pytest.fixture
def native(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="socket-") as directory:
        with socket.socket(socket.AF_UNIX) as endpoint:
            endpoint.bind(str(Path(directory) / "docker.sock"))
            monkeypatch.setenv("DOCKER_HOST", "unix://" + endpoint.getsockname())
            monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
            monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
            monkeypatch.setenv("DOCKER_CERT_PATH", "/untrusted")
            seam = NativeDocker()
            monkeypatch.setattr(docker.subprocess, "run", seam)
            yield docker.DockerWorkBackend(image_ref=BASE), seam


def preflight_responses(args, _options):
    if args[0] == "version":
        return result("25.0.1|linux|amd64")
    if args[0] == "info":
        return result('["name=seccomp,profile=builtin"]')
    if args[:2] == ["image", "inspect"]:
        return result(json.dumps([image_metadata()]))
    raise AssertionError(args)


def test_preflight_pins_socket_clears_remote_environment_and_caches_verified_image(native):
    backend, seam = native
    seam.response = preflight_responses
    backend.preflight_work(_restriction(docker))
    backend.preflight_work(_restriction(docker))
    assert [call[0][3:5] for call in seam.calls] == [
        ["version", "--format"],
        ["info", "--format"],
        ["image", "inspect"],
    ]
    assert backend._base_layer_digests == ("base",)
    assert all(
        "DOCKER_TLS_VERIFY" not in options["env"] and "DOCKER_CERT_PATH" not in options["env"]
        for _, options in seam.calls
    )


def test_explicit_local_context_overrides_remote_host_before_engine_probe(native, monkeypatch):
    backend, seam = native
    local_host = docker.os.environ["DOCKER_HOST"]
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.example:2376")
    monkeypatch.setenv("DOCKER_CONTEXT", "verified-local")
    events = []

    def resolve_context(command, **options):
        if command[1:3] == ["context", "inspect"]:
            assert command[3] == "verified-local"
            events.append("context")
            return result(local_host)
        events.append("engine")
        return seam(command, **options)

    seam.response = preflight_responses
    monkeypatch.setattr(docker.subprocess, "run", resolve_context)
    backend.preflight_work(_restriction(docker))
    backend.preflight_work(_restriction(docker))
    assert events == ["context", "engine", "engine", "engine"]
    assert all(command[2] == local_host for command, _ in seam.calls)


@pytest.mark.parametrize(
    "version", ["24.9.0|linux|amd64", "invalid", "25.0.0|windows|amd64", "25.0.0|linux|arm64"]
)
def test_preflight_rejects_unverified_engine_before_inspecting_image(native, version):
    backend, seam = native
    seam.response = lambda args, options: result(version)
    with pytest.raises(docker.UnsupportedWorkEnforcement):
        backend.preflight_work(_restriction(docker))
    assert len(seam.calls) == 1


@pytest.mark.parametrize("security", ["not json", "{}", '["name=apparmor"]'])
def test_preflight_requires_seccomp(native, security):
    backend, seam = native
    seam.response = lambda args, options: (
        result(security) if args[0] == "info" else preflight_responses(args, options)
    )
    with pytest.raises(docker.UnsupportedWorkEnforcement, match="seccomp"):
        backend.preflight_work(_restriction(docker))
    assert len(seam.calls) == 2


@pytest.mark.parametrize("metadata", [[], [{}], "invalid", None])
def test_preflight_rejects_malformed_pinned_image_metadata(native, metadata):
    backend, seam = native
    seam.response = lambda args, options: (
        result(json.dumps(metadata))
        if args[:2] == ["image", "inspect"]
        else preflight_responses(args, options)
    )
    with pytest.raises(docker.UnsupportedWorkEnforcement, match="metadata"):
        backend.preflight_work(_restriction(docker))
    assert backend._validated_image is False


@pytest.mark.parametrize(
    "section,key,value",
    [
        (None, "Id", IMAGE),
        ("Config", "User", "1000"),
        ("Config", "Entrypoint", ["/shell"]),
        ("Config", "Env", ["SECRET=bad"]),
        ("RootFS", "Layers", ["base", "extra"]),
    ],
)
def test_preflight_refuses_image_configuration_drift(native, section, key, value):
    backend, seam = native
    metadata = image_metadata()
    (metadata[section] if section else metadata)[key] = value
    seam.response = lambda args, options: (
        result(json.dumps([metadata]))
        if args[:2] == ["image", "inspect"]
        else preflight_responses(args, options)
    )
    with pytest.raises(docker.UnsupportedWorkEnforcement, match="rootfs"):
        backend.preflight_work(_restriction(docker))


def test_native_run_preserves_binary_input_and_denies_failed_required_operation(native):
    backend, seam = native
    payload = b"\x00\xffarchive"
    backend._run(["image", "build", "-"], input_data=payload, timeout=60)
    _, options = seam.calls[-1]
    assert options["input"] == payload and options["text"] is False and options["timeout"] == 60
    seam.response = lambda *_: result(code=1, stderr="denied")
    with pytest.raises(docker.DockerWorkBackendUnavailable, match="required operation"):
        backend._run(["info"])
    assert backend._run(["info"], check=False).returncode == 1
    with pytest.raises(TypeError):
        backend._run(["image", "build"], input_data=bytearray(payload))


@pytest.mark.parametrize("error", [OSError("missing CLI"), subprocess.TimeoutExpired("docker", 1)])
def test_native_query_failure_is_unavailability(native, error):
    backend, seam = native

    def fail(*_):
        raise error

    seam.response = fail
    with pytest.raises(docker.DockerWorkBackendUnavailable, match="could not be queried"):
        backend._run(["info"])


@pytest.mark.parametrize(
    "payload",
    [b"", bytearray(b"worker"), b"x" * (8 * 1024 * 1024 + 1)],
    ids=["empty", "mutable", "oversized"],
)
def test_archive_rejects_empty_mutable_or_unbounded_worker(payload):
    with pytest.raises(docker.DockerWorkBackendUnavailable, match="byte bound"):
        docker.DockerWorkBackend._worker_archive(payload)


@pytest.mark.parametrize(
    "base,attempt,generation",
    [
        (BASE, ATTEMPT, 1),
        ("cao-work-rootfs:" + "b" * 64, "", 1),
        ("cao-work-rootfs:" + "b" * 64, ATTEMPT, True),
    ],
)
def test_attempt_archive_rejects_unbound_identity(base, attempt, generation):
    with pytest.raises(docker.DockerWorkBackendUnavailable, match="binding"):
        docker.DockerWorkBackend._attempt_image_archive(base, attempt, generation, b"worker")


def test_frame_roundtrip_real_file_and_chunked_pipe(tmp_path):
    payload = b"\x00\xff\nworker"
    path = tmp_path / "supervisor.frame"
    path.write_bytes(docker.DockerWorkBackend._frame(b"CAO-MCP/1 ", payload))
    with path.open("rb") as pipe:
        assert docker.DockerWorkBackend._read_frame(pipe, b"CAO-MCP/1 ", 100) == payload

    class Chunked(io.BytesIO):
        def read(self, size):
            return super().read(min(size, 2))

    assert docker.DockerWorkBackend._read_exact(Chunked(payload), len(payload)) == payload


@pytest.mark.parametrize(
    "frame,limit,message",
    [
        (b"wrong 0\n\n", 10, "header"),
        (b"CAO-MCP/1 1", 10, "header"),
        (b"CAO-MCP/1 x\n", 10, "size"),
        (b"CAO-MCP/1 \n", 10, "size"),
        (b"CAO-MCP/1 1234567890\n", 10, "size"),
        (b"CAO-MCP/1 11\n", 10, "bound"),
        (b"CAO-MCP/1 2\na", 10, "partial"),
        (b"CAO-MCP/1 1\na!", 10, "terminator"),
    ],
)
def test_supervisor_invalid_frames_fail_closed(frame, limit, message):
    with pytest.raises(docker.DockerWorkExecutionUncertain, match=message):
        docker.DockerWorkBackend._read_frame(io.BytesIO(frame), b"CAO-MCP/1 ", limit)


@pytest.mark.parametrize(
    "operation",
    [
        "create_session",
        "create_window",
        "send_keys",
        "send_special_key",
        "kill_session",
        "kill_window",
        "pipe_pane",
        "stop_pipe_pane",
    ],
)
def test_process_backend_cannot_fall_back_to_terminal_lifecycle(operation):
    with pytest.raises(docker.UnsupportedWorkEnforcement, match="does not provide"):
        getattr(docker.DockerWorkBackend(image_ref=BASE), operation)("target")


def test_attempt_build_validates_real_archive_and_native_image_binding(native):
    backend, seam = native
    backend._base_layer_count, backend._base_layer_digests = 1, ("base",)

    def response(args, options):
        if args[:2] == ["image", "build"]:
            assert "--network=none" in args and "--pull=false" in args
            with __import__("tarfile").open(fileobj=io.BytesIO(options["input"])) as archive:
                assert archive.getnames() == ["Dockerfile", "cao-work-worker"]
                assert archive.extractfile("cao-work-worker").read() == b"worker"
                assert (
                    f'LABEL org.cao.work.generation="{GENERATION}"'.encode()
                    in archive.extractfile("Dockerfile").read()
                )
            return result()
        if args[:3] == ["image", "inspect", "--format"]:
            return result(BASE if args[-1] == backend._base_image_tag else IMAGE)
        if args == ["image", "inspect", IMAGE]:
            return result(json.dumps([image_metadata(attempt=True)]))
        raise AssertionError(args)

    seam.response = response
    assert backend._build_attempt_image(
        attempt_id=ATTEMPT, generation=GENERATION, executable=b"worker"
    ) == (backend._attempt_image_tag(ATTEMPT, GENERATION), IMAGE)


@pytest.mark.parametrize("bad", ["not json", "[]", "[{}]"])
def test_attempt_image_bad_metadata_is_not_a_valid_worker_rootfs(native, bad):
    backend, seam = native
    seam.response = lambda *_: result(bad)
    with pytest.raises(docker.DockerWorkBackendUnavailable, match="metadata"):
        backend._validate_attempt_image(
            IMAGE,
            attempt_id=ATTEMPT,
            generation=GENERATION,
            worker_sha256=hashlib.sha256(b"worker").hexdigest(),
        )


@pytest.mark.parametrize("drift", ["generation", "digest", "layers"])
def test_attempt_image_rejects_label_or_layer_drift(native, drift):
    backend, seam = native
    metadata = image_metadata(attempt=True)
    backend._base_layer_count, backend._base_layer_digests = 1, ("base",)
    if drift == "layers":
        metadata["RootFS"]["Layers"] = ["foreign", "worker"]
    else:
        metadata["Config"]["Labels"][
            "org.cao.work." + ("generation" if drift == "generation" else "worker_sha256")
        ] = "foreign"
    seam.response = lambda *_: result(json.dumps([metadata]))
    with pytest.raises(docker.DockerWorkBackendUnavailable, match="differs"):
        backend._validate_attempt_image(
            IMAGE,
            attempt_id=ATTEMPT,
            generation=GENERATION,
            worker_sha256=hashlib.sha256(b"worker").hexdigest(),
        )


def validate_container(backend, *, running=True):
    return backend._validate_running_container(
        CONTAINER,
        attempt_id=ATTEMPT,
        generation=GENERATION,
        image_id=IMAGE,
        command=["--protocol=1"],
        mcp_enabled=False,
        expected_running=running,
    )


@pytest.mark.parametrize("running", [True, False])
def test_container_inspection_binds_running_and_stopped_identity(native, running):
    backend, seam = native
    seam.response = lambda *_: result(json.dumps(container_metadata(running=running)))
    assert validate_container(backend, running=running) == {
        "container_id": CONTAINER,
        "image_id": IMAGE,
        "started_at": "2026-10-07T01:00:00Z",
    }


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("State", "Running", False),
        ("State", "StartedAt", "0001-01-01T00:00:00Z"),
        ("Config", "User", "1000"),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "Privileged", True),
        ("HostConfig", "Binds", ["/host:/worker"]),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "Memory", 0),
    ],
)
def test_container_observed_isolation_drift_is_uncertain(native, section, key, value):
    backend, seam = native
    metadata = container_metadata()
    metadata[section][key] = value
    seam.response = lambda *_: result(json.dumps(metadata))
    with pytest.raises(docker.DockerWorkExecutionUncertain, match="isolation"):
        validate_container(backend)


@pytest.mark.parametrize("raw", ["invalid", "[]", "null", "{}"])
def test_container_incomplete_inspection_never_confirms_runtime(native, raw):
    backend, seam = native
    seam.response = lambda *_: result(raw)
    with pytest.raises(docker.DockerWorkExecutionUncertain):
        validate_container(backend)


@pytest.mark.parametrize(
    "attempt,generation", [("", 1), (ATTEMPT, 0), (ATTEMPT, True), ("bad\x00", 1), ("x" * 257, 1)]
)
def test_reconciliation_invalid_owner_cannot_reach_daemon(native, attempt, generation):
    backend, seam = native
    with pytest.raises(ValueError):
        backend.reconcile_attempt(attempt, generation)
    assert seam.calls == []


@pytest.mark.parametrize("artifact", ["container", "image"])
@pytest.mark.parametrize("drift", ["labels", "malformed"])
def test_reconciliation_foreign_or_unreadable_artifact_is_preserved(native, artifact, drift):
    backend, seam = native

    def response(args, options):
        if args[0] != artifact:
            return result(code=1, stderr="No such object")
        metadata = container_metadata() if artifact == "container" else image_metadata(attempt=True)
        if drift == "labels":
            metadata["Config"]["Labels"] = {}
        return result("invalid" if drift == "malformed" else json.dumps(metadata))

    seam.response = response
    with pytest.raises(docker.DockerWorkAttemptRecoveryRequired):
        backend.reconcile_attempt(ATTEMPT, GENERATION)
    assert all(call[0][4] == "inspect" for call in seam.calls)


def test_reconciliation_removes_exact_owners_and_confirms_absence(native):
    backend, seam = native

    def response(args, options):
        if args[1] == "inspect":
            if "--format" in args:
                return result(
                    json.dumps(
                        container_metadata()
                        if args[0] == "container"
                        else image_metadata(attempt=True)
                    )
                )
            return result(code=1, stderr="No such object")
        return result()

    seam.response = response
    assert backend.reconcile_attempt(ATTEMPT, GENERATION) == {
        "container_removed": True,
        "image_removed": True,
    }
    commands = [call[0][3:] for call in seam.calls]
    assert ["container", "kill", CONTAINER] in commands
    assert ["container", "wait", CONTAINER] in commands
    assert ["container", "rm", "--force", CONTAINER] in commands
    assert ["image", "rm", "--force", backend._attempt_image_tag(ATTEMPT, GENERATION)] in commands


def test_cleanup_cannot_claim_absence_during_daemon_outage(native):
    backend, seam = native
    seam.response = lambda args, options: (
        result(code=1, stderr="Cannot connect to daemon") if args[1] == "inspect" else result()
    )
    with pytest.raises(docker.DockerWorkExecutionUncertain):
        backend._cleanup_container(CONTAINER, kill=True)
    with pytest.raises(docker.DockerWorkExecutionUncertain):
        backend._cleanup_attempt_image("tag")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_protocol,late_cleanup",
    [
        (False, "none"),
        (True, "none"),
        (False, "closed"),
        (False, "unknown"),
        (False, "attach_failed"),
        (False, "attach_lost_ack"),
        (False, "attach_lost_ack_memory"),
        (False, "build_lost_ack"),
        (False, "inspect_lost_ack"),
    ],
)
async def test_durable_dispatch_real_authority_and_content_with_native_transport(
    native, monkeypatch, tmp_path, bad_protocol, late_cleanup
):
    """Real database/credentials/content bind setup and GO; only Docker I/O is fake."""
    from test.integration.t098.test_work_launch_dispatch import _setup
    from test.security.test_work_bubblewrap_composition import _minimal_static_worker

    from cli_agent_orchestrator.services.work_service import DeliveryUncertain

    _, seam = native
    worker = _minimal_static_worker()
    release_reader, reader_entered = threading.Event(), threading.Event()
    real_popen = subprocess.Popen
    unobserved_processes = []
    observed_ready = []

    def lose_start_ack(error_type=OSError):
        child = real_popen(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys;sys.stdout.buffer.write(b'R');sys.stdout.buffer.flush();sys.stdin.buffer.read()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={"LC_ALL": "C"},
        )
        unobserved_processes.append(child)
        assert select.select([child.stdout], [], [], 5)[0]
        observed_ready.append(child.stdout.read(1))
        assert observed_ready[-1] == b"R" and child.poll() is None
        raise error_type("controlled lost start acknowledgement after owned child handshake")

    owner = None
    if late_cleanup != "none":
        from test.services.test_delivery_admission import create, observe

        owner = create()
        assert observe(owner, 2)
    runtime = {}
    processes = []
    inspected = []

    def factory(_marker, repository):
        runtime["backend"] = docker.DockerWorkBackend(image_ref=BASE, repository=repository)
        return runtime["backend"]

    repository, principal, gateway, backend, _marker, request = _setup(
        tmp_path,
        backend_factory=factory,
        worker_binary=worker,
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
        resource_admission=owner,
        resource_clock=lambda: 10,
    )
    seam.response = preflight_responses
    receipt = gateway.admit(principal, request)
    attempt = repository.get_work(receipt.work_item_id)["attempts"][-1]
    attempt_id, generation = attempt["id"], attempt["generation"]
    attempt_hash = hashlib.sha256(attempt_id.encode()).hexdigest()
    supervisor = [
        "--protocol=1",
        "--worker",
        "/cao-work-worker",
        "--argv0",
        "/worker",
        "--uid",
        "65532",
        "--gid",
        "65532",
    ]
    created = []

    def response(args, options):
        if args[0] in {"version", "info"}:
            return preflight_responses(args, options)
        if args[:2] == ["image", "inspect"]:
            if args[-1] == BASE:
                return result(json.dumps([image_metadata()]))
            if args[-1] == backend._base_image_tag:
                return result(BASE)
            if args[-1] == IMAGE:
                metadata = image_metadata(attempt=True)
                metadata["Config"]["Labels"].update(
                    {
                        "org.cao.work.attempt_sha256": attempt_hash,
                        "org.cao.work.generation": str(generation),
                        "org.cao.work.worker_sha256": hashlib.sha256(worker).hexdigest(),
                    }
                )
                return result(json.dumps([metadata]))
            if created and "--format" in args:
                return result(IMAGE)
            return result(code=1, stderr="No such image")
        if args[:2] == ["image", "build"]:
            if late_cleanup == "build_lost_ack":
                lose_start_ack()
            import tarfile

            with tarfile.open(fileobj=io.BytesIO(options["input"])) as archive:
                assert archive.extractfile("cao-work-worker").read() == worker
            created.append("image")
            return result()
        if args[:2] == ["container", "create"]:
            assert "--read-only" in args and "--network=none" in args
            assert "cao.work.attempt_sha256=" + attempt_hash in args
            assert args[-len(supervisor) :] == supervisor
            created.append("container")
            return result(CONTAINER)
        if args[:2] == ["container", "inspect"]:
            if late_cleanup == "inspect_lost_ack" and not created:
                lose_start_ack()
            if "--format" not in args or args[-1] != CONTAINER:
                return result(code=1, stderr="No such container")
            metadata = container_metadata(running=not inspected)
            metadata["Config"]["Cmd"] = supervisor
            metadata["Config"]["Labels"].update(
                {"cao.work.attempt_sha256": attempt_hash, "cao.work.generation": str(generation)}
            )
            inspected.append(metadata)
            return result(json.dumps(metadata))
        if args[1] in {"kill", "wait", "rm"}:
            return result()
        raise AssertionError(args)

    seam.response = response

    class Input(io.BytesIO):
        def close(self):
            self.saved = self.getvalue()
            super().close()

    class LateReader(io.BytesIO):
        def readline(self, *args):
            reader_entered.set()
            assert release_reader.wait(30), "owned Docker reader was not released"
            return super().readline(*args)

    class Attach:
        def __init__(self, command, **options):
            assert command[3:] == ["container", "start", "--attach", "--interactive", CONTAINER]
            assert options["close_fds"] is True
            if late_cleanup == "attach_failed":
                raise OSError("controlled attach start failure after container creation")
            if late_cleanup == "attach_lost_ack":
                lose_start_ack()
            if late_cleanup == "attach_lost_ack_memory":
                lose_start_ack(MemoryError)
            self.stdin = Input()
            ready = {
                "protocol": 1,
                "mcp_enabled": False,
                "worker_uid": 65532,
                "worker_gid": 65532,
                "supervisor_source_sha256": docker._SUPERVISOR_SOURCE_SHA256,
                "worker_socket_fd": -1,
            }
            protocol = docker.DockerWorkBackend._frame(b"CAO-READY/1 ", json.dumps(ready).encode())
            protocol += b"CAO-OUT/1 stdout 5\nhello\nCAO-OUT/1 stderr 4\nwarn\nCAO-EXIT/1 0\n"
            self.stdout = io.BytesIO(b"invalid protocol\n" if bad_protocol else protocol)
            self.stderr = LateReader() if late_cleanup != "none" else io.BytesIO()
            self.returncode = 0
            self.killed = False
            processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            return self.returncode

        def kill(self):
            self.killed = True

    monkeypatch.setattr(docker.subprocess, "Popen", Attach)
    cleanup_calls = []
    if late_cleanup == "unknown":

        def unavailable(*args, **kwargs):
            cleanup_calls.append(kwargs)
            return False

        monkeypatch.setattr(backend, "_cleanup_container", unavailable)
    native_threads = []
    try:
        if bad_protocol or late_cleanup != "none":
            with pytest.raises(DeliveryUncertain):
                await gateway.dispatch_registered_next()
            assert repository.get_work(receipt.work_item_id)["state"] == "reconcile"
            if bad_protocol:
                assert ["container", "kill", CONTAINER] in [call[0][3:] for call in seam.calls]
        else:
            dispatched = await gateway.dispatch_registered_next()
            assert dispatched["id"] == receipt.work_item_id
            persisted = repository.get_work(receipt.work_item_id)
            assert dispatched == persisted
            assert persisted["state"] == "running"
            assert persisted["accepted_result_id"] is None
            assert persisted["attempts"][-1]["state"] == "sent"
            assert persisted["attempts"][-1]["id"] == receipt.attempt_id
            assert persisted["attempts"][-1]["generation"] == receipt.generation
            assert processes[0].stdin.saved.endswith(b"CAO-GO/1\n")
            assert b"frozen task context\n\npayload for exact worker" in processes[0].stdin.saved
            assert len(inspected) == 3 and inspected[0]["State"]["Running"] is True
        if late_cleanup in {
            "attach_lost_ack",
            "attach_lost_ack_memory",
            "build_lost_ack",
            "inspect_lost_ack",
        }:
            assert observed_ready == [b"R"] and len(unobserved_processes) == 1
            assert unobserved_processes[0].poll() is None
            assert owner.snapshot(now=10).inflight == 1  # Live unobserved child still owns cost.
            assert not owner._physical_closes
            assert owner.retry_physical_closes().clean
            assert owner.snapshot(now=10).inflight == 1
        if late_cleanup not in {"unknown", "build_lost_ack", "inspect_lost_ack"}:
            assert ["container", "rm", "--force", CONTAINER] in [call[0][3:] for call in seam.calls]
        if late_cleanup not in {"build_lost_ack", "inspect_lost_ack"}:
            assert [
                "image",
                "rm",
                "--force",
                backend._attempt_image_tag(attempt_id, generation),
            ] in [call[0][3:] for call in seam.calls]
        if owner is not None and late_cleanup == "attach_failed":
            assert processes == [] and not owner._physical_closes
            assert owner.snapshot(now=10).inflight == 1  # An exception supplies no no-start proof.
            persisted = repository.get_work(receipt.work_item_id)
            assert persisted["state"] == "reconcile" and persisted["accepted_result_id"] is None
        elif owner is not None and late_cleanup in {
            "attach_lost_ack",
            "attach_lost_ack_memory",
            "build_lost_ack",
            "inspect_lost_ack",
        }:
            child = unobserved_processes[0]
            child.stdin.close()
            assert child.wait(timeout=5) == 0
            assert owner.retry_physical_closes().clean
            assert owner.snapshot(now=10).inflight == 1  # No later original-owner close proof.
        elif owner is not None:
            assert reader_entered.is_set()
            assert owner.snapshot(now=10).inflight == 1
            tokens = [
                token for lease in owner._leases.values() for token in lease._physical_effects
            ]
            assert len(tokens) == 1
            native_threads = [
                obj for obj, _ in tokens[0]._owners if isinstance(obj, threading.Thread)
            ]
            assert len(native_threads) == 2 and any(thread.is_alive() for thread in native_threads)
            assert bool(owner._physical_closes) is (late_cleanup == "closed")
            release_reader.set()
            for thread in native_threads:
                thread.join(3)
                assert not thread.is_alive()
            assert owner.retry_physical_closes().clean
            assert owner.snapshot(now=10).inflight == int(late_cleanup == "unknown")
            assert owner.retry_physical_closes().clean
            persisted = repository.get_work(receipt.work_item_id)
            assert persisted["state"] == "reconcile" and persisted["accepted_result_id"] is None
            assert len(processes) == 1  # Resource closure never replays or completes Work.
            if late_cleanup == "unknown":
                assert cleanup_calls == [{"kill": True}]
                assert not owner._physical_closes
            else:
                assert not tokens[0].finish()
    finally:
        release_reader.set()
        for child in unobserved_processes:
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()  # Only this fixture's exact owned child, never other processes.
                child.wait(timeout=5)
            if child.stdout is not None:
                child.stdout.close()
        if owner is not None:
            for lease in tuple(owner._leases.values()):
                for token in tuple(lease._physical_effects):
                    for obj, _ in tuple(token._owners):
                        if isinstance(obj, threading.Thread):
                            obj.join(3)
            owner.retry_physical_closes()
