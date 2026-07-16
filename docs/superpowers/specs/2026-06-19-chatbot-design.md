# 聊天机器人平台设计文档

## 概述

一个功能完善的聊天机器人平台，支持微信和 QQ 双平台接入，使用 DeepSeek API 作为 LLM 后端。支持角色卡、多风格聊天、长期记忆、指令系统等核心功能。

## 技术栈

| 层级 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.11+ | 生态丰富，DeepSeek SDK 原生支持 |
| Web 框架 | FastAPI | 异步高性能，自带 API 文档 |
| 微信接入 | WeChatFerry (wcferry) | 免费、功能全、Python 原生 SDK |
| QQ 接入 | NapCat + OneBot11 协议 | 现代 QQNT 支持、社区活跃 |
| LLM | DeepSeek API (OpenAI 兼容) | 性价比高、中文能力强 |
| 数据库 | SQLite + aiosqlite | 轻量零配置 |
| 向量存储 | ChromaDB | 嵌入式向量数据库 |
| 缓存 | cachetools | 内存缓存，简单场景无需 Redis |
| 配置 | YAML + Pydantic | 类型安全 |

## 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────┐
│                   用户消息入口                     │
│         WeChat (WeChatFerry)  QQ (NapCat)        │
└──────────────┬──────────────┬────────────────────┘
               │              │
               ▼              ▼
┌─────────────────────────────────────────────────┐
│              消息路由层 (Message Router)           │
│  - 统一消息格式 (UnifiedMessage)                   │
│  - 平台识别 & 用户映射                              │
│  - 消息去重 & 频率限制                              │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              核心处理引擎 (Core Engine)             │
│  ┌───────────┐ ┌───────────┐ ┌───────────────┐  │
│  │ 会话管理器  │ │ 角色卡引擎 │ │  风格引擎      │  │
│  └─────┬─────┘ └─────┬─────┘ └──────┬────────┘  │
│        │             │               │            │
│        ▼             ▼               ▼            │
│  ┌─────────────────────────────────────────┐     │
│  │         Prompt 构建器 (PromptBuilder)     │     │
│  └─────────────────┬───────────────────────┘     │
│                    │                              │
│                    ▼                              │
│  ┌─────────────────────────────────────────┐     │
│  │         LLM 调用层 (DeepSeek API)         │     │
│  └─────────────────┬───────────────────────┘     │
│                    │                              │
│                    ▼                              │
│  ┌─────────────────────────────────────────┐     │
│  │         记忆系统 (Memory System)           │     │
│  │  短期记忆(上下文) + 长期记忆(向量+实体)      │     │
│  └─────────────────────────────────────────┘     │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              响应处理层                            │
│  - 内容后处理                                     │
│  - 消息拆分 (长消息分段发送)                        │
│  - 回复到对应平台                                  │
└─────────────────────────────────────────────────┘
```

### 数据流：消息处理生命周期

1. 用户发送消息 → 平台 SDK 接收
2. 转为 UnifiedMessage → 消息路由层
3. 去重 → 频率限制 → 权限检查 → 指令检测
4. 加载会话 → 加载角色卡 → 加载风格 → 检索记忆
5. 构建 Prompt → 调用 DeepSeek API (流式)
6. 响应后处理 → 长消息拆分 → 发送回复
7. 异步：保存对话 → 提取实体记忆 → 向量化存储

## 核心模块

### 1. 角色卡系统 (Character Card)

角色卡定义机器人的人格、知识背景和行为模式。

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

**指令**：`/card list`、`/card use <名称>`、`/card create <名称>`、`/card edit <字段> <值>`、`/card export`、`/card import <yaml>`

### 2. 聊天风格引擎 (Style Engine)

预设风格 + 自定义风格，影响 LLM 回复方式：

| 风格 | 描述 | 修饰词 |
|------|------|--------|
| casual | 随性闲聊 | 轻松随意的口吻，可用网络用语和表情 |
| professional | 专业严谨 | 专业严谨，逻辑清晰，引用来源 |
| creative | 创意写作 | 富有想象力，善用比喻和修辞 |
| concise | 极简回复 | 最简短的方式，只说重点 |
| socratic | 苏格拉底式 | 通过提问引导思考 |

**指令**：`/style list`、`/style use <名称>`、`/style create <名称> <描述>`、`/style mix <名称1> <名称2>`

### 3. 记忆系统 (Memory System)

三层记忆架构：

- **短期记忆**：当前会话上下文，滑动窗口 + 摘要压缩，存储在内存
- **长期语义记忆**：历史对话向量化存储，基于语义相似度检索，ChromaDB
- **长期实体记忆**：用户画像、偏好、关键事实，结构化 SQLite 存储，LLM 自动提取 + 用户手动修正

**自动记忆提取**：每次对话结束后 LLM 自动分析提取用户信息、重要事实、情绪态度。

**指令**：`/memory recall <关键词>`、`/memory forget <主题>`、`/memory profile`、`/memory export`、`/memory clear`

### 4. 自学习系统 (Self-Learning System)

机器人根据聊天记录定期总结自身人设，持续进化角色卡，无需每次查询历史记录。

**核心机制**：

```
┌─────────────────────────────────────────────────┐
│              自学习循环                            │
│                                                   │
│  聊天记录积累                                     │
│       │                                           │
│       ▼ (触发条件: N条对话 / 定时 / 手动)           │
│  学习分析器 (Learning Analyzer)                    │
│       │                                           │
│       ├── 提取：哪些回复效果好（用户继续对话=好）    │
│       ├── 提取：哪些话题用户最感兴趣                │
│       ├── 提取：什么样的语气/风格用户最喜欢          │
│       ├── 提取：哪些规则需要调整                    │
│       └── 提取：新的知识领域和技能                  │
│       │                                           │
│       ▼                                           │
│  更新角色卡摘要 (不覆盖原始设定，追加演化层)         │
│       │                                           │
│       ▼                                           │
│  下次对话自动使用更新后的人设                       │
└─────────────────────────────────────────────────┘
```

**学习数据结构**：

```yaml
# characters/cards/default_evolution.yaml (自动生成，与原始角色卡合并)
learned_traits:
  communication_style:
    - "用户喜欢简洁直接的回复"
    - "解释技术概念时用类比效果好"
  topic_expertise:
    - "用户经常讨论 Python 和 AI 话题"
    - "对效率工具有浓厚兴趣"
  behavioral_patterns:
    - "用户不喜欢太长的回复"
    - "用户偏好分步骤说明"
  emotional_intelligence:
    - "用户压力大时需要鼓励性回复"
    - "讨论工作时保持专业语气"
  last_updated: "2026-06-19T10:00:00"
  learning_version: 3
  conversations_analyzed: 150
