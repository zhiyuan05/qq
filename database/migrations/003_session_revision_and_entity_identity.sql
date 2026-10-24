-- 003: optimistic session revision and deterministic entity upsert key

ALTER TABLE chat_sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;

-- Older schemas didn't declare the uniqueness required by EntityMemory's
-- ON CONFLICT clause. Retain the newest duplicate before adding the index.
DELETE FROM entity_memories
WHERE id NOT IN (
    SELECT MAX(id)
    FROM entity_memories
    GROUP BY user_id, entity_type, key
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_user_type_key
ON entity_memories(user_id, entity_type, key);
