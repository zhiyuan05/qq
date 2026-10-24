-- 007: per-turn LLM usage ledger

CREATE TABLE IF NOT EXISTS llm_usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key VARCHAR(64) NOT NULL UNIQUE,
    request_id VARCHAR(255) NOT NULL DEFAULT '',
    session_id VARCHAR(200) NOT NULL DEFAULT '',
    user_id VARCHAR(200) NOT NULL DEFAULT '',
    model VARCHAR(200) NOT NULL DEFAULT '',
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_micros BIGINT NOT NULL DEFAULT 0,
    currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_usage_session_time ON llm_usage_events(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_usage_time ON llm_usage_events(occurred_at);
