# Chatbot v2 Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the chatbot into a secure, extensible, multi-platform system with comprehensive anti-ban protection, Telegram support, multi-LLM failover, and a web management dashboard.

**Architecture:** Middleware-based message pipeline with three-layer security (behavior simulation, rate limiting, circuit breaker), multi-provider LLM with failover, and a FastAPI web dashboard. Priority: Security > Quality > Features.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy (async), ChromaDB, python-telegram-bot v20+, httpx, PyYAML

---

## File Map

### New Files

| File | Responsibility |
|------|---------------|
| `config/config.yaml` | Centralized configuration |
| `config/sensitive_words.txt` | Sensitive word list |
| `utils/config.py` | Config loader with hot-reload support |
| `security/__init__.py` | Security package |
| `security/rate_limiter.py` | Three-tier rate limiter (per-user, per-group, global) |
| `security/circuit_breaker.py` | Circuit breaker with CLOSED/OPEN/HALF_OPEN states |
| `security/content_filter.py` | Sensitive word filtering |
| `security/alerting.py` | Alert manager with Telegram notifications |
| `core/pipeline.py` | Message pipeline + middleware orchestration |
| `core/middleware/__init__.py` | Middleware package |
| `core/middleware/rate_limit.py` | Rate limit middleware |
| `core/middleware/filter.py` | User/message filter middleware |
| `core/middleware/active_window.py` | Active time window middleware |
| `core/middleware/memory.py` | Memory injection middleware |
| `core/middleware/character.py` | Character injection middleware |
| `core/middleware/content_filter.py` | Content filter middleware |
| `core/middleware/safety.py` | Circuit breaker + final safety check middleware |
| `core/middleware/split.py` | Message splitting middleware |
| `core/middleware/delay.py` | Human delay middleware |
| `platforms/telegram.py` | Telegram Bot API adapter |
| `llm/providers/__init__.py` | Provider registry |
| `llm/providers/deepseek.py` | DeepSeek provider |
| `llm/providers/openai_compat.py` | OpenAI-compatible provider |
| `llm/providers/ollama.py` | Ollama local provider |
| `memory/forgetting.py` | Memory forgetting/archival mechanism |
| `web/api/__init__.py` | API package |
| `web/api/dashboard.py` | Dashboard stats API |
| `web/api/conversations.py` | Conversation management API |
| `web/api/characters.py` | Character management API |
| `web/api/memory.py` | Memory management API |
| `web/api/security.py` | Security status API |
| `web/api/settings.py` | Settings API |
| `web/websocket.py` | WebSocket real-time updates |
| `web/static/index.html` | Dashboard SPA |
| `web/static/app.js` | Frontend JavaScript |
| `web/static/style.css` | Custom styles |
| `tests/test_security.py` | Security system tests |
| `tests/test_pipeline.py` | Pipeline + middleware tests |
| `tests/test_telegram.py` | Telegram adapter tests |
| `tests/test_multi_llm.py` | Multi-LLM tests |
| `tests/test_memory_enhanced.py` | Memory enhancement tests |
| `tests/test_web_api.py` | Web API tests |

### Modified Files

| File | Changes |
|------|---------|
| `requirements.txt` | Add python-telegram-bot, aiofiles, python-multipart, jinja2 |
| `platforms/models.py` | Add TELEGRAM to Platform enum |
| `platforms/base.py` | Minor: remove WeChat-specific references |
| `platforms/qq.py` | Use pipeline instead of direct callback |
| `llm/client.py` | Refactor to use provider abstraction |
| `memory/long_term.py` | Add relevance threshold, deduplication |
| `memory/entity_memory.py` | Add relation tracking, temporal data |
| `memory/summarizer.py` | Improve summary prompts |
| `database/models.py` | Add Alert, Metric models |
| `database/db.py` | Add new table creation |
| `main.py` | Remove WeChat, add Telegram, wire pipeline |
| `web/app.py` | Refactor to use new API routes |

### Deleted Files

| File | Reason |
|------|--------|
| `platforms/wechat.py` | WeChat removed temporarily (ban risk) |

---

## Phase 1: Security Core

### Task 1: Centralized Configuration System

**Files:**
- Create: `config/config.yaml`
- Create: `config/sensitive_words.txt`
- Create: `utils/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest
import yaml
import tempfile
import os
from utils.config import Config


def test_load_config_from_yaml():
    """Config loads all sections from YAML file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({
            "platforms": {"qq": {"enabled": True, "onebot_url": "ws://127.0.0.1:3001"}},
            "llm": {"primary": {"provider": "deepseek", "api_key": "test-key"}},
            "security": {"rate_limit": {"per_user_per_minute": 10}},
            "memory": {"short_term": {"max_messages": 50}},
            "web": {"enabled": True, "port": 8080},
        }, f)
        f.flush()
        config = Config(f.name)

    assert config.get("platforms.qq.enabled") is True
    assert config.get("platforms.qq.onebot_url") == "ws://127.0.0.1:3001"
    assert config.get("llm.primary.api_key") == "test-key"
    assert config.get("security.rate_limit.per_user_per_minute") == 10
    assert config.get("nonexistent.key", "default") == "default"
    os.unlink(f.name)


def test_config_default_values():
    """Config returns defaults for missing keys."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({}, f)
        f.flush()
        config = Config(f.name)

    assert config.get("missing", 42) == 42
    assert config.get("deep.nested.missing") is None
    os.unlink(f.name)


def test_config_set_and_get():
    """Config can set values at runtime."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({}, f)
        f.flush()
        config = Config(f.name)

    config.set("security.rate_limit.per_user_per_minute", 15)
    assert config.get("security.rate_limit.per_user_per_minute") == 15
    os.unlink(f.name)


def test_config_reload():
    """Config reloads from disk."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({"test": {"value": 1}}, f)
        f.flush()
        config = Config(f.name)

    assert config.get("test.value") == 1

    with open(f.name, 'w') as f2:
        yaml.dump({"test": {"value": 2}}, f2)
    config.reload()

    assert config.get("test.value") == 2
    os.unlink(f.name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# utils/config.py
"""Centralized configuration with dot-notation access and hot-reload."""
import yaml
from pathlib import Path
from typing import Any, Optional
from utils.logger import setup_logger

logger = setup_logger("config")


class Config:
    def __init__(self, config_path: str = "config/config.yaml"):
        self._path = Path(config_path)
        self._data: dict = {}
        self.reload()

    def reload(self):
        """Reload configuration from disk."""
        if self._path.exists():
            with open(self._path, 'r', encoding='utf-8') as f:
                self._data = yaml.safe_load(f) or {}
            logger.info(f"配置已加载: {self._path}")
        else:
            logger.warning(f"配置文件不存在: {self._path}，使用空配置")
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get value using dot-notation key (e.g., 'security.rate_limit.per_user_per_minute')."""
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    def set(self, key: str, value: Any):
        """Set value using dot-notation key."""
        keys = key.split(".")
        data = self._data
        for k in keys[:-1]:
            if k not in data or not isinstance(data[k], dict):
                data[k] = {}
            data = data[k]
        data[keys[-1]] = value

    def save(self):
        """Save current config to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, 'w', encoding='utf-8') as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False)


# Global singleton
_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def init_config(path: str = "config/config.yaml") -> Config:
    global _config
    _config = Config(path)
    return _config
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Create default config files**

```yaml
# config/config.yaml
platforms:
  qq:
    enabled: true
    onebot_url: "ws://127.0.0.1:3001"
    max_msg_length: 2000
  telegram:
    enabled: false
    token: ""
    owner_id: ""
    allowed_users: []
    group_mode: "mention"

llm:
  primary:
    provider: "deepseek"
    api_key: ""
    model: "deepseek-chat"
    max_tokens: 2048
    temperature: 0.7
  backup:
    provider: "openai_compatible"
    api_key: ""
    base_url: ""
    model: ""
  ollama:
    enabled: false
    model: "qwen2.5:7b"
  budget:
    daily_token_limit: 500000
    warn_at_percent: 80

security:
  rate_limit:
    per_user_per_minute: 10
    per_group_per_minute: 20
    global_per_hour: 200
    cooldown_minutes: 5
  conversation:
    max_depth: 20
    cooldown_after_depth: 10
  daily_message_cap: 500
  active_hours:
    start: "08:00"
    end: "23:00"
    timezone: "Asia/Shanghai"
  sensitive_words_file: "config/sensitive_words.txt"
  circuit_breaker:
    failure_threshold: 0.3
    window_seconds: 300
    recovery_seconds: 600

memory:
  short_term:
    max_messages: 50
    summarize_after: 20
  long_term:
    relevance_threshold: 0.7
    max_retrieval: 5
    archive_after_days: 60
  entity:
    cleanup_after_days: 30

characters:
  default: "default"
  dir: "characters/"

web:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  auth_token: ""

