-- 006: versioned persona profiles, traits, cursors

CREATE TABLE IF NOT EXISTS persona_profiles (
    scope VARCHAR(20) NOT NULL,
    scope_key VARCHAR(200) NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    profile_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope, scope_key)
);

CREATE TABLE IF NOT EXISTS persona_traits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope VARCHAR(20) NOT NULL,
    scope_key VARCHAR(200) NOT NULL,
    category VARCHAR(50) NOT NULL,
    trait_key VARCHAR(200) NOT NULL,
    value TEXT NOT NULL,
    confidence FLOAT NOT NULL DEFAULT 0,
    evidence TEXT NOT NULL DEFAULT '',
    source_conversation_id INTEGER,
    source_sessions TEXT NOT NULL DEFAULT '[]',
    tier VARCHAR(20) NOT NULL DEFAULT 'retrieved',
    status VARCHAR(20) NOT NULL DEFAULT 'candidate',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_persona_trait_identity UNIQUE (scope, scope_key, category, trait_key, value)
);

CREATE INDEX IF NOT EXISTS ix_persona_traits_active
ON persona_traits(scope, scope_key, status, confidence);

CREATE TABLE IF NOT EXISTS persona_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope VARCHAR(20) NOT NULL,
    scope_key VARCHAR(200) NOT NULL,
    revision INTEGER NOT NULL,
    diff_json TEXT NOT NULL DEFAULT '{}',
    reason VARCHAR(200) NOT NULL DEFAULT 'evolve',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_persona_version UNIQUE (scope, scope_key, revision)
);

CREATE TABLE IF NOT EXISTS persona_cursors (
    session_id VARCHAR(200) PRIMARY KEY,
    last_conversation_id INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
