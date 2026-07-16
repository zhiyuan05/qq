"""实体记忆 - 支持 SQLite 持久化"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("entity_memory")


@dataclass
class Entity:
    entity_type: str
    key: str
    value: str
    confidence: float = 1.0
    source: str = "auto"
    updated_at: datetime = None

    def __post_init__(self):
        if self.updated_at is None:
            self.updated_at = datetime.now()


class EntityMemory:
    def __init__(self, db_path: str = "data/chatbot.db"):
        self._entities: Dict[str, List[Entity]] = {}
        self._db_path = db_path
        self._db_initialized = False

    def _ensure_db(self):
        if self._db_initialized:
            return
        try:
            import sqlite3
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    source TEXT DEFAULT 'auto',
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(user_id, entity_type, key)
                )
            """)
            conn.commit()
            # 加载已有数据
            rows = conn.execute("SELECT user_id, entity_type, key, value, confidence, source, updated_at FROM entity_memories").fetchall()
            for row in rows:
                uid, etype, key, value, conf, source, updated = row
                if uid not in self._entities:
                    self._entities[uid] = []
                self._entities[uid].append(Entity(
                    entity_type=etype, key=key, value=value,
                    confidence=conf, source=source,
                    updated_at=datetime.fromisoformat(updated) if updated else None,
                ))
            conn.close()
            self._db_initialized = True
            logger.info(f"实体记忆已加载，{sum(len(v) for v in self._entities.values())} 条记录")
        except Exception as e:
            logger.warning(f"实体记忆数据库初始化失败(内存模式): {e}")

    def _save_to_db(self, user_id: str, entity: Entity):
        try:
            import sqlite3
            conn = sqlite3.connect(self._db_path)
            now = datetime.now().isoformat()
            conn.execute("""
                INSERT INTO entity_memories (user_id, entity_type, key, value, confidence, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, entity_type, key) DO UPDATE SET
                    value=excluded.value, confidence=excluded.confidence,
                    source=excluded.source, updated_at=excluded.updated_at
            """, (user_id, entity.entity_type, entity.key, entity.value,
                  entity.confidence, entity.source, now, now))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"保存实体记忆失败: {e}")

    def _delete_from_db(self, user_id: str, key: str):
        try:
            import sqlite3
            conn = sqlite3.connect(self._db_path)
            conn.execute("DELETE FROM entity_memories WHERE user_id=? AND key=?", (user_id, key))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _clear_from_db(self, user_id: str):
        try:
            import sqlite3
            conn = sqlite3.connect(self._db_path)
            conn.execute("DELETE FROM entity_memories WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def store(self, user_id: str, entity_type: str, key: str, value: str,
              confidence: float = 1.0, source: str = "auto"):
        self._ensure_db()
        if user_id not in self._entities:
            self._entities[user_id] = []

        for e in self._entities[user_id]:
            if e.key == key and e.entity_type == entity_type:
                e.value = value
                e.confidence = confidence
                e.updated_at = datetime.now()
                self._save_to_db(user_id, e)
                return

        entity = Entity(
            entity_type=entity_type, key=key, value=value,
            confidence=confidence, source=source,
        )
        self._entities[user_id].append(entity)
        self._save_to_db(user_id, entity)

    def query(self, user_id: str, entity_type: Optional[str] = None) -> List[Entity]:
        self._ensure_db()
        entities = self._entities.get(user_id, [])
        if entity_type:
            return [e for e in entities if e.entity_type == entity_type]
        return entities

    def get_profile_text(self, user_id: str) -> str:
        self._ensure_db()
        entities = self._entities.get(user_id, [])
        if not entities:
            return ""
        lines = ["用户画像:"]
        for e in entities:
            lines.append(f"- {e.key}: {e.value}")
        return "\n".join(lines)

    def forget(self, user_id: str, key: str) -> bool:
        self._ensure_db()
        entities = self._entities.get(user_id, [])
        before = len(entities)
        self._entities[user_id] = [e for e in entities if e.key != key]
        deleted = len(self._entities[user_id]) < before
        if deleted:
            self._delete_from_db(user_id, key)
        return deleted

    def clear(self, user_id: str):
        self._ensure_db()
        self._entities.pop(user_id, None)
        self._clear_from_db(user_id)
