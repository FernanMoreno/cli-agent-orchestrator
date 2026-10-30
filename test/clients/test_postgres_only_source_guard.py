"""Guard the runtime tree against reintroducing the retired local database path."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]
TUI_ROOT = ROOT / "tui"
CODE_ROOTS = (
    ROOT / "src" / "cli_agent_orchestrator",
    ROOT / "scripts",
    ROOT / "examples",
    ROOT / "test",
)
GUARD_PATH = Path(__file__).resolve()
UPSTREAM_SQLITE_PATHS = frozenset(
    {
        "src/cli_agent_orchestrator/utils/opencode_v2_store.py",
        "test/providers/test_opencode_v2_store.py",
        "test/providers/test_opencode_v2_canonical_result.py",
    }
)
EXCLUDED_DISCOVERY_DIRECTORIES = {
    ".git",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
    "__pycache__",
}
ALLOWED_UNSUPPORTED_ARCHIVE_MARKERS = (
    (
        "src/cli_agent_orchestrator/services/memory_archive/okf.py",
        "sqlite-dump.json",
    ),
    ("test/services/test_memory_archive_okf_import.py", "sqlite-dump.json"),
)
EXPECTED_UNSUPPORTED_ARCHIVE_MARKER_LITERALS = (
    *ALLOWED_UNSUPPORTED_ARCHIVE_MARKERS,
    ALLOWED_UNSUPPORTED_ARCHIVE_MARKERS[1],
)
REMOVED_SQLITE_SCHEMA_MODULES = (
    "beads_work_schema.py",
    "knowledge_access_schema.py",
    "knowledge_schema.py",
    "legacy_work_schema.py",
    "memory_access_schema.py",
    "step_contract_schema.py",
    "work_attempt_credential_schema.py",
    "work_authority_schema.py",
    "work_bubblewrap_identity_schema.py",
    "work_bubblewrap_setup_intent_schema.py",
    "work_continuation_schema.py",
    "work_decisions_schema.py",
    "work_delivery_schema.py",
    "work_dispatch_schema.py",
    "work_executable_content_schema.py",
    "work_mcp_proxy_schema.py",
    "work_origin_schema.py",
    "work_process_identity_schema.py",
    "work_reservations_schema.py",
    "work_scheduler_schema.py",
    "work_snapshot_schema.py",
    "work_task_receiver_credential_schema.py",
    "worktree_evidence_schema.py",
)
RETIRED_DRIVER_MODULES = (
    "apsw",
    "aiosqlite",
    "pysqlite3",
    "sqlcipher",
    "sqlcipher3",
    "pysqlcipher3",
)
SQLITE_CATALOG_LITERAL = re.compile(r"\bsqlite_(?:master|sequence|autoindex)\b", re.IGNORECASE)
SQLITE_URL_LITERAL = re.compile(r"\bsqlite(?:\+[a-z0-9_]+)?://", re.IGNORECASE)
SQLITE_PRAGMA_LITERAL = re.compile(r"\bPRAGMA\b", re.IGNORECASE)
RUST_SQLITE_PATTERNS = (
    re.compile(r"\b(?:rusqlite|libsqlite3[_-]sys|sqlx::sqlite|diesel::sqlite)\b", re.IGNORECASE),
    re.compile(r"\bsqlite(?:\+[a-z0-9_]+)?://", re.IGNORECASE),
    re.compile(r"\bsqlite_(?:master|sequence|autoindex)\b", re.IGNORECASE),
    re.compile(r"\bsqlite-dump\.json\b", re.IGNORECASE),
    re.compile(r"\bPRAGMA\b", re.IGNORECASE),
)


def _python_sources() -> list[Path]:
    return sorted(
        path
        for root in CODE_ROOTS
        for path in _files_with_suffix(root, ".py")
        if path.resolve() != GUARD_PATH
    )


def _files_with_suffix(root: Path, suffix: str) -> list[Path]:
    paths: list[Path] = []
    for directory, child_directories, files in os.walk(root):
        child_directories[:] = sorted(
            name for name in child_directories if name not in EXCLUDED_DISCOVERY_DIRECTORIES
        )
        paths.extend(Path(directory) / name for name in files if name.endswith(suffix))
    return paths


def _module_imports(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append((node.lineno, module))
            imports.extend(
                (
                    node.lineno,
                    f"{module}.{alias.name}" if module else alias.name,
                )
                for alias in node.names
            )
    return imports


def _sqlite_feature_findings(path: Path) -> list[str]:
    relative = path.relative_to(ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    findings: list[str] = []

    for line, module in _module_imports(tree):
        if relative in UPSTREAM_SQLITE_PATHS and (
            module == "sqlite3" or module.startswith("sqlite3.")
        ):
            continue
        if (
            module == "sqlite3"
            or module.startswith("sqlite3.")
            or any(
                module == driver or module.startswith(f"{driver}.")
                for driver in RETIRED_DRIVER_MODULES
            )
            or module == "sqlalchemy.dialects.sqlite"
            or module.startswith("sqlalchemy.dialects.sqlite.")
            or module.startswith("cli_agent_orchestrator.clients.legacy_sqlite")
            or module == "legacy_sqlite"
            or module.endswith(".legacy_sqlite")
            or module.endswith("clients.postgres.importer")
            or module.endswith("services.recovery_inventory")
            or module.endswith("legacy_work_schema")
        ):
            findings.append(f"{relative}:{line}: imports retired database module {module}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        lowered = value.lower()
        marker = (relative, value)
        if (
            relative in UPSTREAM_SQLITE_PATHS
            and not SQLITE_URL_LITERAL.search(value)
            and (SQLITE_CATALOG_LITERAL.search(value) or SQLITE_PRAGMA_LITERAL.search(value))
        ):
            continue
        if value == "sqlite-dump.json":
            if marker not in ALLOWED_UNSUPPORTED_ARCHIVE_MARKERS:
                findings.append(f"{relative}:{node.lineno}: recognizes a retired archive marker")
            continue
        if (
            lowered == "sqlite"
            or "sqlite3" == lowered.strip()
            or any(
                re.search(rf"(?<![a-z0-9_]){re.escape(driver)}(?![a-z0-9_])", lowered)
                for driver in RETIRED_DRIVER_MODULES
            )
            or "sqlalchemy.dialects.sqlite" in lowered
            or "cli_agent_orchestrator.clients.legacy_sqlite" in lowered
            or "clients.postgres.importer" in lowered
            or "services.recovery_inventory" in lowered
            or "legacy_work_schema" in lowered
            or SQLITE_URL_LITERAL.search(value)
            or SQLITE_CATALOG_LITERAL.search(value)
            or SQLITE_PRAGMA_LITERAL.search(value)
        ):
            findings.append(f"{relative}:{node.lineno}: contains retired database behavior")

    return findings


def test_runtime_and_functional_test_trees_have_no_sqlite_support():
    findings = [finding for path in _python_sources() for finding in _sqlite_feature_findings(path)]
    shell_findings = []
    for root in CODE_ROOTS:
        for path in _files_with_suffix(root, ".sh"):
            content = path.read_text(encoding="utf-8")
            if (
                SQLITE_URL_LITERAL.search(content)
                or SQLITE_CATALOG_LITERAL.search(content)
                or re.search(r"\bsqlite3\b", content, re.IGNORECASE)
            ):
                shell_findings.append(
                    f"{path.relative_to(ROOT)}: contains retired database behavior"
                )

    assert findings + shell_findings == []


@pytest.mark.parametrize(
    "relative",
    [
        "src/cli_agent_orchestrator/utils/opencode_v2_store.py",
        "test/providers/test_opencode_v2_store.py",
        "src/cli_agent_orchestrator/services/metadata_store.py",
    ],
)
def test_upstream_exceptions_never_allow_a_cao_sqlite_engine(tmp_path, monkeypatch, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(
        "from sqlalchemy import create_engine\nengine = create_engine('sqlite:///:memory:')\n"
    )
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    assert _sqlite_feature_findings(path)


def test_only_upstream_adapter_connects_with_read_only_uri_and_authorizer():
    path = ROOT / "src/cli_agent_orchestrator/utils/opencode_v2_store.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    connections = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sqlite3"
        and node.func.attr == "connect"
    ]
    assert len(connections) == 1
    connection = connections[0]
    assert len(connection.args) == 1
    uri = connection.args[0]
    assert isinstance(uri, ast.BinOp) and isinstance(uri.op, ast.Add)
    assert isinstance(uri.right, ast.Constant) and uri.right.value == "?mode=ro"
    assert isinstance(uri.left, ast.Call) and isinstance(uri.left.func, ast.Attribute)
    assert uri.left.func.attr == "as_uri"
    uri_options = [keyword.value for keyword in connection.keywords if keyword.arg == "uri"]
    assert len(uri_options) == 1
    assert isinstance(uri_options[0], ast.Constant) and uri_options[0].value is True
    assert any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "PRAGMA query_only=ON"
        for node in calls
    )
    assert any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_authorizer"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "_authorizer"
        for node in calls
    )


def test_legacy_importers_and_packaged_payloads_are_removed():
    client_dir = ROOT / "src" / "cli_agent_orchestrator" / "clients" / "legacy_sqlite"
    assert not client_dir.exists()

    clients_dir = ROOT / "src" / "cli_agent_orchestrator" / "clients"
    assert not (clients_dir / "postgres" / "importer.py").exists()
    assert not (
        ROOT / "src" / "cli_agent_orchestrator" / "services" / "recovery_inventory.py"
    ).exists()
    assert all(not (clients_dir / name).exists() for name in REMOVED_SQLITE_SCHEMA_MODULES)

    archive_dir = ROOT / "src" / "cli_agent_orchestrator" / "services" / "memory_archive"
    assert not (archive_dir / "legacy_reader.py").exists()
    assert not (archive_dir / "legacy_format.py").exists()
    marker_literals = [
        (path.relative_to(ROOT).as_posix(), node.value)
        for path in _python_sources()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and node.value == "sqlite-dump.json"
    ]
    assert tuple(marker_literals) == EXPECTED_UNSUPPORTED_ARCHIVE_MARKER_LITERALS

    assert not (ROOT / "test" / "integration" / "test_docker_personal_sqlite_import.py").exists()

    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "legacy_sqlite" not in pyproject_text
    assert "sqlite" not in pyproject_text.lower()
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    runtime_dependencies = project["project"]["dependencies"]
    assert all("sqlite" not in dependency.lower() for dependency in runtime_dependencies)


def test_database_artifact_anchor_stays_stable_and_peer_file_constant_is_gone():
    constants_path = ROOT / "src" / "cli_agent_orchestrator" / "constants.py"
    constants_tree = ast.parse(constants_path.read_text(encoding="utf-8"))
    assignments: dict[str, ast.expr] = {}
    for node in ast.walk(constants_tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value

    database_file = assignments["DATABASE_FILE"]
    assert isinstance(database_file, ast.BinOp)
    assert isinstance(database_file.left, ast.Name)
    assert database_file.left.id == "DB_DIR"
    assert isinstance(database_file.op, ast.Div)
    assert isinstance(database_file.right, ast.Constant)
    assert database_file.right.value == "cli-agent-orchestrator.db"
    assert "LOCAL_PEER_REGISTRY_FILE" not in assignments


def _dependency_tables(value: object):
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        if key in {"dependencies", "dev-dependencies", "build-dependencies"}:
            if isinstance(child, dict):
                yield child
        else:
            yield from _dependency_tables(child)


def _cargo_sqlite_findings() -> list[str]:
    findings: list[str] = []
    manifest_path = TUI_ROOT / "Cargo.toml"
    lock_path = TUI_ROOT / "Cargo.lock"
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    for dependencies in _dependency_tables(manifest):
        for dependency_name, specification in dependencies.items():
            detail = specification if isinstance(specification, dict) else {}
            package_name = detail.get("package", "")
            features = detail.get("features", ())
            values = (dependency_name, package_name, *features)
            if any("sqlite" in str(value).lower() for value in values):
                findings.append(
                    f"{manifest_path.relative_to(ROOT)}: enables SQLite Cargo dependency or feature "
                    f"{dependency_name}"
                )

    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    for package in lock.get("package", []):
        name = package.get("name", "")
        dependencies = package.get("dependencies", ())
        if "sqlite" in name.lower() or any("sqlite" in str(dep).lower() for dep in dependencies):
            findings.append(f"{lock_path.relative_to(ROOT)}: resolves SQLite package {name}")
    return findings


def test_rust_tui_has_no_sqlite_storage_dependency_or_implementation():
    findings = _cargo_sqlite_findings()
    for path in _files_with_suffix(TUI_ROOT, ".rs"):
        content = path.read_text(encoding="utf-8")
        for pattern in RUST_SQLITE_PATTERNS:
            if pattern.search(content):
                findings.append(
                    f"{path.relative_to(ROOT)}: contains retired SQLite storage behavior"
                )
                break

    assert findings == []


def test_python_lockfile_has_no_retired_sqlite_dependency():
    """The resolved Python dependency graph must remain PostgreSQL-only too."""
    lock_path = ROOT / "uv.lock"
    assert lock_path.exists(), "uv.lock is required for the PostgreSQL-only runtime"
    lock_text = lock_path.read_text(encoding="utf-8")
    assert not re.search(
        r"(?im)^name\s*=\s*['\"](?:aiosqlite|pysqlite3|sqlcipher(?:3)?|apsw)['\"]\s*$",
        lock_text,
    )
    assert not re.search(r"(?i)\b(?:aiosqlite|pysqlite3|sqlcipher3?|apsw)\b", lock_text)


def test_memory_repair_catalog_summary_names_postgresql():
    catalog = (TUI_ROOT / "src" / "catalog.rs").read_text(encoding="utf-8")
    command = re.search(
        r"CommandId::MemoryRepair\s*=>\s*Command\s*\{(?P<body>.*?)(?=\n\s*CommandId::[A-Z][A-Za-z0-9_]*\s*=>|\n\s*\};)",
        catalog,
        re.DOTALL,
    )
    assert command is not None, "MemoryRepair command catalog entry is missing"

    summaries = re.findall(r'\bsummary:\s*"([^"]*)"', command.group("body"))
    assert len(summaries) == 1
    assert "postgresql" in summaries[0].lower()
    assert "sqlite" not in summaries[0].lower()
