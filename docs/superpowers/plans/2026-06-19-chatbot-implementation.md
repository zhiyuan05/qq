# 聊天机器人平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个支持微信/QQ双平台、DeepSeek API驱动的聊天机器人平台，具备角色卡、风格引擎、三层记忆、自学习等完整功能。

**Architecture:** 单体FastAPI应用，消息从平台适配层进入，经路由层处理后由核心引擎协调角色卡、风格、记忆、LLM等模块生成回复。SQLite存储结构化数据，ChromaDB存储向量记忆。

**Tech Stack:** Python 3.11+, FastAPI, DeepSeek API (OpenAI兼容), SQLite+aiosqlite, ChromaDB, PyYAML, Pydantic, WeChatFerry, NapCat(OneBot11)

---

## 文件结构总览

```
chatbot/
├── main.py                          # 入口，启动所有平台和引擎
├── config.yaml                      # 主配置
├── .env                             # 环境变量
├── requirements.txt                 # 依赖
├── core/
│   ├── __init__.py
│   ├── engine.py                    # 核心引擎，协调所有模块
│   ├── router.py                    # 消息路由，去重/限流/分发
│   ├── session.py                   # 会话管理
│   ├── prompt_builder.py            # Prompt构建器
│   └── command_parser.py            # 指令解析器
├── platforms/
│   ├── __init__.py
│   ├── base.py                      # 平台抽象基类
│   ├── models.py                    # UnifiedMessage等数据模型
│   ├── wechat.py                    # 微信适配器
│   └── qq.py                        # QQ适配器
├── llm/
│   ├── __init__.py
│   ├── client.py                    # DeepSeek API客户端
│   └── token_counter.py             # Token计数
├── memory/
│   ├── __init__.py
│   ├── short_term.py                # 短期记忆
│   ├── long_term.py                 # 长期语义记忆
│   ├── entity_memory.py             # 实体记忆
│   └── summarizer.py                # 对话摘要
├── characters/
│   ├── __init__.py
│   ├── manager.py                   # 角色卡管理器
│   └── cards/
│       └── default.yaml             # 默认角色卡
├── styles/
│   ├── __init__.py
│   ├── engine.py                    # 风格引擎
│   └── presets.yaml                 # 预设风格
├── learning/
│   ├── __init__.py
│   ├── analyzer.py                  # 学习分析器
│   ├── scheduler.py                 # 学习调度器
│   └── evolution.py                 # 角色卡演化
├── database/
│   ├── __init__.py
│   ├── db.py                        # 数据库连接管理
│   └── models.py                    # SQLAlchemy模型
├── utils/
│   ├── __init__.py
│   ├── logger.py                    # 日志配置
│   ├── config.py                    # 配置加载
│   ├── rate_limiter.py              # 频率限制
│   └── message_splitter.py          # 长消息拆分
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_command_parser.py
    ├── test_character_manager.py
    ├── test_style_engine.py
    ├── test_memory.py
    ├── test_prompt_builder.py
    ├── test_session.py
    ├── test_router.py
    ├── test_engine.py
    └── test_llm_client.py
```

---

### Task 1: 项目脚手架与配置系统

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `config.yaml`
- Create: `utils/__init__.py`
- Create: `utils/config.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 创建 requirements.txt**

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
```

- [ ] **Step 2: 创建 .env.example**

```env
DEEPSEEK_API_KEY=your-api-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

- [ ] **Step 3: 创建 config.yaml**

```yaml
bot:
  name: "AI助手"
  default_character: "default"
  default_style: "casual"
  command_prefix: "/"

llm:
  provider: "deepseek"
  model: "deepseek-chat"
  fallback_model: "deepseek-reasoner"
  max_tokens: 2048
  temperature: 0.7
  timeout: 30
  max_retries: 3
  stream: true

memory:
  short_term:
    max_messages: 50
    summarize_after: 30
  long_term:
    enabled: true
    vector_db: "chroma"
    top_k: 5
  entity:
    enabled: true
    auto_extract: true

platforms:
  wechat:
    enabled: true
    auto_accept_friend: false
    allowed_users: []
    allowed_groups: []
  qq:
    enabled: true
    onebot_url: "ws://127.0.0.1:3001"
    allowed_users: []
    allowed_groups: []

rate_limit:
  enabled: true
  max_per_minute: 20
  max_per_hour: 200

security:
  admin_users: []
  content_filter: false
  max_message_length: 2000

learning:
  enabled: true
  trigger_interval: 50
  trigger_schedule: "03:00"
  max_history_for_learning: 200
  auto_apply: true
  backup_before_learn: true

logging:
  level: "INFO"
  file: "data/logs/chatbot.log"
```

- [ ] **Step 4: 创建 utils/config.py**

```python
"""配置加载与管理"""
import os
from pathlib import Path
from typing import List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class BotConfig(BaseModel):
    name: str = "AI助手"
    default_character: str = "default"
    default_style: str = "casual"
    command_prefix: str = "/"


class LLMConfig(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    fallback_model: str = "deepseek-reasoner"
    max_tokens: int = 2048
    temperature: float = 0.7
    timeout: int = 30
    max_retries: int = 3
    stream: bool = True


class ShortTermMemoryConfig(BaseModel):
    max_messages: int = 50
    summarize_after: int = 30


class LongTermMemoryConfig(BaseModel):
    enabled: bool = True
    vector_db: str = "chroma"
    top_k: int = 5


class EntityMemoryConfig(BaseModel):
    enabled: bool = True
    auto_extract: bool = True


class MemoryConfig(BaseModel):
    short_term: ShortTermMemoryConfig = ShortTermMemoryConfig()
    long_term: LongTermMemoryConfig = LongTermMemoryConfig()
    entity: EntityMemoryConfig = EntityMemoryConfig()


class PlatformConfig(BaseModel):
    enabled: bool = True
    auto_accept_friend: bool = False
    allowed_users: List[str] = Field(default_factory=list)
    allowed_groups: List[str] = Field(default_factory=list)
    onebot_url: str = "ws://127.0.0.1:3001"


class PlatformsConfig(BaseModel):
    wechat: PlatformConfig = PlatformConfig()
    qq: PlatformConfig = PlatformConfig()


class RateLimitConfig(BaseModel):
    enabled: bool = True
    max_per_minute: int = 20
    max_per_hour: int = 200


class SecurityConfig(BaseModel):
    admin_users: List[str] = Field(default_factory=list)
    content_filter: bool = False
    max_message_length: int = 2000


class LearningConfig(BaseModel):
    enabled: bool = True
    trigger_interval: int = 50
    trigger_schedule: str = "03:00"
    max_history_for_learning: int = 200
    auto_apply: bool = True
    backup_before_learn: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "data/logs/chatbot.log"


class AppConfig(BaseModel):
    bot: BotConfig = BotConfig()
    llm: LLMConfig = LLMConfig()
    memory: MemoryConfig = MemoryConfig()
    platforms: PlatformsConfig = PlatformsConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    security: SecurityConfig = SecurityConfig()
    learning: LearningConfig = LearningConfig()
    logging: LoggingConfig = LoggingConfig()


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """加载配置文件和环境变量"""
    load_dotenv(env_path)

    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    # 从环境变量注入API Key
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    llm_raw = raw.get("llm", {})
    llm_raw["api_key"] = api_key or llm_raw.get("api_key", "")
    llm_raw["base_url"] = base_url or llm_raw.get("base_url", "https://api.deepseek.com")
    raw["llm"] = llm_raw

    return AppConfig(**raw)
```

- [ ] **Step 5: 创建 tests/conftest.py**

```python
"""测试公共配置"""
import pytest
from utils.config import AppConfig, BotConfig, LLMConfig


@pytest.fixture
def default_config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def config_with_key() -> AppConfig:
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    return cfg
```

- [ ] **Step 6: 创建 tests/test_config.py**

```python
"""配置系统测试"""
from utils.config import AppConfig, load_config


def test_default_config():
    cfg = AppConfig()
    assert cfg.bot.name == "AI助手"
    assert cfg.bot.command_prefix == "/"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.llm.max_tokens == 2048
    assert cfg.memory.short_term.max_messages == 50
    assert cfg.rate_limit.max_per_minute == 20
    assert cfg.learning.trigger_interval == 50


def test_config_with_custom_values():
    cfg = AppConfig(
        bot={"name": "测试机器人", "command_prefix": "!"},
        llm={"model": "deepseek-reasoner", "temperature": 0.5},
    )
    assert cfg.bot.name == "测试机器人"
    assert cfg.bot.command_prefix == "!"
    assert cfg.llm.model == "deepseek-reasoner"
    assert cfg.llm.temperature == 0.5


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "bot:\n  name: YAML机器人\nllm:\n  model: deepseek-chat\n",
        encoding="utf-8",
    )
    cfg = load_config(str(config_file))
    assert cfg.bot.name == "YAML机器人"
    assert cfg.llm.model == "deepseek-chat"
