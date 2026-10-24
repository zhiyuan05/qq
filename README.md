# 聊天机器人平台 v2.4

支持 QQ + Telegram 双平台、DeepSeek API 驱动的聊天机器人，具备角色卡、风格引擎、可恢复会话、三层记忆、自学习、安全防护和运行监控。

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
      │  · SQLite/PostgreSQL  │
      │    会话恢复与并发控制  │
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
vim .env   # 填入 CHATBOT_LLM_API_KEY；公网部署同时设置 CHATBOT_WEB_AUTH_TOKEN
```

编辑根目录的 `config.yaml` 配置平台和安全参数。`CHATBOT_WEB_AUTH_TOKEN` 至少 16 位；留空时管理面板不启用认证，只适合受信任的本地网络。
根配置默认监听容器网络地址，因此缺少该令牌时应用会拒绝启动，不会降级为匿名管理端。

### 2. 部署到 Linux 服务器

```bash
chmod +x deploy.sh
./deploy.sh
```

容器默认使用 UID/GID `1000:1000`。如部署账号不同，可在 `.env` 设置 `CHATBOT_UID`、`CHATBOT_GID`；部署脚本会同步持久化目录权限。

### 3. 登录账号

- 端口默认仅绑定服务器回环地址。远程登录可先执行
  `ssh -L 6099:127.0.0.1:6099 -L 8080:127.0.0.1:8080 user@服务器IP`
- 访问本机 `http://127.0.0.1:6099` 扫码登录 QQ
- 在 `.env` 中设置 `CHATBOT_TELEGRAM_TOKEN`；如 NapCat 启用了鉴权，再设置 `CHATBOT_ONEBOT_ACCESS_TOKEN`
- 访问本机 `http://127.0.0.1:8080` 查看管理面板；公网访问应配置带 TLS 的反向代理

### 4. 测试

给 QQ / Telegram 机器人发消息测试。

## 本地开发（控制台模式）

```bash
pip install -r requirements-dev.txt
python main.py --console

# 质量检查
python -m ruff check .
python -m pytest tests -q
```

## 功能

### 会话可靠性与运行监控

- 会话元数据、消息快照和实体画像统一使用异步 SQLAlchemy 存储；单机默认 SQLite，生产可切换 PostgreSQL
- 快照使用单调递增 revision 做乐观并发控制，过期副本不能覆盖新状态
- 单机使用自动回收的会话锁；多实例使用 PostgreSQL 事务级 advisory lock，普通与流式消息都保持同会话有序
- Web 对话支持 `request_id` 幂等、并发重试合并和一次自动断线重连，防止重复调用 LLM
- `/api/v1/health` 提供存活探针，`/api/v1/health/ready` 检查引擎和持久化依赖是否就绪
- `/api/v1/metrics` 提供有界的 HTTP、WebSocket、引擎、限流、熔断与持久化指标
- 所有 HTTP 响应携带 `X-Request-ID`，可串联请求与结构化日志
- 认证、安全响应头、请求追踪和指标使用纯 ASGI 中间件，完整保留流式响应语义
- SQLite 启用 WAL、外键检查和 busy timeout；迁移记录 SHA-256，启动时拒绝被修改的历史迁移
- PostgreSQL 启动建表由 advisory lock 串行化；CI 使用真实 PostgreSQL 16 验证快照冲突、实体 upsert 和跨连接锁
- 完整管理 API 统一发布在 `/api/v1`；旧 `/api` 保持兼容但不进入 OpenAPI
- 登录、聊天和全部管理页面采用独立 HTML/CSS/JS 资源，前端自身只调用 v1 API；严格 CSP 禁止内联脚本与样式
- Docker 默认以非 root UID/GID 运行，并启用只读根文件系统、能力清空和 `no-new-privileges`
- Docker 端口默认只绑定宿主机回环地址；非静态响应统一 `Cache-Control: no-store, private`

管理指标接口受 Web 令牌保护；存活和就绪探针保持公开，供容器编排系统使用。

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

7 个内置中间件（限流、过滤、活跃窗口、熔断、内容过滤、拆分、延迟），可自由组合。

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
| `/persona show/approve/forget/rollback/reset` | 人格特质管理 |
| `/sticker list/pending/approve/reject` | 表情包与审核队列 |
| `/search <关键词>` | 联网搜索 |
| `/read <链接>` | 读取网页摘要 |
| `/weather <城市>` | 实时天气 |
| `/remind 10m 喝水` | 定时/每日提醒（私聊也支持自然语言） |
| `/avatar` | 用附带图片设置 QQ 头像(管理员) |
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

### QQ 拟人化（识图 / 表情包 / 语音 / 分段）

