-- 004: durable inbound-event, run, run-event, and delivery-outbox core

CREATE TABLE IF NOT EXISTS inbound_events (
    id VARCHAR(36) PRIMARY KEY,
    dedupe_key VARCHAR(64) NOT NULL UNIQUE,
    source VARCHAR(32) NOT NULL,
    connector_key VARCHAR(128) NOT NULL,
    external_event_id VARCHAR(255) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    principal_key VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255) NOT NULL,
    request_json JSON NOT NULL DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'RECEIVED'
        CHECK (status IN ('RECEIVED', 'PROCESSING', 'COMPLETED', 'FAILED', 'IGNORED', 'UNKNOWN')),
    run_id VARCHAR(36) NOT NULL UNIQUE,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP,
    lease_owner VARCHAR(128),
    lease_token BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMP,
    error_code VARCHAR(128),
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_inbound_source_connector_external
        UNIQUE (source, connector_key, external_event_id)
);

CREATE INDEX IF NOT EXISTS ix_inbound_events_queue
ON inbound_events(status, next_attempt_at, received_at);

CREATE INDEX IF NOT EXISTS ix_inbound_events_conversation
ON inbound_events(conversation_id, received_at);

CREATE TABLE IF NOT EXISTS runs (
    id VARCHAR(36) PRIMARY KEY,
    inbound_event_id VARCHAR(36) NOT NULL UNIQUE
        REFERENCES inbound_events(id) ON DELETE CASCADE,
    conversation_id VARCHAR(255) NOT NULL,
    turn_no BIGINT NOT NULL,
    request_id VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN (
            'QUEUED', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED',
            'FAILED', 'CANCELLED', 'UNKNOWN', 'SUPERSEDED'
        )),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP,
    lease_owner VARCHAR(128),
    lease_token BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMP,
    input_revision INTEGER,
    output_revision INTEGER,
    agent_version VARCHAR(100),
    prompt_version VARCHAR(100),
    provider VARCHAR(100),
    model VARCHAR(200),
    provider_request_id VARCHAR(255),
    prompt_hash VARCHAR(64),
    response_text TEXT,
    response_hash VARCHAR(64),
    completion_hash VARCHAR(64),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cost_micros BIGINT,
    cost_currency VARCHAR(8),
    error_type VARCHAR(100),
    error_code VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    first_token_at TIMESTAMP,
    completed_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_runs_conversation_turn UNIQUE (conversation_id, turn_no)
);

CREATE INDEX IF NOT EXISTS ix_runs_claim
ON runs(status, next_attempt_at, created_at);

CREATE INDEX IF NOT EXISTS ix_runs_request_id
ON runs(request_id);

CREATE TABLE IF NOT EXISTS run_events (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_run_events_run_sequence UNIQUE (run_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_run_events_run_created
ON run_events(run_id, created_at);

CREATE TABLE IF NOT EXISTS outbox_deliveries (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    destination VARCHAR(32) NOT NULL,
    connector_key VARCHAR(128) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0 CHECK (chunk_index >= 0),
    payload JSON NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(200) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN (
            'PENDING', 'SENDING', 'RETRY_WAIT', 'DELIVERED',
            'UNKNOWN', 'DEAD', 'CANCELLED', 'EXPIRED'
        )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner VARCHAR(128),
    lease_token BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMP,
    remote_message_id VARCHAR(255),
    accepted_at TIMESTAMP,
    deliver_before TIMESTAMP,
    last_error_type VARCHAR(100),
    last_error_code VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_outbox_run_destination_chunk
        UNIQUE (run_id, destination, connector_key, target_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS ix_outbox_deliveries_queue
ON outbox_deliveries(status, available_at, created_at);