```

- [ ] **Step 7: 运行测试验证**

```bash
cd D:/AAA/chatbot && python -m pytest tests/test_config.py -v
```

- [ ] **Step 8: 初始化项目目录结构**

```bash
mkdir -p core platforms llm memory characters/cards styles learning database utils tests data/logs data/chroma data/learning
```

创建所有 `__init__.py`:

```bash
touch core/__init__.py platforms/__init__.py llm/__init__.py memory/__init__.py characters/__init__.py styles/__init__.py learning/__init__.py database/__init__.py utils/__init__.py
```

- [ ] **Step 9: Commit**

```bash
git init && git add -A && git commit -m "feat: project scaffolding with config system"
```

---

### Task 2: 数据库层

**Files:**
- Create: `database/db.py`
- Create: `database/models.py`

- [ ] **Step 1: 创建 database/models.py**

```python
"""SQLAlchemy 数据模型"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, Boolean, JSON
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False)  # wechat / qq
    platform_id = Column(String(200), nullable=False, unique=True)
    nickname = Column(String(100), default="")
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user / assistant / system
    content = Column(Text, nullable=False)
    character_card = Column(String(100), default="")
    style = Column(String(50), default="")
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class EntityMemory(Base):
    __tablename__ = "entity_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    entity_type = Column(String(50), nullable=False)  # person/preference/fact/skill
    key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0)
    source = Column(String(20), default="auto")  # auto / manual
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LearningReport(Base):
    __tablename__ = "learning_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_card = Column(String(100), nullable=False)
    conversations_analyzed = Column(Integer, default=0)
    traits_learned = Column(JSON, default=dict)
    summary = Column(Text, default="")
    applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 2: 创建 database/db.py**

```python
"""数据库连接管理"""
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base

_engine = None
_async_session_factory = None


def get_engine(db_path: str = "data/chatbot.db"):
    global _engine
    if _engine is None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    return _engine


def get_async_engine(db_path: str = "data/chatbot.db"):
    global _engine
    if _engine is None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    return _engine


def get_session_factory(db_path: str = "data/chatbot.db"):
    global _async_session_factory
    if _async_session_factory is None:
        engine = get_async_engine(db_path)
        _async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _async_session_factory


async def init_db(db_path: str = "data/chatbot.db"):
    """创建所有表"""
    engine = get_async_engine(db_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    """获取数据库会话"""
    factory = get_session_factory()
    async with factory() as session:
        yield session
```

- [ ] **Step 3: Commit**

```bash
git add database/ && git commit -m "feat: database layer with SQLAlchemy models"
```

---

### Task 3: 日志与工具函数

**Files:**
- Create: `utils/logger.py`
- Create: `utils/rate_limiter.py`
- Create: `utils/message_splitter.py`

- [ ] **Step 1: 创建 utils/logger.py**

```python
"""日志配置"""
import logging
import sys
from pathlib import Path


def setup_logger(
    name: str = "chatbot",
    level: str = "INFO",
    log_file: str = "data/logs/chatbot.log",
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 文件
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
```

- [ ] **Step 2: 创建 utils/rate_limiter.py**

```python
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

        # 清理过期记录
        records[:] = [t for t in records if now - t < 3600]

        # 检查小时限制
        if len(records) >= self.max_per_hour:
            return False, "每小时请求次数已达上限"

        # 检查分钟限制
        recent = [t for t in records if now - t < 60]
        if len(recent) >= self.max_per_minute:
            return False, "请求太频繁，请稍后再试"

        records.append(now)
        return True, ""

    def reset(self, user_id: str):
        self._records.pop(user_id, None)
```

- [ ] **Step 3: 创建 utils/message_splitter.py**

```python
"""长消息智能拆分"""
from typing import List


def split_message(text: str, max_length: int = 2000) -> List[str]:
    """按语义边界拆分长消息"""
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = ""

    # 优先按段落拆分
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_length:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                chunks.append(current.strip())
            # 段落本身超长，按句子拆分
            if len(para) > max_length:
                sentences = _split_sentences(para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) <= max_length:
                        current += sent
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = sent
            else:
                current = para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _split_sentences(text: str) -> List[str]:
    """按句子拆分"""
    import re
    sentences = re.split(r'(?<=[。！？.!?])\s*', text)
    return [s for s in sentences if s.strip()]
```

- [ ] **Step 4: Commit**

```bash
git add utils/ && git commit -m "feat: add logger, rate limiter, message splitter"
```

---

### Task 4: 平台抽象层与数据模型

**Files:**
- Create: `platforms/models.py`
- Create: `platforms/base.py`

- [ ] **Step 1: 创建 platforms/models.py**

```python
"""统一消息模型"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    FILE = "file"
    SYSTEM = "system"


class Platform(str, Enum):
    WECHAT = "wechat"
    QQ = "qq"
    CONSOLE = "console"


@dataclass
class UnifiedMessage:
    """统一消息格式，所有平台消息转为此格式"""
    platform: Platform
    user_id: str
    content: str
    message_type: MessageType = MessageType.TEXT
    group_id: Optional[str] = None
    nickname: str = ""
    message_id: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_group(self) -> bool:
        return self.group_id is not None

    @property
    def session_key(self) -> str:
        """会话唯一标识"""
        if self.group_id:
            return f"{self.platform.value}:{self.group_id}"
        return f"{self.platform.value}:{self.user_id}"
```

- [ ] **Step 2: 创建 platforms/base.py**

```python
"""平台抽象基类"""
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from platforms.models import UnifiedMessage

MessageCallback = Callable[[UnifiedMessage], Awaitable[Optional[str]]]


class BasePlatform(ABC):
    """所有平台适配器的基类"""

    name: str = "base"

    def __init__(self, config: dict):
        self.config = config
        self._callback: Optional[MessageCallback] = None

    def on_message(self, callback: MessageCallback):
        """注册消息回调"""
        self._callback = callback

    @abstractmethod
    async def start(self):
        """启动平台监听"""
        ...

    @abstractmethod
    async def stop(self):
        """停止平台"""
        ...

    @abstractmethod
    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        """发送文本消息"""
        ...

    async def send_image(self, user_id: str, image_path: str, group_id: Optional[str] = None):
        """发送图片（可选实现）"""
        pass
```

- [ ] **Step 3: Commit**

```bash
git add platforms/ && git commit -m "feat: platform abstraction layer with UnifiedMessage"
```

---

### Task 5: LLM 客户端

**Files:**
- Create: `llm/client.py`
- Create: `llm/token_counter.py`

- [ ] **Step 1: 创建 llm/token_counter.py**

```python
"""Token 计数"""
from typing import List, Dict


def count_tokens(messages: List[Dict[str, str]], model: str = "deepseek-chat") -> int:
    """估算消息的token数量"""
    try:
        import tiktoken
        encoding = tiktoken.encoding_for_model("gpt-4")  # DeepSeek兼容OpenAI格式
    except Exception:
        # 回退：按字符估算
        total = sum(len(m.get("content", "")) for m in messages)
        return total // 2

    total = 0
    for msg in messages:
        total += 4  # 每条消息的基础开销
        for value in msg.values():
            total += len(encoding.encode(str(value)))
    total += 2  # reply priming
    return total
```

- [ ] **Step 2: 创建 llm/client.py**

```python
"""DeepSeek API 客户端"""
import asyncio
from typing import AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk

from utils.logger import setup_logger

logger = setup_logger("llm.client")


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", fallback_model: str = "deepseek-reasoner",
                 max_tokens: int = 2048, temperature: float = 0.7,
                 timeout: int = 30, max_retries: int = 3):
        self.model = model
        self.fallback_model = fallback_model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def chat(self, messages: List[Dict[str, str]], stream: bool = False) -> str:
        """非流式调用"""
        for attempt in range(self.max_retries):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    stream=False,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.warning(f"LLM调用失败 (attempt {attempt+1}): {e}")
                if attempt == self.max_retries - 1:
                    # 尝试降级模型
                    if self.model != self.fallback_model:
                        logger.info(f"降级到 {self.fallback_model}")
                        old = self.model
                        self.model = self.fallback_model
                        try:
                            result = await self.chat(messages, stream=False)
                            self.model = old
                            return result
                        except Exception:
                            self.model = old
                    raise
                await asyncio.sleep(1 * (attempt + 1))

    async def chat_stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """流式调用"""
        for attempt in range(self.max_retries):
            try:
                stream = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    stream=True,
                )
                async for chunk in stream:  # type: ignore
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield delta.content
                return
            except Exception as e:
                logger.warning(f"流式调用失败 (attempt {attempt+1}): {e}")
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(1 * (attempt + 1))
```

- [ ] **Step 3: Commit**

```bash
git add llm/ && git commit -m "feat: DeepSeek LLM client with streaming and retry"
```

---

### Task 6: 角色卡系统

**Files:**
- Create: `characters/manager.py`
- Create: `characters/cards/default.yaml`

- [ ] **Step 1: 创建默认角色卡 characters/cards/default.yaml**

```yaml
name: "默认助手"
description: "一个友善、专业的AI助手"
skills: "回答问题、写作、编程、分析"
system_prompt: |
  你是{{name}}，{{description}}。
  你极为擅长{{skills}}。
  你需要遵守以下规则:
  {{rules}}
rules:
  - 以中文输出为主
  - 回复简洁有力
  - 必要时使用 Markdown 格式
personality:
  tone: "友善专业"
  humor: 0.6
  formality: 0.5
  verbosity: 0.5
greeting: "你好！我是{{name}}，有什么可以帮你的？"
examples:
  - user: "你好"
    assistant: "你好呀！有什么需要帮忙的吗？"
```

- [ ] **Step 2: 创建 characters/manager.py**

```python
"""角色卡管理器"""
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("characters")


class CharacterCard:
    def __init__(self, data: dict):
        self.name: str = data.get("name", "未知")
        self.description: str = data.get("description", "")
        self.skills: str = data.get("skills", "")
        self.system_prompt: str = data.get("system_prompt", "")
        self.rules: List[str] = data.get("rules", [])
        self.personality: dict = data.get("personality", {})
        self.greeting: str = data.get("greeting", "")
        self.examples: List[dict] = data.get("examples", [])
        self.learned_traits: dict = data.get("learned_traits", {})

    def get_system_prompt(self) -> str:
        """生成完整的system prompt"""
        rules_text = "\n".join(f"- {r}" for r in self.rules)
        prompt = self.system_prompt.replace("{{name}}", self.name)
        prompt = prompt.replace("{{description}}", self.description)
        prompt = prompt.replace("{{skills}}", self.skills)
        prompt = prompt.replace("{{rules}}", rules_text)

        # 追加学习到的特质
        if self.learned_traits:
            prompt += "\n\n根据以往交流经验:\n"
            for category, traits in self.learned_traits.items():
                if isinstance(traits, list):
                    for trait in traits:
                        prompt += f"- {trait}\n"

        return prompt

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "skills": self.skills,
            "system_prompt": self.system_prompt,
            "rules": self.rules,
            "personality": self.personality,
            "greeting": self.greeting,
            "examples": self.examples,
            "learned_traits": self.learned_traits,
        }


