"""Offline admission of the complete pinned Codex package, never a model turn."""

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    metadata = {
        "layoutVersion": 1,
        "version": "0.164.0",
        "target": "x86_64-unknown-linux-musl",
        "variant": "codex",
        "entrypoint": "bin/codex",
        "resourcesDir": "codex-resources",
        "pathDir": "codex-path",
    }
    names = ("bin/codex", "bin/codex-code-mode-host", "codex-path/rg", "codex-resources/bwrap")
    for name in names:
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(("synthetic " + name).encode())
        path.chmod(0o700)
    path = root / "codex-package.json"
    path.write_text(json.dumps(metadata))
    path.chmod(0o600)
    data = {
        "schema": "caos.codex.runtime-snapshot.v1",
        "root": str(root),
        "assets": {name: digest(root / name) for name in (*names, "codex-package.json")},
    }
    manifest = tmp_path / "runtime.json"
    manifest.write_text(json.dumps(data))
    manifest.chmod(0o600)
    return root, manifest, data


def check(root, manifest, expected=None):
    from test.live_provider.codex_runtime_snapshot import verify_codex_runtime_snapshot

    return verify_codex_runtime_snapshot(
        root / "bin/codex", manifest, expected_manifest_sha256=expected or digest(manifest)
    )


def test_complete_snapshot_is_admitted_without_spawning(snapshot):
    root, manifest, _ = snapshot
    result = check(root, manifest)
    assert result["version"] == "0.164.0"
    assert result["asset_count"] == 5
    assert result["code_mode_host"] == str(root / "bin/codex-code-mode-host")


@pytest.mark.parametrize(
    "name",
    ["codex-package.json", "bin/codex-code-mode-host", "codex-path/rg", "codex-resources/bwrap"],
)
def test_missing_runtime_asset_is_refused(snapshot, name):
    root, manifest, _ = snapshot
    (root / name).unlink()
    with pytest.raises(RuntimeError, match="codex_runtime_snapshot_unavailable"):
        check(root, manifest)


def test_bare_binary_snapshot_is_refused_even_if_only_binary_is_declared(snapshot):
    root, manifest, data = snapshot
    data["assets"] = {"bin/codex": data["assets"]["bin/codex"]}
    manifest.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        check(root, manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        "changed",
        "symlink",
        "not_executable",
        "undeclared",
        "missing_expected_hash",
        "unsafe_manifest",
        "wrong_root",
        "host_override",
        "traversal",
    ],
)
def test_altered_or_redirected_snapshot_is_refused(snapshot, mutation, monkeypatch, tmp_path):
    root, manifest, data = snapshot
    host = root / "bin/codex-code-mode-host"
    if mutation == "changed":
        host.write_bytes(b"altered")
    elif mutation == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(host.read_bytes())
        host.unlink()
        host.symlink_to(outside)
    elif mutation == "not_executable":
        host.chmod(0o600)
    elif mutation == "undeclared":
        (root / "codex-resources/extra").write_bytes(b"undeclared")
    elif mutation == "missing_expected_hash":
        with pytest.raises(RuntimeError):
            check(root, manifest, "0" * 64)
        return
    elif mutation == "unsafe_manifest":
        manifest.chmod(0o644)
    elif mutation == "wrong_root":
        data["root"] = str(tmp_path)
    elif mutation == "host_override":
        monkeypatch.setenv("CODEX_CODE_MODE_HOST_PATH", str(host))
    elif mutation == "traversal":
        data["assets"]["../outside"] = "0" * 64
    manifest.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        check(root, manifest)


def test_manifest_bytes_are_independently_bound(snapshot):
    root, manifest, data = snapshot
    original = digest(manifest)
    host = root / "bin/codex-code-mode-host"
    host.write_bytes(b"different host")
    data["assets"]["bin/codex-code-mode-host"] = digest(host)
    manifest.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        check(root, manifest, original)


