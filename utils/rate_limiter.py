"""频率限制器"""
import time
from collections import defaultdict
from typing import Dict, Tuple


class RateLimiter:
    def __init__(self, max_per_minute: int = 20, max_per_hour: int = 200):
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self._records: Dict[str, list] = defaultdict(list)

    def is_allowed(self, user_id: str) -> Tuple[bool, str]:
        now = time.time()
        records = self._records[user_id]
        records[:] = [t for t in records if now - t < 3600]

        if len(records) >= self.max_per_hour:
            return False, "每小时请求次数已达上限"

        recent = [t for t in records if now - t < 60]
        if len(recent) >= self.max_per_minute:
            return False, "请求太频繁，请稍后再试"

        records.append(now)
        return True, ""

    def reset(self, user_id: str):
        self._records.pop(user_id, None)
