"""Additive, immutable binding and store context for managed inbox work."""

from dataclasses import dataclass

INBOX_SCHEMA = (
    """CREATE TABLE work_inbox_store_identity (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        store_identity TEXT NOT NULL UNIQUE,
        opened_at REAL NOT NULL
    )""",
    """CREATE TABLE work_inbox_bindings (
        inbox_id INTEGER NOT NULL UNIQUE CHECK(inbox_id>0),
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0),
        store_identity TEXT NOT NULL REFERENCES work_inbox_store_identity(store_identity),
        created_at REAL NOT NULL,
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation)
    )""",
    """CREATE TRIGGER work_inbox_store_identity_immutable_update
        BEFORE UPDATE ON work_inbox_store_identity
        BEGIN SELECT RAISE(ABORT,'work inbox store identity is immutable'); END""",
    """CREATE TRIGGER work_inbox_store_identity_immutable_delete
        BEFORE DELETE ON work_inbox_store_identity
        BEGIN SELECT RAISE(ABORT,'work inbox store identity cannot be deleted'); END""",
    """CREATE TRIGGER work_inbox_bindings_immutable_update
        BEFORE UPDATE ON work_inbox_bindings
        BEGIN SELECT RAISE(ABORT,'work inbox bindings are immutable'); END""",
    """CREATE TRIGGER work_inbox_bindings_immutable_delete
        BEFORE DELETE ON work_inbox_bindings
        BEGIN SELECT RAISE(ABORT,'work inbox bindings cannot be deleted'); END""",
)


@dataclass(frozen=True)
class ManagedInboxStoreIdentity:
    """Non-secret logical identity read from a verified SQLite connection."""

    store_identity: str
    store_uuid: str


INBOX_STORE_CONTEXT_SCHEMA = (
    """CREATE TABLE work_inbox_store_context (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        store_uuid TEXT NOT NULL UNIQUE,
        store_identity TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL
    )""",
    """CREATE TRIGGER work_inbox_store_context_immutable_update
        BEFORE UPDATE ON work_inbox_store_context
        BEGIN SELECT RAISE(ABORT,'work inbox store context is immutable'); END""",
    """CREATE TRIGGER work_inbox_store_context_immutable_delete
        BEFORE DELETE ON work_inbox_store_context
        BEGIN SELECT RAISE(ABORT,'work inbox store context cannot be deleted'); END""",
)
