# 聊天机器人平台 v2

支持 QQ + Telegram 双平台、DeepSeek API 驱动的聊天机器人，具备角色卡、风格引擎、三层记忆、自学习、三层安全防护等功能。

## 架构

```
┌─────────────┐  ┌─────────────┐
│    QQ用户    │  │ Telegram用户 │
└──────┬──────┘  └──────┬──────┘
       │                │
       ▼                ▼
┌─────────────┐  ┌─────────────┐
│   NapCat    │  │  Telegram   │
│ (OneBot11)  │  │  Bot API    │
└──────┬──────┘  └──────┬──────┘
       │                │
       └───────┬────────┘
               ▼
      ┌─────────────────────┐
      │    消息管道 Pipeline   │
      │  ┌─────────────────┐ │
      │  │ 限流 → 过滤 → 窗口│ │
      │  │ → LLM → 过滤 →   │ │
      │  │ 安全 → 拆分 → 延迟│ │
      │  └─────────────────┘ │
      └──────────┬──────────┘
                 ▼
      ┌─────────────────────┐
      │   核心引擎            │
      │  · 角色卡 + 风格引擎  │
      │  · 三层记忆系统       │
      │  · 自学习系统         │
      │  · 多LLM故障转移      │
      └──────────┬──────────┘
                 ▼
      ┌─────────────────────┐
      │   Web 管理面板 :8080  │
      └─────────────────────┘
```

## 快速开始

### 1. 配置

```bash
cp .env.example .env
vim .env   # 填入 DEEPSEEK_API_KEY
```

编辑 `config/config.yaml` 配置平台和安全参数。

### 2. 部署到 Linux 服务器

```bash
chmod +x deploy.sh
./deploy.sh
```

### 3. 登录账号

- 访问 `http://服务器IP:6099` 扫码登录 QQ
- 在 `config/config.yaml` 中填入 Telegram Bot Token
- 访问 `http://服务器IP:8080` 查看管理面板

### 4. 测试

给 QQ / Telegram 机器人发消息测试。

## 本地开发（控制台模式）

```bash
pip install -r requirements.txt
python main.py --console
```

## 功能

### 三层安全防护

| 层级 | 组件 | 功能 |
|------|------|------|
| 第一层 | 行为模拟 | 随机延迟、消息拆分、活跃时间窗口 |
| 第二层 | 频率控制 | 每用户/每群/全局限流、每日上限、对话深度限制、URL频率限制 |
| 第三层 | 熔断保护 | 失败率监控、自动熔断/恢复、告警通知 |

### 消息管道（中间件架构）

```
接收 → [限流] → [过滤] → [活跃窗口] → LLM → [内容过滤] → [安全检查] → [拆分] → [延迟] → 发送
```

8 个内置中间件，可自由组合。

### 多 LLM 支持

- **主力**: DeepSeek
- **备用**: OpenAI 兼容接口 / Ollama 本地模型
- 主模型故障自动切换备用，零感知

### 指令系统

| 指令 | 功能 |
|------|------|
| `/help` | 查看帮助 |
| `/card list/use/create` | 角色卡管理 |
| `/style list/use` | 风格管理 |
| `/memory profile/recall/clear` | 记忆管理 |
| `/learn now/status/history` | 自学习管理 |
| `/search <关键词>` | 联网搜索 |
| `/settings show` | 查看设置 |
| `/group info` | 群配置(群聊) |
| `/group mention on/off` | @才回复模式(群聊) |
| `/context` | 会话状态 |
| `/history [n]` | 对话历史 |
| `/undo` | 撤销上轮 |
| `/new` | 新会话 |

### 聊天风格

| 风格 | 描述 |
|------|------|
| casual | 随性闲聊 |
| professional | 专业严谨 |
| creative | 创意写作 |
| concise | 极简回复 |
| socratic | 苏格拉底式引导 |

### 核心功能

