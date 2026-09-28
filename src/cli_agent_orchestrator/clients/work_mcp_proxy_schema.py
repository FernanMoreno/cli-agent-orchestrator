"""Durable one-shot issue fence for attempt-bound Work MCP endpoints."""

WORK_MCP_PROXY_ISSUE_SCHEMA = (
    "CREATE UNIQUE INDEX work_dispatch_proxy_issue_reference "
    "ON work_dispatch_bindings(attempt_id,generation,contract_hash)",
    """CREATE TABLE work_mcp_proxy_issues (
        attempt_id TEXT PRIMARY KEY,
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        attempt_revision INTEGER NOT NULL
            CHECK(typeof(attempt_revision)='integer' AND attempt_revision>0),
        contract_hash TEXT NOT NULL
            CHECK(length(contract_hash)=64 AND contract_hash NOT GLOB '*[^0-9a-f]*'),
        expires_at REAL NOT NULL CHECK(typeof(expires_at)='real' AND expires_at>0),
        issued_at REAL NOT NULL CHECK(typeof(issued_at)='real' AND issued_at>0),
        schema_version INTEGER NOT NULL DEFAULT 1
            CHECK(typeof(schema_version)='integer' AND schema_version=1),
        FOREIGN KEY(attempt_id,generation,contract_hash)
            REFERENCES work_dispatch_bindings(attempt_id,generation,contract_hash)
    )""",
    """CREATE TRIGGER work_mcp_proxy_issues_immutable_update
        BEFORE UPDATE ON work_mcp_proxy_issues
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy issue is immutable'); END""",
    """CREATE TRIGGER work_mcp_proxy_issues_immutable_delete
        BEFORE DELETE ON work_mcp_proxy_issues
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy issue cannot be deleted'); END""",
)

WORK_MCP_PROXY_EFFECT_SCHEMA = (
    "CREATE UNIQUE INDEX work_mcp_proxy_issue_attempt_generation "
    "ON work_mcp_proxy_issues(attempt_id,generation)",
    """CREATE TABLE work_mcp_proxy_effects (
        effect_id TEXT PRIMARY KEY CHECK(length(effect_id)=32 AND effect_id NOT GLOB '*[^0-9a-f]*'),
        attempt_id TEXT NOT NULL REFERENCES work_mcp_proxy_issues(attempt_id),
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        request_sha256 TEXT NOT NULL
            CHECK(length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
        created_at REAL NOT NULL CHECK(typeof(created_at)='real' AND created_at>0),
        UNIQUE(attempt_id,generation,request_sha256),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_mcp_proxy_issues(attempt_id,generation)
    )""",
    """CREATE TABLE work_mcp_proxy_effect_events (
        effect_id TEXT NOT NULL REFERENCES work_mcp_proxy_effects(effect_id),
        sequence INTEGER NOT NULL CHECK(typeof(sequence)='integer' AND sequence>0),
        state TEXT NOT NULL CHECK(state IN
            ('intent','completed','failed_before_effect','uncertain','reconciled')),
        response_sha256 TEXT CHECK(response_sha256 IS NULL OR
            (length(response_sha256)=64 AND response_sha256 NOT GLOB '*[^0-9a-f]*')),
        resolution_sha256 TEXT CHECK(resolution_sha256 IS NULL OR
            (length(resolution_sha256)=64 AND resolution_sha256 NOT GLOB '*[^0-9a-f]*')),
        occurred_at REAL NOT NULL CHECK(typeof(occurred_at)='real' AND occurred_at>0),
        PRIMARY KEY(effect_id,sequence),
        CHECK((state='completed' AND response_sha256 IS NOT NULL) OR
              (state!='completed' AND response_sha256 IS NULL)),
        CHECK((state='reconciled' AND resolution_sha256 IS NOT NULL) OR
              (state!='reconciled' AND resolution_sha256 IS NULL))
    )""",
    """CREATE INDEX work_mcp_proxy_effect_attempt
        ON work_mcp_proxy_effects(attempt_id,generation,created_at)""",
    """CREATE TRIGGER work_mcp_proxy_effects_immutable_update
        BEFORE UPDATE ON work_mcp_proxy_effects
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy effect is immutable'); END""",
    """CREATE TRIGGER work_mcp_proxy_effects_immutable_delete
        BEFORE DELETE ON work_mcp_proxy_effects
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy effect cannot be deleted'); END""",
    """CREATE TRIGGER work_mcp_proxy_effect_events_transition_guard
        BEFORE INSERT ON work_mcp_proxy_effect_events
        BEGIN
          SELECT CASE WHEN NEW.sequence != coalesce(
              (SELECT max(sequence) FROM work_mcp_proxy_effect_events
               WHERE effect_id=NEW.effect_id), 0) + 1
            THEN RAISE(ABORT,'Work MCP proxy effect sequence is not monotonic') END;
          SELECT CASE WHEN NOT (
              (NEW.sequence=1 AND NEW.state='intent') OR
              (NEW.sequence=2 AND NEW.state IN
                  ('completed','failed_before_effect','uncertain') AND
               (SELECT state FROM work_mcp_proxy_effect_events
                WHERE effect_id=NEW.effect_id AND sequence=1)='intent') OR
              (NEW.sequence=3 AND NEW.state='reconciled' AND
               (SELECT state FROM work_mcp_proxy_effect_events
                WHERE effect_id=NEW.effect_id AND sequence=2)='uncertain')
            ) THEN RAISE(ABORT,'Work MCP proxy effect transition is invalid') END;
        END""",
    """CREATE TRIGGER work_mcp_proxy_effect_events_immutable_update
        BEFORE UPDATE ON work_mcp_proxy_effect_events
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy effect event is immutable'); END""",
    """CREATE TRIGGER work_mcp_proxy_effect_events_immutable_delete
        BEFORE DELETE ON work_mcp_proxy_effect_events
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy effect event cannot be deleted'); END""",
)

