"""Memory forgetting mechanism: archive old, deprioritize low-access memories."""
import time
from utils.logger import setup_logger

logger = setup_logger("forgetting")


class MemoryForgetting:
    def __init__(
        self,
        low_access_threshold: int = 3,
        low_access_days: int = 30,
        archive_after_days: int = 60,
    ):
        self.low_access_threshold = low_access_threshold
        self.low_access_seconds = low_access_days * 86400
        self.archive_seconds = archive_after_days * 86400

    def should_archive(self, memory: dict) -> bool:
        last_access = memory.get("last_access", 0)
        access_count = memory.get("access_count", 0)
        age = time.time() - last_access

        if access_count == 0 and age >= self.archive_seconds:
            return True
        return False

    def is_low_priority(self, memory: dict) -> bool:
        last_access = memory.get("last_access", 0)
        access_count = memory.get("access_count", 0)
        age = time.time() - last_access

        if access_count < self.low_access_threshold and age >= self.low_access_seconds:
            return True
        return False

    def get_cleanup_report(self, memories: list[dict]) -> dict:
        archive = []
        low_priority = []
        active = []

        for m in memories:
            if self.should_archive(m):
                archive.append(m["id"])
            elif self.is_low_priority(m):
                low_priority.append(m["id"])
            else:
                active.append(m["id"])

        return {
            "archive_count": len(archive),
            "archive_ids": archive,
            "low_priority_count": len(low_priority),
            "low_priority_ids": low_priority,
            "active_count": len(active),
            "active_ids": active,
        }
