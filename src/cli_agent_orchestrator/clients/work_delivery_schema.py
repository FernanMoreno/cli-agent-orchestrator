"""Additive, immutable execution data associated with an existing order binding."""

DELIVERY_SCHEMA = (
    """CREATE TABLE work_delivery_orders (
        attempt_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
        operation_kind TEXT NOT NULL, schema_version INTEGER NOT NULL CHECK(schema_version=1),
        adapter_version INTEGER NOT NULL CHECK(adapter_version>0),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)
            AND json_type(payload_json)='object' AND length(payload_json)<=65536),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
        delivery_hash TEXT NOT NULL CHECK(length(delivery_hash)=64),
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation)
    )""",
    """CREATE TRIGGER work_delivery_orders_immutable_update BEFORE UPDATE ON work_delivery_orders
        BEGIN SELECT RAISE(ABORT,'delivery orders are immutable'); END""",
    """CREATE TRIGGER work_delivery_orders_immutable_delete BEFORE DELETE ON work_delivery_orders
        BEGIN SELECT RAISE(ABORT,'delivery history cannot be deleted'); END""",
)


DELIVERY_CONTENT_SCHEMA = (
    """CREATE TABLE work_delivery_content_refs (
        attempt_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
        owner_principal_id TEXT NOT NULL REFERENCES work_principals(id),
        contract_hash TEXT NOT NULL CHECK(length(contract_hash)=64),
        snapshot_id TEXT NOT NULL REFERENCES work_delegation_snapshots(id),
        content_ref TEXT NOT NULL CHECK(length(content_ref)=64
            AND content_ref NOT GLOB '*[^0-9a-f]*'),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64
            AND content_hash NOT GLOB '*[^0-9a-f]*' AND content_hash=content_ref),
        byte_length INTEGER NOT NULL CHECK(byte_length>=0 AND byte_length<=65536),
        PRIMARY KEY(attempt_id,generation),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_delivery_orders(attempt_id,generation),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_dispatch_bindings(attempt_id,generation)
    )""",
    """CREATE TRIGGER work_delivery_content_refs_immutable_update
        BEFORE UPDATE ON work_delivery_content_refs
        BEGIN SELECT RAISE(ABORT,'delivery content references are immutable'); END""",
    """CREATE TRIGGER work_delivery_content_refs_immutable_delete
        BEFORE DELETE ON work_delivery_content_refs
        BEGIN SELECT RAISE(ABORT,'delivery content reference history cannot be deleted'); END""",
)