logging:
  level: "INFO"
  file: "logs/chatbot.log"
  max_size_mb: 50
  backup_count: 5
```

```text
# config/sensitive_words.txt
# One word per line, lines starting with # are comments
# Add sensitive words here (example placeholders)
赌博
色情
```

- [ ] **Step 6: Commit**

```bash
git add config/ utils/config.py tests/test_config.py
git commit -m "feat: add centralized configuration system with dot-notation access and hot-reload"
```

---

### Task 2: Enhanced Rate Limiter

**Files:**
- Create: `security/__init__.py`
- Create: `security/rate_limiter.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_security.py
import pytest
import time
from security.rate_limiter import RateLimiter


@pytest.fixture
def limiter():
    return RateLimiter(
        per_user_per_minute=3,
        per_group_per_minute=5,
        global_per_hour=10,
        cooldown_minutes=1,
    )


def test_per_user_rate_limit(limiter):
    """User hits per-user limit after N messages."""
    for i in range(3):
        allowed, reason = limiter.check("user1", group_id=None)
        assert allowed is True

    allowed, reason = limiter.check("user1", group_id=None)
    assert allowed is False
    assert "用户" in reason


def test_per_group_rate_limit(limiter):
    """Group hits per-group limit with different users."""
    for i in range(5):
        allowed, _ = limiter.check(f"user{i}", group_id="group1")
        assert allowed is True

    allowed, reason = limiter.check("user99", group_id="group1")
    assert allowed is False
    assert "群" in reason


def test_global_rate_limit(limiter):
    """Global limit triggers across all users/groups."""
    for i in range(10):
        allowed, _ = limiter.check(f"user{i}", group_id=None)
        assert allowed is True

    allowed, reason = limiter.check("user99", group_id=None)
    assert allowed is False
    assert "全局" in reason


def test_per_user_independent(limiter):
    """Different users have independent limits."""
    for i in range(3):
        limiter.check("user1", group_id=None)

    allowed, _ = limiter.check("user2", group_id=None)
    assert allowed is True


def test_daily_message_cap(limiter):
    """Daily cap blocks all messages."""
    limiter.set_daily_cap(5)
    for i in range(5):
        limiter.check("user1", group_id=None)

    allowed, reason = limiter.check("user1", group_id=None)
    assert allowed is False
    assert "每日" in reason


def test_url_frequency_limit(limiter):
    """URL frequency limit triggers."""
    for i in range(5):
        limiter.record_url_sent()

    allowed, reason = limiter.check_url_limit()
    assert allowed is False
    assert "URL" in reason


def test_conversation_depth(limiter):
    """Conversation depth limit triggers."""
    for i in range(20):
        limiter.record_exchange("user1")

    allowed, reason = limiter.check_depth("user1")
    assert allowed is False
    assert "对话深度" in reason


def test_conversation_depth_reset_on_idle(limiter):
    """Depth resets after idle timeout."""
    for i in range(20):
        limiter.record_exchange("user1")

    limiter._depth_timestamps["user1"] = time.time() - 1800  # 30 min ago
    allowed, _ = limiter.check_depth("user1")
    assert allowed is True


def test_stats(limiter):
    """Stats returns current state."""
    limiter.check("user1", group_id=None)
    stats = limiter.stats()
    assert "user_counts" in stats
    assert "global_count" in stats
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_security.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# security/__init__.py
"""Security system: rate limiting, circuit breaker, content filtering, alerting."""
```

```python
# security/rate_limiter.py
"""Three-tier rate limiter: per-user, per-group, global + daily cap + URL limit + depth control."""
import time
from collections import defaultdict
from typing import Optional, Tuple
from utils.logger import setup_logger

logger = setup_logger("rate_limiter")


class RateLimiter:
    def __init__(
        self,
        per_user_per_minute: int = 10,
        per_group_per_minute: int = 20,
        global_per_hour: int = 200,
        cooldown_minutes: int = 5,
        daily_message_cap: int = 500,
        url_per_hour: int = 5,
        max_conversation_depth: int = 20,
        depth_idle_reset_seconds: int = 1800,
    ):
        self.per_user_per_minute = per_user_per_minute
        self.per_group_per_minute = per_group_per_minute
        self.global_per_hour = global_per_hour
        self.cooldown_minutes = cooldown_minutes
        self.daily_message_cap = daily_message_cap
        self.url_per_hour = url_per_hour
        self.max_conversation_depth = max_conversation_depth
        self.depth_idle_reset = depth_idle_reset_seconds

        # Per-user timestamps
        self._user_timestamps: dict[str, list[float]] = defaultdict(list)
        # Per-group timestamps
        self._group_timestamps: dict[str, list[float]] = defaultdict(list)
        # Global timestamps
        self._global_timestamps: list[float] = []
        # Daily counter
        self._daily_count: int = 0
        self._daily_reset_time: float = time.time()
        # URL timestamps
        self._url_timestamps: list[float] = []
        # Conversation depth tracking
        self._depth_counts: dict[str, int] = defaultdict(int)
        self._depth_timestamps: dict[str, float] = defaultdict(float)

    def check(self, user_id: str, group_id: Optional[str] = None) -> Tuple[bool, str]:
        """Check if message is allowed. Returns (allowed, reason)."""
        now = time.time()

        # Daily cap
        self._maybe_reset_daily(now)
        if self._daily_count >= self.daily_message_cap:
            return False, f"每日消息上限({self.daily_message_cap})已达到"

        # Global limit
        self._clean_old(self._global_timestamps, now, 3600)
        if len(self._global_timestamps) >= self.global_per_hour:
            return False, f"全局消息频率超限({self.global_per_hour}/小时)"

        # Per-user limit
        self._clean_old(self._user_timestamps[user_id], now, 60)
        if len(self._user_timestamps[user_id]) >= self.per_user_per_minute:
            return False, f"用户消息频率超限({self.per_user_per_minute}/分钟)"

        # Per-group limit
        if group_id:
            self._clean_old(self._group_timestamps[group_id], now, 60)
            if len(self._group_timestamps[group_id]) >= self.per_group_per_minute:
                return False, f"群消息频率超限({self.per_group_per_minute}/分钟)"

        # Record
        self._user_timestamps[user_id].append(now)
        self._global_timestamps.append(now)
        if group_id:
            self._group_timestamps[group_id].append(now)
        self._daily_count += 1

        return True, ""

    def check_depth(self, user_id: str) -> Tuple[bool, str]:
        """Check conversation depth limit."""
        now = time.time()
        if now - self._depth_timestamps[user_id] > self.depth_idle_reset:
            self._depth_counts[user_id] = 0

        if self._depth_counts[user_id] >= self.max_conversation_depth:
            return False, f"对话深度超限({self.max_conversation_depth})，请稍后再聊"
        return True, ""

    def record_exchange(self, user_id: str):
        """Record a conversation exchange for depth tracking."""
        self._depth_counts[user_id] += 1
        self._depth_timestamps[user_id] = time.time()

    def reset_depth(self, user_id: str):
        """Reset conversation depth for a user."""
        self._depth_counts[user_id] = 0

    def check_url_limit(self) -> Tuple[bool, str]:
        """Check URL sending frequency."""
        now = time.time()
        self._clean_old(self._url_timestamps, now, 3600)
        if len(self._url_timestamps) >= self.url_per_hour:
            return False, f"URL发送频率超限({self.url_per_hour}/小时)"
        return True, ""

    def record_url_sent(self):
        """Record that a URL was sent."""
        self._url_timestamps.append(time.time())

    def set_daily_cap(self, cap: int):
        """Set daily message cap."""
        self.daily_message_cap = cap

    def stats(self) -> dict:
        """Return current rate limiter statistics."""
        return {
            "user_counts": {uid: len(ts) for uid, ts in self._user_timestamps.items()},
            "group_counts": {gid: len(ts) for gid, ts in self._group_timestamps.items()},
            "global_count": len(self._global_timestamps),
            "daily_count": self._daily_count,
            "daily_cap": self.daily_message_cap,
            "url_count": len(self._url_timestamps),
        }

    def _clean_old(self, timestamps: list, now: float, window: int):
        """Remove timestamps older than window seconds."""
        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

    def _maybe_reset_daily(self, now: float):
        """Reset daily counter at midnight."""
        if now - self._daily_reset_time > 86400:
            self._daily_count = 0
            self._daily_reset_time = now
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_security.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add security/ tests/test_security.py
git commit -m "feat: add three-tier rate limiter with daily cap, URL limit, and depth control"
```

---

### Task 3: Circuit Breaker

**Files:**
- Create: `security/circuit_breaker.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_security.py
import pytest
from security.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture
def breaker():
    return CircuitBreaker(
        failure_threshold=0.3,
        window_seconds=60,
        recovery_seconds=10,
    )


