"""Opt-in native PostgreSQL backup and isolated-restore acceptance.

Set CAO_PERSONAL_POSTGRES_RESTORE_ACCEPTANCE=1 and
CAO_PERSONAL_DOCKER_IMAGE=sha256:... to run against a sealed application image.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path
from test.integration.test_docker_personal_installation import (
    _DEFAULT_PERSONAL_IMAGE,
    _PERSONAL_IMAGE_ENV,
    _container_fingerprint,
    _existing_image_id,
    _new_test_image_tag,
    _private_test_home,
    _resolve_personal_image,
)
from types import SimpleNamespace

import httpx
import pytest

_ACCEPTANCE_ENV = "CAO_PERSONAL_POSTGRES_RESTORE_ACCEPTANCE"


def _write_private_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        metadata = path.lstat()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise AssertionError("PostgreSQL backup contains an unsupported filesystem object")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative + (b"\0dir\0" if path.is_dir() else b"\0file\0"))
        if path.is_file():
            digest.update(path.read_bytes())
        if metadata.st_mode & 0o077:
            raise AssertionError("PostgreSQL backup contains a non-private file or directory")
    return digest.hexdigest()


def _assert_owned_profile(installer, root: Path, private_home: Path):
    deployment = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    instance = deployment.get("instance_id")
    if not isinstance(instance, str) or not re.fullmatch(r"[0-9a-f]{32}", instance):
        raise AssertionError("private deployment has no valid instance identity")
    config_path = root / "cao" / "database-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    managed = config.get("managed")
    network = managed.get("profile_network") if isinstance(managed, dict) else None
    expected_network = f"cao-profile-{instance[:12]}"
    if network != expected_network:
        raise AssertionError("private profile network identity differs")
    network_state = installer.inspect_network(network)
    network_labels = (network_state or {}).get("Labels") or {}
    network_id = (network_state or {}).get("Id")
    expected_owner = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()[
        :16
    ]
    if (
        network_state is None
        or network_state.get("Name") != expected_network
        or not isinstance(network_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", network_id)
        or network_labels.get(installer.DATABASE_SERVICE_LABEL) != "profile-network"
        or network_labels.get(installer.DATABASE_INSTANCE_LABEL) != instance
        or network_labels.get(installer.DATABASE_OWNER_LABEL) != expected_owner
    ):
        raise AssertionError("private profile network is not owned by this test")
    main_name = managed.get("main_container")
    peer_name = managed.get("peer_container")
    if main_name != f"cao-main-pg-{instance[:12]}" or not isinstance(peer_name, str):
        raise AssertionError("managed PostgreSQL service identities differ")
    peer_root = private_home / ".local/share/cao-peer-postgres"
    peer_settings_path = peer_root / "service.json"
    if not peer_settings_path.is_file() or peer_settings_path.is_symlink():
        raise AssertionError("private peer PostgreSQL identity is unavailable")
    peer = json.loads(peer_settings_path.read_text(encoding="utf-8"))
    if peer.get("container") != peer_name or peer.get("isolated") is not False:
        raise AssertionError("private peer PostgreSQL profile identity differs")
    expected_containers = {
        main_name: {
            installer.DATABASE_SERVICE_LABEL: "main-auth",
            installer.DATABASE_OWNER_LABEL: instance,
            installer.DATABASE_INSTANCE_LABEL: instance,
        },
        peer_name: {
            installer.DATABASE_SERVICE_LABEL: "peer",
            installer.DATABASE_OWNER_LABEL: peer["owner_id"],
            installer.DATABASE_INSTANCE_LABEL: peer["store_id"],
        },
    }
    for name, labels_expected in expected_containers.items():
        state = installer.inspect(name)
        labels = (state or {}).get("Config", {}).get("Labels") or {}
        attached = (state or {}).get("NetworkSettings", {}).get("Networks") or {}
        if (
            state is None
            or not state.get("State", {}).get("Running")
            or state.get("Config", {}).get("Image") != installer.POSTGRES_IMAGE
            or any(labels.get(key) != value for key, value in labels_expected.items())
            or network not in attached
        ):
            raise AssertionError("managed PostgreSQL container is not owned by this test")
    return config, network, network_id, main_name, peer_name


def _cleanup_owned_database_resources_by_id(
    installer, root: Path, private_home: Path, expected_network_id: str | None
) -> None:
    deployment_path = root / "deployment.json"
    if not deployment_path.is_file() or deployment_path.is_symlink():
        return
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    instance_id = deployment.get("instance_id")
    if not isinstance(instance_id, str) or not re.fullmatch(r"[0-9a-f]{32}", instance_id):
        raise AssertionError("test deployment does not have a valid private instance identity")

    expected_containers = {
        "cao-main-pg-"
        + instance_id[:12]: {
            "org.cao.personal.database.service": "main-auth",
            "org.cao.personal.database.owner": instance_id,
            "org.cao.personal.database.instance": instance_id,
        }
    }
    peer_root = private_home / ".local/share/cao-peer-postgres"
    peer_settings_path = peer_root / "service.json"
    if peer_settings_path.is_file() and not peer_settings_path.is_symlink():
        peer = json.loads(peer_settings_path.read_text(encoding="utf-8"))
        if peer.get("isolated") is not False:
            raise AssertionError("test peer store is not the expected private shared-peer profile")
        expected_containers[peer["container"]] = {
            "org.cao.personal.database.service": "peer",
            "org.cao.personal.database.owner": peer["owner_id"],
            "org.cao.personal.database.instance": peer["store_id"],
        }

    for name, expected_labels in expected_containers.items():
        container = installer.inspect(name)
        if container is None:
            continue
        container_id = container.get("Id")
        labels = container.get("Config", {}).get("Labels") or {}
        if (
            container.get("Name") != f"/{name}"
            or not isinstance(container_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", container_id)
            or any(labels.get(key) != value for key, value in expected_labels.items())
        ):
            raise AssertionError("refusing to remove a PostgreSQL container not owned by this test")
        installer.docker(["rm", "--force", container_id])

    network_name = "cao-profile-" + instance_id[:12]
    network = installer.inspect_network(network_name)
    if network is not None:
        network_id = network.get("Id")
        labels = network.get("Labels") or {}
        expected_owner = hashlib.sha256(
            f"{socket.gethostname()}:{os.getuid()}".encode()
        ).hexdigest()[:16]
        if (
            network.get("Name") != network_name
            or not isinstance(network_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", network_id)
            or (expected_network_id is not None and network_id != expected_network_id)
            or labels.get("org.cao.personal.database.service") != "profile-network"
            or labels.get("org.cao.personal.database.instance") != instance_id
            or labels.get("org.cao.personal.database.owner") != expected_owner
        ):
            raise AssertionError("refusing to remove a profile network not owned by this test")
        installer.docker(["network", "rm", network_id])

    for owned_path, expected_parent in (
        (root / "cao/postgres", root / "cao"),
        (peer_root, private_home / ".local/share"),
    ):
        if owned_path.exists() or owned_path.is_symlink():
            if owned_path.is_symlink() or owned_path.parent != expected_parent:
                raise AssertionError(
                    "refusing to remove PostgreSQL state outside this test profile"
                )
            shutil.rmtree(owned_path)


def _create_database(installer, container: str, database: str, owner: str, connected_database: str):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database) or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,62}", owner
    ):
        raise AssertionError("private restore database identity is invalid")
    statement = f'CREATE DATABASE "{database}" OWNER "{owner}"'
    result = installer.docker(
        [
            "exec",
            "--user",
            "postgres",
            container,
            "psql",
            "--username",
            owner,
            "--dbname",
            connected_database,
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            statement,
        ],
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError("could not create a private PostgreSQL restore database")


_SOURCE_SNAPSHOT_CODE = r"""
import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/opt/cao/scripts")
from psycopg import sql
from cli_agent_orchestrator.clients.postgres import connect
import personal_deployment as deployment

