"""T065 v24 durable recovery context; restore stays blocked until a later workflow reconciles it."""

from dataclasses import dataclass

RECOVERY_CONTEXT_VERSION = 1
RECOVERY_STATE_NORMAL = "normal"
RECOVERY_STATE_BLOCKED_RESTORE = "blocked_restore"


@dataclass(frozen=True)
class RecoveryStoreContext:
    """Verified, non-secret context for one v24 work-store installation."""

    context_version: int
    execution_state: str
    installation_uuid: str
    source_installation_uuid: str | None
    bundle_digest: str | None
    restore_receipt: str | None