def test_initial_state_closed(breaker):
    """Circuit starts CLOSED."""
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_records_success(breaker):
    """Success doesn't change state."""
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_opens_on_failure_threshold(breaker):
    """Circuit opens when failure rate exceeds threshold."""
    for _ in range(7):
        breaker.record_success()
    for _ in range(3):
        breaker.record_failure()

    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_half_open_after_recovery(breaker):
    """Circuit goes HALF_OPEN after recovery period."""
    for _ in range(10):
        breaker.record_failure()

    assert breaker.state == CircuitState.OPEN

    # Fast-forward recovery time
    breaker._last_failure_time = time.time() - 11

    assert breaker.allow_request() is True
    assert breaker.state == CircuitState.HALF_OPEN


def test_half_open_success_closes(breaker):
    """Success in HALF_OPEN closes the circuit."""
    breaker._force_state(CircuitState.HALF_OPEN)
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_half_open_failure_reopens(breaker):
    """Failure in HALF_OPEN reopens the circuit."""
    breaker._force_state(CircuitState.HALF_OPEN)
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_force_open(breaker):
    """Can manually force open."""
    breaker.force_open()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_force_close(breaker):
    """Can manually force close."""
    breaker.force_open()
    breaker.force_close()
    assert breaker.state == CircuitState.CLOSED


def test_stats(breaker):
    """Stats returns current state."""
    stats = breaker.stats()
    assert stats["state"] == "CLOSED"
    assert "total_requests" in stats
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_security.py -v -k "breaker"`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# security/circuit_breaker.py
"""Circuit breaker: CLOSED → OPEN → HALF_OPEN with configurable thresholds."""
import time
from enum import Enum
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger("circuit_breaker")


class CircuitState(Enum):
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Blocking all requests
    HALF_OPEN = "HALF_OPEN" # Testing if service recovered


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: float = 0.3,
        window_seconds: int = 300,
        recovery_seconds: int = 600,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_seconds = recovery_seconds

        self.state = CircuitState.CLOSED
        self._total_requests: int = 0
        self._total_failures: int = 0
        self._window_requests: int = 0
        self._window_failures: int = 0
        self._window_start: float = time.time()
        self._last_failure_time: float = 0
        self._state_changes: list[tuple[str, float]] = [("CLOSED", time.time())]

    def allow_request(self) -> bool:
        """Check if a request should be allowed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_seconds:
                self._transition(CircuitState.HALF_OPEN)
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True

        return False

    def record_success(self):
        """Record a successful request."""
        self._total_requests += 1
        self._window_requests += 1
        self._maybe_reset_window()

        if self.state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)
            self._window_requests = 0
            self._window_failures = 0

    def record_failure(self):
        """Record a failed request."""
        self._total_requests += 1
        self._total_failures += 1
        self._window_requests += 1
        self._window_failures += 1
        self._last_failure_time = time.time()
        self._maybe_reset_window()

        if self.state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN)
            return

        if self.state == CircuitState.CLOSED and self._window_requests > 0:
            failure_rate = self._window_failures / self._window_requests
            if failure_rate >= self.failure_threshold and self._window_requests >= 10:
                logger.warning(f"熔断器触发: 失败率 {failure_rate:.1%} >= {self.failure_threshold:.1%}")
                self._transition(CircuitState.OPEN)

    def force_open(self):
        """Manually open the circuit."""
        self._transition(CircuitState.OPEN)

    def force_close(self):
        """Manually close the circuit."""
        self._transition(CircuitState.CLOSED)
        self._window_requests = 0
        self._window_failures = 0

    def stats(self) -> dict:
        """Return current statistics."""
        return {
            "state": self.state.value,
            "total_requests": self._total_requests,
            "total_failures": self._total_failures,
            "window_requests": self._window_requests,
            "window_failures": self._window_failures,
            "failure_rate": self._window_failures / max(self._window_requests, 1),
            "last_failure": self._last_failure_time,
            "recent_state_changes": self._state_changes[-5:],
        }

    def _transition(self, new_state: CircuitState):
        """Transition to a new state."""
        old = self.state
        self.state = new_state
        self._state_changes.append((new_state.value, time.time()))
        if old != new_state:
            logger.info(f"熔断器状态变更: {old.value} → {new_state.value}")

    def _maybe_reset_window(self):
        """Reset sliding window if expired."""
        now = time.time()
        if now - self._window_start >= self.window_seconds:
            self._window_requests = 0
            self._window_failures = 0
            self._window_start = now

    def _force_state(self, state: CircuitState):
        """Force state (for testing)."""
        self.state = state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_security.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add security/circuit_breaker.py tests/test_security.py
git commit -m "feat: add circuit breaker with CLOSED/OPEN/HALF_OPEN states"
```

---

### Task 4: Content Filter

**Files:**
- Create: `security/content_filter.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_security.py
from security.content_filter import ContentFilter


@pytest.fixture
def content_filter(tmp_path):
    words_file = tmp_path / "sensitive.txt"
    words_file.write_text("赌博\n色情\n# comment\n暴力\n")
    return ContentFilter(str(words_file))


def test_filter_replaces_sensitive_words(content_filter):
    """Sensitive words are replaced with ***."""
    result = content_filter.filter("这是一个关于赌博的故事")
    assert "赌博" not in result
    assert "***" in result


def test_filter_multiple_words(content_filter):
    """Multiple sensitive words are all filtered."""
    result = content_filter.filter("赌博和色情和暴力都不好")
    assert result.count("***") == 3


def test_filter_no_sensitive_content(content_filter):
    """Clean text passes through unchanged."""
    text = "今天天气真好"
    assert content_filter.filter(text) == text


def test_filter_empty_file(tmp_path):
    """Empty word list filters nothing."""
    words_file = tmp_path / "empty.txt"
    words_file.write_text("")
    cf = ContentFilter(str(words_file))
    assert cf.filter("任何文本") == "任何文本"


def test_filter_missing_file(tmp_path):
    """Missing file is handled gracefully."""
    cf = ContentFilter(str(tmp_path / "nonexistent.txt"))
    assert cf.filter("任何文本") == "任何文本"


def test_filter_case_insensitive(content_filter):
    """Filter is case-insensitive for mixed content."""
    result = content_filter.filter("DuBo is 赌博")
    assert "赌博" not in result


def test_has_sensitive(content_filter):
    """has_sensitive detects sensitive content."""
    assert content_filter.has_sensitive("这里有赌博内容") is True
    assert content_filter.has_sensitive("正常内容") is False


def test_filter_logs(content_filter):
    """Filtered messages are logged."""
    content_filter.filter("赌博内容")
    logs = content_filter.get_filter_log()
    assert len(logs) > 0
    assert "赌博" in logs[0]["original"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_security.py -v -k "content_filter"`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# security/content_filter.py
"""Sensitive content filtering with configurable word list."""
import re
import time
from pathlib import Path
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger("content_filter")