class CharacterManager:
    def __init__(self, cards_dir: str = "characters/cards"):
        self.cards_dir = Path(cards_dir)
        self._cards: Dict[str, CharacterCard] = {}
        self._load_all()

    def _load_all(self):
        """加载所有角色卡"""
        if not self.cards_dir.exists():
            return
        for f in self.cards_dir.glob("*.yaml"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = yaml.safe_load(fp)
                if data:
                    card = CharacterCard(data)
                    self._cards[f.stem] = card
                    logger.info(f"加载角色卡: {card.name}")
            except Exception as e:
                logger.error(f"加载角色卡失败 {f}: {e}")

    def get(self, name: str) -> Optional[CharacterCard]:
        return self._cards.get(name)

    def get_default(self) -> CharacterCard:
        card = self._cards.get("default")
        if not card:
            card = CharacterCard({"name": "默认助手", "description": "AI助手"})
            self._cards["default"] = card
        return card

    def list_cards(self) -> List[str]:
        return list(self._cards.keys())

    def save(self, name: str, card: CharacterCard):
        """保存角色卡到文件"""
        self._cards[name] = card
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        path = self.cards_dir / f"{name}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(card.to_dict(), f, allow_unicode=True, default_flow_style=False)

    def update_learned_traits(self, name: str, traits: dict):
        """更新角色卡的学习特质"""
        card = self._cards.get(name)
        if card:
            card.learned_traits = traits
            self.save(name, card)
```

- [ ] **Step 3: 测试角色卡系统**

创建 `tests/test_character_manager.py`:

```python
"""角色卡管理器测试"""
import yaml
from characters.manager import CharacterCard, CharacterManager


def test_character_card_system_prompt():
    card = CharacterCard({
        "name": "小助手",
        "description": "一个测试助手",
        "skills": "测试",
        "system_prompt": "你是{{name}}，{{description}}。擅长{{skills}}。\n规则:\n{{rules}}",
        "rules": ["说中文", "简洁"],
    })
    prompt = card.get_system_prompt()
    assert "小助手" in prompt
    assert "测试助手" in prompt
    assert "- 说中文" in prompt
    assert "- 简洁" in prompt


def test_character_manager_load(tmp_path):
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    data = {
        "name": "测试卡",
        "description": "测试",
        "system_prompt": "你是{{name}}",
        "rules": ["规则1"],
    }
    with open(cards_dir / "test.yaml", "w") as f:
        yaml.dump(data, f)

    mgr = CharacterManager(str(cards_dir))
    assert "test" in mgr.list_cards()
    card = mgr.get("test")
    assert card.name == "测试卡"


def test_character_card_learned_traits():
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}",
        "learned_traits": {
            "communication_style": ["用户喜欢简洁"],
        },
    })
    prompt = card.get_system_prompt()
    assert "用户喜欢简洁" in prompt
```

```bash
python -m pytest tests/test_character_manager.py -v
```

- [ ] **Step 4: Commit**

```bash
git add characters/ tests/test_character_manager.py && git commit -m "feat: character card system with YAML loading"
```

---

### Task 7: 风格引擎

**Files:**
- Create: `styles/engine.py`
- Create: `styles/presets.yaml`

- [ ] **Step 1: 创建 styles/presets.yaml**

```yaml
styles:
  casual:
    name: "随性闲聊"
    description: "轻松随意的口吻，可用网络用语和表情"
    prompt_modifier: "用轻松随意的口吻回复，可以使用网络用语和表情符号，像朋友聊天一样自然。"
    emoji_density: 0.3
    max_length: 200

  professional:
    name: "专业严谨"
    description: "专业严谨，逻辑清晰，引用来源"
    prompt_modifier: "用专业、严谨的方式回复，逻辑清晰，结构化输出，必要时引用来源。"
    emoji_density: 0.0
    max_length: 500

  creative:
    name: "创意写作"
    description: "富有想象力，善用比喻和修辞"
    prompt_modifier: "用富有创意和想象力的方式回复，善用比喻、排比等修辞手法，语言生动。"
    emoji_density: 0.1
    max_length: 800

  concise:
    name: "极简回复"
    description: "最简短的方式，只说重点"
    prompt_modifier: "用最简短的方式回复，只说核心要点，不展开解释，除非用户要求详细说明。"
    emoji_density: 0.0
    max_length: 100

  socratic:
    name: "苏格拉底式"
    description: "通过提问引导思考"
    prompt_modifier: "通过提问引导用户自己思考和发现问题的答案，而不是直接给出答案。每次回复以一个引导性问题结尾。"
    emoji_density: 0.0
    max_length: 300
```

- [ ] **Step 2: 创建 styles/engine.py**

```python
"""风格引擎"""
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("styles")


class Style:
    def __init__(self, data: dict):
        self.name: str = data.get("name", "")
        self.description: str = data.get("description", "")
        self.prompt_modifier: str = data.get("prompt_modifier", "")
        self.emoji_density: float = data.get("emoji_density", 0.0)
        self.max_length: int = data.get("max_length", 500)


class StyleEngine:
    def __init__(self, presets_path: str = "styles/presets.yaml"):
        self._styles: Dict[str, Style] = {}
        self._load_presets(presets_path)

    def _load_presets(self, path: str):
        p = Path(path)
        if not p.exists():
            logger.warning(f"风格预设文件不存在: {path}")
            return
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for key, val in data.get("styles", {}).items():
            self._styles[key] = Style(val)
            logger.info(f"加载风格: {key}")

    def get(self, name: str) -> Optional[Style]:
        return self._styles.get(name)

    def get_default(self) -> Style:
        return self._styles.get("casual", Style({"name": "默认", "prompt_modifier": ""}))

    def list_styles(self) -> List[str]:
        return list(self._styles.keys())

    def create(self, name: str, description: str, prompt_modifier: str) -> Style:
        """创建自定义风格"""
        style = Style({
            "name": name,
            "description": description,
            "prompt_modifier": prompt_modifier,
        })
        self._styles[name] = style
        return style

    def mix(self, name1: str, name2: str, ratio: float = 0.5) -> Optional[Style]:
        """混合两种风格"""
        s1 = self._styles.get(name1)
        s2 = self._styles.get(name2)
        if not s1 or not s2:
            return None
        return Style({
            "name": f"{name1}+{name2}",
            "description": f"{s1.description} 与 {s2.description} 的混合",
            "prompt_modifier": f"{s1.prompt_modifier}\n同时也要：{s2.prompt_modifier}",
            "emoji_density": s1.emoji_density * ratio + s2.emoji_density * (1 - ratio),
            "max_length": int(s1.max_length * ratio + s2.max_length * (1 - ratio)),
        })
```

- [ ] **Step 3: 测试风格引擎**

创建 `tests/test_style_engine.py`:

```python
"""风格引擎测试"""
from styles.engine import StyleEngine


def test_load_presets():
    engine = StyleEngine("styles/presets.yaml")
    styles = engine.list_styles()
    assert "casual" in styles
    assert "professional" in styles
    assert "creative" in styles
    assert "concise" in styles
    assert "socratic" in styles


def test_get_style():
    engine = StyleEngine("styles/presets.yaml")
    casual = engine.get("casual")
    assert casual is not None
    assert casual.name == "随性闲聊"
    assert casual.emoji_density == 0.3


def test_create_style():
    engine = StyleEngine("styles/presets.yaml")
    style = engine.create("custom", "自定义", "自定义的回复风格")
    assert style.name == "自定义"
    assert engine.get("custom") is not None


def test_mix_styles():
    engine = StyleEngine("styles/presets.yaml")
    mixed = engine.mix("casual", "professional")
    assert mixed is not None
    assert "casual+professional" == mixed.name