def test_declared_voice_assets_are_preserved_and_checked(snapshot):
    root, manifest, data = snapshot
    voice = root / "codex-resources/voice/runtime.json"
    voice.parent.mkdir(mode=0o700)
    voice.write_bytes(b"{}")
    data["assets"]["codex-resources/voice/runtime.json"] = digest(voice)
    manifest.write_text(json.dumps(data))
    assert check(root, manifest)["asset_count"] == 6
    voice.write_bytes(b"changed")
    with pytest.raises(RuntimeError):
        check(root, manifest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("layoutVersion", True),
        ("version", "0.163.0"),
        ("target", "other"),
        ("variant", "other"),
        ("entrypoint", "../outside"),
        ("resourcesDir", "other"),
        ("pathDir", "other"),
    ],
)
def test_rehashed_but_wrong_package_metadata_is_refused(snapshot, field, value):
    root, manifest, data = snapshot
    metadata = root / "codex-package.json"
    body = json.loads(metadata.read_text())
    body[field] = value
    metadata.write_text(json.dumps(body))
    data["assets"]["codex-package.json"] = digest(metadata)
    manifest.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        check(root, manifest)


@pytest.mark.parametrize(
    "damage", ["duplicate", "oversized", "symlink_directory", "hardlinked_host"]
)
def test_ambiguous_or_unbounded_snapshot_is_refused(snapshot, damage, tmp_path):
    root, manifest, data = snapshot
    if damage == "duplicate":
        manifest.write_text(json.dumps(data)[:-1] + ',"root":' + json.dumps(str(root)) + "}")
    elif damage == "oversized":
        manifest.write_text(" " * 65537 + json.dumps(data))
    elif damage == "symlink_directory":
        outside = tmp_path / "bin-outside"
        (root / "bin").rename(outside)
        (root / "bin").symlink_to(outside, target_is_directory=True)
    else:
        import os

        os.link(root / "bin/codex-code-mode-host", tmp_path / "shared-host")
    with pytest.raises(RuntimeError):
        check(root, manifest)


def _live(monkeypatch):
    import importlib

    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    return importlib.import_module("test.live_provider.message_api_restart_live")


@pytest.mark.parametrize("complete", [True, False])
def test_live_runtime_admission_is_source_bound_and_records_only_fixed_metadata(
    snapshot, tmp_path, monkeypatch, complete
):
    live = _live(monkeypatch)
    root, manifest, _ = snapshot
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    state = live.RunState(
        provider="codex",
        run_root=run,
        workspace=run / "workspace",
        home=run / "workspace/home",
        source_root=Path(__file__).resolve().parents[2],
        audit_path=run / "audit",
    )
    monkeypatch.setattr(live.shutil, "which", lambda _: str(root / "bin/codex"))
    monkeypatch.setenv("CAO_SPEC023_CODEX_RUNTIME_MANIFEST", str(manifest))
    monkeypatch.setenv("CAO_SPEC023_CODEX_RUNTIME_SHA256", digest(manifest))
    if not complete:
        (root / "bin/codex-code-mode-host").unlink()
        with pytest.raises(live.AcceptanceFailure):
            live._admit_codex_runtime(state, "codex")
        assert state.failure_point == "runtime_admission"
        assert not (run / "codex-runtime-admission.json").exists()
    else:
        live._admit_codex_runtime(state, "codex")
        receipt = json.loads((run / "codex-runtime-admission.json").read_text())
        assert set(receipt) == {"schema", "version", "asset_count", "manifest_sha256"}
        assert receipt["asset_count"] == 5