root = Path(sys.argv[1])
mode = sys.argv[2]
marker = sys.argv[3]
snapshot = {}
for scope in ("main", "auth", "peer"):
    target = deployment.database_target(root, scope)
    table = sql.Identifier(target.schema, "_cao_import_receipts")
    with connect(target.migration_target()) as connection:
        with connection.transaction():
            if mode == "seed":
                count = connection.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(table)
                ).fetchone()[0]
                if count != 0:
                    raise RuntimeError("private PostgreSQL profile already has import receipts")
                profile_id = f"historical-{marker}-{scope}"
                source_identity = json.dumps(
                    {"kind": "historical-source", "marker": marker, "scope": scope},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                target_identity = json.dumps(
                    list(target.identity),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                target_identity_hash = hashlib.sha256(
                    target_identity.encode("utf-8")
                ).hexdigest()
                table_manifest = json.dumps(
                    {"historical_source": {"scope": scope, "row_count": 0}},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    sql.SQL(
                        "INSERT INTO {} "
                        "(receipt_id,scope,profile_id,source_path,source_digest,"
                        "source_schema_fingerprint,source_identity,target_store_id,"
                        "target_schema,target_identity_hash,state,table_manifest,verified_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified',%s,CURRENT_TIMESTAMP)"
                    ).format(table),
                    (
                        str(uuid.uuid4()),
                        scope,
                        profile_id,
                        f"historical-source://{marker}/{scope}",
                        hashlib.sha256(f"{marker}:{scope}:source".encode()).hexdigest(),
                        hashlib.sha256(f"{marker}:{scope}:schema".encode()).hexdigest(),
                        source_identity,
                        target.store_id,
                        target.schema,
                        target_identity_hash,
                        table_manifest,
                    ),
                )
            profile_id = f"historical-{marker}-{scope}"
            receipts = connection.execute(
                sql.SQL(
                    "SELECT receipt_id::text,scope,profile_id,source_path,source_digest,"
                    "source_schema_fingerprint,source_identity,target_store_id::text,"
                    "target_schema,target_identity_hash,state,table_manifest,"
                    "created_at::text,verified_at::text FROM {} ORDER BY receipt_id"
                ).format(table),
            ).fetchall()
            if (
                len(receipts) != 1
                or receipts[0][2] != profile_id
                or receipts[0][10] != "verified"
            ):
                raise RuntimeError("historical PostgreSQL import receipt is missing")
            identity = connection.execute(sql.SQL("SELECT scope,store_id FROM {}._cao_store_identity WHERE singleton=1").format(sql.Identifier(target.schema))).fetchone()
            migrations = connection.execute(sql.SQL("SELECT scope,component,version,checksum,manifest_digest FROM {}._cao_schema_migrations ORDER BY scope,component,version").format(sql.Identifier(target.schema))).fetchall()
            row = {
                "physical": [target.host, int(target.port), target.database, target.schema, target.store_id],
                "identity": list(identity),
                "migrations": [list(item) for item in migrations],
                "import_receipts": [list(item) for item in receipts],
            }
            if scope == "main":
                recovery = connection.execute(sql.SQL("SELECT execution_state,installation_uuid,source_installation_uuid,bundle_digest,restore_receipt FROM {}.work_recovery_context WHERE singleton=1").format(sql.Identifier(target.schema))).fetchone()
                row["work_recovery"] = list(recovery)
            snapshot[scope] = row
print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
"""


_RESTORE_VERIFY_CODE = r"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/cao/scripts")
from psycopg import sql
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.models.browser_auth import BrowserAuthError
import personal_deployment as deployment

root = Path(sys.argv[1])
expected = json.loads(sys.argv[2])
expected_bundle_digest = sys.argv[3]
marker = sys.argv[4]
cookie_secret = sys.stdin.read().strip()
actual = {}
for scope in ("main", "auth", "peer"):
    target = deployment.database_target(root, scope)
    table = sql.Identifier(target.schema, "_cao_import_receipts")
    with connect(target.migration_target(), readonly=True) as connection:
        identity = connection.execute(sql.SQL("SELECT scope,store_id FROM {}._cao_store_identity WHERE singleton=1").format(sql.Identifier(target.schema))).fetchone()
        migrations = connection.execute(sql.SQL("SELECT scope,component,version,checksum,manifest_digest FROM {}._cao_schema_migrations ORDER BY scope,component,version").format(sql.Identifier(target.schema))).fetchall()
        profile_id = f"historical-{marker}-{scope}"
        receipts = connection.execute(
            sql.SQL(
                "SELECT receipt_id::text,scope,profile_id,source_path,source_digest,"
                "source_schema_fingerprint,source_identity,target_store_id::text,"
                "target_schema,target_identity_hash,state,table_manifest,"
                "created_at::text,verified_at::text FROM {} ORDER BY receipt_id"
            ).format(table),
        ).fetchall()
        if (
            len(receipts) != 1
            or receipts[0][2] != profile_id
            or receipts[0][10] != "verified"
        ):
            raise RuntimeError("restored historical PostgreSQL receipt is missing")
        actual[scope] = {
            "physical": [target.host, int(target.port), target.database, target.schema, target.store_id],
            "identity": list(identity),
            "migrations": [list(item) for item in migrations],
            "import_receipts": [list(item) for item in receipts],
        }
        if scope == "main":
            recovery = connection.execute(sql.SQL("SELECT execution_state,installation_uuid,source_installation_uuid,bundle_digest,restore_receipt FROM {}.work_recovery_context WHERE singleton=1").format(sql.Identifier(target.schema))).fetchone()
            actual[scope]["work_recovery"] = list(recovery)

for scope in ("main", "auth", "peer"):
    old = expected[scope]
    new = actual[scope]
    if new["physical"][2] == old["physical"][2]:
        raise RuntimeError("restore reused its source PostgreSQL database")
    if new["physical"][:2] + new["physical"][3:] != old["physical"][:2] + old["physical"][3:]:
        raise RuntimeError("restore changed a logical PostgreSQL target identity")
    if new["identity"] != old["identity"] or new["migrations"] != old["migrations"]:
        raise RuntimeError("restored PostgreSQL schema receipts differ from the source")
    if new["import_receipts"] != old["import_receipts"]:
        raise RuntimeError("restored historical PostgreSQL receipts differ from the source")
    destination_identity = json.dumps(
        new["physical"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    destination_identity_hash = hashlib.sha256(destination_identity.encode("utf-8")).hexdigest()
    if new["import_receipts"][0][0][9] == destination_identity_hash:
        raise RuntimeError("historical receipt target identity was rewritten during restore")

namespaces = {tuple(actual[scope]["physical"][:4]) for scope in ("main", "auth", "peer")}
if len(namespaces) != 3:
    raise RuntimeError("restored PostgreSQL scopes are not physically distinct")
recovery = actual["main"]["work_recovery"]
source_recovery = expected["main"]["work_recovery"]
if recovery[0] != "blocked_restore" or recovery[2] != source_recovery[1]:
    raise RuntimeError("restored Work data was not fenced as blocked_restore")
if recovery[3] != expected_bundle_digest or not recovery[4] or len(recovery[4]) != 64:
    raise RuntimeError("restored Work recovery receipt is incomplete")
if recovery[1] == recovery[2]:
    raise RuntimeError("restored Work installation identity was not renewed")

try:
    deployment.browser_auth_service(root, validate=False).session(cookie_secret)
except BrowserAuthError as error:
    if error.code != "session_revoked":
        raise
else:
    raise RuntimeError("restored browser session cookie remains active")

print(json.dumps({"scopes": sorted(actual), "browser_cookie": "session_revoked", "work_state": recovery[0]}, sort_keys=True, separators=(",", ":")))
"""


def _docker_verify_restore(
    installer, image_id, network, mount_root, destination, expected, bundle_digest, marker, cookie
):
    command = [
        "run",
        "--rm",
        "--init",
        "--interactive",
        "--network",
        network,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--env",
        f"HOME={destination}",
        "--env",
        f"CAO_HOME_DIR={destination / 'cao'}",
        "--env",
        f"CAO_DATABASE_CONFIG_FILE={destination / 'cao' / 'database-config.json'}",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--mount",
        installer.mount_argument(mount_root, readonly=True),
        "--entrypoint",
        "python",
        image_id,
        "-c",
        _RESTORE_VERIFY_CODE,
        str(destination),
        json.dumps(expected, sort_keys=True, separators=(",", ":")),
        bundle_digest,
        marker,
    ]
    result = installer.docker(command, check=False, input_data=cookie)
    if result.returncode != 0:
        raise AssertionError("native PostgreSQL restore verification container failed")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise AssertionError("native PostgreSQL restore verification output is invalid") from error


@pytest.mark.integration
def test_real_postgres_backup_restores_isolated_scopes_without_activation(tmp_path, monkeypatch):
    if os.environ.get(_ACCEPTANCE_ENV) != "1":
        pytest.skip(f"set {_ACCEPTANCE_ENV}=1 for native PostgreSQL backup/restore acceptance")
    private_home = _private_test_home(tmp_path, monkeypatch)
    assert tmp_path.stat().st_mode & 0o077 == 0
    repo = Path(__file__).resolve().parents[2]
    installer = _load_installer(repo)
    _image_reference, image_id = _resolve_personal_image(installer, os.environ)
    root = (tmp_path / "source-profile").resolve()
    backup = (tmp_path / "native-backup").resolve()
    destination = (tmp_path / "restored-profile").resolve()
    restore_config_path = (tmp_path / "restore-target-config.json").resolve()
    name = "cao-postgres-restore-" + os.urandom(6).hex()
    port = _free_port_pair()
    worker_tag = _new_test_image_tag(installer, "cao-personal-restore-worker")
    protected_image_ids = {
        _DEFAULT_PERSONAL_IMAGE: _existing_image_id(installer, _DEFAULT_PERSONAL_IMAGE),
        "cao-personal-worker:local": _existing_image_id(installer, "cao-personal-worker:local"),
    }
    operator_container = _container_fingerprint(installer, "cao-personal")
    worker_image_id = None
    network_id = None
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"Origin": f"http://127.0.0.1:{port}", "X-CAO-Browser": "1"},
        trust_env=False,
        timeout=15,
    )
    try:
        original_build = installer.build

        def build_with_private_worker_tag(tag, *, worker=False):
            nonlocal worker_image_id
            if worker:
                if tag != "cao-personal-worker:local":
                    raise AssertionError("installer requested an unexpected worker image tag")
                worker_image_id = original_build(worker_tag, worker=True)
                return worker_image_id
            return original_build(tag, worker=worker)

        with monkeypatch.context() as patch:
            patch.setattr(installer, "build", build_with_private_worker_tag)
            installer.install(
                SimpleNamespace(
                    root=root,
                    name=name,
                    image=image_id,
                    no_build=True,
                    port=port,
                    workspace=[],
                    workspace_readonly=[],
                    database_config=None,
                )
            )
        if worker_image_id is None or installer.image_id(worker_tag) != worker_image_id:
            raise AssertionError("private worker image tag does not resolve to its image ID")
        worker_source = repo / "src/cli_agent_orchestrator/backends/docker_work_supervisor.c"
        expected_worker_source = hashlib.sha256(worker_source.read_bytes()).hexdigest()
        worker_label = installer.docker(
            [
                "image",
                "inspect",
                "--format",
                '{{ index .Config.Labels "org.cao.work.supervisor.source_sha256" }}',
                worker_image_id,
            ]
        ).stdout.strip()
        if worker_label != expected_worker_source:
            raise AssertionError("private worker image does not match the current source digest")

        config, network, network_id, main_container, peer_container = _assert_owned_profile(
            installer, root, private_home
        )
        app = installer.inspect(name)
        if (
            app is None
            or app.get("Name") != f"/{name}"
            or not isinstance(app.get("Id"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", app["Id"])
            or app.get("Image") != image_id
            or app.get("Config", {}).get("Labels", {}).get(installer.LABEL) != str(root)
            or network not in (app.get("NetworkSettings", {}).get("Networks") or {})
            or not app.get("State", {}).get("Running")
        ):
            raise AssertionError("personal app container is outside the private PostgreSQL profile")
        source_container_id = app["Id"]

        assert client.get("/").status_code == 200
        token_code = "import sys;sys.path.insert(0,'/opt/cao/scripts');import personal_deployment as p;from pathlib import Path;print(p.mint_token(Path(sys.argv[1])))"
        token = installer.docker(
            ["exec", name, "python", "-c", token_code, str(root)]
        ).stdout.strip()
        setup = client.post(
            "/auth/setup",
            headers={"Authorization": "Bearer " + token},
            json={
                "username": "postgres-restore-proof",
                "password": "RestoreProof!2026",
                "remember": True,
            },
        )
        assert setup.status_code == 200
        deployment = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
        cookie_name = "cao_browser_" + deployment["browser_login"]["installation_id"]
        cookie = client.cookies.get(cookie_name)
        if not cookie:
            raise AssertionError("browser setup did not return a private session cookie")
        assert client.get("/auth/session").status_code == 200

        marker = "native-backup-" + os.urandom(8).hex()
        seeded = installer.docker(
            ["exec", name, "python", "-c", _SOURCE_SNAPSHOT_CODE, str(root), "seed", marker],
            check=False,
        )
        if seeded.returncode != 0:
            raise AssertionError("could not seed historical source PostgreSQL receipts")
        source_snapshot = json.loads(seeded.stdout)
        if set(source_snapshot) != {"main", "auth", "peer"}:
            raise AssertionError("source PostgreSQL backup did not cover all scopes")
        if source_snapshot["main"]["work_recovery"][0] != "normal":
            raise AssertionError("source Work recovery state is not normal before backup")
        source_file_hashes = {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in (
                "deployment.json",
                "issuer.pem",
                "cao/database-config.json",
            )
        }

        stop_attempted = False
        try:
            stop_attempted = True
            stopped = installer.docker(["stop", source_container_id], check=False)
            if stopped.returncode != 0:
                raise AssertionError("could not stop the verified private app container for backup")
            stopped_state = installer.inspect(source_container_id)
            if (
                stopped_state is None
                or stopped_state.get("Id") != source_container_id
                or stopped_state.get("Name") != f"/{name}"
                or stopped_state.get("State", {}).get("Running")
                or stopped_state.get("Config", {}).get("Labels", {}).get(installer.LABEL)
                != str(root)
            ):
                raise AssertionError(
                    "private app container identity changed while stopping for backup"
                )
            installer.admin(image_id, root, ["backup", str(backup)], network=network)
        finally:
            if stop_attempted:
                stopped_state = installer.inspect(source_container_id)
                if (
                    stopped_state is None
                    or stopped_state.get("Id") != source_container_id
                    or stopped_state.get("Name") != f"/{name}"
                    or stopped_state.get("Config", {}).get("Labels", {}).get(installer.LABEL)
                    != str(root)
                    or stopped_state.get("Image") != image_id
                ):
                    raise AssertionError(
                        "verified private app container identity changed during backup"
                    )
                if not stopped_state.get("State", {}).get("Running"):
                    installer.docker(["start", source_container_id])
                installer.wait_healthy(name, port)
                restarted_state = installer.inspect(name)
                if (
                    restarted_state is None
                    or restarted_state.get("Id") != source_container_id
                    or not restarted_state.get("State", {}).get("Running")
                ):
                    raise AssertionError("verified private app container did not restart in place")
                if client.get("/auth/session").status_code != 200:
                    raise AssertionError(
                        "source browser session did not survive the backup restart"
                    )
        source_container_before = _container_fingerprint(installer, name)
        if any(
            hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest
            for relative, digest in source_file_hashes.items()
        ):
            raise AssertionError("native backup changed the source deployment identity")
        if not backup.is_dir() or backup.is_symlink():
            raise AssertionError("native PostgreSQL backup was not published privately")
        backup_digest = _tree_digest(backup)
        manifest_path = backup / "backup-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dump_records = manifest.get("dumps")
        if (
            not isinstance(dump_records, list)
            or not dump_records
            or any(not isinstance(item, dict) for item in dump_records)
        ):
            raise AssertionError("native PostgreSQL backup manifest has no logical dumps")
        dump_scopes = []
        for item in dump_records:
            scopes = item.get("scopes")
            if (
                not isinstance(scopes, list)
                or not scopes
                or any(scope not in {"main", "auth", "peer"} for scope in scopes)
            ):
                raise AssertionError("native PostgreSQL backup scope entry is invalid")
            dump_scopes.extend(scopes)
        if sorted(dump_scopes) != ["auth", "main", "peer"]:
            raise AssertionError("native PostgreSQL backup manifest omits a scope")
        dump_names = []
        for item in dump_records:
            filename = item.get("file")
            digest = item.get("sha256")
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise AssertionError("native PostgreSQL backup dump manifest entry is invalid")
            dump_names.append(filename)
        if len(dump_names) != len(set(dump_names)):
            raise AssertionError("native PostgreSQL backup manifest repeats a dump")
        postgres_dump_dir = backup / "postgres"
        if postgres_dump_dir.is_symlink() or not postgres_dump_dir.is_dir():
            raise AssertionError("native PostgreSQL backup dump directory is unavailable")
        actual_dump_names = {item.name for item in postgres_dump_dir.iterdir()}
        if actual_dump_names != set(dump_names):
            raise AssertionError("backup PostgreSQL files do not match the manifest allowlist")
        for item in dump_records:
            dump_path = postgres_dump_dir / item["file"]
            if (
                dump_path.is_symlink()
                or not dump_path.is_file()
                or hashlib.sha256(dump_path.read_bytes()).hexdigest() != item["sha256"]
            ):
                raise AssertionError("native PostgreSQL dump differs from its manifest digest")
        excluded_names = {
            "account.lock",
            "client.env",
            "database-config.json",
            "mcp-bearer.jwt",
            "service.lock",
            "web-link.txt",
        }
        leaked_paths = [
            item.relative_to(backup).as_posix()
            for item in backup.rglob("*")
            if item.name in excluded_names
        ]
        if leaked_paths or any("pg-client-" in item.parts for item in backup.rglob("*")):
            raise AssertionError("portable backup contains excluded credentials or client state")
        if (backup / "cao" / "postgres").exists():
            raise AssertionError("portable backup contains raw PostgreSQL state")
        if any(
            item.is_file() and (item.suffix == ".db" or item.name.endswith(("-wal", "-shm")))
            for item in backup.rglob("*")
        ):
            raise AssertionError("portable backup contains raw database artifacts")
        backup_issuer = backup / "issuer.pem"
        if (
            backup_issuer.is_symlink()
            or not backup_issuer.is_file()
            or hashlib.sha256(backup_issuer.read_bytes()).hexdigest()
            != source_file_hashes["issuer.pem"]
        ):
            raise AssertionError("private backup did not preserve the signing identity")

        suffix = os.urandom(6).hex()
        restore_main_database = "cao_restore_" + suffix
        restore_peer_database = "peer_restore_" + suffix
        source_targets = config["targets"]
        main_owner = source_targets["main"]["migration_user"]
        peer_owner = source_targets["peer"]["migration_user"]
        _create_database(
            installer,
            main_container,
            restore_main_database,
            main_owner,
            source_targets["main"]["database"],
        )
        _create_database(
            installer,
            peer_container,
            restore_peer_database,
            peer_owner,
            source_targets["peer"]["database"],
        )
        restore_targets = json.loads(json.dumps(source_targets))
        restore_targets["main"]["database"] = restore_main_database
        restore_targets["auth"]["database"] = restore_main_database
        restore_targets["peer"]["database"] = restore_peer_database
        for target in restore_targets.values():
            for key in ("secret_file", "migration_secret_file"):
                secret = Path(target[key])
                if not secret.is_absolute():
                    secret = root / "cao" / secret
                target[key] = str(secret.resolve())
        restore_config = {
            "version": 1,
            "targets": restore_targets,
            "managed": config.get("managed"),
        }
        _write_private_file(
            restore_config_path,
            json.dumps(restore_config, sort_keys=True, indent=2).encode("utf-8"),
        )

        installer.admin(
            image_id,
            destination,
            ["restore", str(backup), "--database-config", str(restore_config_path)],
            network=network,
            database_config=restore_config_path,
        )
        if not destination.is_dir() or destination.is_symlink():
            raise AssertionError("native PostgreSQL restore was not published")
        installer.admin(image_id, destination, ["verify-database"], network=network)
        verification = _docker_verify_restore(
            installer,
            image_id,
            network,
            root.parent,
            destination,
            source_snapshot,
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            marker,
            cookie,
        )
        if verification != {
            "scopes": ["auth", "main", "peer"],
            "browser_cookie": "session_revoked",
            "work_state": "blocked_restore",
        }:
            raise AssertionError("native PostgreSQL restore proof differs")

        if client.get("/auth/session").status_code != 200:
            raise AssertionError("restore changed the source browser session")
        source_after = installer.docker(
            ["exec", name, "python", "-c", _SOURCE_SNAPSHOT_CODE, str(root), "read", marker],
            check=False,
        )
        if source_after.returncode != 0 or json.loads(source_after.stdout) != source_snapshot:
            raise AssertionError("native restore changed source PostgreSQL data or receipts")
        if _tree_digest(backup) != backup_digest:
            raise AssertionError("native PostgreSQL restore changed the source backup")
        if any(
            hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest
            for relative, digest in source_file_hashes.items()
        ):
            raise AssertionError("native restore changed the source deployment identity")
        if _container_fingerprint(installer, name) != source_container_before:
            raise AssertionError("native restore changed the running source container")
        destination_containers = installer.docker(
            [
                "ps",
                "--all",
                "--filter",
                f"label={installer.LABEL}={destination}",
                "--format",
                "{{.ID}}",
            ],
            check=False,
        )
        if destination_containers.returncode != 0 or destination_containers.stdout.strip():
            raise AssertionError("native restore activated a personal service for its destination")
    finally:
        client.close()
        installed = installer.docker(
            [
                "ps",
                "--all",
                "--filter",
                f"label={installer.LABEL}={root}",
                "--format",
                "{{.ID}}",
            ],
            check=False,
        )
        for container_id in installed.stdout.split():
            installer.docker(["rm", "--force", container_id], check=False)
        _cleanup_owned_database_resources_by_id(installer, root, private_home, network_id)
        installer.docker(["image", "rm", worker_tag], check=False)
        for reference, protected_id in protected_image_ids.items():
            assert _existing_image_id(installer, reference) == protected_id
        assert _container_fingerprint(installer, "cao-personal") == operator_container


def _load_installer(repo: Path):
    spec = importlib.util.spec_from_file_location(
        "docker_install_postgres_restore_acceptance", repo / "scripts/docker_install.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the personal Docker installer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port_pair() -> int:
    """Choose a currently free API/JWKS pair in the installer's accepted range."""
    start = 20000 + int.from_bytes(os.urandom(2), "big") % 40000
    for offset in range(40000):
        port = 20000 + ((start - 20000 + offset) % 40000)
        if port in (8079, 8080) or port + 1 > 65534:
            continue
        sockets = []
        try:
            for candidate in (port, port + 1):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sockets.append(sock)
                sock.bind(("127.0.0.1", candidate))
            return port
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("could not find a free loopback API/JWKS port pair")


def _write_private_python_home_wrapper(private_home: Path) -> Path:
    """Resolve test state privately and optionally capture Docker exec failures."""
    wrapper_dir = private_home / "bin"
    wrapper_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    wrapper = wrapper_dir / "python3"
    private_home_literal = repr(str(private_home.resolve()))
    if not os.path.isabs(sys.executable):
        raise RuntimeError("the pinned Python executable must be absolute")
    expected_prefix_literal = repr(str(Path(sys.prefix).resolve()))
    source = """#!@PYTHON@
import hashlib
import importlib.util
import json
import os
import pathlib
import runpy
import stat
import subprocess
import sys

account_home = pathlib.Path.home().resolve()
private_home = pathlib.Path(@PRIVATE_HOME@).resolve()
expected_prefix = pathlib.Path(@EXPECTED_PREFIX@).resolve()
if private_home == account_home:
    raise SystemExit("private test home must differ from account home")
if "HOME" in os.environ:
    raise SystemExit("private Python wrapper must not receive HOME")
if pathlib.Path(sys.prefix).resolve() != expected_prefix:
    raise SystemExit("private Python wrapper lost the pinned environment")
pathlib.Path.home = classmethod(lambda _cls: private_home)
if pathlib.Path.home().resolve() != private_home:
    raise SystemExit("private Path.home resolution failed")
arguments = sys.argv[1:]
if not arguments:
    raise SystemExit("Python script argument is required")
script, *script_arguments = arguments
sys.argv = [script, *script_arguments]

diagnostic_dir_text = os.environ.get("CAO_TEST_DOCKER_EXEC_DIAGNOSTIC_DIR")
if not diagnostic_dir_text or pathlib.Path(script).name != "docker_install.py":
    runpy.run_path(script, run_name="__main__")
    raise SystemExit(0)

diagnostic_dir = pathlib.Path(diagnostic_dir_text)
directory_info = diagnostic_dir.lstat()
if (
    diagnostic_dir.is_symlink()
    or not diagnostic_dir.is_dir()
    or directory_info.st_uid != os.getuid()
    or directory_info.st_mode & 0o077
):
    raise SystemExit("Docker diagnostic directory is not private")

def _docker_exec_program(arguments):
    remaining = list(arguments[1:])
    index = 0
    takes_value = {"--user", "--workdir", "--env", "--env-file", "--detach-keys"}
    while index < len(remaining) and str(remaining[index]).startswith("-"):
        option = str(remaining[index])
        index += 1
        if option in takes_value and index < len(remaining):
            index += 1
    if index + 1 >= len(remaining):
        return "unknown"
    return pathlib.Path(str(remaining[index + 1])).name

def _record_docker_exec_error(arguments, result):
    program = _docker_exec_program(arguments)
    if program == "pg_isready":
        return
    stderr = result.stderr
    if isinstance(stderr, bytes):
        stderr_bytes = stderr
    elif isinstance(stderr, str):
        stderr_bytes = stderr.encode("utf-8", errors="replace")
    else:
        stderr_bytes = b""
    digest = hashlib.sha256(stderr_bytes).hexdigest()
    error_text = stderr_bytes.decode("utf-8", errors="replace").casefold()
    if "fatal:" in error_text:
        error_type = "postgres_fatal"
    elif "error:" in error_text and program == "psql":
        error_type = "postgres_sql_error"
    elif "permission denied" in error_text:
        error_type = "permission_denied"
    elif "not found" in error_text:
        error_type = "not_found"
    elif "usage:" in error_text:
        error_type = "command_usage_error"
    else:
        error_type = "unclassified"
    index = 1
    while True:
        stem = f"docker-exec-{index:04d}"
        raw_path = diagnostic_dir / f"{stem}.stderr"
        record_path = diagnostic_dir / f"{stem}.json"
        try:
            descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            index += 1
            continue
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(stderr_bytes)
        os.chmod(raw_path, 0o600)
        record = {
            "phase": "docker_exec",
            "program": program,
            "exit_code": result.returncode,
            "stderr_bytes": len(stderr_bytes),
            "stderr_sha256": digest,
            "error_type": error_type,
        }
        record_bytes = (json.dumps(record, sort_keys=True) + "\\n").encode("utf-8")
        descriptor = os.open(record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(record_bytes)
        os.chmod(record_path, 0o600)
        return

spec = importlib.util.spec_from_file_location("private_docker_install", script)
if spec is None or spec.loader is None:
    raise SystemExit("could not load the personal Docker installer")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
original_docker = installer.docker

def diagnostic_docker(arguments, *, check=True, input_file=None, input_data=None, stream=False):
    result = original_docker(
        arguments,
        check=False,
        input_file=input_file,
        input_data=input_data,
        stream=stream,
    )
    if check and result.returncode:
        if arguments and arguments[0] == "exec":
            _record_docker_exec_error(arguments, result)
        raise RuntimeError(f"Docker {arguments[0]} failed (exit {result.returncode})")
    return result

installer.docker = diagnostic_docker
try:
    installer.main()
except KeyboardInterrupt:
    print(
        "Instalación interrumpida; recuperación ejecutada si había un corte activo.",
        file=sys.stderr,
    )
    raise SystemExit(130)
except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
    print(f"Instalación interrumpida: {error}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
"""
    source = source.replace("@PYTHON@", sys.executable)
    source = source.replace("@PRIVATE_HOME@", private_home_literal)
    source = source.replace("@EXPECTED_PREFIX@", expected_prefix_literal)
    descriptor = os.open(wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(source)
    os.chmod(wrapper, 0o700)
    return wrapper


@pytest.mark.offline_database
def test_backup_restart_health_uses_selected_port_and_keeps_identity_checks():
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    acceptance = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_real_postgres_backup_restores_isolated_scopes_without_activation"
    )
    source = ast.unparse(acceptance)
    assert "port=port" in source
    assert "installer.wait_healthy(name, port)" in source
    assert "restarted_state.get('Id') != source_container_id" in source
    assert "restarted_state.get('State', {}).get('Running')" in source
    assert "client.get('/auth/session').status_code != 200" in source


@pytest.mark.offline_database
def test_private_python_home_wrapper_keeps_home_unset(tmp_path):
    private_home = tmp_path / "operator-state"
    private_home.mkdir(mode=0o700)
    wrapper = _write_private_python_home_wrapper(private_home)
    probe = tmp_path / "path_home_probe.py"
    expected_private_home = repr(str(private_home.resolve()))
    expected_prefix = repr(str(Path(sys.prefix).resolve()))
    probe.write_text(
        "import os, pathlib, sys, httpx\n"
        f"private_home = pathlib.Path({expected_private_home}).resolve()\n"
        f"expected_prefix = pathlib.Path({expected_prefix}).resolve()\n"
        "origin = pathlib.Path(httpx.__file__).resolve()\n"
        "pinned_import = origin.is_relative_to(expected_prefix)\n"
        "valid = ('HOME' not in os.environ and "
        "pathlib.Path.home().resolve() == private_home and "
        "pathlib.Path(sys.prefix).resolve() == expected_prefix and pinned_import)\n"
        "print('private-pinned-home-ok' if valid else 'failed')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(wrapper), str(probe)],
        env={"PATH": str(wrapper.parent), "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == "private-pinned-home-ok\n"
    assert result.stderr == ""


@pytest.mark.offline_database
def test_private_python_home_wrapper_captures_docker_exec_stderr_privately(tmp_path):
    private_home = tmp_path / "operator-state"
    private_home.mkdir(mode=0o700)
    wrapper = _write_private_python_home_wrapper(private_home)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(mode=0o700)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'ERROR: synthetic diagnostic marker' >&2\nexit 2\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)
    installer = tmp_path / "docker_install.py"
    installer.write_text(
        """import subprocess

def docker(arguments, *, check=True, input_file=None, input_data=None, stream=False):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"Docker {arguments[0]} failed (exit {result.returncode})")
    return result

def main():
    try:
        docker(["exec", "private-container", "psql"])
    except RuntimeError as error:
        if str(error) != "Docker exec failed (exit 2)":
            return 10
        print("diagnostic-wrapper-preserved-error")
        return 0
    return 11
""",
        encoding="utf-8",
    )
    diagnostic_dir = tmp_path / "docker-exec-diagnostics"
    diagnostic_dir.mkdir(mode=0o700)
    result = subprocess.run(
        [str(wrapper), str(installer)],
        env={
            "PATH": os.pathsep.join((str(wrapper.parent), str(fake_bin))),
            "LC_ALL": "C",
            "CAO_TEST_DOCKER_EXEC_DIAGNOSTIC_DIR": str(diagnostic_dir),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0 or result.stdout != "diagnostic-wrapper-preserved-error\n":
        raise AssertionError("private Docker diagnostic wrapper changed installer behavior")
    if (
        "synthetic diagnostic marker" in result.stdout
        or "synthetic diagnostic marker" in result.stderr
    ):
        raise AssertionError("Docker diagnostic content escaped to process output")
    raw_path = diagnostic_dir / "docker-exec-0001.stderr"
    record_path = diagnostic_dir / "docker-exec-0001.json"
    raw_metadata = raw_path.lstat()
    record_metadata = record_path.lstat()
    if raw_metadata.st_uid != os.getuid() or raw_metadata.st_mode & 0o077:
        raise AssertionError("captured Docker stderr is not private")
    if record_metadata.st_uid != os.getuid() or record_metadata.st_mode & 0o077:
        raise AssertionError("Docker diagnostic metadata is not private")
    raw_stderr = raw_path.read_bytes()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if b"synthetic diagnostic marker" not in raw_stderr:
        raise AssertionError("Docker stderr was not captured")
    if record != {
        "phase": "docker_exec",
        "program": "psql",
        "exit_code": 2,
        "stderr_bytes": len(raw_stderr),
        "stderr_sha256": hashlib.sha256(raw_stderr).hexdigest(),
        "error_type": "postgres_sql_error",
    }:
        raise AssertionError("Docker diagnostic metadata was not sanitized as expected")
    if "synthetic diagnostic marker" in record_path.read_text(encoding="utf-8"):
        raise AssertionError("raw Docker stderr was copied into diagnostic metadata")