```

```bash
python -m pytest tests/test_style_engine.py -v
```

- [ ] **Step 4: Commit**

```bash
git add styles/ tests/test_style_engine.py && git commit -m "feat: style engine with presets and mixing"
```

---

### Task 8: 会话管理

**Files:**
- Create: `core/session.py`

- [ ] **Step 1: 创建 core/session.py**

```python
"""会话管理"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from platforms.models import UnifiedMessage


@dataclass
class ChatMessage:
    role: str  # user / assistant / system
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    token_count: int = 0


@dataclass
class Session:
    session_id: str
    user_id: str
    platform: str
    character_card: str = "default"
    style: str = "casual"
    messages: List[ChatMessage] = field(default_factory=list)
    summary: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)

    def add_message(self, role: str, content: str, token_count: int = 0):
        self.messages.append(ChatMessage(
            role=role, content=content, token_count=token_count,
        ))
        self.last_active = datetime.now()

    def get_recent_messages(self, n: int = 20) -> List[Dict[str, str]]:
        """获取最近n条消息"""
        recent = self.messages[-n:]
        return [{"role": m.role, "content": m.content} for m in recent]

    def get_context_messages(self, max_messages: int = 50) -> List[Dict[str, str]]:
        """获取上下文消息（含摘要）"""
        result = []
        if self.summary:
            result.append({"role": "system", "content": f"之前的对话摘要：{self.summary}"})
        recent = self.messages[-max_messages:]
        result.extend([{"role": m.role, "content": m.content} for m in recent])
        return result

    def undo_last_exchange(self):
        """撤销最后一组对话（用户+助手）"""
        if len(self.messages) >= 2:
            # 移除最后两条（一问一答）
            self.messages = self.messages[:-2]


class SessionManager:
    def __init__(self, max_messages: int = 50, summarize_after: int = 30):
        self.max_messages = max_messages
        self.summarize_after = summarize_after
        self._sessions: Dict[str, Session] = {}

    def get_or_create(self, msg: UnifiedMessage, default_character: str = "default",
                      default_style: str = "casual") -> Session:
        key = msg.session_key
        if key not in self._sessions:
            self._sessions[key] = Session(
                session_id=key,
                user_id=msg.user_id,
                platform=msg.platform.value,
                character_card=default_character,
                style=default_style,
            )
        return self._sessions[key]

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> List[str]:
        return list(self._sessions.keys())

    def clear(self, session_id: str):
        self._sessions.pop(session_id, None)

    def should_summarize(self, session: Session) -> bool:
        """是否需要摘要压缩"""
        return len(session.messages) > self.summarize_after
```

- [ ] **Step 2: 测试会话管理**

创建 `tests/test_session.py`:

```python
"""会话管理测试"""
from core.session import Session, SessionManager
from platforms.models import UnifiedMessage, Platform


def test_session_add_message():
    session = Session(session_id="test:1", user_id="1", platform="wechat")
    session.add_message("user", "你好")
    session.add_message("assistant", "你好呀！")
    assert len(session.messages) == 2
    assert session.messages[0].role == "user"


def test_session_get_recent():
    session = Session(session_id="test:1", user_id="1", platform="wechat")
    for i in range(10):
        session.add_message("user", f"msg{i}")
    recent = session.get_recent_messages(3)
    assert len(recent) == 3
    assert recent[0]["content"] == "msg7"


def test_session_undo():
    session = Session(session_id="test:1", user_id="1", platform="wechat")
    session.add_message("user", "问题1")
    session.add_message("assistant", "回答1")
    session.add_message("user", "问题2")
    session.add_message("assistant", "回答2")
    session.undo_last_exchange()
    assert len(session.messages) == 2
    assert session.messages[-1].content == "回答1"


def test_session_manager():
    mgr = SessionManager()
    msg = UnifiedMessage(platform=Platform.WECHAT, user_id="user1", content="hi")
    session = mgr.get_or_create(msg)
    assert session.session_id == "wechat:user1"
    assert mgr.get_or_create(msg) is session  # 同一会话
```

```bash
python -m pytest tests/test_session.py -v
```

- [ ] **Step 3: Commit**

```bash
git add core/session.py tests/test_session.py && git commit -m "feat: session management with context window"
```

---

### Task 9: 指令解析器

**Files:**
- Create: `core/command_parser.py`

- [ ] **Step 1: 创建 core/command_parser.py**

```python
"""指令解析器"""
from dataclasses import dataclass
from typing import List, Optional

from utils.logger import setup_logger

logger = setup_logger("command_parser")


@dataclass
class ParsedCommand:
    is_command: bool
    command: str = ""
    subcommand: str = ""
    args: List[str] = None
    raw: str = ""

    def __post_init__(self):
        if self.args is None:
            self.args = []


class CommandParser:
    def __init__(self, prefix: str = "/"):
        self.prefix = prefix

    def parse(self, text: str) -> ParsedCommand:
        """解析消息，判断是否为指令"""
        text = text.strip()

        if not text.startswith(self.prefix):
            return ParsedCommand(is_command=False, raw=text)

        # 移除前缀，按空格分割
        parts = text[len(self.prefix):].strip().split()
        if not parts:
            return ParsedCommand(is_command=False, raw=text)

        command = parts[0].lower()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        args = parts[2:] if len(parts) > 2 else []

        logger.debug(f"解析指令: {command} {subcommand} {args}")

        return ParsedCommand(
            is_command=True,
            command=command,
            subcommand=subcommand,
            args=args,
            raw=text,
        )


# 指令处理结果
@dataclass
class CommandResult:
    handled: bool
    response: str = ""
    action: str = ""  # new_session / switch_character / switch_style / etc.
    action_data: dict = None

    def __post_init__(self):
        if self.action_data is None:
            self.action_data = {}
```

- [ ] **Step 2: 测试指令解析器**

创建 `tests/test_command_parser.py`:

```python
"""指令解析器测试"""
from core.command_parser import CommandParser


def test_parse_non_command():
    parser = CommandParser("/")
    result = parser.parse("你好，今天天气怎么样？")
    assert result.is_command is False
    assert result.raw == "你好，今天天气怎么样？"


def test_parse_simple_command():
    parser = CommandParser("/")
    result = parser.parse("/help")
    assert result.is_command is True
    assert result.command == "help"


def test_parse_command_with_subcommand():
    parser = CommandParser("/")
    result = parser.parse("/card use creative")
    assert result.is_command is True
    assert result.command == "card"
    assert result.subcommand == "use"
    assert result.args == ["creative"]


def test_parse_command_with_args():
    parser = CommandParser("/")
    result = parser.parse("/memory recall Python编程")
    assert result.command == "memory"
    assert result.subcommand == "recall"
    assert result.args == ["Python编程"]


def test_parse_command_case_insensitive():
    parser = CommandParser("/")
    result = parser.parse("/Card List")
    assert result.command == "card"
    assert result.subcommand == "list"
```

```bash
python -m pytest tests/test_command_parser.py -v
```

- [ ] **Step 3: Commit**

```bash
git add core/command_parser.py tests/test_command_parser.py && git commit -m "feat: command parser with subcommand support"
```

---

### Task 10: 记忆系统

**Files:**
- Create: `memory/short_term.py`
- Create: `memory/long_term.py`
- Create: `memory/entity_memory.py`
- Create: `memory/summarizer.py`

- [ ] **Step 1: 创建 memory/short_term.py**

```python
"""短期记忆 - 基于会话的消息管理"""
from typing import Dict, List

from core.session import Session


class ShortTermMemory:
    def __init__(self, max_messages: int = 50):
        self.max_messages = max_messages

    def get_context(self, session: Session) -> List[Dict[str, str]]:
        """获取会话上下文"""
        return session.get_context_messages(self.max_messages)

    def trim_if_needed(self, session: Session) -> bool:
        """如果消息过多，返回True表示需要摘要"""
        return len(session.messages) > self.max_messages
```

- [ ] **Step 2: 创建 memory/summarizer.py**

```python
"""对话摘要器"""
from typing import List, Dict


class ConversationSummarizer:
    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def summarize(self, messages: List[Dict[str, str]]) -> str:
        """将对话历史压缩为摘要"""
        if not self._llm:
            # 无LLM时简单截取
            recent = messages[-5:]
            return "；".join(m["content"][:50] for m in recent if m["role"] == "user")

        summary_prompt = [
            {"role": "system", "content": "请将以下对话压缩为简洁的摘要，保留关键信息和用户偏好，不超过200字。"},
            {"role": "user", "content": self._format_messages(messages)},
        ]
        return await self._llm.chat(summary_prompt)

    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        lines = []
        for m in messages:
            role = "用户" if m["role"] == "user" else "助手"
            lines.append(f"{role}: {m['content'][:200]}")
        return "\n".join(lines)
```

- [ ] **Step 3: 创建 memory/entity_memory.py**

```python
"""实体记忆 - 用户画像与关键事实"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("entity_memory")


@dataclass
class Entity:
    entity_type: str  # person/preference/fact/skill
    key: str
    value: str
    confidence: float = 1.0
    source: str = "auto"
    updated_at: datetime = None

    def __post_init__(self):
        if self.updated_at is None:
            self.updated_at = datetime.now()


class EntityMemory:
    def __init__(self):
        # user_id -> list of entities
        self._entities: Dict[str, List[Entity]] = {}

    def store(self, user_id: str, entity_type: str, key: str, value: str,
              confidence: float = 1.0, source: str = "auto"):
        """存储实体"""
        if user_id not in self._entities:
            self._entities[user_id] = []

        # 检查是否已存在，更新而非重复
        for e in self._entities[user_id]:
            if e.key == key and e.entity_type == entity_type:
                e.value = value
                e.confidence = confidence
                e.updated_at = datetime.now()
                return

        self._entities[user_id].append(Entity(
            entity_type=entity_type, key=key, value=value,
            confidence=confidence, source=source,
        ))

    def query(self, user_id: str, entity_type: Optional[str] = None) -> List[Entity]:
        """查询实体"""
        entities = self._entities.get(user_id, [])
        if entity_type:
            return [e for e in entities if e.entity_type == entity_type]
        return entities

    def get_profile_text(self, user_id: str) -> str:
        """获取用户画像文本"""
        entities = self._entities.get(user_id, [])
        if not entities:
            return ""
        lines = ["用户画像:"]
        for e in entities:
            lines.append(f"- {e.key}: {e.value}")
        return "\n".join(lines)

    def forget(self, user_id: str, key: str) -> bool:
        """删除特定记忆"""
        entities = self._entities.get(user_id, [])
        before = len(entities)
        self._entities[user_id] = [e for e in entities if e.key != key]
        return len(self._entities[user_id]) < before

    def clear(self, user_id: str):
        """清空用户所有记忆"""
        self._entities.pop(user_id, None)
```

- [ ] **Step 4: 创建 memory/long_term.py**

```python
"""长期语义记忆 - 基于向量检索"""
from typing import Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("long_term")