def test_actual_journey_refuses_missing_host_before_credentials_or_postgres(
    snapshot, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    live = _live(monkeypatch)
    root, manifest, _ = snapshot
    (root / "bin/codex-code-mode-host").unlink()
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    repo = Path(__file__).resolve().parents[2]
    for name in live.PROVIDER_GATE_ENV.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(live.PROVIDER_GATE_ENV["codex"], "1")
    monkeypatch.setenv(live.SOURCE_ROOT_ENV, str(repo))
    monkeypatch.setenv(live.EVIDENCE_ROOT_ENV, str(evidence))
    monkeypatch.setenv("CAO_SPEC023_CODEX_RUNTIME_MANIFEST", str(manifest))
    monkeypatch.setenv("CAO_SPEC023_CODEX_RUNTIME_SHA256", digest(manifest))
    pins = {"synthetic": "pin"}
    monkeypatch.setattr(
        live,
        "_verify_selected_setup",
        lambda *_: (
            pins,
            pins,
            SimpleNamespace(SESSION_PREFIX="cao-"),
            object(),
            object(),
            object(),
            object(),
            object(),
        ),
    )
    monkeypatch.setattr(live, "_hash_source_bindings", lambda *_: pins)
    monkeypatch.setattr(live, "_hash_candidate_bindings", lambda *_: pins)
    monkeypatch.setattr(live, "_create_private_tmux_tmpdir", lambda: tmp_path / "unused-tmux")
    monkeypatch.setattr(live, "_pick_free_port", lambda: 41000)
    stages = []
    monkeypatch.setattr(
        live,
        "_provider_auth_helpers",
        lambda *_: (
            lambda *_a, **_k: SimpleNamespace(binary="codex"),
            lambda *_: stages.append("credentials"),
        ),
    )
    monkeypatch.setattr(live.shutil, "which", lambda _: str(root / "bin/codex"))

    def cleanup(state, **_kwargs):
        stages.append("cleanup")
        state.cleanup = "pass"
        return True

    monkeypatch.setattr(live, "_cleanup", cleanup)
    request = SimpleNamespace(
        getfixturevalue=lambda _: (_ for _ in ()).throw(
            AssertionError("PostgreSQL must not be acquired")
        )
    )
    receipt = live.run_selected_message_acceptance(request)
    assert receipt["result"] == "failed"
    assert receipt["failure_point"] == "runtime_admission"
    assert receipt["cleanup"] == "pass"
    assert stages == ["cleanup"]


@pytest.mark.parametrize(
    "name", ["CAO_SPEC023_CODEX_RUNTIME_MANIFEST", "CAO_SPEC023_CODEX_RUNTIME_SHA256"]
)
@pytest.mark.parametrize("value", [None, ""])
def test_live_admission_rejects_missing_or_empty_binding_before_reading_assets(
    snapshot, tmp_path, monkeypatch, name, value
):
    from test.live_provider import codex_runtime_snapshot as module

    live = _live(monkeypatch)
    root, manifest, _ = snapshot
    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "work",
        home=tmp_path / "work/home",
        source_root=Path(__file__).resolve().parents[2],
        audit_path=tmp_path / "audit",
    )
    monkeypatch.setattr(live.shutil, "which", lambda _: str(root / "bin/codex"))
    monkeypatch.setenv("CAO_SPEC023_CODEX_RUNTIME_MANIFEST", str(manifest))
    monkeypatch.setenv("CAO_SPEC023_CODEX_RUNTIME_SHA256", digest(manifest))
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unbound snapshot must not be read")

    monkeypatch.setattr(module, "verify_codex_runtime_snapshot", forbidden)
    with pytest.raises(live.AcceptanceFailure):
        live._admit_codex_runtime(state, "codex")
    assert state.failure_point == "runtime_admission"


def test_live_admission_rejects_foreign_module_origin(snapshot, tmp_path, monkeypatch):
    live = _live(monkeypatch)
    foreign = tmp_path / "foreign/test/live_provider"
    foreign.mkdir(parents=True)
    (foreign / "codex_runtime_snapshot.py").write_text("# another checkout")
    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "work",
        home=tmp_path / "work/home",
        source_root=tmp_path / "foreign",
        audit_path=tmp_path / "audit",
    )
    with pytest.raises(live.AcceptanceFailure):
        live._admit_codex_runtime(state, "codex")
    assert state.failure_point == "runtime_admission"


def test_selected_binary_must_match_the_pinned_package_entrypoint(snapshot):
    from test.live_provider.codex_runtime_snapshot import verify_codex_runtime_snapshot

    root, manifest, _ = snapshot
    with pytest.raises(RuntimeError):
        verify_codex_runtime_snapshot(
            root / "bin/codex-code-mode-host", manifest, expected_manifest_sha256=digest(manifest)
        )