```

**学习触发策略**：

| 策略 | 触发条件 | 说明 |
|------|---------|------|
| 定量触发 | 每 N 条新对话 | 默认每 50 条对话触发一次学习 |
| 定时触发 | 每日凌晨 3 点 | 低峰期自动执行 |
| 手动触发 | `/learn now` | 用户主动触发 |

**学习流程**：

1. 收集自上次学习以来的所有对话记录
2. LLM 分析对话模式，提取人设演化建议
3. 与原始角色卡合并（原始设定优先，学习结果追加）
4. 生成学习摘要，存储到 `learned_traits`
5. 清理已分析的原始对话记录（只保留摘要）

**指令**：

| 指令 | 功能 |
|------|------|
| `/learn now` | 立即触发学习 |
| `/learn status` | 查看学习状态和版本 |
| `/learn history` | 查看学习演化历史 |
| `/learn reset` | 重置学习结果到初始角色卡 |
| `/learn interval <N>` | 设置学习触发间隔 |

### 5. 指令系统 (Command System)

统一 `/` 前缀：

```
/
├── card          # 角色卡管理
├── style         # 风格管理
├── memory        # 记忆管理
├── learn         # 自学习管理
├── new           # 新会话
├── history       # 历史记录
├── undo          # 撤销
├── context       # 上下文状态
├── token         # Token 用量
├── search        # 联网搜索
├── help          # 帮助信息
├── settings      # 个人设置
└── admin         # 管理员指令
    ├── users     # 用户管理
    ├── groups    # 群管理
    ├── logs      # 日志查看
    └── reload    # 重载配置