class LongTermMemory:
    """基于ChromaDB的长期语义记忆"""

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
        """存储一段记忆"""
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
        """语义搜索相关记忆"""
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
        """清空记忆"""
        if not self._collection:
            return
        try:
            if user_id:
                # ChromaDB 不直接支持按metadata删除，需要重建
                all_data = self._collection.get()
                ids_to_keep = []
                ids_to_delete = []
                for i, meta in enumerate(all_data["metadatas"]):
                    if meta.get("user_id") == user_id:
                        ids_to_delete.append(all_data["ids"][i])
                    else:
                        ids_to_keep.append(all_data["ids"][i])
                if ids_to_delete:
                    self._collection.delete(ids=ids_to_delete)
            else:
                # 删除所有
                import chromadb
                client = chromadb.PersistentClient(path="data/chroma")
                client.delete_collection(self._collection_name)
                self._collection = client.get_or_create_collection(
                    name=self._collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
        except Exception as e:
            logger.error(f"清空记忆失败: {e}")
```

- [ ] **Step 5: 测试记忆系统**

创建 `tests/test_memory.py`:

```python
"""记忆系统测试"""
from memory.entity_memory import EntityMemory
from memory.short_term import ShortTermMemory
from core.session import Session


def test_entity_memory_store_and_query():
    mem = EntityMemory()
    mem.store("user1", "preference", "喜欢的语言", "Python")
    mem.store("user1", "fact", "职业", "程序员")
    mem.store("user2", "preference", "喜欢的颜色", "蓝色")

    results = mem.query("user1")
    assert len(results) == 2

    prefs = mem.query("user1", "preference")
    assert len(prefs) == 1
    assert prefs[0].value == "Python"


def test_entity_memory_update():
    mem = EntityMemory()
    mem.store("user1", "preference", "喜欢的语言", "Python")
    mem.store("user1", "preference", "喜欢的语言", "Go")
    results = mem.query("user1", "preference")
    assert len(results) == 1
    assert results[0].value == "Go"


def test_entity_memory_forget():
    mem = EntityMemory()
    mem.store("user1", "fact", "职业", "程序员")
    assert mem.forget("user1", "职业") is True
    assert len(mem.query("user1")) == 0


def test_entity_memory_profile():
    mem = EntityMemory()
    mem.store("user1", "preference", "语言", "Python")
    mem.store("user1", "fact", "职业", "程序员")
    profile = mem.get_profile_text("user1")
    assert "Python" in profile
    assert "程序员" in profile


def test_short_term_memory():
    stm = ShortTermMemory(max_messages=5)
    session = Session(session_id="test:1", user_id="1", platform="test")
    for i in range(10):
        session.add_message("user", f"msg{i}")
    assert stm.trim_if_needed(session) is True
```

```bash
python -m pytest tests/test_memory.py -v
```

- [ ] **Step 6: Commit**

```bash
git add memory/ tests/test_memory.py && git commit -m "feat: 3-layer memory system (short-term, entity, long-term)"
```

---

### Task 11: Prompt 构建器

**Files:**
- Create: `core/prompt_builder.py`

- [ ] **Step 1: 创建 core/prompt_builder.py**

```python
"""Prompt 构建器 - 组装所有模块输出为最终prompt"""
from typing import Dict, List, Optional

from characters.manager import CharacterCard
from styles.engine import Style
from memory.entity_memory import EntityMemory


class PromptBuilder:
    def __init__(self, entity_memory: Optional[EntityMemory] = None):
        self.entity_memory = entity_memory

    def build(
        self,
        character: CharacterCard,
        style: Style,
        user_message: str,
        history: List[Dict[str, str]],
        memory_results: Optional[List[Dict]] = None,
        user_id: str = "",
    ) -> List[Dict[str, str]]:
        """构建完整的LLM消息列表"""
        messages = []

        # 1. System prompt: 角色卡 + 风格
        system_content = character.get_system_prompt()
        if style and style.prompt_modifier:
            system_content += f"\n\n回复风格要求：{style.prompt_modifier}"

        # 2. 注入用户画像
        if self.entity_memory and user_id:
            profile = self.entity_memory.get_profile_text(user_id)
            if profile:
                system_content += f"\n\n{profile}"

        # 3. 注入相关记忆
        if memory_results:
            memory_text = "\n".join(f"- {m['text']}" for m in memory_results[:5])
            system_content += f"\n\n相关记忆:\n{memory_text}"

        messages.append({"role": "system", "content": system_content})

        # 4. 对话历史
        messages.extend(history)

        # 5. 当前用户消息
        messages.append({"role": "user", "content": user_message})

        return messages

    def build_with_examples(
        self,
        character: CharacterCard,
        style: Style,
        user_message: str,
        history: List[Dict[str, str]],
        memory_results: Optional[List[Dict]] = None,
        user_id: str = "",
    ) -> List[Dict[str, str]]:
        """构建带few-shot示例的prompt"""
        messages = self.build(character, style, user_message, history, memory_results, user_id)

        # 在system和history之间插入示例
        if character.examples:
            examples = []
            for ex in character.examples:
                examples.append({"role": "user", "content": ex.get("user", "")})
                examples.append({"role": "assistant", "content": ex.get("assistant", "")})
            # 插入到system之后
            messages = [messages[0]] + examples + messages[1:]

        return messages
```

- [ ] **Step 2: 测试Prompt构建器**

创建 `tests/test_prompt_builder.py`:

```python
"""Prompt构建器测试"""
from core.prompt_builder import PromptBuilder
from characters.manager import CharacterCard
from styles.engine import Style
from memory.entity_memory import EntityMemory


def test_build_basic_prompt():
    builder = PromptBuilder()
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}。规则:\n{{rules}}",
        "rules": ["说中文"],
    })
    style = Style({"name": "casual", "prompt_modifier": "轻松随意"})
    messages = builder.build(card, style, "你好", [], user_id="u1")

    assert messages[0]["role"] == "system"
    assert "助手" in messages[0]["content"]
    assert "轻松随意" in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "你好"


def test_build_with_memory():
    builder = PromptBuilder()
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    memory_results = [
        {"text": "用户喜欢Python", "metadata": {}},
        {"text": "用户是程序员", "metadata": {}},
    ]
    messages = builder.build(card, style, "推荐个框架", [], memory_results, "u1")
    assert "用户喜欢Python" in messages[0]["content"]


def test_build_with_entity_memory():
    em = EntityMemory()
    em.store("u1", "preference", "语言", "Python")
    builder = PromptBuilder(entity_memory=em)
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    messages = builder.build(card, style, "你好", [], user_id="u1")
    assert "Python" in messages[0]["content"]


def test_build_with_examples():
    builder = PromptBuilder()
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}",
        "examples": [{"user": "hi", "assistant": "hello!"}],
    })
    style = Style({"name": "casual", "prompt_modifier": ""})
    messages = builder.build_with_examples(card, style, "你好", [], user_id="u1")
    assert any(m["content"] == "hi" for m in messages)
    assert any(m["content"] == "hello!" for m in messages)
```

```bash
python -m pytest tests/test_prompt_builder.py -v
```

- [ ] **Step 3: Commit**

```bash
git add core/prompt_builder.py tests/test_prompt_builder.py && git commit -m "feat: prompt builder assembling character, style, memory"
```

---

### Task 12: 消息路由

**Files:**
- Create: `core/router.py`

- [ ] **Step 1: 创建 core/router.py**

```python
"""消息路由 - 去重、限流、指令检测、分发"""
import time
from typing import Dict, Optional, Tuple

from platforms.models import UnifiedMessage
from core.command_parser import CommandParser, ParsedCommand
from utils.rate_limiter import RateLimiter
from utils.logger import setup_logger

logger = setup_logger("router")


class MessageRouter:
    def __init__(self, command_prefix: str = "/", rate_limit_enabled: bool = True,
                 max_per_minute: int = 20, max_per_hour: int = 200,
                 allowed_users: list = None, admin_users: list = None):
        self.command_parser = CommandParser(prefix=command_prefix)
        self.rate_limiter = RateLimiter(max_per_minute, max_per_hour) if rate_limit_enabled else None
        self.allowed_users = allowed_users or []
        self.admin_users = admin_users or []
        self._recent_messages: Dict[str, float] = {}
        self._dedup_window = 5  # 5秒内相同消息视为重复

    def route(self, msg: UnifiedMessage) -> Tuple[bool, str, Optional[ParsedCommand]]:
        """
        路由消息，返回 (should_process, reason, parsed_command)
        """
        # 1. 权限检查
        if self.allowed_users and msg.user_id not in self.allowed_users:
            return False, "未授权用户", None

        # 2. 去重检查
        dedup_key = f"{msg.user_id}:{msg.content}:{msg.group_id}"
        now = time.time()
        if dedup_key in self._recent_messages:
            if now - self._recent_messages[dedup_key] < self._dedup_window:
                logger.debug(f"重复消息，跳过: {msg.content[:30]}")
                return False, "重复消息", None
        self._recent_messages[dedup_key] = now

        # 清理过期去重记录
        if len(self._recent_messages) > 1000:
            cutoff = now - self._dedup_window * 2
            self._recent_messages = {k: v for k, v in self._recent_messages.items() if v > cutoff}

        # 3. 频率限制
        if self.rate_limiter:
            allowed, reason = self.rate_limiter.is_allowed(msg.user_id)
            if not allowed:
                return False, reason, None

        # 4. 指令检测
        parsed = self.command_parser.parse(msg.content)

        logger.info(f"路由消息: user={msg.user_id} cmd={parsed.command if parsed.is_command else 'none'}")
        return True, "", parsed

    def is_admin(self, user_id: str) -> bool:
        return user_id in self.admin_users
```

- [ ] **Step 2: 测试消息路由**

创建 `tests/test_router.py`:

```python
"""消息路由测试"""
from core.router import MessageRouter
from platforms.models import UnifiedMessage, Platform


def _make_msg(user_id="u1", content="hello", group_id=None):
    return UnifiedMessage(platform=Platform.WECHAT, user_id=user_id, content=content, group_id=group_id)


def test_route_normal_message():
    router = MessageRouter(rate_limit_enabled=False)
    ok, reason, cmd = router.route(_make_msg(content="你好"))
    assert ok is True
    assert cmd.is_command is False


def test_route_command():
    router = MessageRouter(rate_limit_enabled=False)
    ok, reason, cmd = router.route(_make_msg(content="/help"))
    assert ok is True
    assert cmd.is_command is True
    assert cmd.command == "help"


def test_route_dedup():
    router = MessageRouter(rate_limit_enabled=False)
    ok1, _, _ = router.route(_make_msg(content="same"))
    ok2, _, _ = router.route(_make_msg(content="same"))
    assert ok1 is True
    assert ok2 is False


def test_route_allowed_users():
    router = MessageRouter(rate_limit_enabled=False, allowed_users=["u1"])
    ok1, _, _ = router.route(_make_msg(user_id="u1"))
    ok2, _, _ = router.route(_make_msg(user_id="u2"))
    assert ok1 is True
    assert ok2 is False


def test_route_rate_limit():
    router = MessageRouter(rate_limit_enabled=True, max_per_minute=2, max_per_hour=100)
    ok1, _, _ = router.route(_make_msg(content="msg1"))
    ok2, _, _ = router.route(_make_msg(content="msg2"))
    ok3, reason, _ = router.route(_make_msg(content="msg3"))
    assert ok1 is True
    assert ok2 is True
    assert ok3 is False
    assert "频繁" in reason
```

```bash
python -m pytest tests/test_router.py -v
```

- [ ] **Step 3: Commit**

```bash
git add core/router.py tests/test_router.py && git commit -m "feat: message router with dedup, rate limit, command detection"
```

---

### Task 13: 核心引擎

**Files:**
- Create: `core/engine.py`

- [ ] **Step 1: 创建 core/engine.py**

```python
"""核心引擎 - 协调所有模块"""
from typing import Optional

from utils.config import AppConfig
from utils.logger import setup_logger
from utils.message_splitter import split_message
from platforms.models import UnifiedMessage
from core.session import SessionManager, Session
from core.router import MessageRouter
from core.prompt_builder import PromptBuilder
from core.command_parser import ParsedCommand, CommandResult
from characters.manager import CharacterManager
from styles.engine import StyleEngine
from llm.client import DeepSeekClient
from memory.short_term import ShortTermMemory
from memory.entity_memory import EntityMemory
from memory.long_term import LongTermMemory
from memory.summarizer import ConversationSummarizer

logger = setup_logger("engine")