- **识图**：QQ 私聊图片经 SSRF 校验下载、缩放后送入多模态模型；失败自动退回文字
- **引用**：解析入站引用为「被引用人 + 原文」；图片/语音回复自动引用
- **分段**：长回复拆成最多 3 条，间隔按字数模拟打字；仅私聊生效
- **表情包**：`characters/stickers/` 本地库，支持自动收集、审核队列、标签选图与冷却；私聊提示词会注入可用标签，模型可主动配 `[sticker:标签]`
- **链接/视频/天气**：私聊自动读取网页摘要、视频关键帧交给视觉模型；`/read`、`/weather` 按需查询
- **语音（可选）**：Whisper ASR + Edge TTS，默认关闭；`./deploy.sh --voice` 开启
- **人格**：全局/用户特质增量演化；全局特质在有两次会话证据后默认自动生效，也可改为管理员确认，可回滚/重置
- **上下文预算**：按 token 预算组装摘要、特质、最近消息与长期记忆，不加载全量历史
- **媒体安全**：下载/重定向逐跳校验、大小与像素上限、出站路径/凭据拦截、发送配额

详见 [docs/media.md](docs/media.md) 与 [docs/voice.md](docs/voice.md)。

### 管理员 Agent

管理员（`security.admin_users`）在 QQ 里发送 `#` 开头的自然语言要求，机器人会直接操作服务器上的资源：

```text
# 新建一个人设叫“冷面助手”，性格高冷，说话简短
# 把猫娘的语气改得更黏人一点
# 让机器人以后少用敬语
# 看看服务状态
# 重启机器人
```

- 人设卡直接写入 `characters/roles/*.md`，覆盖前自动备份
- 人格特质写入 persona store
- 配置、删卡、写文件、重启/重建/迁移/命令等高风险动作会先返回确认码，回复 `#确认 123456` 才执行
- 服务器侧由 systemd 服务 `qqbot-admin-executor` 执行：默认 `ADMIN_EXECUTOR_MODE=safe` 只开放白名单动作；显式设为 `full` 时可执行任意 root 命令（带频率限制、超时、审计和 `data/admin-executor.disabled` 急停）
- 操作审计写入 `data/admin-audit.log`

首次部署后启用：

```bash
systemctl enable --now qqbot-admin-executor
```


### 核心功能

- **角色卡系统**: Markdown 人格卡（YAML front matter + Markdown 人设正文），兼容旧 YAML 卡；内置 11 张卡（默认助手、猫娘、傲娇、女仆、学姐、御姐、温柔姐姐、甜美、病娇、知心、青梅竹马）
- **风格引擎**: 5种预设 + 自定义 + 混合
- **三层记忆**: 短期上下文 + 实体画像（按相关性只注入 top-k，完整档案持久化在磁盘）+ 长期语义向量(ChromaDB)
- **身份隔离**: 实体与向量记忆使用 `platform:user_id` 主体键，跨平台同号用户默认互相隔离
- **记忆遗忘**: 低访问降权、无访问归档，防止记忆膨胀
- **人格演化**: 定期从归档对话增量提取用户/全局特质，带置信度门槛、候选审核与版本回滚
- **联网搜索**: Bing HTML 优先、DuckDuckGo 兜底；私聊说“搜一下/查一下”会自动注入结果
- **多模型路由**: DeepSeek、OpenAI-compatible、Ollama 统一契约，支持普通/流式故障转移、失败冷却和连接池复用
- **Web管理面板**: 桌面侧栏 + 移动端导航，支持状态、会话、Markdown 人设编辑、人格审核、表情包审核、用量看板；对话页可恢复历史、显示流式状态、失败重试
- **用量预算**: `usage.daily_budget_micros` 大于 0 时，达到每日费用后拒绝新的模型调用
- **群聊管理**: @mention检测、群专属角色卡/风格、仅@回复模式
- **敏感词过滤**: 可配置敏感词列表，自动替换为 ***
- **告警系统**: 异常事件通过 Telegram 推送通知
- **平台可靠性**: 统一生命周期状态机、发送回执、入站重放保护和有界运行指标；OneBot 使用标准双向 WebSocket

## 与服务器上已有项目共存

如果服务器已有其他 Docker 项目，部署前在 `.env` 中设置独立项目名、容器前缀和空闲端口：

```ini
COMPOSE_PROJECT_NAME=qqbot
CHATBOT_CONTAINER_PREFIX=qqbot-
CHATBOT_WEB_PORT=18080
CHATBOT_ONEBOT_PORT=13001
NAPCAT_WEBUI_PORT=16099
CHATBOT_MEMORY_LIMIT=1024M
```

`COMPOSE_PROJECT_NAME` 会让镜像、网络、命名卷都带上 `qqbot` 前缀，不会复用或覆盖原项目的资源；容器前缀避免 `chatbot`/`napcat` 名称冲突。`deploy.sh` 会在启动前检查端口占用并给出警告。请把本项目放在独立目录（例如 `/opt/qq-chatbot`），不要与已有项目共用目录。

## Docker 管理

```bash
# 部署（可选：--voice 开启语音，--postgres 使用 PostgreSQL）
./deploy.sh --voice

# 查看日志
docker compose logs -f chatbot    # 机器人日志
docker compose logs -f napcat     # QQ日志

# 重启
docker compose restart

# 停止
docker compose down

# 更新
git pull
docker compose build chatbot
docker compose run --rm --no-deps chatbot python -m database.migrate  # 失败时阻止发布
docker compose up -d
```

## 配置说明

### config.yaml