```

### 6. 平台适配层

统一接口抽象，每个平台实现相同接口：

```python
class BasePlatform(ABC):
    async def start(self): ...
    async def stop(self): ...
    async def send_message(self, user_id, content): ...
    async def send_image(self, user_id, image_path): ...
    async def on_message(self, callback): ...
    def get_user_info(self, user_id): ...
```

### 7. 插件系统

可扩展的插件架构，支持消息预处理、后处理、自定义指令：

```python
class BasePlugin(ABC):
    name: str
    version: str

    async def on_message(self, message) -> Optional[Message]: ...
    async def on_response(self, response) -> Optional[str]: ...
    def get_commands(self) -> Dict[str, Callable]: ...
```

## 项目结构

```
chatbot/
├── main.py                     # 入口
├── config.yaml                 # 主配置
├── requirements.txt
├── .env                        # 环境变量
├── core/                       # 核心引擎
│   ├── engine.py               # 主引擎
│   ├── router.py               # 消息路由
│   ├── session.py              # 会话管理
│   ├── prompt_builder.py       # Prompt 构建
│   └── command_parser.py       # 指令解析
├── platforms/                  # 平台适配
│   ├── base.py                 # 抽象基类
│   ├── wechat.py               # 微信
│   └── qq.py                   # QQ
├── llm/                        # LLM 层
│   ├── deepseek.py             # DeepSeek API
│   ├── response_handler.py     # 响应处理
│   └── token_counter.py        # Token 计数
├── memory/                     # 记忆系统
│   ├── short_term.py           # 短期记忆
│   ├── long_term.py            # 长期记忆
│   ├── entity_memory.py        # 实体记忆
│   └── summarizer.py           # 对话摘要
├── characters/                 # 角色卡
│   ├── card_manager.py
│   └── cards/
├── styles/                     # 风格引擎
│   ├── style_engine.py
│   └── presets.yaml
├── learning/                   # 自学习系统
│   ├── __init__.py
│   ├── analyzer.py             # 学习分析器
│   ├── scheduler.py            # 学习调度器
│   └── evolution.py            # 角色卡演化管理
├── plugins/                    # 插件
│   ├── base.py
│   └── web_search.py
├── database/                   # 数据层
│   ├── models.py
│   └── db.py
├── utils/                      # 工具
│   ├── logger.py
│   ├── rate_limiter.py
│   ├── message_splitter.py
│   └── security.py
├── web/                        # 管理面板
│   ├── app.py
│   └── routes/
└── data/                       # 运行时数据
    ├── chatbot.db
    ├── chroma/
    ├── learning/               # 学习报告和演化历史
    └── logs/
```

## 配置

```yaml
bot:
  name: "AI助手"
  default_character: "default"
  default_style: "casual"
  command_prefix: "/"

llm:
  provider: "deepseek"
  api_key: "${DEEPSEEK_API_KEY}"
  base_url: "https://api.deepseek.com"
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
    embedding_model: "text-embedding-v3"
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
  trigger_interval: 50          # 每 50 条对话触发一次
  trigger_schedule: "03:00"     # 或每天凌晨 3 点定时触发
  max_history_for_learning: 200 # 学习时最多回溯 200 条对话
  auto_apply: true              # 自动应用学习结果到角色卡
  backup_before_learn: true     # 学习前备份当前角色卡

logging:
  level: "INFO"
  file: "data/logs/chatbot.log"
```

## 部署

- **开发环境**：Windows 本地，WeChatFerry 注入 PC 微信 + NapCat 运行 QQNT
- **生产环境**：Windows Server/VPS，NSSM 注册服务或 Docker Compose

## 范围

- MVP 包含：微信接入、DeepSeek 对话、基础角色卡、基础风格、短期记忆、指令系统
- 后续迭代：QQ 接入、长期记忆、实体记忆、插件系统、Web 管理面板
