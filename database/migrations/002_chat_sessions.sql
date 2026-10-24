-- 002: 持久化会话元数据

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id VARCHAR(200) PRIMARY KEY,
    user_id VARCHAR(200) NOT NULL,
    platform VARCHAR(20) NOT NULL,
    character_card VARCHAR(100) DEFAULT 'default',
    style VARCHAR(50) DEFAULT 'casual',
    summary TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_platform ON chat_sessions(platform);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_last_active ON chat_sessions(last_active);
CREATE INDEX IF NOT EXISTS idx_conversations_session_created ON conversations(session_id, created_at);