class Engine:
    def __init__(self, config: AppConfig):
        self.config = config

        # 初始化各模块
        self.character_manager = CharacterManager()
        self.style_engine = StyleEngine()
        self.session_manager = SessionManager(
            max_messages=config.memory.short_term.max_messages,
            summarize_after=config.memory.short_term.summarize_after,
        )
        self.router = MessageRouter(
            command_prefix=config.bot.command_prefix,
            rate_limit_enabled=config.rate_limit.enabled,
            max_per_minute=config.rate_limit.max_per_minute,
            max_per_hour=config.rate_limit.max_per_hour,
            admin_users=config.security.admin_users,
        )
        self.entity_memory = EntityMemory()
        self.long_term_memory = LongTermMemory(top_k=config.memory.long_term.top_k) if config.memory.long_term.enabled else None
        self.short_term_memory = ShortTermMemory(max_messages=config.memory.short_term.max_messages)
        self.prompt_builder = PromptBuilder(entity_memory=self.entity_memory)
        self.summarizer = ConversationSummarizer()

        self._llm: Optional[DeepSeekClient] = None

    def _get_llm(self) -> DeepSeekClient:
        if self._llm is None:
            self._llm = DeepSeekClient(
                api_key=self.config.llm.api_key,
                base_url=self.config.llm.base_url,
                model=self.config.llm.model,
                fallback_model=self.config.llm.fallback_model,
                max_tokens=self.config.llm.max_tokens,
                temperature=self.config.llm.temperature,
                timeout=self.config.llm.timeout,
                max_retries=self.config.llm.max_retries,
            )
        return self._llm

    async def process_message(self, msg: UnifiedMessage) -> Optional[str]:
        """处理一条消息，返回回复文本"""
        # 1. 路由
        should_process, reason, parsed = self.router.route(msg)
        if not should_process:
            if reason == "重复消息":
                return None
            return reason  # 返回限流等提示

        # 2. 指令处理
        if parsed and parsed.is_command:
            return await self._handle_command(msg, parsed)

        # 3. LLM对话
        return await def process_message(self, msg: UnifiedMessage) -> Optional[str]:
        """处理一条消息，返回回复文本"""
        # 1. 路由
        should_process, reason, parsed = self.router.route(msg)
        if not should_process:
            if reason == "重复消息":
                return None
            return reason  # 返回限流等提示

        # 2. 指令处理
        if parsed and parsed.is_command:
            return await self._handle_command(msg, parsed)

        # 3. LLM对话
        return await self._handle_chat(msg)

    async def _handle_chat(self, msg: UnifiedMessage) -> str:
        """处理普通对话"""
        session = self.session_manager.get_or_create(
            msg,
            default_character=self.config.bot.default_character,
            default_style=self.config.bot.default_style,
        )

        # 获取角色卡和风格
        character = self.character_manager.get(session.character_card) or self.character_manager.get_default()
        style = self.style_engine.get(session.style) or self.style_engine.get_default()

        # 检索记忆
        memory_results = []
        if self.long_term_memory:
            memory_results = self.long_term_memory.search(msg.content, user_id=msg.user_id)

        # 构建prompt
        history = session.get_context_messages(self.config.memory.short_term.max_messages)
        messages = self.prompt_builder.build(
            character=character,
            style=style,
            user_message=msg.content,
            history=history,
            memory_results=memory_results,
            user_id=msg.user_id,
        )

        # 调用LLM
        llm = self._get_llm()
        response = await llm.chat(messages)

        # 保存对话
        session.add_message("user", msg.content)
        session.add_message("assistant", response)

        # 异步存储到长期记忆
        if self.long_term_memory:
            self.long_term_memory.store(
                text=f"用户: {msg.content}\n助手: {response}",
                metadata={"user_id": msg.user_id, "session": session.session_id},
            )

        # 自动提取实体记忆
        if self.config.memory.entity.auto_extract:
            await self._extract_entities(msg.user_id, msg.content, response)

        # 检查是否需要摘要
        if self.session_manager.should_summarize(session):
            await self._summarize_session(session)

        # 拆分长消息
        max_len = self.config.security.max_message_length
        chunks = split_message(response, max_len)
        return "\n\n".join(chunks)  # 单条返回，平台层负责拆分发送

    async def _handle_command(self, msg: UnifiedMessage, parsed: ParsedCommand) -> str:
        """处理指令"""
        session = self.session_manager.get_or_create(
            msg,
            default_character=self.config.bot.default_character,
            default_style=self.config.bot.default_style,
        )

        cmd = parsed.command
        sub = parsed.subcommand

        if cmd == "help":
            return self._help_text()

        elif cmd == "new":
            self.session_manager.clear(msg.session_key)
            return "已开启新会话。"

        elif cmd == "undo":
            session.undo_last_exchange()
            return "已撤销上一轮对话。"

        elif cmd == "card":
            return self._handle_card_command(session, sub, parsed.args)

        elif cmd == "style":
            return self._handle_style_command(session, sub, parsed.args)

        elif cmd == "memory":
            return await self._handle_memory_command(msg, sub, parsed.args)

        elif cmd == "learn":
            return await self._handle_learn_command(session, sub, parsed.args)

        elif cmd == "context":
            msg_count = len(session.messages)
            return f"当前会话: {msg_count} 条消息\n角色卡: {session.character_card}\n风格: {session.style}"

        elif cmd == "token":
            total = sum(m.token_count for m in session.messages)
            return f"当前会话 token 估算: {total}"

        elif cmd == "history":
            n = int(parsed.args[0]) if parsed.args else 10
            recent = session.get_recent_messages(n)
            if not recent:
                return "暂无对话历史"
            lines = []
            for m in recent:
                role = "我" if m["role"] == "user" else "AI"
                lines.append(f"[{role}] {m['content'][:100]}")
            return "\n".join(lines)

        return f"未知指令: /{cmd}，输入 /help 查看帮助"

    def _handle_card_command(self, session: Session, sub: str, args: list) -> str:
        if sub == "list":
            cards = self.character_manager.list_cards()
            current = session.character_card
            lines = [f"{'→ ' if c == current else '  '}{c}" for c in cards]
            return "角色卡列表:\n" + "\n".join(lines)

        elif sub == "use" and args:
            name = args[0]
            if self.character_manager.get(name):
                session.character_card = name
                return f"已切换到角色卡: {name}"
            return f"角色卡不存在: {name}"

        elif sub == "create" and args:
            name = args[0]
            from characters.manager import CharacterCard
            card = CharacterCard({"name": name, "system_prompt": f"你是{name}。", "rules": ["以中文输出为主"]})
            self.character_manager.save(name, card)
            return f"已创建角色卡: {name}"

        return "用法: /card list | /card use <名称> | /card create <名称>"

    def _handle_style_command(self, session: Session, sub: str, args: list) -> str:
        if sub == "list":
            styles = self.style_engine.list_styles()
            current = session.style
            lines = [f"{'→ ' if s == current else '  '}{s}" for s in styles]
            return "风格列表:\n" + "\n".join(lines)

        elif sub == "use" and args:
            name = args[0]
            if self.style_engine.get(name):
                session.style = name
                return f"已切换风格: {name}"
            return f"风格不存在: {name}"

        return "用法: /style list | /style use <名称>"

    async def _handle_memory_command(self, msg: UnifiedMessage, sub: str, args: list) -> str:
        if sub == "profile":
            profile = self.entity_memory.get_profile_text(msg.user_id)
            return profile or "暂无用户画像"

        elif sub == "recall" and args:
            query = " ".join(args)
            if self.long_term_memory:
                results = self.long_term_memory.search(query, user_id=msg.user_id)
                if results:
                    return "\n".join(f"- {r['text'][:100]}" for r in results)
            return "未找到相关记忆"

        elif sub == "forget" and args:
            key = " ".join(args)
            if self.entity_memory.forget(msg.user_id, key):
                return f"已删除记忆: {key}"
            return f"未找到记忆: {key}"

        elif sub == "clear":
            self.entity_memory.clear(msg.user_id)
            if self.long_term_memory:
                self.long_term_memory.clear(msg.user_id)
            return "已清空所有记忆"

        return "用法: /memory profile | /memory recall <关键词> | /memory forget <主题> | /memory clear"

    async def _handle_learn_command(self, session: Session, sub: str, args: list) -> str:
        if sub == "now":
            return "学习任务已触发，请稍候..."  # 实际实现在 Task 16
        elif sub == "status":
            return f"当前角色卡: {session.character_card}\n学习间隔: {self.config.learning.trigger_interval} 条对话"
        return "用法: /learn now | /learn status"

    async def _extract_entities(self, user_id: str, user_msg: str, assistant_msg: str):
        """LLM自动提取实体记忆"""
        try:
            llm = self._get_llm()
            prompt = [
                {"role": "system", "content": "从对话中提取用户信息，返回JSON格式: {\"entities\": [{\"type\": \"preference/fact/skill/person\", \"key\": \"...\", \"value\": \"...\"}]}。如果没有值得记录的信息，返回空列表。只返回JSON。"},
                {"role": "user", "content": f"用户: {user_msg}\n助手: {assistant_msg}"},
            ]
            result = await llm.chat(prompt)
            import json
            data = json.loads(result.strip().strip("```json").strip("```"))
            for entity in data.get("entities", []):
                self.entity_memory.store(
                    user_id=user_id,
                    entity_type=entity.get("type", "fact"),
                    key=entity.get("key", ""),
                    value=entity.get("value", ""),
                )
        except Exception as e:
            logger.debug(f"实体提取失败(可忽略): {e}")

    async def _summarize_session(self, session: Session):
        """摘要压缩会话"""
        try:
            old_messages = session.get_context_messages()
            summary = await self.summarizer.summarize(old_messages)
            session.summary = summary
            # 保留最近的消息，清理旧的
            keep = self.config.memory.short_term.max_messages // 2
            session.messages = session.messages[-keep:]
            logger.info(f"会话 {session.session_id} 已摘要，保留 {keep} 条消息")
        except Exception as e:
            logger.error(f"摘要失败: {e}")

    def _help_text(self) -> str:
        return """可用指令:
/card list - 列出角色卡
/card use <名称> - 切换角色卡
/style list - 列出风格
/style use <名称> - 切换风格
/memory profile - 查看用户画像
/memory recall <关键词> - 搜索记忆
/memory clear - 清空记忆
/learn now - 触发学习
/context - 查看会话状态
/history [n] - 查看历史
/undo - 撤销上轮对话
/new - 新会话
/help - 帮助"""
```

- [ ] **Step 2: 测试核心引擎**

创建 `tests/test_engine.py`:

```python
"""核心引擎测试"""
import pytest
from unittest.mock import AsyncMock, patch
from core.engine import Engine
from utils.config import AppConfig
from platforms.models import UnifiedMessage, Platform


