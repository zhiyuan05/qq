"""长期语义记忆"""
from typing import Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("long_term")


class LongTermMemory:
    def __init__(self, collection_name: str = "chat_memory", top_k: int = 5):
        self.top_k = top_k
        self._collection = None
        self._collection_name = collection_name
        self._init_chroma()

    def _init_chroma(self):
        try:
            import chromadb
            client = chromadb.PersistentClient(path="data/chroma")
            self._collection = client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("ChromaDB 初始化成功")
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败: {e}，长期记忆不可用")
            self._collection = None

    def store(self, text: str, metadata: Dict = None, doc_id: str = None):
        if not self._collection:
            return
        try:
            self._collection.add(
                documents=[text],
                metadatas=[metadata or {}],
                ids=[doc_id or f"mem_{self._collection.count() + 1}"],
            )
        except Exception as e:
            logger.error(f"存储记忆失败: {e}")

    def search(self, query: str, user_id: Optional[str] = None) -> List[Dict]:
        if not self._collection:
            return []
        try:
            where = {"user_id": user_id} if user_id else None
            results = self._collection.query(
                query_texts=[query],
                n_results=self.top_k,
                where=where,
            )
            memories = []
            if results and results["documents"]:
                for i, doc in enumerate(results["documents"][0]):
                    memories.append({
                        "text": doc,
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else 0,
                    })
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
            else:
                import chromadb
                client = chromadb.PersistentClient(path="data/chroma")
                client.delete_collection(self._collection_name)
                self._collection = client.get_or_create_collection(
                    name=self._collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
        except Exception as e:
            logger.error(f"清空记忆失败: {e}")
