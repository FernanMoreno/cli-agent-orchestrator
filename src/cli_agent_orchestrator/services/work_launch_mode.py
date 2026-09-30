"""Explicit local routing policy for ordinary launch admission."""

import os


def managed_launch_required() -> bool:
    """Require durable admission only when the operator selects that mode."""
    mode = os.environ.get("CAO_WORK_LAUNCH_MODE", "legacy")
    if mode in ("", "legacy"):
        return False
    if mode == "required":
        return True
    raise ValueError("CAO_WORK_LAUNCH_MODE must be 'legacy' or 'required'")
