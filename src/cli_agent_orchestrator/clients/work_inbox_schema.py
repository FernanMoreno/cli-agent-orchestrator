"""Additive, immutable binding and store context for managed inbox work."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedInboxStoreIdentity:
    """Non-secret logical identity read from a verified Work store context."""

    store_identity: str
    store_uuid: str