class ContentFilter:
    def __init__(self, words_file: str = "config/sensitive_words.txt"):
        self._words: list[str] = []
        self._filter_log: list[dict] = []
        self._load_words(words_file)

    def _load_words(self, path: str):
        """Load sensitive words from file."""
        p = Path(path)
        if not p.exists():
            logger.warning(f"敏感词文件不存在: {path}")
            return

        words = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
        self._words = words
        logger.info(f"已加载 {len(words)} 个敏感词")

    def filter(self, text: str) -> str:
        """Replace sensitive words with ***. Returns filtered text."""
        if not self._words:
            return text

        result = text
        found = []
        for word in self._words:
            if word in result:
                result = result.replace(word, "***")
                found.append(word)

        if found:
            self._filter_log.append({
                "time": time.time(),
                "words_found": found,
                "original": text,
                "filtered": result,
            })
            logger.info(f"内容过滤: 发现敏感词 {found}")

        return result

    def has_sensitive(self, text: str) -> bool:
        """Check if text contains sensitive words without modifying it."""
        return any(word in text for word in self._words)

    def get_filter_log(self, limit: int = 100) -> list[dict]:
        """Return recent filter log entries."""
        return self._filter_log[-limit:]

    def reload(self, path: str):
        """Reload sensitive words from file."""
        self._load_words(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_security.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add security/content_filter.py tests/test_security.py
git commit -m "feat: add content filter with sensitive word replacement and logging"
```

---

### Task 5: Alerting System

**Files:**
- Create: `security/alerting.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_security.py
from security.alerting import AlertManager, AlertLevel
import asyncio


@pytest.fixture
def alert_manager():
    return AlertManager()


def test_create_alert(alert_manager):
    """Alert is created and stored."""
    alert_manager.create_alert(
        level=AlertLevel.WARNING,
        title="Test Alert",
        message="Something happened",
    )
    alerts = alert_manager.get_recent()
    assert len(alerts) == 1
    assert alerts[0]["level"] == "WARNING"
    assert alerts[0]["title"] == "Test Alert"


def test_alert_levels(alert_manager):
    """Different alert levels work."""
    for level in AlertLevel:
        alert_manager.create_alert(level=level, title=f"Test {level.value}", message="msg")

    alerts = alert_manager.get_recent()
    assert len(alerts) == len(AlertLevel)


def test_alert_filter_by_level(alert_manager):
    """Can filter alerts by level."""
    alert_manager.create_alert(level=AlertLevel.INFO, title="Info", message="msg")
    alert_manager.create_alert(level=AlertLevel.CRITICAL, title="Critical", message="msg")

    critical = alert_manager.get_recent(level=AlertLevel.CRITICAL)
    assert len(critical) == 1
    assert critical[0]["title"] == "Critical"


def test_alert_callback(alert_manager):
    """Callback is called on alert."""
    received = []

    async def handler(alert):
        received.append(alert)

    alert_manager.on_alert(handler)
    alert_manager.create_alert(level=AlertLevel.INFO, title="Test", message="msg")
    assert len(received) == 1


def test_alert_stats(alert_manager):
    """Stats returns counts by level."""
    alert_manager.create_alert(level=AlertLevel.INFO, title="1", message="msg")
    alert_manager.create_alert(level=AlertLevel.WARNING, title="2", message="msg")
    alert_manager.create_alert(level=AlertLevel.WARNING, title="3", message="msg")

    stats = alert_manager.stats()
    assert stats["total"] == 3
    assert stats["by_level"]["WARNING"] == 2
    assert stats["by_level"]["INFO"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_security.py -v -k "alert"`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# security/alerting.py
"""Alert manager with level-based filtering and callback support."""
import time
from enum import Enum
from typing import Callable, Awaitable, Optional
from utils.logger import setup_logger

logger = setup_logger("alerting")


class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertManager:
    def __init__(self, max_history: int = 1000):
        self._alerts: list[dict] = []
        self._max_history = max_history
        self._callbacks: list[Callable[[dict], Awaitable[None]]] = []

    def on_alert(self, callback: Callable[[dict], Awaitable[None]]):
        """Register a callback to be called on each alert."""
        self._callbacks.append(callback)

    def create_alert(self, level: AlertLevel, title: str, message: str, metadata: Optional[dict] = None):
        """Create and store an alert."""
        alert = {
            "level": level.value,
            "title": title,
            "message": message,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }

        self._alerts.append(alert)
        if len(self._alerts) > self._max_history:
            self._alerts = self._alerts[-self._max_history:]

        log_msg = f"[{level.value}] {title}: {message}"
        if level == AlertLevel.CRITICAL:
            logger.error(log_msg)
        elif level == AlertLevel.WARNING:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # Fire callbacks (best-effort, don't block)
        for cb in self._callbacks:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(cb(alert))
                else:
                    loop.run_until_complete(cb(alert))
            except Exception as e:
                logger.error(f"告警回调失败: {e}")

    def get_recent(self, limit: int = 50, level: Optional[AlertLevel] = None) -> list[dict]:
        """Return recent alerts, optionally filtered by level."""
        alerts = self._alerts
        if level:
            alerts = [a for a in alerts if a["level"] == level.value]
        return alerts[-limit:]

    def stats(self) -> dict:
        """Return alert statistics."""
        by_level = {}
        for a in self._alerts:
            lvl = a["level"]
            by_level[lvl] = by_level.get(lvl, 0) + 1
        return {
            "total": len(self._alerts),
            "by_level": by_level,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_security.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add security/alerting.py tests/test_security.py
git commit -m "feat: add alert manager with level filtering and callback support"
```

---

### Task 6: Active Time Window

**Files:**
- Create: `core/middleware/__init__.py`
- Create: `core/middleware/active_window.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline.py
import pytest
from datetime import datetime
from core.pipeline import PipelineContext
from core.middleware.active_window import ActiveWindowMiddleware


@pytest.fixture
def middleware():
    return ActiveWindowMiddleware(start_hour=8, end_hour=23, timezone_name="Asia/Shanghai")


@pytest.mark.asyncio
async def test_within_active_hours(middleware):
    """Messages during active hours pass through."""
    ctx = PipelineContext(user_id="user1", content="hello", timestamp=datetime(2026, 6, 20, 14, 0))
    result = await middleware.on_receive(ctx)
    assert result is True


@pytest.mark.asyncio
async def test_outside_active_hours(middleware):
    """Messages outside active hours are blocked."""
    ctx = PipelineContext(user_id="user1", content="hello", timestamp=datetime(2026, 6, 20, 3, 0))
    result = await middleware.on_receive(ctx)
    assert result is False


@pytest.mark.asyncio
async def test_at_boundary_start(middleware):
    """Message at start boundary is allowed."""
    ctx = PipelineContext(user_id="user1", content="hello", timestamp=datetime(2026, 6, 20, 8, 0))
    assert await middleware.on_receive(ctx) is True


@pytest.mark.asyncio
async def test_at_boundary_end(middleware):
    """Message at end boundary is blocked (23:00 is exclusive)."""
    ctx = PipelineContext(user_id="user1", content="hello", timestamp=datetime(2026, 6, 20, 23, 0))
    assert await middleware.on_receive(ctx) is False


@pytest.mark.asyncio
async def test_just_before_end(middleware):
    """Message at 22:59 is allowed."""
    ctx = PipelineContext(user_id="user1", content="hello", timestamp=datetime(2026, 6, 20, 22, 59))
    assert await middleware.on_receive(ctx) is True


@pytest.mark.asyncio
async def test_owner_bypass(middleware):
    """Owner can send outside active hours."""
    ctx = PipelineContext(
        user_id="owner1", content="hello",
        timestamp=datetime(2026, 6, 20, 3, 0), is_owner=True,
    )
    assert await middleware.on_receive(ctx) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v -k "active"`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

First, add `timestamp` field to `PipelineContext` in `core/pipeline.py`:

```python
# Add to PipelineContext in core/pipeline.py
from datetime import datetime

@dataclass
class PipelineContext:
    user_id: str
    content: str
    platform: str = ""
    group_id: Optional[str] = None
    is_owner: bool = False
    character_name: Optional[str] = None
    session_id: Optional[str] = None
    response: Optional[str] = None
    memories: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    blocked: bool = False
    block_reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
```

```python
# core/middleware/__init__.py
"""Message pipeline middleware."""
```

```python
# core/middleware/active_window.py
"""Active time window middleware: only respond during configured hours."""
from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("active_window")


class ActiveWindowMiddleware(Middleware):
    def __init__(self, start_hour: int = 8, end_hour: int = 23, timezone_name: str = "Asia/Shanghai"):
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.timezone_name = timezone_name

    async def on_receive(self, ctx: PipelineContext) -> bool:
        """Return True if message is within active hours or sender is owner."""
        if ctx.is_owner:
            return True

        hour = ctx.timestamp.hour
        if self.start_hour <= hour < self.end_hour:
            return True

        ctx.blocked = True
        ctx.block_reason = f"不在活跃时段({self.start_hour}:00-{self.end_hour}:00)"
        logger.info(f"活跃时间窗口外的消息被忽略: {ctx.user_id} at {ctx.timestamp}")
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add core/middleware/ tests/test_pipeline.py
git commit -m "feat: add active time window middleware"
```

---

## Phase 2: Architecture Refactor

### Task 7: Message Pipeline Core

**Files:**
- Create: `core/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_pipeline.py
import pytest
import asyncio
from core.pipeline import Pipeline, Middleware, PipelineContext


class MockMiddleware(Middleware):
    def __init__(self, name: str, modify_response: str = None, block: bool = False):
        self.name = name
        self.modify_response = modify_response
        self.block = block
        self.called = False

    async def on_receive(self, ctx: PipelineContext) -> bool:
        self.called = True
        if self.block:
            return False
        ctx.metadata[f"{self.name}_receive"] = True
        return True

    async def on_llm(self, ctx: PipelineContext) -> bool:
        self.called = True
        if self.block:
            return False
        return True

    async def on_response(self, ctx: PipelineContext) -> bool:
        self.called = True
        if self.modify_response:
            ctx.response = self.modify_response
        return True

    async def on_send(self, ctx: PipelineContext) -> bool:
        self.called = True
        return True


@pytest.mark.asyncio
async def test_pipeline_runs_all_middleware():
    """Pipeline runs all middleware in order."""
    m1 = MockMiddleware("m1")
    m2 = MockMiddleware("m2")
    pipeline = Pipeline(middleware=[m1, m2])

    ctx = PipelineContext(user_id="user1", content="hello")
    await pipeline.process(ctx)

    assert m1.called is True
    assert m2.called is True


@pytest.mark.asyncio
async def test_pipeline_blocks_on_receive():
    """Pipeline stops if receive middleware returns False."""
    blocker = MockMiddleware("blocker", block=True)
    after = MockMiddleware("after")
    pipeline = Pipeline(middleware=[blocker, after])

    ctx = PipelineContext(user_id="user1", content="hello")
    result = await pipeline.process(ctx)

    assert result is None
    assert blocker.called is True
    assert after.called is False


@pytest.mark.asyncio
async def test_pipeline_modifies_response():
    """Response middleware can modify the response."""
    modifier = MockMiddleware("modifier", modify_response="modified")
    pipeline = Pipeline(middleware=[modifier])

    ctx = PipelineContext(user_id="user1", content="hello")
    ctx.response = "original"
    await pipeline.process(ctx)

    assert ctx.response == "modified"


@pytest.mark.asyncio
async def test_pipeline_stores_metadata():
    """Middleware can store metadata on context."""
    m1 = MockMiddleware("m1")
    pipeline = Pipeline(middleware=[m1])

    ctx = PipelineContext(user_id="user1", content="hello")
    await pipeline.process(ctx)

    assert ctx.metadata.get("m1_receive") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v -k "pipeline"`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# core/pipeline.py
"""Message pipeline with middleware chain."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger("pipeline")


@dataclass
class PipelineContext:
    user_id: str
    content: str
    platform: str = ""
    group_id: Optional[str] = None
    is_owner: bool = False
    character_name: Optional[str] = None
    session_id: Optional[str] = None
    response: Optional[str] = None
    memories: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    blocked: bool = False
    block_reason: str = ""


class Middleware(ABC):
    async def on_receive(self, ctx: PipelineContext) -> bool:
        """Called when message is received. Return False to stop pipeline."""
        return True

    async def on_llm(self, ctx: PipelineContext) -> bool:
        """Called before LLM processing. Return False to stop."""
        return True

    async def on_response(self, ctx: PipelineContext) -> bool:
        """Called after LLM produces response. Return False to stop."""
        return True

    async def on_send(self, ctx: PipelineContext) -> bool:
        """Called before sending. Return False to block send."""
        return True


class Pipeline:
    def __init__(self, middleware: Optional[list[Middleware]] = None):
        self._middleware: list[Middleware] = middleware or []

    def add(self, mw: Middleware):
        self._middleware.append(mw)

    async def process(self, ctx: PipelineContext) -> Optional[str]:
        """Run the full pipeline. Returns response or None if blocked."""
        # Stage 1: on_receive
        for mw in self._middleware:
            if not await mw.on_receive(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 receive 阶段阻断")
                return None

        # Stage 2: on_llm
        for mw in self._middleware:
            if not await mw.on_llm(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 llm 阶段阻断")
                return None

        # Stage 3: on_response
        for mw in self._middleware:
            if not await mw.on_response(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 response 阶段阻断")
                return None

        # Stage 4: on_send
        for mw in self._middleware:
            if not await mw.on_send(ctx):
                logger.debug(f"管道被 {mw.__class__.__name__} 在 send 阶段阻断")
                return None

        return ctx.response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: add message pipeline with 4-stage middleware chain"
```

---

### Task 8: Built-in Middleware - Rate Limit + Filter + Safety

**Files:**
- Create: `core/middleware/rate_limit.py`
- Create: `core/middleware/filter.py`
- Create: `core/middleware/safety.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_pipeline.py
from core.middleware.rate_limit import RateLimitMiddleware
from core.middleware.filter import FilterMiddleware
from core.middleware.safety import SafetyMiddleware
from security.rate_limiter import RateLimiter
from security.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_rate_limit_middleware_allows_normal():
    """Rate limit middleware allows normal traffic."""
    limiter = RateLimiter(per_user_per_minute=10, global_per_hour=100)
    mw = RateLimitMiddleware(limiter)

    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_receive(ctx) is True


@pytest.mark.asyncio
async def test_rate_limit_middleware_blocks_excess():
    """Rate limit middleware blocks when limit exceeded."""
    limiter = RateLimiter(per_user_per_minute=2, global_per_hour=100)
    mw = RateLimitMiddleware(limiter)

    for _ in range(2):
        ctx = PipelineContext(user_id="user1", content="hello")
        await mw.on_receive(ctx)

    ctx = PipelineContext(user_id="user1", content="hello")
    result = await mw.on_receive(ctx)
    assert result is False
    assert ctx.blocked is True


@pytest.mark.asyncio
async def test_filter_middleware_blocks_empty():
    """Filter blocks empty messages."""
    mw = FilterMiddleware()

    ctx = PipelineContext(user_id="user1", content="   ")
    assert await mw.on_receive(ctx) is False


@pytest.mark.asyncio
async def test_filter_middleware_allows_valid():
    """Filter allows valid messages."""
    mw = FilterMiddleware()

    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_receive(ctx) is True


@pytest.mark.asyncio
async def test_safety_middleware_blocks_on_open_circuit():
    """Safety middleware blocks when circuit breaker is open."""
    breaker = CircuitBreaker()
    breaker.force_open()
    mw = SafetyMiddleware(breaker)

    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_send(ctx) is False
    assert ctx.blocked is True


@pytest.mark.asyncio
async def test_safety_middleware_allows_on_closed_circuit():
    """Safety middleware allows when circuit breaker is closed."""
    breaker = CircuitBreaker()
    mw = SafetyMiddleware(breaker)

    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_send(ctx) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v -k "rate_limit or filter or safety"`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# core/middleware/rate_limit.py
"""Rate limit middleware."""
from core.pipeline import Middleware, PipelineContext
from security.rate_limiter import RateLimiter
from utils.logger import setup_logger

logger = setup_logger("mw_rate_limit")


class RateLimitMiddleware(Middleware):
    def __init__(self, limiter: RateLimiter):
        self.limiter = limiter

    async def on_receive(self, ctx: PipelineContext) -> bool:
        allowed, reason = self.limiter.check(ctx.user_id, ctx.group_id)
        if not allowed:
            ctx.blocked = True
            ctx.block_reason = reason
            logger.warning(f"限流: {ctx.user_id} - {reason}")
            return False

        depth_ok, depth_reason = self.limiter.check_depth(ctx.user_id)
        if not depth_ok:
            ctx.blocked = True
            ctx.block_reason = depth_reason
            logger.warning(f"深度超限: {ctx.user_id} - {depth_reason}")
            return False

        return True
```

```python
# core/middleware/filter.py
"""Message filter middleware."""
from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("mw_filter")


class FilterMiddleware(Middleware):
    async def on_receive(self, ctx: PipelineContext) -> bool:
        if not ctx.content or not ctx.content.strip():
            logger.debug("空消息被过滤")
            return False
        return True
```

```python
# core/middleware/safety.py
"""Safety middleware with circuit breaker check."""
from core.pipeline import Middleware, PipelineContext
from security.circuit_breaker import CircuitBreaker, CircuitState
from utils.logger import setup_logger

logger = setup_logger("mw_safety")


class SafetyMiddleware(Middleware):
    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker

    async def on_send(self, ctx: PipelineContext) -> bool:
        if not self.breaker.allow_request():
            ctx.blocked = True
            ctx.block_reason = "熔断器开启，暂停发送"
            logger.warning("熔断器开启，消息发送被阻断")
            return False
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add core/middleware/rate_limit.py core/middleware/filter.py core/middleware/safety.py tests/test_pipeline.py
git commit -m "feat: add rate limit, filter, and safety middleware"
```

---

### Task 9: Content Filter + Split + Delay Middleware

**Files:**
- Create: `core/middleware/content_filter.py`
- Create: `core/middleware/split.py`
- Create: `core/middleware/delay.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_pipeline.py
from core.middleware.content_filter import ContentFilterMiddleware
from core.middleware.split import SplitMiddleware
from core.middleware.delay import DelayMiddleware
from security.content_filter import ContentFilter


@pytest.mark.asyncio
async def test_content_filter_middleware_filters():
    """Content filter middleware filters sensitive words."""
    cf = ContentFilter.__new__(ContentFilter)
    cf._words = ["赌博"]
    cf._filter_log = []
    mw = ContentFilterMiddleware(cf)

    ctx = PipelineContext(user_id="user1", content="hello", response="关于赌博的故事")
    await mw.on_response(ctx)
    assert "赌博" not in ctx.response
    assert "***" in ctx.response


@pytest.mark.asyncio
async def test_split_middleware_splits_long():
    """Split middleware splits long responses."""
    mw = SplitMiddleware(max_length=50)

    ctx = PipelineContext(user_id="user1", content="hello", response="A" * 100)
    await mw.on_response(ctx)
    assert len(ctx.response) <= 50
    assert ctx.metadata.get("chunks") is not None


@pytest.mark.asyncio
async def test_split_middleware_keeps_short():
    """Split middleware keeps short responses unchanged."""
    mw = SplitMiddleware(max_length=2000)

    ctx = PipelineContext(user_id="user1", content="hello", response="short")
    await mw.on_response(ctx)
    assert ctx.response == "short"


@pytest.mark.asyncio
async def test_delay_middleware_adds_delay():
    """Delay middleware records that delay should happen."""
    mw = DelayMiddleware(min_seconds=0.01, max_seconds=0.02)

    ctx = PipelineContext(user_id="user1", content="hello", response="response")
    import time
    start = time.time()
    await mw.on_send(ctx)
    elapsed = time.time() - start
    assert elapsed >= 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v -k "content_filter or split or delay"`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# core/middleware/content_filter.py
"""Content filter middleware for responses."""
from core.pipeline import Middleware, PipelineContext
from security.content_filter import ContentFilter
from utils.logger import setup_logger

logger = setup_logger("mw_content_filter")


class ContentFilterMiddleware(Middleware):
    def __init__(self, content_filter: ContentFilter):
        self.filter = content_filter

    async def on_response(self, ctx: PipelineContext) -> bool:
        if ctx.response:
            original = ctx.response
            ctx.response = self.filter.filter(ctx.response)
            if ctx.response != original:
                logger.info(f"回复内容已过滤: {ctx.user_id}")
        return True
```

```python
# core/middleware/split.py
"""Message splitting middleware."""
from core.pipeline import Middleware, PipelineContext
from utils.message_splitter import split_message
from utils.logger import setup_logger

logger = setup_logger("mw_split")


class SplitMiddleware(Middleware):
    def __init__(self, max_length: int = 2000):
        self.max_length = max_length

    async def on_response(self, ctx: PipelineContext) -> bool:
        if ctx.response and len(ctx.response) > self.max_length:
            chunks = split_message(ctx.response, max_length=self.max_length)
            ctx.metadata["chunks"] = chunks
            ctx.response = chunks[0]  # First chunk, rest sent via send_long_message
            logger.debug(f"消息已拆分为 {len(chunks)} 段")
        return True
```

```python
# core/middleware/delay.py
"""Human-like delay middleware."""
import asyncio
import random
from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("mw_delay")


class DelayMiddleware(Middleware):
    def __init__(self, min_seconds: float = 0.5, max_seconds: float = 3.0, chars_per_second: float = 15.0):
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.chars_per_second = chars_per_second

    async def on_send(self, ctx: PipelineContext) -> bool:
        if ctx.response:
            base = random.uniform(self.min_seconds, self.max_seconds)
            typing_time = len(ctx.response) / self.chars_per_second
            total = min((base + typing_time) * random.uniform(0.8, 1.2), 10.0)
            total = max(0.3, total)
            await asyncio.sleep(total)
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add core/middleware/content_filter.py core/middleware/split.py core/middleware/delay.py tests/test_pipeline.py
git commit -m "feat: add content filter, split, and delay middleware"
```

---

## Phase 3: Telegram + Multi-LLM

### Task 10: Telegram Platform Adapter

**Files:**
- Create: `platforms/telegram.py`
- Modify: `platforms/models.py`
- Create: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_telegram.py
import pytest
from platforms.models import Platform, UnifiedMessage, MessageType


def test_telegram_platform_enum():
    """TELEGRAM is in Platform enum."""
    assert hasattr(Platform, 'TELEGRAM')
    assert Platform.TELEGRAM.value == "telegram"


def test_unified_message_for_telegram():
    """UnifiedMessage works for Telegram."""
    msg = UnifiedMessage(
        platform=Platform.TELEGRAM,
        user_id="12345",
        content="hello",
        message_type=MessageType.TEXT,
        group_id=None,
        nickname="TestUser",
    )
    assert msg.platform == Platform.TELEGRAM
    assert msg.is_group is False
    assert msg.session_key == "telegram:12345"


def test_unified_message_telegram_group():
    """Telegram group message session key."""
    msg = UnifiedMessage(
        platform=Platform.TELEGRAM,
        user_id="12345",
        content="hello",
        message_type=MessageType.TEXT,
        group_id="-100123456",
    )
    assert msg.is_group is True
    assert msg.session_key == "telegram:-100123456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_telegram.py -v`
Expected: FAIL (no TELEGRAM in Platform enum)

- [ ] **Step 3: Add TELEGRAM to Platform enum**

```python
# In platforms/models.py, add TELEGRAM to Platform enum
class Platform(Enum):
    WECHAT = "wechat"
    QQ = "qq"
    TELEGRAM = "telegram"
    CONSOLE = "console"
```

- [ ] **Step 4: Write Telegram adapter tests**

```python
# Add to tests/test_telegram.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from platforms.telegram import TelegramPlatform


@pytest.fixture
def tg_config():
    return {
        "token": "test-token",
        "owner_id": "12345",
        "allowed_users": [],
        "group_mode": "mention",
        "max_msg_length": 4000,
    }


def test_telegram_platform_init(tg_config):
    """TelegramPlatform initializes correctly."""
    platform = TelegramPlatform(tg_config)
    assert platform.name == "telegram"
    assert platform._token == "test-token"
    assert platform._owner_id == "12345"


def test_telegram_platform_with_user_whitelist(tg_config):
    """TelegramPlatform respects allowed_users whitelist."""
    tg_config["allowed_users"] = ["12345", "67890"]
    platform = TelegramPlatform(tg_config)
    assert platform._is_allowed("12345") is True
    assert platform._is_allowed("99999") is False


def test_telegram_platform_empty_whitelist_allows_all(tg_config):
    """Empty whitelist allows all users."""
    platform = TelegramPlatform(tg_config)
    assert platform._is_allowed("anyone") is True


def test_telegram_should_respond_in_group_mention_mode(tg_config):
    """In mention mode, only responds when bot is mentioned."""
    platform = TelegramPlatform(tg_config)
    platform._bot_username = "testbot"
    assert platform._should_respond_group("hello @testbot") is True
    assert platform._should_respond_group("hello") is False


def test_telegram_should_respond_in_group_always_mode(tg_config):
    """In always mode, responds to all group messages."""
    tg_config["group_mode"] = "always"
    platform = TelegramPlatform(tg_config)
    assert platform._should_respond_group("hello") is True
```

- [ ] **Step 5: Run test to verify it fails**

Run: `python -m pytest tests/test_telegram.py -v`
Expected: FAIL (module not found)

- [ ] **Step 6: Write minimal implementation**

```python
# platforms/telegram.py
"""Telegram Bot API adapter."""
import asyncio
from typing import Optional

import httpx

from platforms.base import BasePlatform
from platforms.models import UnifiedMessage, Platform, MessageType
from utils.logger import setup_logger

logger = setup_logger("telegram")


class TelegramPlatform(BasePlatform):
    name = "telegram"

    def __init__(self, config: dict):
        super().__init__(config)
        self._token = config.get("token", "")
        self._owner_id = config.get("owner_id", "")
        self._allowed_users = [str(u) for u in config.get("allowed_users", [])]
        self._group_mode = config.get("group_mode", "mention")
        self._api_base = f"https://api.telegram.org/bot{self._token}"
        self._running = False
        self._bot_username = ""
        self._offset = 0

    async def start(self):
        """Start polling for Telegram updates."""
        if not self._token:
            logger.error("Telegram token 未配置")
            return

        self._running = True
        # Get bot info
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._api_base}/getMe", timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    self._bot_username = data.get("result", {}).get("username", "")
                    logger.info(f"Telegram bot 已连接: @{self._bot_username}")
        except Exception as e:
            logger.error(f"Telegram 连接失败: {e}")
            return

        loop = asyncio.get_event_loop()
        loop.create_task(self._poll_loop())

    async def _poll_loop(self):
        """Long-poll for updates."""
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    resp = await client.get(
                        f"{self._api_base}/getUpdates",
                        params={"offset": self._offset, "timeout": 30},
                        timeout=35,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for update in data.get("result", []):
                            self._offset = update["update_id"] + 1
                            await self._process_update(update)
                except httpx.ConnectError:
                    logger.warning("Telegram 连接失败，重试中...")
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"Telegram 轮询错误: {e}")
                    await asyncio.sleep(1)

    async def _process_update(self, update: dict):
        """Process a single Telegram update."""
        message = update.get("message")
        if not message:
            return

        text = message.get("text", "")
        if not text:
            return

        user = message.get("from", {})
        user_id = str(user.get("id", ""))
        nickname = user.get("first_name", "")

        if not self._is_allowed(user_id):
            return

        chat = message.get("chat", {})
        chat_type = chat.get("type", "private")
        group_id = str(chat.get("id", "")) if chat_type != "private" else None

        # Group mode check
        if group_id and not self._should_respond_group(text):
            return

        # Strip @bot mention
        if group_id and self._bot_username:
            text = text.replace(f"@{self._bot_username}", "").strip()

        unified = UnifiedMessage(
            platform=Platform.TELEGRAM,
            user_id=user_id,
            content=text,
            message_type=MessageType.TEXT,
            group_id=group_id,
            nickname=nickname,
            message_id=str(message.get("message_id", "")),
        )

        await self.handle_callback(unified)

    async def stop(self):
        self._running = False
        logger.info("Telegram 已停止")

    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        """Send message via Telegram Bot API."""
        target = group_id or user_id
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._api_base}/sendMessage",
                    json={
                        "chat_id": target,
                        "text": content,
                        "parse_mode": "Markdown",
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    # Retry without Markdown if parse fails
                    resp = await client.post(
                        f"{self._api_base}/sendMessage",
                        json={"chat_id": target, "text": content},
                        timeout=10,
                    )
                logger.debug(f"Telegram 消息已发送: {target}")
        except Exception as e:
            logger.error(f"Telegram 发送失败: {e}")

    async def send_alert(self, message: str):
        """Send alert to owner."""
        if self._owner_id:
            await self.send_message(self._owner_id, f"⚠️ {message}")

    def _is_allowed(self, user_id: str) -> bool:
        """Check if user is in whitelist (empty = allow all)."""
        if not self._allowed_users:
            return True
        return user_id in self._allowed_users

    def _should_respond_group(self, text: str) -> bool:
        """Check if bot should respond in group."""
        if self._group_mode == "always":
            return True
        # mention mode: respond only when @bot_username is in text
        return self._bot_username and f"@{self._bot_username}" in text
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_telegram.py -v`
Expected: PASS (all tests)

- [ ] **Step 8: Commit**

```bash
git add platforms/telegram.py platforms/models.py tests/test_telegram.py
git commit -m "feat: add Telegram platform adapter with polling, group support, and alerts"
```

---

### Task 11: Multi-LLM Provider System

**Files:**
- Create: `llm/providers/__init__.py`
- Create: `llm/providers/deepseek.py`
- Create: `llm/providers/openai_compat.py`
- Create: `llm/providers/ollama.py`
- Modify: `llm/client.py`
- Create: `tests/test_multi_llm.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_multi_llm.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from llm.providers import LLMProvider, ProviderRegistry


class MockProvider(LLMProvider):
    def __init__(self, name: str, should_fail: bool = False):
        self._name = name
        self._should_fail = should_fail

    @property
    def name(self) -> str:
        return self._name

    async def chat(self, messages: list, **kwargs) -> str:
        if self._should_fail:
            raise ConnectionError("Provider unavailable")
        return f"Response from {self._name}"

    async def health_check(self) -> bool:
        return not self._should_fail

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


@pytest.mark.asyncio
async def test_provider_primary_success():
    """Primary provider succeeds."""
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)

    result = await registry.chat([{"role": "user", "content": "hello"}])
    assert result == "Response from primary"


@pytest.mark.asyncio
async def test_provider_failover_to_backup():
    """Falls back to backup when primary fails."""
    registry = ProviderRegistry()
    primary = MockProvider("primary", should_fail=True)
    backup = MockProvider("backup")
    registry.set_primary(primary)
    registry.add_backup(backup)

    result = await registry.chat([{"role": "user", "content": "hello"}])
    assert result == "Response from backup"


@pytest.mark.asyncio
async def test_provider_all_fail():
    """Returns fallback message when all providers fail."""
    registry = ProviderRegistry()
    primary = MockProvider("primary", should_fail=True)
    backup = MockProvider("backup", should_fail=True)
    registry.set_primary(primary)
    registry.add_backup(backup)

    result = await registry.chat([{"role": "user", "content": "hello"}])
    assert "暂时无法" in result or "错误" in result


@pytest.mark.asyncio
async def test_provider_health_check():
    """Health check returns provider status."""
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)

    health = await registry.health_check()
    assert health["primary"] is True


@pytest.mark.asyncio
async def test_provider_token_budget():
    """Token budget tracking works."""
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)
    registry.set_budget(1000)

    # Use some tokens
    await registry.chat([{"role": "user", "content": "hello"}])
    stats = registry.budget_stats()
    assert stats["used"] > 0
    assert stats["limit"] == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_llm.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# llm/providers/__init__.py
"""LLM provider abstraction with failover support."""
from abc import ABC, abstractmethod
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger("llm_providers")


class LLMProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def chat(self, messages: list, **kwargs) -> str: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    def count_tokens(self, text: str) -> int: ...


class ProviderRegistry:
    def __init__(self):
        self._primary: Optional[LLMProvider] = None
        self._backups: list[LLMProvider] = []
        self._daily_tokens_used: int = 0
        self._daily_token_limit: int = 500000

    def set_primary(self, provider: LLMProvider):
        self._primary = provider

    def add_backup(self, provider: LLMProvider):
        self._backups.append(provider)

    def set_budget(self, limit: int):
        self._daily_token_limit = limit

    async def chat(self, messages: list, **kwargs) -> str:
        """Try primary, then backups. Returns fallback message on total failure."""
        providers = [self._primary] + self._backups if self._primary else list(self._backups)

        for provider in providers:
            if provider is None:
                continue
            try:
                result = await provider.chat(messages, **kwargs)
                tokens = provider.count_tokens(str(messages)) + provider.count_tokens(result)
                self._daily_tokens_used += tokens
                return result
            except Exception as e:
                logger.warning(f"Provider {provider.name} 失败: {e}")
                continue

        logger.error("所有 LLM 提供者均不可用")
        return "抱歉，我暂时无法回复，请稍后再试。"

    async def health_check(self) -> dict:
        """Check health of all providers."""
        result = {}
        if self._primary:
            result["primary"] = await self._primary.health_check()
        for i, backup in enumerate(self._backups):
            result[f"backup_{i}"] = await backup.health_check()
        return result

    def budget_stats(self) -> dict:
        return {
            "used": self._daily_tokens_used,
            "limit": self._daily_token_limit,
            "percent": self._daily_tokens_used / max(self._daily_token_limit, 1) * 100,
        }

    def reset_daily_budget(self):
        self._daily_tokens_used = 0
```

```python
# llm/providers/deepseek.py
"""DeepSeek provider using OpenAI-compatible API."""
from openai import AsyncOpenAI
from llm.providers import LLMProvider
from utils.logger import setup_logger

logger = setup_logger("deepseek")


class DeepSeekProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com"):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    @property
    def name(self) -> str:
        return f"deepseek/{self._model}"

    async def chat(self, messages: list, **kwargs) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 2048),
            temperature=kwargs.get("temperature", 0.7),
        )
        return resp.choices[0].message.content or ""

    async def health_check(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return True
        except Exception:
            return False

    def count_tokens(self, text: str) -> int:
        return len(text) // 3  # Approximate for Chinese
```

```python
# llm/providers/openai_compat.py
"""Generic OpenAI-compatible provider."""
from openai import AsyncOpenAI
from llm.providers import LLMProvider


class OpenAICompatProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._base_url = base_url

    @property
    def name(self) -> str:
        return f"openai_compat/{self._model}"

    async def chat(self, messages: list, **kwargs) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 2048),
            temperature=kwargs.get("temperature", 0.7),
        )
        return resp.choices[0].message.content or ""

    async def health_check(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return True
        except Exception:
            return False

    def count_tokens(self, text: str) -> int:
        return len(text) // 4
```

```python
# llm/providers/ollama.py
"""Ollama local provider."""
import httpx
from llm.providers import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    async def chat(self, messages: list, **kwargs) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._base_url}/api/tags", timeout=5)
                return resp.status_code == 200
        except Exception:
            return False

    def count_tokens(self, text: str) -> int:
        return len(text) // 4
```

- [ ] **Step 4: Refactor llm/client.py to use providers**

```python
# llm/client.py - refactored
"""Multi-provider LLM client with failover."""
from typing import Optional
from llm.providers import LLMProvider, ProviderRegistry
from llm.providers.deepseek import DeepSeekProvider
from utils.logger import setup_logger

logger = setup_logger("llm_client")


class LLMClient:
    """High-level LLM client that delegates to provider registry."""

    def __init__(self, config: dict):
        self.registry = ProviderRegistry()
        self._setup_providers(config)

    def _setup_providers(self, config: dict):
        primary_cfg = config.get("primary", {})
        if primary_cfg.get("provider") == "deepseek":
            provider = DeepSeekProvider(
                api_key=primary_cfg.get("api_key", ""),
                model=primary_cfg.get("model", "deepseek-chat"),
            )
            self.registry.set_primary(provider)

        backup_cfg = config.get("backup", {})
        if backup_cfg.get("api_key"):
            from llm.providers.openai_compat import OpenAICompatProvider
            backup = OpenAICompatProvider(
                api_key=backup_cfg["api_key"],
                base_url=backup_cfg.get("base_url", "https://api.openai.com/v1"),
                model=backup_cfg.get("model", "gpt-3.5-turbo"),
            )
            self.registry.add_backup(backup)

        ollama_cfg = config.get("ollama", {})
        if ollama_cfg.get("enabled"):
            from llm.providers.ollama import OllamaProvider
            ollama = OllamaProvider(model=ollama_cfg.get("model", "qwen2.5:7b"))
            self.registry.add_backup(ollama)

        budget_cfg = config.get("budget", {})
        self.registry.set_budget(budget_cfg.get("daily_token_limit", 500000))

    async def chat(self, messages: list, **kwargs) -> str:
        return await self.registry.chat(messages, **kwargs)

    async def health_check(self) -> dict:
        return await self.registry.health_check()

    def budget_stats(self) -> dict:
        return self.registry.budget_stats()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_multi_llm.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add llm/providers/ llm/client.py tests/test_multi_llm.py
git commit -m "feat: add multi-LLM provider system with failover and token budget"
```

---

## Phase 4: Memory Enhancements

### Task 12: Memory Forgetting Mechanism

**Files:**
- Create: `memory/forgetting.py`
- Modify: `memory/long_term.py`
- Create: `tests/test_memory_enhanced.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_enhanced.py
import pytest
import time
from memory.forgetting import MemoryForgetting


@pytest.fixture
def forgetting():
    return MemoryForgetting(
        low_access_threshold=3,
        low_access_days=30,
        archive_after_days=60,
    )


def test_should_not_archive_recent(forgetting):
    """Recently accessed memories are not archived."""
    memory = {"id": "1", "access_count": 5, "last_access": time.time()}
    assert forgetting.should_archive(memory) is False


def test_should_archive_old_unused(forgetting):
    """Old unused memories should be archived."""
    old_time = time.time() - 61 * 86400
    memory = {"id": "1", "access_count": 0, "last_access": old_time}
    assert forgetting.should_archive(memory) is True


def test_should_mark_low_priority(forgetting):
    """Low-access memories are marked as low priority."""
    old_time = time.time() - 31 * 86400
    memory = {"id": "1", "access_count": 2, "last_access": old_time}
    assert forgetting.is_low_priority(memory) is True


def test_should_not_mark_active_as_low(forgetting):
    """Active memories are not low priority."""
    memory = {"id": "1", "access_count": 10, "last_access": time.time()}
    assert forgetting.is_low_priority(memory) is False


def test_get_cleanup_report(forgetting):
    """Cleanup report summarizes what would be archived."""
    memories = [
        {"id": "1", "access_count": 0, "last_access": time.time() - 61 * 86400},
        {"id": "2", "access_count": 5, "last_access": time.time()},
        {"id": "3", "access_count": 1, "last_access": time.time() - 31 * 86400},
    ]
    report = forgetting.get_cleanup_report(memories)
    assert report["archive_count"] == 1
    assert report["low_priority_count"] == 1
    assert report["active_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_enhanced.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# memory/forgetting.py
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
        """Check if a memory should be archived (excluded from search but not deleted)."""
        last_access = memory.get("last_access", 0)
        access_count = memory.get("access_count", 0)
        age = time.time() - last_access

        if access_count == 0 and age >= self.archive_seconds:
            return True
        return False

    def is_low_priority(self, memory: dict) -> bool:
        """Check if a memory is low priority (few accesses, old)."""
        last_access = memory.get("last_access", 0)
        access_count = memory.get("access_count", 0)
        age = time.time() - last_access

        if access_count < self.low_access_threshold and age >= self.low_access_seconds:
            return True
        return False

    def get_cleanup_report(self, memories: list[dict]) -> dict:
        """Summarize what would be archived/degraded."""
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
```

- [ ] **Step 4: Enhance long_term.py with relevance threshold**

Add to `memory/long_term.py` - update the `search` method:

```python
# Add to LongTermMemory class in memory/long_term.py

def search(self, query: str, user_id: str = None, threshold: float = 0.7, top_k: int = 5) -> list:
    """Search with relevance threshold filter."""
    if not self._collection:
        return []
    try:
        where = {"user_id": user_id} if user_id else None
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
        )
        items = []
        if results and results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                similarity = 1 - distance  # cosine distance to similarity
                if similarity >= threshold:
                    items.append({
                        "text": doc,
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "similarity": similarity,
                    })
        return items
    except Exception as e:
        logger.error(f"长期记忆搜索失败: {e}")
        return []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_memory_enhanced.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add memory/forgetting.py memory/long_term.py tests/test_memory_enhanced.py
git commit -m "feat: add memory forgetting mechanism and relevance threshold"
```

---

## Phase 5: Cleanup & Integration

### Task 13: Remove WeChat, Update Dependencies, Wire Everything Together

**Files:**
- Delete: `platforms/wechat.py`
- Modify: `requirements.txt`
- Modify: `main.py`
- Modify: `platforms/__init__.py`

- [ ] **Step 1: Delete WeChat platform file**

```bash
git rm platforms/wechat.py
```

- [ ] **Step 2: Update requirements.txt**

```txt
fastapi>=0.110.0
uvicorn>=0.29.0
pyyaml>=6.0
pydantic>=2.0
pydantic-settings>=2.0
python-dotenv>=1.0
aiosqlite>=0.20.0
sqlalchemy>=2.0
openai>=1.12.0
chromadb>=0.4.22
cachetools>=5.3.0
tiktoken>=0.6.0
apscheduler>=3.10.0
httpx>=0.27.0
pytest>=8.0
pytest-asyncio>=0.23.0
python-telegram-bot>=20.0
aiofiles>=24.0
python-multipart>=0.0.9
```

- [ ] **Step 3: Update main.py to use new components**

```python
# main.py - key changes:
# 1. Import new components
# 2. Remove WeChat platform
# 3. Add Telegram platform
# 4. Wire security system
# 5. Use centralized config

from utils.config import init_config, get_config
from security.rate_limiter import RateLimiter
from security.circuit_breaker import CircuitBreaker
from security.content_filter import ContentFilter
from security.alerting import AlertManager
from core.pipeline import Pipeline
from core.middleware.rate_limit import RateLimitMiddleware
from core.middleware.filter import FilterMiddleware
from core.middleware.active_window import ActiveWindowMiddleware
from core.middleware.content_filter import ContentFilterMiddleware
from core.middleware.safety import SafetyMiddleware
from core.middleware.split import SplitMiddleware
from core.middleware.delay import DelayMiddleware
from llm.client import LLMClient
from platforms.telegram import TelegramPlatform
# ... rest of imports
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS (all tests, excluding any that depended on wechat.py)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove WeChat, add Telegram, wire security pipeline and multi-LLM"
```

---

### Task 14: Update Database Models for Alerts and Metrics

**Files:**
- Modify: `database/models.py`
- Modify: `database/db.py`

- [ ] **Step 1: Add Alert and Metric models**

```python
# Add to database/models.py

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String(20), nullable=False)  # INFO, WARNING, CRITICAL
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=func.now())


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_type = Column(String(50), nullable=False)  # msg_sent, msg_received, llm_call, etc.
    value = Column(Float, nullable=False)
    platform = Column(String(20))
    created_at = Column(DateTime, default=func.now())
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add database/models.py
git commit -m "feat: add Alert and Metric database models"
```

---

### Task 15: Run Full Test Suite and Fix Any Issues

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Fix any import errors or broken tests**

Fix any tests that reference deleted `wechat.py` or old API signatures.

- [ ] **Step 3: Run linting**

```bash
python -m py_compile main.py
python -m py_compile platforms/telegram.py
python -m py_compile security/rate_limiter.py
python -m py_compile security/circuit_breaker.py
python -m py_compile security/content_filter.py
python -m py_compile security/alerting.py
python -m py_compile core/pipeline.py
python -m py_compile llm/client.py
python -m py_compile memory/forgetting.py
```

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve test failures and import errors from v2 refactor"
```

---

## Summary

| Phase | Tasks | Key Deliverables |
|-------|-------|-----------------|
| 1: Security Core | 1-5 | Config system, rate limiter, circuit breaker, content filter, alerting |
| 2: Architecture | 6-9 | Pipeline, middleware framework, built-in middleware |
| 3: Telegram + LLM | 10-11 | Telegram adapter, multi-LLM with failover |
| 4: Memory | 12 | Forgetting mechanism, relevance threshold |
| 5: Cleanup | 13-15 | Remove WeChat, wire everything, fix tests |

**Total: 15 tasks, ~60 steps, ~45 new files, ~10 modified files**

After completing all tasks, the system will have:
- Three-layer security (rate limiting, circuit breaker, content filtering)
- Middleware-based message pipeline
- QQ + Telegram dual platform support
- Multi-LLM with automatic failover
- Memory with forgetting and relevance filtering
- Centralized configuration
- Alerting via Telegram
