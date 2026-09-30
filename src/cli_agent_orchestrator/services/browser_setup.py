"""Owner-authorized first account provisioning for the private personal deployment."""

import fcntl
import json
import os
import stat
import tempfile
import uuid
from pathlib import Path

from cli_agent_orchestrator.models.browser_auth import BrowserAuthError
from cli_agent_orchestrator.services.browser_auth import BrowserAuthService


class PublicationUncertain(BrowserAuthError):
    """The replacement is visible, but its durability could not be confirmed."""

    def __init__(self):
        super().__init__("setup_publication_uncertain", 503)


def private_deployment(root):
    root = Path(root)
    for path, directory in (
        (root, True),
        (root / "deployment.json", False),
        (root / "issuer.pem", False),
    ):
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or directory != stat.S_ISDIR(info.st_mode)
        ):
            raise ValueError("browser login requires private owner-controlled deployment")
    deployment = json.loads((root / "deployment.json").read_text())
    if deployment.get("host") != "127.0.0.1" or type(deployment.get("server_port")) is not int:
        raise ValueError("invalid local browser deployment")
    return deployment


def setup_available(root, deployment=None):
    deployment = deployment or private_deployment(root)
    policy = deployment.get("browser_login")
    # Only an interrupted first creation is resumable. Disabled existing accounts
    # require the local recovery tool, never the browser setup route.
    return (policy is None and not (Path(root) / "browser-auth.sqlite3").exists()) or (
        isinstance(policy, dict)
        and policy.get("provisioning_pending") is True
        and not policy.get("enabled")
    )


def _publish(path, deployment):
    fd, temporary = tempfile.mkstemp(prefix=".browser-setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as writer:
            json.dump(deployment, writer, indent=2)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            # Repeating setup would assume the replacement never happened.
            # The caller must reconcile the visible, validated state instead.
            raise PublicationUncertain() from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


def create_first_account(root, binding, username, password, remember, peer):
    root = Path(root)
    lock = os.open(root / "account.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BrowserAuthError("auth_unavailable", 503)
        fcntl.flock(lock, fcntl.LOCK_EX)
        deployment = private_deployment(root)
        from cli_agent_orchestrator.security.auth import _verified_principal

        scopes = deployment.get("scopes", ["cao:read", "cao:write", "cao:admin"])
        expected = _verified_principal(deployment["issuer"], deployment["subject"], scopes, "jwt")
        if (
            any(
                binding.get(key) != getattr(expected, key)
                for key in ("id", "issuer", "subject", "kind")
            )
            or frozenset(binding.get("scopes", [])) != expected.scopes
        ):
            raise BrowserAuthError("auth_unavailable", 503)
        if not setup_available(root, deployment):
            raise BrowserAuthError("account_exists", 409)
        policy = deployment.get("browser_login") or {
            "enabled": False,
            "provisioning_pending": True,
            "installation_id": uuid.uuid4().hex,
            "canonical_origin": f"http://127.0.0.1:{deployment['server_port']}",
            "transport_policy": "loopback_http",
            "binding": binding,
            "limits": {
                "access_seconds": 3600,
                "remembered_idle_seconds": 604800,
                "remembered_absolute_seconds": 2592000,
                "temporal_idle_seconds": 28800,
                "temporal_absolute_seconds": 86400,
            },
        }
        if {**policy["binding"], "scopes": sorted(policy["binding"]["scopes"])} != {
            **binding,
            "scopes": sorted(binding["scopes"]),
        }:
            raise BrowserAuthError("auth_unavailable", 503)
        # Validate before persisting an interrupted provisioning marker.
        BrowserAuthService._username(username)
        if not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise BrowserAuthError("invalid_auth_request", 422)
        deployment["browser_login"] = policy
        _publish(root / "deployment.json", deployment)
        service = BrowserAuthService(root / "browser-auth.sqlite3", binding, policy)
        try:
            service.create_account(username, password)
        except BrowserAuthError as error:
            if error.code != "account_exists" or not service.account_matches(username, password):
                raise
        service.enabled = True
        policy["enabled"] = True
        policy.pop("provisioning_pending", None)
        # Authenticate before publishing enabled state. No bearer policy changes.
        secret, dto = service.login(username, password, remember, peer)
        _publish(root / "deployment.json", deployment)
        return service, policy, secret, dto
    finally:
        os.close(lock)