def _make_msg(content, user_id="u1"):
    return UnifiedMessage(platform=Platform.WECHAT, user_id=user_id, content=content)


@pytest.fixture
def engine():
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    return Engine(cfg)


def test_engine_init(engine):
    assert engine.character_manager is not None
    assert engine.style_engine is not None
    assert engine.session_manager is not None


@pytest.mark.asyncio
async def test_engine_help_command(engine):
    result = await engine.process_message(_make_msg("/help"))
    assert "可用指令" in result


@pytest.mark.asyncio
async def test_engine_new_command(engine):
    result = await engine.process_message(_make_msg("/new"))
    assert "新会话" in result


@pytest.mark.asyncio
async def test_engine_card_list(engine):
    result = await engine.process_message(_make_msg("/card list"))
    assert "角色卡列表" in result


@pytest.mark.asyncio
async def test_engine_style_list(engine):
    result = await engine.process_message(_make_msg("/style list"))
    assert "风格列表" in result


@pytest.mark.asyncio
async def test_engine_context(engine):
    result = await engine.process_message(_make_msg("/context"))
    assert "当前会话" in result


@pytest.mark.asyncio
async def test_engine_chat_calls_llm(engine):
    with patch.object(engine, "_get_llm") as mock_get_llm:
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = "测试回复"
        mock_get_llm.return_value = mock_llm

        result = await engine.process_message(_make_msg("你好"))
        assert result == "测试回复"
        mock_llm.chat.assert_called_once()
```

```bash
python -m pytest tests/test_engine.py -v
```

- [ ] **Step 3: Commit**

```bash
git add core/engine.py tests/test_engine.py && git commit -m "feat: core engine coordinating all modules"
```

---

### Task 14: 平台适配 - 微信

**Files:**
- Create: `platforms/wechat.py`

- [ ] **Step 1: 创建 platforms/wechat.py**

```python
"""微信平台适配器 (WeChatFerry)"""
import asyncio
from typing import Optional

from platforms.base import BasePlatform, MessageCallback
from platforms.models import UnifiedMessage, Platform, MessageType
from utils.logger import setup_logger

logger = setup_logger("wechat")


class WeChatPlatform(BasePlatform):
    name = "wechat"

    def __init__(self, config: dict):
        super().__init__(config)
        self._wcf = None
        self._running = False

    async def start(self):
        """启动微信消息监听"""
        try:
            from wcferry import Wcf

            self._wcf = Wcf()
            logger.info("WeChatFerry 初始化成功")

            self._running = True
            # 在线程中运行消息接收循环
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, self._message_loop)

        except ImportError:
            logger.error("wcferry 未安装，请运行: pip install wcferry")
            raise
        except Exception as e:
            logger.error(f"微信启动失败: {e}")
            raise

    def _message_loop(self):
        """消息接收循环（阻塞，在线程中运行）"""
        while self._running:
            try:
                if self._wcf and self._wcf.msgs:
                    msg = self._wcf.msgs.get(timeout=1)
                    if msg:
                        asyncio.run_coroutine_threadsafe(
                            self._process_raw_message(msg),
                            asyncio.get_event_loop(),
                        )
            except Exception:
                continue

    async def _process_raw_message(self, raw_msg):
        """将原始微信消息转为UnifiedMessage"""
        try:
            # WeChatFerry消息结构
            content = raw_msg.content or ""
            sender = raw_msg.sender or ""
            roomid = raw_msg.roomid or ""
            msg_type = raw_msg.type

            if msg_type != 1:  # 只处理文本消息
                return

            # 过滤自己发的消息
            if sender == self._wcf.self_wxid:
                return

            unified = UnifiedMessage(
                platform=Platform.WECHAT,
                user_id=sender,
                content=content,
                message_type=MessageType.TEXT,
                group_id=roomid if roomid else None,
                message_id=str(raw_msg.id),
            )

            if self._callback:
                response = await self._callback(unified)
                if response:
                    target = roomid if roomid else sender
                    await self.send_message(target, response)

        except Exception as e:
            logger.error(f"处理微信消息失败: {e}")

    async def stop(self):
        self._running = False
        if self._wcf:
            self._wcf.disable_recv_msg()
            logger.info("微信已停止")

    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        """发送消息"""
        if not self._wcf:
            logger.error("WeChatFerry未初始化")
            return

        try:
            target = group_id or user_id
            # WeChatFerry发送消息
            self._wcf.send_text(content, target)
            logger.debug(f"微信消息已发送: {target} <- {content[:50]}")
        except Exception as e:
            logger.error(f"发送微信消息失败: {e}")

    async def send_image(self, user_id: str, image_path: str, group_id: Optional[str] = None):
        if not self._wcf:
            return
        try:
            target = group_id or user_id
            self._wcf.send_image(image_path, target)
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add platforms/wechat.py && git commit -m "feat: WeChat platform adapter via WeChatFerry"
```

---

### Task 15: 平台适配 - QQ

**Files:**
- Create: `platforms/qq.py`

- [ ] **Step 1: 创建 platforms/qq.py**

```python
"""QQ平台适配器 (NapCat/OneBot11)"""
import asyncio
import json
from typing import Optional

import httpx

from platforms.base import BasePlatform, MessageCallback
from platforms.models import UnifiedMessage, Platform, MessageType
from utils.logger import setup_logger

logger = setup_logger("qq")


