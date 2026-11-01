"""Descriptive durable intent receipts, never TCP or retirement capabilities."""

from dataclasses import dataclass


class SqlOpeningPreparationUncertain(RuntimeError):
    """Preparation may have committed; this does not say that TCP was attempted."""


@dataclass(frozen=True)
class SqlOpeningIntent:
    intent_id: str
    journal_id: str
    ordinal: int
    path: str
    destination_sha256: str
    sql_application_name: str
