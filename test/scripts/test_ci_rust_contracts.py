from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "ci_rust_contracts.py"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
ENDPOINT_TEST_PATH = PROJECT_ROOT / "tui" / "tests" / "endpoint_contract.rs"

_SPEC = importlib.util.spec_from_file_location("ci_rust_contracts", SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
ci_rust_contracts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci_rust_contracts)


class RustCiPostgresContract(unittest.TestCase):
    def test_both_matrix_legs_install_postgres_and_run_locked_live_contracts(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        rust_job = workflow.split("  rust:\n", 1)[1].split("\n  security:", 1)[0]

        self.assertIn("os: macos-latest", rust_job)
        self.assertIn("os: ubuntu-latest", rust_job)
        self.assertIn("if: runner.os == 'Linux'", rust_job)
        self.assertIn("sudo apt-get install -y postgresql-18", rust_job)
        self.assertIn("if: runner.os == 'macOS'", rust_job)
        self.assertIn("brew install postgresql@18", rust_job)
        pg_install = rust_job.index("sudo apt-get install -y postgresql-18")
        uv_sync = rust_job.index("uv sync --locked --all-extras --dev")
        live_gate = rust_job.index(
            "uv run python scripts/ci_rust_contracts.py -- cargo test --locked"
        )
        self.assertLess(pg_install, uv_sync)
        self.assertLess(uv_sync, live_gate)
        self.assertIn("uv run python scripts/ci_rust_contracts.py -- cargo test --locked", rust_job)
        self.assertIn("cargo fmt --check", rust_job)
        self.assertIn("cargo clippy --locked --all-targets -- -D warnings", rust_job)
        self.assertLess(
            rust_job.index("cargo fmt --check"), rust_job.index("cargo clippy --locked")
        )
        self.assertLess(rust_job.index("cargo clippy --locked"), live_gate)
        self.assertIn("timeout-minutes: 20", rust_job)
        self.assertNotIn("uv run cao-server &", rust_job)
        self.assertNotIn("kill -TERM", rust_job)

    def test_exact_three_live_endpoint_tests_remain_unignored_and_unfiltered(self):
        source = ENDPOINT_TEST_PATH.read_text(encoding="utf-8")
        names = re.findall(r"(?m)^\s*#\[test\]\s*\n\s*fn\s+([a-z0-9_]+)", source)
        self.assertEqual(
            names,
            [
                "profiles_endpoint_returns_exactly_the_eight_projected_keys",
                "profiles_endpoint_does_not_return_a_provider_field",
                "providers_endpoint_returns_picker_fields_and_operation_capabilities",
                "this_file_contains_no_skip_mechanism",
            ],
        )
        self.assertNotRegex(source, r"(?m)^\s*#\[ignore(?:\(|\])")
        self.assertTrue(ci_rust_contracts._valid_cargo_command(["--", "cargo", "test", "--locked"]))
        self.assertFalse(
            ci_rust_contracts._valid_cargo_command(
                ["--", "cargo", "test", "--locked", "--", "--skip", "profiles"]
            )
        )

    def test_runner_uses_only_its_cluster_and_runtime_fixture_targets(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("isolated_live_targets(targets_home, service=service)", source)
        self.assertIn("database_targets=targets", source)
        self.assertIn("postgres_service=service", source)
        self.assertIn(
            'extra_env={"CAO_AUTH_LOCAL_TOKEN": "", "CAO_AUTH_LOCAL_TOKEN_FILE": ""}', source
        )
        self.assertIn('environment.pop("CAO_DATABASE_CONFIG_FILE", None)', source)
        self.assertIn('environment.pop("CAO_AUTH_LOCAL_TOKEN", None)', source)
        self.assertIn('environment.pop("CAO_AUTH_LOCAL_TOKEN_FILE", None)', source)
        self.assertNotIn("_discover_postgres_service", source)
        self.assertNotIn("docker", source.lower())

    def test_entrypoint_imports_real_postgres_service_without_ambient_pythonpath(self):
        child = f"""\
import importlib.util
import sys
from pathlib import Path

script = Path({str(SCRIPT_PATH)!r})
project_root = str(script.resolve().parents[1])
spec = importlib.util.spec_from_file_location("ci_rust_contracts_entry", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
observed = {{}}

class Cluster:
    def __init__(self, root):
        self.port = 55432
        self.username = "probe_user"
        self.password = "probe_password"
    def start(self):
        observed["project_root_at_cluster_start"] = project_root in sys.path
    def stop(self):
        observed["stopped"] = True

def run_live(root, service, command):
    observed["service_type"] = type(service).__module__ + "." + type(service).__name__
    observed["metadata"] = service.metadata
    observed["password"] = service.password
    observed["command"] = list(command)
    return 0

module.PrivatePostgresCluster = Cluster
module._run_live_rust_tests = run_live
status = module.main(["--", "cargo", "test", "--locked"])
assert status == 0, status
assert observed["project_root_at_cluster_start"] is True
assert observed["service_type"] == "test.postgres.fixtures.PostgresService"
assert observed["metadata"] == {{"host": "127.0.0.1", "port": 55432, "user": "probe_user"}}
assert observed["password"] == "probe_password"
assert observed["command"] == ["cargo", "test", "--locked"]
assert observed["stopped"] is True
"""
        with tempfile.TemporaryDirectory(prefix="ci-rust-entry-import-") as temporary:
            os.chmod(temporary, 0o700)
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment.pop("PYTHONHOME", None)
            environment["RUNNER_TEMP"] = temporary
            result = subprocess.run(
                [sys.executable, "-I", "-c", child],
                cwd=temporary,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_cluster_uses_private_password_and_loopback_only_server(self):
        with tempfile.TemporaryDirectory(prefix="ci-rust-pg-contract-") as temporary:
            root = Path(temporary) / "run"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            binary_dir = root / "bin"
            binary_dir.mkdir(mode=0o700)
            initdb = binary_dir / "initdb"
            pg_ctl = binary_dir / "pg_ctl"
            initdb.touch(mode=0o700)
            pg_ctl.touch(mode=0o700)
            initdb.chmod(0o700)
            pg_ctl.chmod(0o700)
            commands: list[list[str]] = []
            running = False
            password_modes: list[int] = []

            def runner(args, **kwargs):
                nonlocal running
                command = list(args)
                commands.append(command)
                if command[0] == str(initdb) and command[-1] == "--version":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="initdb (PostgreSQL) 18.1\n",
                        stderr="",
                    )
                if command[0] == str(pg_ctl) and command[-1] == "--version":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="pg_ctl (PostgreSQL) 18.1\n",
                        stderr="",
                    )
                if command[0] == str(initdb):
                    password_path = Path(command[command.index("--pwfile") + 1])
                    password_modes.append(stat.S_IMODE(password_path.stat().st_mode))
                    self.assertEqual(password_path.parent, root)
                    self.assertTrue(password_path.read_text(encoding="utf-8").strip())
                    data_dir = Path(command[command.index("--pgdata") + 1])
                    (data_dir / "PG_VERSION").write_text("18\n", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                if command[0] == str(pg_ctl) and command[-1] == "start":
                    running = True
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                if command[0] == str(pg_ctl) and command[-1] == "status":
                    status = 0 if running else 3
                    return subprocess.CompletedProcess(command, status, stdout="", stderr="")
                if command[0] == str(pg_ctl) and command[-1] == "stop":
                    running = False
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                self.fail(f"unexpected PostgreSQL command: {command!r}")

            cluster = ci_rust_contracts.PrivatePostgresCluster(
                root,
                pg_bin_dir=binary_dir,
                runner=runner,
                port_picker=lambda: 55432,
            )
            cluster.start()
            self.assertTrue(cluster.started)
            self.assertEqual(cluster.port, 55432)
            self.assertEqual(password_modes, [0o600])
            self.assertFalse((root / "bootstrap-password").exists())

            initdb_command = next(
                command
                for command in commands
                if command[0] == str(initdb) and command[-1] != "--version"
            )
            self.assertIn("--auth-host=scram-sha-256", initdb_command)
            self.assertIn("--auth-local=trust", initdb_command)
            start_command = next(command for command in commands if command[-1] == "start")
            options = start_command[start_command.index("--options") + 1]
            self.assertIn("-h 127.0.0.1", options)
            self.assertIn("-p 55432", options)
            self.assertIn(f"unix_socket_directories={root / 'socket'}", options)

            cluster.stop()
            self.assertFalse(cluster.started)
            self.assertFalse(running)

    def test_failed_initialization_removes_bootstrap_secret_without_starting_server(self):
        with tempfile.TemporaryDirectory(prefix="ci-rust-pg-red-") as temporary:
            root = Path(temporary) / "run"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            binary_dir = root / "bin"
            binary_dir.mkdir(mode=0o700)
            initdb = binary_dir / "initdb"
            pg_ctl = binary_dir / "pg_ctl"
            initdb.touch(mode=0o700)
            pg_ctl.touch(mode=0o700)
            initdb.chmod(0o700)
            pg_ctl.chmod(0o700)
            commands: list[list[str]] = []

            def runner(args, **kwargs):
                command = list(args)
                commands.append(command)
                if command[0] == str(initdb) and command[-1] == "--version":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="initdb (PostgreSQL) 18.1\n",
                        stderr="",
                    )
                if command[0] == str(pg_ctl) and command[-1] == "--version":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="pg_ctl (PostgreSQL) 18.1\n",
                        stderr="",
                    )
                if command[0] == str(initdb):
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")
                self.fail(f"server must not start after failed initdb: {command!r}")

            cluster = ci_rust_contracts.PrivatePostgresCluster(
                root,
                pg_bin_dir=binary_dir,
                runner=runner,
                port_picker=lambda: 55432,
            )
            with self.assertRaisesRegex(RuntimeError, "initialization failed"):
                cluster.start()
            self.assertFalse((root / "bootstrap-password").exists())
            self.assertFalse(cluster.started)
            self.assertFalse(any(command[-1] == "start" for command in commands))

    def test_uncertain_startup_is_stopped_by_its_private_data_directory(self):
        with tempfile.TemporaryDirectory(prefix="ci-rust-pg-rollback-") as temporary:
            root = Path(temporary) / "run"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            binary_dir = root / "bin"
            binary_dir.mkdir(mode=0o700)
            initdb = binary_dir / "initdb"
            pg_ctl = binary_dir / "pg_ctl"
            initdb.touch(mode=0o700)
            pg_ctl.touch(mode=0o700)
            initdb.chmod(0o700)
            pg_ctl.chmod(0o700)
            commands: list[list[str]] = []
            running = False

            def runner(args, **kwargs):
                nonlocal running
                command = list(args)
                commands.append(command)
                if command[0] == str(initdb) and command[-1] == "--version":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="initdb (PostgreSQL) 18.1\n",
                        stderr="",
                    )
                if command[0] == str(pg_ctl) and command[-1] == "--version":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="pg_ctl (PostgreSQL) 18.1\n",
                        stderr="",
                    )
                if command[0] == str(initdb):
                    password_path = Path(command[command.index("--pwfile") + 1])
                    data_dir = Path(command[command.index("--pgdata") + 1])
                    (data_dir / "PG_VERSION").write_text("18\n", encoding="utf-8")
                    self.assertEqual(stat.S_IMODE(password_path.stat().st_mode), 0o600)
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                if command[0] == str(pg_ctl) and command[-1] == "start":
                    running = True
                    return subprocess.CompletedProcess(
                        command, 1, stdout="", stderr="startup timed out"
                    )
                if command[0] == str(pg_ctl) and command[-1] == "status":
                    return subprocess.CompletedProcess(
                        command, 0 if running else 3, stdout="", stderr=""
                    )
                if command[0] == str(pg_ctl) and command[-1] == "stop":
                    running = False
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                self.fail(f"unexpected PostgreSQL command: {command!r}")

            cluster = ci_rust_contracts.PrivatePostgresCluster(
                root,
                pg_bin_dir=binary_dir,
                runner=runner,
                port_picker=lambda: 55432,
            )
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                cluster.start()
            self.assertTrue(cluster.started)
            cluster.stop()
            self.assertFalse(cluster.started)
            self.assertFalse(running)

            data_dir = str(cluster.data_dir)
            stop_command = next(command for command in commands if command[-1] == "stop")
            self.assertEqual(stop_command[stop_command.index("--pgdata") + 1], data_dir)

    def test_run_cleanup_removes_only_the_private_cluster_root(self):
        with tempfile.TemporaryDirectory(prefix="ci-rust-pg-cleanup-") as temporary:
            parent = Path(temporary)
            owned_root = parent / "owned"
            owned_root.mkdir(mode=0o700)
            os.chmod(owned_root, 0o700)
            (owned_root / "cluster-data").mkdir(mode=0o700)
            sibling = parent / "keep"
            sibling.mkdir(mode=0o700)
            marker = sibling / "sentinel"
            marker.write_text("preserve", encoding="utf-8")

            ci_rust_contracts._remove_private_run_root(owned_root)

            self.assertFalse(owned_root.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