class QQPlatform(BasePlatform):
    name = "qq"

    def __init__(self, config: dict):
        super().__init__(config)
        self.onebot_url = config.get("onebot_url", "ws://127.0.0.1:3001")
        self._http_base = self.onebot_url.replace("ws://", "http://").replace("wss://", "https://")
        self._running = False
        self._ws = None

    async def start(self):
        """启动QQ消息监听（HTTP轮询模式）"""
        self._running = True
        loop = asyncio.get_event_loop()
        loop.create_task(self._poll_loop())
        logger.info(f"QQ适配器启动，连接: {self.onebot_url}")

    async def _poll_loop(self):
        """HTTP轮询获取消息"""
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    resp = await client.get(
                        f"{self._http_base}/get_new_msg",
                        params={"count": 10},
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for msg in data.get("data", []):
                            await self._process_raw_message(msg)
                except httpx.ConnectError:
                    logger.warning("无法连接到NapCat，等待重试...")
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"QQ轮询错误: {e}")
                    await asyncio.sleep(1)

    async def _process_raw_message(self, raw: dict):
        """处理OneBot11格式消息"""
        try:
            post_type = raw.get("post_type")
            if post_type != "message":
                return

            message = raw.get("message", "")
            if isinstance(message, list):
                # OneBot11消息段格式
                text_parts = [seg.get("data", {}).get("text", "") for seg in message if seg.get("type") == "text"]
                message = "".join(text_parts)

            if not message.strip():
                return

            user_id = str(raw.get("user_id", ""))
            group_id = str(raw.get("group_id", "")) if raw.get("message_type") == "group" else None

            unified = UnifiedMessage(
                platform=Platform.QQ,
                user_id=user_id,
                content=message,
                message_type=MessageType.TEXT,
                group_id=group_id,
                nickname=raw.get("sender", {}).get("nickname", ""),
                message_id=str(raw.get("message_id", "")),
            )

            if self._callback:
                response = await self._callback(unified)
                if response:
                    await self.send_message(user_id, response, group_id)

        except Exception as e:
            logger.error(f"处理QQ消息失败: {e}")

    async def stop(self):
        self._running = False
        logger.info("QQ适配器已停止")

    async def send_message(self, user_id: str, content: str, group_id: Optional[str] = None):
        """通过OneBot11 HTTP API发送消息"""
        async with httpx.AsyncClient() as client:
            try:
                if group_id:
                    endpoint = "/send_group_msg"
                    payload = {"group_id": int(group_id), "message": content}
                else:
                    endpoint = "/send_private_msg"
                    payload = {"user_id": int(user_id), "message": content}

                resp = await client.post(
                    f"{self._http_base}{endpoint}",
                    json=payload,
                    timeout=10,
                )
                if resp.status_code != 200:
                    logger.error(f"发送QQ消息失败: {resp.text}")
                else:
                    logger.debug(f"QQ消息已发送: {group_id or user_id}")
            except Exception as e:
                logger.error(f"发送QQ消息异常: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add platforms/qq.py && git commit -m "feat: QQ platform adapter via NapCat/OneBot11"
```

---

### Task 16: 自学习系统

**Files:**
- Create: `learning/analyzer.py`
- Create: `learning/evolution.py`
- Create: `learning/scheduler.py`

- [ ] **Step 1: 创建 learning/analyzer.py**

```python
"""学习分析器 - 分析对话模式提取人设演化建议"""
from typing import Dict, List

from utils.logger import setup_logger

logger = setup_logger("learning.analyzer")


class LearningAnalyzer:
    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def analyze(self, conversations: List[Dict]) -> Dict:
        """分析对话记录，提取人设演化建议"""
        if not self._llm or not conversations:
            return {}

        conv_text = self._format_conversations(conversations)

        prompt = [
            {"role": "system", "content": """分析以下对话记录，提取AI助手的人设演化建议。
返回JSON格式:
{
    "communication_style": ["建议1", "建议2"],
    "topic_expertise": ["建议1"],
    "behavioral_patterns": ["建议1"],
    "emotional_intelligence": ["建议1"],
    "rules_to_add": ["新规则1"],
    "summary": "一句话总结"
}
只返回JSON，不要其他内容。"""},
            {"role": "user", "content": conv_text},
        ]

        try:
            result = await self._llm.chat(prompt)
            import json
            return json.loads(result.strip().strip("```json").strip("```"))
        except Exception as e:
            logger.error(f"学习分析失败: {e}")
            return {}

    def _format_conversations(self, conversations: List[Dict]) -> str:
        lines = []
        for conv in conversations[-200:]:  # 最多分析200条
            role = "用户" if conv.get("role") == "user" else "助手"
            content = conv.get("content", "")[:200]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
```

- [ ] **Step 2: 创建 learning/evolution.py**

```python
"""角色卡演化管理"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("learning.evolution")


class EvolutionManager:
    def __init__(self, cards_dir: str = "characters/cards", learning_dir: str = "data/learning"):
        self.cards_dir = Path(cards_dir)
        self.learning_dir = Path(learning_dir)
        self.learning_dir.mkdir(parents=True, exist_ok=True)

    def backup_card(self, card_name: str) -> str:
        """备份角色卡"""
        src = self.cards_dir / f"{card_name}.yaml"
        if not src.exists():
            return ""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = self.learning_dir / f"{card_name}_backup_{timestamp}.yaml"
        shutil.copy2(src, dst)
        logger.info(f"角色卡已备份: {dst}")
        return str(dst)

    def apply_learned_traits(self, card_name: str, traits: Dict) -> bool:
        """将学习结果应用到角色卡"""
        card_path = self.cards_dir / f"{card_name}.yaml"
        if not card_path.exists():
            logger.error(f"角色卡不存在: {card_name}")
            return False

        try:
            with open(card_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # 合并学习特质（不覆盖原始设定）
            existing_traits = data.get("learned_traits", {})
            for key, values in traits.items():
                if key == "summary":
                    continue
                if isinstance(values, list):
                    if key in existing_traits and isinstance(existing_traits[key], list):
                        # 去重合并
                        combined = list(set(existing_traits[key] + values))
                        existing_traits[key] = combined[:20]  # 限制每个类别最多20条
                    else:
                        existing_traits[key] = values

            existing_traits["last_updated"] = datetime.now().isoformat()
            existing_traits["learning_version"] = existing_traits.get("learning_version", 0) + 1
            data["learned_traits"] = existing_traits

            with open(card_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

            logger.info(f"角色卡 {card_name} 已更新学习特质")
            return True
        except Exception as e:
            logger.error(f"应用学习结果失败: {e}")
            return False

    def save_report(self, card_name: str, report: Dict):
        """保存学习报告"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.learning_dir / f"report_{card_name}_{timestamp}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(report, f, allow_unicode=True, default_flow_style=False)
        logger.info(f"学习报告已保存: {path}")

    def get_evolution_history(self, card_name: str) -> list:
        """获取角色卡演化历史"""
        reports = []
        for f in sorted(self.learning_dir.glob(f"report_{card_name}_*.yaml")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    reports.append(yaml.safe_load(fp))
            except Exception:
                continue
        return reports

    def reset_card(self, card_name: str, backup_name: Optional[str] = None) -> bool:
        """重置角色卡到初始状态（清除学习特质）"""
        card_path = self.cards_dir / f"{card_name}.yaml"
        if not card_path.exists():
            return False

        try:
            with open(card_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data.pop("learned_traits", None)
            with open(card_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            return True
        except Exception as e:
            logger.error(f"重置失败: {e}")
            return False
```

- [ ] **Step 3: 创建 learning/scheduler.py**

```python
"""学习调度器"""
import asyncio
from datetime import datetime, time
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from utils.logger import setup_logger

logger = setup_logger("learning.scheduler")


class LearningScheduler:
    def __init__(self, trigger_interval: int = 50, trigger_schedule: str = "03:00"):
        self.trigger_interval = trigger_interval
        self.trigger_schedule = trigger_schedule
        self._scheduler = AsyncIOScheduler()
        self._learn_callback: Optional[Callable] = None
        self._conversation_count = 0

    def set_learn_callback(self, callback: Callable):
        """设置学习回调函数"""
        self._learn_callback = callback

    def start(self):
        """启动调度器"""
        # 定时触发
        hour, minute = self.trigger_schedule.split(":")
        self._scheduler.add_job(
            self._trigger_learn,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id="scheduled_learn",
            name="定时学习",
        )
        self._scheduler.start()
        logger.info(f"学习调度器启动，定时: {self.trigger_schedule}")

    def stop(self):
        self._scheduler.shutdown()
        logger.info("学习调度器已停止")

    def on_conversation_end(self):
        """对话结束时调用，检查是否达到定量触发条件"""
        self._conversation_count += 1
        if self._conversation_count >= self.trigger_interval:
            self._conversation_count = 0
            asyncio.create_task(self._trigger_learn())

    async def _trigger_learn(self):
        """触发学习任务"""
        if self._learn_callback:
            logger.info("触发学习任务...")
            try:
                await self._learn_callback()
            except Exception as e:
                logger.error(f"学习任务执行失败: {e}")
```

- [ ] **Step 4: Commit**

```bash
git add learning/ && git commit -m "feat: self-learning system with analyzer, evolution, scheduler"
```

---

### Task 17: 主入口

**Files:**
- Create: `main.py`

- [ ] **Step 1: 创建 main.py**

```python
"""聊天机器人主入口"""
import asyncio
import signal
import sys

from utils.config import load_config
from utils.logger import setup_logger
from core.engine import Engine
from platforms.wechat import WeChatPlatform
from platforms.qq import QQPlatform
from learning.scheduler import LearningScheduler
from learning.analyzer import LearningAnalyzer
from learning.evolution import EvolutionManager
from database.db import init_db

logger = setup_logger("main")


class ChatBot:
    def __init__(self):
        self.config = load_config()
        self.engine = Engine(self.config)
        self.platforms = []
        self.learning_scheduler = None

        # 注入LLM到学习模块
        if self.config.learning.enabled:
            analyzer = LearningAnalyzer(llm_client=self.engine._get_llm())
            evolution = EvolutionManager()
            self.learning_scheduler = LearningScheduler(
                trigger_interval=self.config.learning.trigger_interval,
                trigger_schedule=self.config.learning.trigger_schedule,
            )

    async def start(self):
        """启动机器人"""
        logger.info(f"启动 {self.config.bot.name}...")

        # 初始化数据库
        await init_db()

        # 启动平台
        if self.config.platforms.wechat.enabled:
            try:
                wechat = WeChatPlatform(self.config.platforms.wechat.model_dump())
                wechat.on_message(self.engine.process_message)
                await wechat.start()
                self.platforms.append(wechat)
                logger.info("微信平台已启动")
            except Exception as e:
                logger.error(f"微信平台启动失败: {e}")

        if self.config.platforms.qq.enabled:
            try:
                qq = QQPlatform(self.config.platforms.qq.model_dump())
                qq.on_message(self.engine.process_message)
                await qq.start()
                self.platforms.append(qq)
                logger.info("QQ平台已启动")
            except Exception as e:
                logger.error(f"QQ平台启动失败: {e}")

        # 启动学习调度器
        if self.learning_scheduler:
            self.learning_scheduler.start()

        logger.info(f"{self.config.bot.name} 已就绪")

        # 保持运行
        stop_event = asyncio.Event()

        def _signal_handler():
            logger.info("收到停止信号...")
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        await stop_event.wait()
        await self.stop()

    async def stop(self):
        """停止所有服务"""
        logger.info("正在停止...")
        for platform in self.platforms:
            await platform.stop()
        if self.learning_scheduler:
            self.learning_scheduler.stop()
        logger.info("已停止")


async def main():
    bot = ChatBot()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 测试主入口可导入**

```bash
python -c "from main import ChatBot; print('import ok')"
```

- [ ] **Step 3: Commit**

```bash
git add main.py && git commit -m "feat: main entry point with platform orchestration"
```

---

### Task 18: 全量集成测试

- [ ] **Step 1: 运行所有测试**

```bash
cd D:/AAA/chatbot && python -m pytest tests/ -v
```

- [ ] **Step 2: 手动测试指令系统（模拟模式）**

创建 `tests/test_integration.py`:

```python
"""集成测试 - 模拟完整对话流程"""
import pytest
from unittest.mock import AsyncMock, patch
from core.engine import Engine
from utils.config import AppConfig
from platforms.models import UnifiedMessage, Platform


def _msg(content, user_id="u1"):
    return UnifiedMessage(platform=Platform.WECHAT, user_id=user_id, content=content)


@pytest.fixture
def engine():
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    return Engine(cfg)


@pytest.mark.asyncio
async def test_full_conversation_flow(engine):
    """完整对话流程: 问好 -> 切换风格 -> 对话 -> 查看历史"""
    with patch.object(engine, "_get_llm") as mock:
        llm = AsyncMock()
        llm.chat.return_value = "你好！有什么可以帮你的？"
        mock.return_value = llm

        # 1. 普通对话
        resp = await engine.process_message(_msg("你好"))
        assert resp is not None
        assert llm.chat.called

    # 2. 切换风格
    resp = await engine.process_message(_msg("/style use professional"))
    assert "已切换风格" in resp

    # 3. 查看上下文
    resp = await engine.process_message(_msg("/context"))
    assert "当前会话" in resp

    # 4. 查看历史
    resp = await engine.process_message(_msg("/history"))
    assert resp is not None

    # 5. 撤销
    resp = await engine.process_message(_msg("/undo"))
    assert "撤销" in resp

    # 6. 新会话
    resp = await engine.process_message(_msg("/new"))
    assert "新会话" in resp


@pytest.mark.asyncio
async def test_character_switch(engine):
    resp = await engine.process_message(_msg("/card use default"))
    assert "已切换到角色卡" in resp


@pytest.mark.asyncio
async def test_memory_commands(engine):
    resp = await engine.process_message(_msg("/memory profile"))
    assert resp is not None

    resp = await engine.process_message(_msg("/memory clear"))
    assert "已清空" in resp
```

```bash
python -m pytest tests/test_integration.py -v
```

- [ ] **Step 3: 最终 Commit**

```bash
git add tests/test_integration.py && git commit -m "test: integration tests for full conversation flow"
```

---

## 执行顺序总结

| 任务 | 内容 | 依赖 |
|------|------|------|
| 1 | 项目脚手架与配置 | 无 |
| 2 | 数据库层 | 1 |
| 3 | 日志与工具函数 | 1 |
| 4 | 平台抽象层 | 1 |
| 5 | LLM客户端 | 1 |
| 6 | 角色卡系统 | 1 |
| 7 | 风格引擎 | 1 |
| 8 | 会话管理 | 4 |
| 9 | 指令解析器 | 1 |
| 10 | 记忆系统 | 1 |
| 11 | Prompt构建器 | 6, 7, 10 |
| 12 | 消息路由 | 9, 3 |
| 13 | 核心引擎 | 5, 6, 7, 8, 10, 11, 12 |
| 14 | 微信适配器 | 4, 13 |
| 15 | QQ适配器 | 4, 13 |
| 16 | 自学习系统 | 5 |
| 17 | 主入口 | 全部 |
| 18 | 集成测试 | 全部 |

任务1-7可并行执行，任务8-12可并行执行，任务13依赖前面所有，任务14-16可并行。