- **角色卡系统**: 自定义人设、规则、示例对话，YAML 格式。内置4张卡: default(默认助手)、programmer(程序员)、teacher(导师)、writer(写手)
- **风格引擎**: 5种预设 + 自定义 + 混合
- **三层记忆**: 短期上下文 + 实体画像 + 长期语义向量(ChromaDB)
- **记忆遗忘**: 低访问降权、无访问归档，防止记忆膨胀
- **自学习**: 定期分析对话，自动进化角色卡
- **联网搜索**: DuckDuckGo 集成
- **Web管理面板**: 浏览器查看状态、会话、角色卡（移动端优化）
- **群聊管理**: @mention检测、群专属角色卡/风格、仅@回复模式
- **敏感词过滤**: 可配置敏感词列表，自动替换为 ***
- **告警系统**: 异常事件通过 Telegram 推送通知

## Docker 管理

```bash
# 查看日志
docker compose logs -f chatbot    # 机器人日志
docker compose logs -f napcat     # QQ日志

# 重启
docker compose restart

# 停止
docker compose down

# 更新
git pull
docker compose up -d --build
```

## 配置说明

### config/config.yaml

```yaml
platforms:
  qq:
    enabled: true
    onebot_url: "ws://127.0.0.1:3001"
  telegram:
    enabled: false
    token: "YOUR_BOT_TOKEN"
    owner_id: "YOUR_USER_ID"

llm:
  primary:
    provider: "deepseek"
    api_key: ""  # 或通过 .env 设置
    model: "deepseek-chat"
  backup:
    provider: "openai_compatible"
    api_key: ""
    base_url: ""
    model: ""

security:
  rate_limit:
    per_user_per_minute: 10
    per_group_per_minute: 20
    global_per_hour: 200
  daily_message_cap: 500
  active_hours:
    start: "08:00"
    end: "23:00"
  circuit_breaker:
    failure_threshold: 0.3
    recovery_seconds: 600

memory:
  short_term:
    max_messages: 50
    summarize_after: 20
  long_term:
    relevance_threshold: 0.7
    archive_after_days: 60
```

### .env

```env
DEEPSEEK_API_KEY=sk-xxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

## 项目结构

```
chatbot/
├── main.py                # 入口
├── config/
│   ├── config.yaml        # 统一配置
│   └── sensitive_words.txt # 敏感词列表
├── core/
│   ├── pipeline.py        # 消息管道
│   ├── middleware/         # 8个内置中间件
│   ├── session.py         # 会话管理
│   ├── command_parser.py  # 指令解析
│   ├── prompt_builder.py  # 提示词构建
│   └── router.py          # 消息路由
├── platforms/
│   ├── base.py            # 平台抽象基类
│   ├── qq.py              # QQ (NapCat OneBot)
│   └── telegram.py        # Telegram Bot API
├── security/
│   ├── rate_limiter.py    # 三层限流器
│   ├── circuit_breaker.py # 熔断器
│   ├── content_filter.py  # 敏感词过滤
│   └── alerting.py        # 告警系统
├── llm/
│   ├── client.py          # 多Provider客户端
│   └── providers/         # DeepSeek/OpenAI/Ollama
├── memory/
│   ├── short_term.py      # 短期记忆
│   ├── long_term.py       # 长期语义记忆(ChromaDB)
│   ├── entity_memory.py   # 实体记忆
│   ├── summarizer.py      # 对话摘要
│   └── forgetting.py      # 记忆遗忘机制
├── characters/            # 角色卡
├── styles/                # 风格引擎
├── learning/              # 自学习
├── plugins/               # 插件
├── web/                   # Web管理面板
├── database/              # 数据库(SQLAlchemy)
├── utils/                 # 工具
├── tests/                 # 测试 (131个)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 技术栈

- Python 3.14 + FastAPI + SQLAlchemy (async)
- ChromaDB 向量数据库
- DeepSeek API (OpenAI 兼容)
- Telegram Bot API
- NapCat + OneBot v11 (QQ)