```yaml
platforms:
  qq:
    enabled: true
    onebot_url: "ws://127.0.0.1:3001"
    connect_timeout: 10
    api_timeout: 15
    event_queue_size: 1000
  telegram:
    enabled: false
    owner_id: "YOUR_USER_ID"
    allowed_groups: []
    group_mode: "mention"
    max_msg_length: 4096

llm:
  provider: "deepseek"
  model: "deepseek-chat"
  fallback_model: "deepseek-reasoner"
  max_tokens: 2048
  timeout: 30
  provider_failure_threshold: 2
  provider_cooldown_seconds: 30
  daily_token_limit: 500000

rate_limit:
  enabled: true
  max_per_minute: 20
  max_per_group_minute: 60
  max_per_hour: 200
  daily_message_cap: 500

security:
  max_message_length: 2000
  active_hours:
    enabled: true
    start_hour: 8
    end_hour: 23

memory:
  short_term:
    max_messages: 50
    summarize_after: 20
  long_term:
    enabled: true
    top_k: 5
    cleanup_interval_seconds: 3600
    archive_after_days: 60

persistence:
  enabled: true
  database_url: ""             # 留空则使用 db_path
  db_path: "data/chatbot.db"
  restore_limit: 200
  multi_instance: false         # PostgreSQL 多副本时开启
  lock_timeout: 30
  pool_size: 5
  max_overflow: 10
  pool_timeout: 30

web:
  enabled: true
  host: "0.0.0.0"
  port: 8080
```

### .env

```env
CHATBOT_LLM_API_KEY=sk-xxxxx
CHATBOT_LLM_BASE_URL=https://api.deepseek.com
CHATBOT_WEB_AUTH_TOKEN=至少16位的随机字符串
CHATBOT_ONEBOT_ACCESS_TOKEN=
CHATBOT_TELEGRAM_TOKEN=
CHATBOT_DATABASE_URL=
CHATBOT_MULTI_INSTANCE=
```

`llm.provider` 可设为 `deepseek`、`openai_compatible` 或 `ollama`。Ollama 默认使用
`http://localhost:11434` 和 `qwen2.5:7b`，无需 API Key。旧版
`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 环境变量仍兼容。

### PostgreSQL 与多实例

SQLite 适合单进程部署。需要共享会话状态或运行多个应用副本时，使用 PostgreSQL，并开启
`multi_instance`；此模式会在处理消息前刷新数据库快照，并以事务级 advisory lock 保证同一会话跨副本串行。

仓库提供带持久卷和健康检查的 Compose 覆盖文件：

```bash
# 在 .env 中设置 CHATBOT_POSTGRES_PASSWORD（建议仅使用字母和数字）
docker compose -f docker-compose.yml -f docker-compose.postgres.yml build chatbot
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d postgres
docker compose -f docker-compose.yml -f docker-compose.postgres.yml run --rm --no-deps chatbot python -m database.migrate
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d chatbot
```

连接外部数据库时，设置 `CHATBOT_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database`
和 `CHATBOT_MULTI_INSTANCE=true`。URL 中的特殊字符必须百分号编码。分布式会话锁在一次 LLM
响应期间占用一个数据库连接，因此 `pool_size + max_overflow` 应覆盖单实例的并发活跃会话数。
完整迁移、扩容和回退说明见 [持久化部署指南](docs/persistence.md)。

## 项目结构

```
chatbot/
├── main.py                # 入口
├── config.yaml            # 运行时统一配置
├── config/
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
│   ├── manager.py         # 平台注册与生命周期编排
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
├── characters/
│   ├── roles/             # Markdown 人格卡（主）
│   └── cards/             # 旧 YAML 角色卡（兼容）
├── styles/                # 风格引擎
├── learning/              # 自学习
├── plugins/               # 插件
├── web/                   # Web管理面板
│   ├── schemas.py         # OpenAPI 请求/响应契约
│   ├── templates/         # HTML 页面模板
│   └── static/            # CSS / JavaScript 资源
├── database/              # SQLite/PostgreSQL 异步存储与增量迁移
│   ├── session_store.py   # revision 乐观并发快照
│   └── session_coordinator.py # 本地/跨实例会话排序
├── observability/         # 轻量运行指标
├── utils/                 # 工具
├── tests/                 # 自动化与真实 PostgreSQL 集成测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 技术栈

- Python 3.11+ + FastAPI + SQLAlchemy (async)
- ChromaDB 向量数据库
- DeepSeek API (OpenAI 兼容)
- Telegram Bot API
- NapCat + OneBot v11 (QQ)

## 参考架构

本轮升级借鉴了 Open WebUI、Chainlit、Rasa、FastAPI Full Stack Template 和 Dify 的成熟实践。采用项、取舍与后续路线见 [GitHub 成熟项目参考架构](docs/reference-architecture.md)。

面向 Workspace、追加式 Run、受治理 Tool 和评测发布闭环的分阶段演进见
[从聊天机器人到 Agent 平台：0→1 路线图](docs/platform-roadmap.md)。

API 版本、认证方式和兼容策略见 [Web API 契约](docs/api.md)。
