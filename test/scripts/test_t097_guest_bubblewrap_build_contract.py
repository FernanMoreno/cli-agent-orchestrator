"""Keep the guest Bubblewrap build on its reviewed digest-producing paths."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUEST_SCRIPT = ROOT / ".github/scripts/t097-qemu-acceptance.sh"
BACKEND = ROOT / "src/cli_agent_orchestrator/backends/bubblewrap_backend.py"
EXTRA_FILESYSTEM_SELECTORS = ROOT / "test/integration/t097/extra-filesystem-selectors.txt"
SCRATCH_SELECTORS = ROOT / "test/integration/t097/scratch-selectors.txt"
GUEST_README = ROOT / "test/integration/t097/README.md"


def test_guest_bubblewrap_build_uses_pinned_source_and_scoped_paths():
    script = GUEST_SCRIPT.read_text(encoding="utf-8")
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]

    assert (
        "source_sha256=4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765" in remote
    )
    assert "bwrap_source_root=/tmp/bwrap-source" in remote
    assert 'bwrap_source_dir="$bwrap_source_root/bubblewrap-0.13.0"' in remote
    assert "scratch_bwrap_root=/tmp/caos-exec/T097" in remote
    assert 'scratch_bwrap_dir="$scratch_bwrap_root/bubblewrap-build"' in remote
    assert 'scratch_bwrap_path="$scratch_bwrap_dir/bwrap"' in remote
    assert (
        'allowlist_file="$workspace/src/cli_agent_orchestrator/backends/bubblewrap_backend.py"'
        in remote
    )
    assert "binary_digest=\"$(sha256sum /usr/bin/bwrap | cut -d ' ' -f 1)\"" in remote
    assert 'echo "T097 canonical Bubblewrap digest is allowlisted: $binary_digest"' in remote

    absent_path_guard = remote.index(
        "if [[ -e /tmp/caos-exec || -L /tmp/caos-exec \\\n"
        '  || -e "$bwrap_source_root" || -L "$bwrap_source_root" ]]; then'
    )
    create_parent = remote.index("sudo install -d -o root -g root -m 0755 /tmp/caos-exec")
    create_scratch_root = remote.index(
        'sudo install -d -o root -g root -m 0755 "$scratch_bwrap_root"'
    )
    create_source = remote.index(
        'install -d -o "$(id -u)" -g "$(id -g)" -m 0700 "$bwrap_source_root"'
    )
    create_build = remote.index(
        'sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0700 "$scratch_bwrap_dir"'
    )
    extract_source = remote.index(
        'tar -xJf "$bwrap_work/bubblewrap-0.13.0.tar.xz" -C "$bwrap_source_root"'
    )
    configure_build = remote.index('meson setup "$scratch_bwrap_dir" "$bwrap_source_dir"')
    install_binary = remote.index(
        'sudo install -o root -g root -m 0755 "$scratch_bwrap_path" /usr/bin/bwrap'
    )
    assert (
        absent_path_guard
        < create_parent
        < create_scratch_root
        < create_source
        < create_build
        < extract_source
        < configure_build
        < install_binary
    )

    root_owned_chain = (
        "sudo chown root:root \\\n"
        '  /tmp/caos-exec "$scratch_bwrap_root" "$scratch_bwrap_dir" "$scratch_bwrap_path"'
    )
    make_root_owned = remote.index(root_owned_chain)
    verify_root_parent = remote.index('test "$(stat -c \'%u:%g:%a\' /tmp/caos-exec)" = "0:0:755"')
    verify_root_scratch = remote.index(
        'test "$(stat -c \'%u:%g:%a\' "$scratch_bwrap_root")" = "0:0:755"'
    )
    verify_root_binary = remote.index(
        'test "$(stat -c \'%u:%g:%a\' "$scratch_bwrap_path")" = "0:0:755"'
    )
    assert (
        install_binary
        < make_root_owned
        < verify_root_parent
        < verify_root_scratch
        < verify_root_binary
    )

    host_partition = (
        '"T097 real host acceptance" 8 /home/runner/t097-pytest-host.log \\\n'
        "  test/integration/t097/test_host_acceptance.py \\\n"
        "  test/integration/t097/test_proxy_concurrency.py \\\n"
        "  test/integration/t097/test_sibling_isolation.py -m t097_host"
    )
    assert host_partition in remote
    assert "  test/integration/t097 -m t097_host" not in remote

    extract_checkout = remote.index('tar -xzf /home/runner/checkout.tar.gz -C "$workspace"')
    digest_gate = remote.index("sed -n '/^_BWRAP_SHA256_ALLOWLIST:")
    uv_python_install = remote.index("/home/runner/uv python install 3.12")
    uv_sync = remote.index("CAO_TUI_AUTOBUILD=0 /home/runner/uv sync --all-extras --dev --frozen")
    assert extract_checkout < digest_gate < uv_python_install < uv_sync

    cleanup = remote.index('sudo rm -rf -- "$scratch_bwrap_root" "$bwrap_source_root"')
    assert "sudo rmdir /tmp/caos-exec 2>/dev/null || true" in remote[cleanup:]
    assert (
        "8a0c2d79415613d5a1cb7ba9acc14a9a86a0f7112ade526d283cc51b4b8c333f"
        not in BACKEND.read_text(encoding="utf-8")
    )
    assert "efd07a9dfd55016ec5cb16facb67aac2735fa900e261f6a44be1517dcf31c015" in BACKEND.read_text(
        encoding="utf-8"
    )


def test_guest_runs_exact_dead_monitor_and_casefold_partition_without_skips():
    script = GUEST_SCRIPT.read_text(encoding="utf-8")
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]
    selectors = EXTRA_FILESYSTEM_SELECTORS.read_text(encoding="utf-8").splitlines()

    assert selectors == [
        "test/services/test_work_bubblewrap_cleanup_recovery.py::"
        "test_dead_monitor_recovery_cleans_surviving_exact_init",
        "test/services/vault/test_vault_reconcile_concurrency.py::"
        "test_projection_lock_serializes_case_variant_root_spellings",
        "test/services/vault/test_vault_reconcile_concurrency.py::"
        "test_projection_lock_reenters_across_case_variant_spellings",
    ]
    assert "  e2fsprogs " in remote
    assert 'mkfs.ext4 -F -O casefold -E encoding=utf8 "$casefold_image"' in remote
    assert 'sudo chattr +F "$casefold_parent"' in remote
    assert 'test -d "$casefold_parent/caseprobe"' in remote
    assert 'test -d "$casefold_parent/TreeProbe/caseprobe"' in remote
    assert '"T097 dead-monitor recovery and ext4 casefold acceptance" 3' in remote
    assert '--basetemp "$casefold_basetemp"' in remote
    assert '"${casefold_selectors[@]}"' in remote
    assert 'assert_pytest_summary "$label" "$expected" "$log_file"' in remote
    assert 'casefold_mount_dir" == /tmp/t097-casefold.*' in remote
    assert 'sudo umount "$casefold_mount_dir"' in remote
    assert "Private T097 casefold filesystem is still mounted" in remote
    assert 'if [[ "$casefold_mount_active" != true ]]' in remote


def test_guest_pytest_helpers_override_project_addopts_and_select_host_tests():
    script = GUEST_SCRIPT.read_text(encoding="utf-8")
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]

    assert remote.count('"$workspace/.venv/bin/python" -m pytest -q -o addopts= \\\n') == 3
    assert "test/integration/t097/test_proxy_sigkill_t097_restored.py -m t097_host" in remote
    assert "test/integration/t097/test_same_uid_fd_theft_t097_restored.py -m t097_host" in remote


def test_guest_selects_all_current_t097_and_t098_acceptance_cases_without_skips():
    script = GUEST_SCRIPT.read_text(encoding="utf-8")
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]
    t097_selectors = (
        "test/security/test_work_bubblewrap_bound_content.py::"
        "test_bound_v2_launch_uses_one_owned_stage_and_persists_identity_before_go",
        "test/security/test_work_bubblewrap_bound_content.py::"
        "test_recorded_bound_launch_activates_private_proxy_only_after_ack_and_durable_setup",
        "test/security/test_work_bubblewrap_bound_content.py::"
        "test_recorded_bound_launch_reports_uncertainty_after_go_write",
        "test/security/test_work_bubblewrap_bound_content.py::"
        "test_recorded_bound_launch_runs_real_worker_after_pending_setup",
        "test/security/test_work_bubblewrap_process_identity.py::"
        "test_callback_without_process_identity_never_releases_worker",
        "test/security/test_work_bubblewrap_process_identity.py::"
        "test_identity_persistence_error_withholds_worker_release",
        "test/security/test_work_bubblewrap_process_identity.py::"
        "test_parent_persists_exact_process_identity_before_worker_release",
    )
    t098_selectors = (
        "test/integration/t098/test_work_launch_dispatch.py::"
        "test_gateway_runs_scratch_worker_through_bubblewrap_and_supervisor",
        "test/integration/t098/test_work_launch_dispatch.py::"
        "test_double_fork_descendants_can_exec_only_the_bound_static_elf",
        "test/integration/t098/test_work_launch_dispatch.py::"
        "test_go_authority_revocation_leaves_no_worker_effect_or_redelivery",
        "test/integration/t098/test_work_launch_dispatch.py::"
        "test_uncertain_cleanup_is_reconciled_without_process_redelivery",
    )

    for selector in (*t097_selectors, *t098_selectors):
        assert remote.count(selector) == 1

    group_counts = [
        int(value)
        for value in re.findall(r'run_(?:broker|uid1000|root)_pytest \\\n\s+"[^"]+" (\d+)', remote)
    ]
    assert group_counts == [8, 2, 1, 62, 1, 1, 2, 3, 7, 4]
    assert sum(group_counts) == 91
    assert remote.count("expected_test_total=$((expected_test_total + expected))") == 3
    assert 'if [[ "$expected_test_total" -ne 91 ]]' in remote
    assert "(failed|error|skipped|xfailed|xpassed|deselected)" in remote
    assert remote.count('assert_pytest_summary "$label" "$expected" "$log_file"') == 3
    assert "T097_TEST_WORKER_TIMEOUT_SECONDS=45" in remote

    guest_readme = GUEST_README.read_text(encoding="utf-8")
    general_suite_selectors = (
        "test_durable_work_contract_can_dispatch_only_its_allowed_mcp_tools",
        "test_gateway_dispatches_v2_to_bound_process_effect_without_terminal_fallback",
        "test_shebang_cannot_be_published_as_bound_static_executable",
        "test_missing_process_capability_never_substitutes_terminal_backend",
        "test_authority_change_at_process_effect_boundary_leaves_no_worker_marker",
    )
    assert "remain in the unfiltered backend" in guest_readme
    assert all(selector in guest_readme for selector in general_suite_selectors)
    assert "45-second worker timeout" in guest_readme
    assert "default host timeout remains 10 seconds" in guest_readme


def test_scratch_partition_keeps_uid1000_case_separate_and_private():
    script = GUEST_SCRIPT.read_text(encoding="utf-8")
    remote = script.split("<<'REMOTE'\n", 1)[1].split("\nREMOTE", 1)[0]
    selectors = SCRATCH_SELECTORS.read_text(encoding="utf-8").splitlines()
    uid1000_selector = (
        "test/security/test_work_bubblewrap_sibling_userns.py::"
        "test_same_host_uid_userns_sibling_cannot_inspect_nondumpable_target"
    )

    assert len(selectors) == 63 and len(set(selectors)) == 63
    assert selectors.count(uid1000_selector) == 1
    assert "runner:1000:1000" in remote
    assert 'runner_pg_test_dir="$(mktemp -d /home/runner/t097-postgres-uid1000.XXXXXX)"' in remote
    assert "stat -c '%u:%a' \"$runner_pg_test_dir/metadata.json\"" in remote
    assert "stat -c '%u:%a' \"$runner_pg_test_dir/admin-password\"" in remote
    assert "run_uid1000_pytest()" in remote
    assert '"T097 scratch Bubblewrap acceptance (dedicated broker)" 62' in remote
    assert '"T097 exact UID-1000 sibling characterization" 1' in remote
    assert '"${scratch_broker_selectors[@]}"' in remote
    assert '"$scratch_uid1000_selector"' in remote
    assert '"$runner_pg_test_dir" == /home/runner/t097-postgres-uid1000.*' in remote
