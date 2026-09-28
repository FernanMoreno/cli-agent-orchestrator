"""The Bubblewrap preflight binds its OS identity before host probes."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.backends.base import (
    ProcessRestrictionContract,
    UnsupportedWorkEnforcement,
)
from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend
from cli_agent_orchestrator.models.work_contract import ExecutableIdentity

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pwd") is None,
    reason="the broker account policy is Linux-specific",
)


def _restriction(tmp_path):
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
        paths=(),
        commands=("/worker",),
        network=(),
        checkout_root=str(tmp_path),
        executable_identities=(identity,),
    )


@pytest.mark.parametrize(
    ("configured_account", "effective_uid", "passwd_record", "failure"),
    [
        (None, 1001, (1001, "/usr/sbin/nologin"), "configuration absent"),
        ("", 1001, (1001, "/usr/sbin/nologin"), "empty configuration"),
        (" caos-work-broker", 1001, (1001, "/usr/sbin/nologin"), "malformed configuration"),
        ("caos-work-broker", 0, (1001, "/usr/sbin/nologin"), "root effective uid"),
        ("caos-work-broker", 1001, None, "account absent"),
        ("caos-work-broker", 1001, (0, "/usr/sbin/nologin"), "root account"),
        ("caos-work-broker", 1002, (1001, "/usr/sbin/nologin"), "uid mismatch"),
        ("caos-work-broker", 1001, (1001, "/bin/bash"), "login shell"),
    ],
    ids=(
        "missing-config",
        "empty-config",
        "malformed-config",
        "root-euid",
        "missing-account",
        "root-account",
        "uid-mismatch",
        "login-shell",
    ),
)
def test_preflight_rejects_untrusted_broker_identity_before_host_probes(
    tmp_path, monkeypatch, configured_account, effective_uid, passwd_record, failure
):
    import pwd

    from cli_agent_orchestrator.backends import bubblewrap_backend

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bubblewrap_backend.os, "geteuid", lambda: effective_uid)
    if configured_account is None:
        monkeypatch.delenv("CAO_WORK_BROKER_ACCOUNT", raising=False)
    else:
        monkeypatch.setenv("CAO_WORK_BROKER_ACCOUNT", configured_account)

    def getpwnam(name):
        assert name == "caos-work-broker"
        if passwd_record is None:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=passwd_record[0], pw_shell=passwd_record[1])

    monkeypatch.setattr(pwd, "getpwnam", getpwnam)
    host_probes = []
    monkeypatch.setattr(
        bubblewrap_backend,
        "_query_abi_version",
        lambda: host_probes.append("landlock") or 11,
    )
    monkeypatch.setattr(
        bubblewrap_backend,
        "_probe_bwrap_version",
        lambda _path: host_probes.append("bubblewrap") or (0, 13, 0),
    )

    backend = BubblewrapWorkBackend()
    with pytest.raises(UnsupportedWorkEnforcement):
        backend.preflight_work(_restriction(tmp_path))

    assert host_probes == [], f"{failure} must reject before Landlock/Bubblewrap probes"


@pytest.mark.parametrize("shell", ["/usr/sbin/nologin", "/bin/false"])
def test_preflight_accepts_matching_non_root_non_login_broker_before_normal_probes(
    tmp_path, monkeypatch, shell
):
    import pwd

    from cli_agent_orchestrator.backends import bubblewrap_backend

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bubblewrap_backend.os, "geteuid", lambda: 1001)
    monkeypatch.setenv("CAO_WORK_BROKER_ACCOUNT", "caos-work-broker")
    monkeypatch.setattr(
        pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=1001, pw_shell=shell),
    )
    probes = []
    monkeypatch.setattr(
        bubblewrap_backend,
        "_query_abi_version",
        lambda: probes.append("landlock") or 11,
    )
    monkeypatch.setattr(
        bubblewrap_backend,
        "_probe_bwrap_version",
        lambda _path: probes.append("bubblewrap") or (0, 13, 0),
    )

    assert BubblewrapWorkBackend().preflight_work(_restriction(tmp_path)) is None
    assert probes == ["landlock", "bubblewrap"]


def test_preflight_rejects_nologin_alias_resolving_to_login_shell(tmp_path, monkeypatch):
    import pwd

    from cli_agent_orchestrator.backends import bubblewrap_backend

    login_shell = Path("/bin/bash")
    if not login_shell.exists():
        pytest.skip("the standard login shell is unavailable")
    nologin_alias = tmp_path / "nologin"
    nologin_alias.symlink_to(login_shell)

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bubblewrap_backend.os, "geteuid", lambda: 1001)
    monkeypatch.setenv("CAO_WORK_BROKER_ACCOUNT", "caos-work-broker")
    monkeypatch.setattr(
        pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=1001, pw_shell=str(nologin_alias)),
    )
    probes = []
    monkeypatch.setattr(
        bubblewrap_backend,
        "_query_abi_version",
        lambda: probes.append("landlock") or 11,
    )
    monkeypatch.setattr(
        bubblewrap_backend,
        "_probe_bwrap_version",
        lambda _path: probes.append("bubblewrap") or (0, 13, 0),
    )

    with pytest.raises(UnsupportedWorkEnforcement, match="non-login shell"):
        BubblewrapWorkBackend().preflight_work(_restriction(tmp_path))
    assert probes == []
