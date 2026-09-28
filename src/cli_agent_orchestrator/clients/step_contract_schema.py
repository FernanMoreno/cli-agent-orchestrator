"""Immutable pre-delivery step evidence; installed by the verified work migration."""

STEP_CONTRACT_SCHEMA = (
    """CREATE TABLE work_step_contracts (
        run_id TEXT NOT NULL REFERENCES workflow_run(run_id),
        step_id TEXT NOT NULL,
        generation TEXT NOT NULL CHECK(length(generation)>0),
        attempt_number INTEGER NOT NULL CHECK(attempt_number>0),
        contract_json TEXT NOT NULL CHECK(
            json_valid(contract_json) AND json_type(contract_json)='object'
            AND coalesce(json_extract(contract_json,'$.schema_version'),0)=1
            AND json_type(contract_json,'$.schema_version')='integer'
            AND length(contract_json)<=65536),
        created_at TEXT NOT NULL,
        PRIMARY KEY(run_id,step_id,generation,attempt_number)
    )""",
    """CREATE TRIGGER work_step_contracts_immutable_update
        BEFORE UPDATE ON work_step_contracts
        BEGIN SELECT RAISE(ABORT,'step contracts are immutable'); END""",
    """CREATE TRIGGER work_step_contracts_immutable_delete
        BEFORE DELETE ON work_step_contracts
        BEGIN SELECT RAISE(ABORT,'step contract history cannot be deleted'); END""",
)
