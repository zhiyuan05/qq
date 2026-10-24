"""长期语义记忆 - ChromaDB 向量存储"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import chromadb
from chromadb import PersistentClient

from utils.logger import setup_logger

logger = setup_logger("long_term")


class LongTermMemory:
    """基于 ChromaDB 的语义记忆检索，支持按用户过滤。

    使用本地计数器避免对 ChromaDB 的 count() 调用（在大量文档时昂贵）。
    """

    def __init__(self, collection_name: str = "chat_memory", top_k: int = 5, path: str = "data/chroma"):
        self.top_k = top_k
        self._collection_name = collection_name
        self._path = path
        self._collection: Any = None  # chromadb.Collection
        self._doc_counter: int = 0
        self._init_chroma()

    def _init_chroma(self):
        try:
            client: PersistentClient = chromadb.PersistentClient(path=self._path)
            self._collection = client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._doc_counter = self._collection.count()
            logger.info(f"ChromaDB 初始化成功 (已有 {self._doc_counter} 条记忆)")
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败: {e}，长期记忆不可用")
            self._collection = None

    def store(self, text: str, metadata: Optional[dict] = None, doc_id: Optional[str] = None):
        if not self._collection:
            return
        try:
            doc_id = doc_id or f"mem_{uuid.uuid4().hex}"
            stored_at = time.time()
            stored_metadata = dict(metadata or {})
            stored_metadata.setdefault("created_at", stored_at)
            stored_metadata.setdefault("last_access", stored_at)
            stored_metadata.setdefault("access_count", 0)
            self._collection.add(
                documents=[text],
                metadatas=[stored_metadata],
                ids=[doc_id],
            )
            self._doc_counter += 1
        except Exception as e:
            logger.error(f"存储记忆失败: {e}")

    def search(self, query: str, user_id: Optional[str] = None) -> list[dict]:
        if not self._collection or self._doc_counter <= 0:
            return []
        try:
            where = {"user_id": user_id} if user_id else None
            results = self._collection.query(
                query_texts=[query],
                n_results=self.top_k,
                where=where,
            )
            memories = []
            if results and results.get("documents"):
                accessed_ids = results.get("ids", [[]])[0]
                updated_metadata = []
                for i, doc in enumerate(results["documents"][0]):
                    metadata = dict(results["metadatas"][0][i] if results.get("metadatas") else {})
                    metadata["last_access"] = time.time()
                    metadata["access_count"] = int(metadata.get("access_count", 0)) + 1
                    updated_metadata.append(metadata)
                    memories.append({
                        "id": accessed_ids[i] if i < len(accessed_ids) else "",
                        "text": doc,
                        "metadata": metadata,
                        "distance": (results["distances"][0][i] if results.get("distances") else 0),
                    })
                if accessed_ids and updated_metadata:
                    self._collection.update(ids=accessed_ids, metadatas=updated_metadata)
            return memories
        except Exception as e:
            logger.error(f"搜索记忆失败: {e}")
            return []

    def clear(self, user_id: Optional[str] = None):
        if not self._collection:
            return
        try:
            if user_id:
                all_data = self._collection.get()
                ids_to_delete = []
                for i, meta in enumerate(all_data["metadatas"]):
                    if meta.get("user_id") == user_id:
                        ids_to_delete.append(all_data["ids"][i])
                if ids_to_delete:
                    self._collection.delete(ids=ids_to_delete)
                    self._doc_counter = self._collection.count()
            else:
                client: PersistentClient = chromadb.PersistentClient(path="data/chroma")
                client.delete_collection(self._collection_name)
                self._collection = client.get_or_create_collection(
                    name=self._collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                self._doc_counter = 0
        except Exception as e:
            logger.error(f"清空记忆失败: {e}")

    def cleanup(self, forgetting) -> dict:
        """按遗忘策略归档过期记忆，并返回清理报告。

        旧版本写入的数据没有时间元数据；这类数据会以当前时间作为基准，
        避免升级后第一次清理时误删历史记忆。
        """

        empty_report = {
            "archive_count": 0,
            "archive_ids": [],
            "low_priority_count": 0,
            "low_priority_ids": [],
            "active_count": 0,
            "active_ids": [],
        }
        if not self._collection:
            return empty_report

        try:
            data = self._collection.get(include=["metadatas"])
            now = time.time()
            memories = []
            for doc_id, raw_metadata in zip(data.get("ids", []), data.get("metadatas", [])):
                metadata = raw_metadata or {}
                memories.append(
                    {
                        "id": doc_id,
                        "last_access": metadata.get("last_access", metadata.get("created_at", now)),
                        "access_count": int(metadata.get("access_count", 0)),
                    }
                )

            report = forgetting.get_cleanup_report(memories)
            if report["archive_ids"]:
                self._collection.delete(ids=report["archive_ids"])
                self._doc_counter = max(0, self._doc_counter - report["archive_count"])
            return report
        except Exception as e:
            logger.error("长期记忆清理失败: %s", e)
            return empty_report
