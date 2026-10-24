-- 005: bounded restore snapshot, attachment metadata, and append-only archive

ALTER TABLE conversations ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE chat_sessions ADD COLUMN summary_until_id INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS conversation_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    request_id VARCHAR(255) NOT NULL,
    turn_index INTEGER NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_archive_session_request_turn UNIQUE (session_id, request_id, turn_index)
);

CREATE INDEX IF NOT EXISTS ix_archive_session_id_id ON conversation_archive(session_id, id);
CREATE INDEX IF NOT EXISTS ix_archive_created_at ON conversation_archive(created_at);