WORK_MCP_PROXY_ISSUE_RECOVERY_SCHEMA = (
    """CREATE TABLE work_mcp_proxy_issue_events (
        attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
        sequence INTEGER NOT NULL CHECK(typeof(sequence)='integer' AND sequence>0),
        state TEXT NOT NULL CHECK(state IN ('issued','abandoned','reconciled')),
        resolution_sha256 TEXT CHECK(resolution_sha256 IS NULL OR
            (length(resolution_sha256)=64 AND resolution_sha256 NOT GLOB '*[^0-9a-f]*')),
        occurred_at REAL NOT NULL CHECK(typeof(occurred_at)='real' AND occurred_at>0),
        PRIMARY KEY(attempt_id,generation,sequence),
        CHECK((state='reconciled' AND resolution_sha256 IS NOT NULL) OR
              (state!='reconciled' AND resolution_sha256 IS NULL)),
        FOREIGN KEY(attempt_id,generation)
            REFERENCES work_mcp_proxy_issues(attempt_id,generation)
    )""",
    """CREATE TRIGGER work_mcp_proxy_issue_events_transition_guard
        BEFORE INSERT ON work_mcp_proxy_issue_events
        BEGIN
          SELECT CASE WHEN NEW.sequence != coalesce(
              (SELECT max(sequence) FROM work_mcp_proxy_issue_events
               WHERE attempt_id=NEW.attempt_id AND generation=NEW.generation), 0) + 1
            THEN RAISE(ABORT,'Work MCP proxy issue sequence is not monotonic') END;
          SELECT CASE WHEN NOT (
              (NEW.sequence=1 AND NEW.state='issued') OR
              (NEW.sequence=2 AND NEW.state='abandoned' AND
               (SELECT state FROM work_mcp_proxy_issue_events
                WHERE attempt_id=NEW.attempt_id AND generation=NEW.generation AND sequence=1)='issued') OR
              (NEW.sequence=3 AND NEW.state='reconciled' AND
               (SELECT state FROM work_mcp_proxy_issue_events
                WHERE attempt_id=NEW.attempt_id AND generation=NEW.generation AND sequence=2)='abandoned')
            ) THEN RAISE(ABORT,'Work MCP proxy issue transition is invalid') END;
        END""",
    """CREATE TRIGGER work_mcp_proxy_issue_events_immutable_update
        BEFORE UPDATE ON work_mcp_proxy_issue_events
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy issue event is immutable'); END""",
    """CREATE TRIGGER work_mcp_proxy_issue_events_immutable_delete
        BEFORE DELETE ON work_mcp_proxy_issue_events
        BEGIN SELECT RAISE(ABORT,'Work MCP proxy issue event cannot be deleted'); END""",
    "INSERT INTO work_mcp_proxy_issue_events "
    "(attempt_id,generation,sequence,state,occurred_at) "
    "SELECT attempt_id,generation,1,'issued',issued_at FROM work_mcp_proxy_issues",
)
