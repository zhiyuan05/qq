# Chatbot v2 Optimization Design

Date: 2026-06-20
Status: Draft
Priority: Security > Quality > Features

## Goals

1. Maximize account safety (QQ focus, add Telegram as safe alternative)
2. Improve architecture for extensibility and maintainability
3. Enhance features: multi-LLM, memory, monitoring, web dashboard
4. Remove WeChat temporarily (unacceptable ban risk for personal use)

## Scope

- Refactor message pipeline with middleware pattern
- Build comprehensive security/anti-ban system
- Add Telegram platform adapter
- Enhance LLM layer with multi-provider support
- Improve memory system (compression, retrieval, forgetting)
- Add web management dashboard
- Add health monitoring and alerting

## Non-Goals

- WeChat integration (deferred until safe approach exists)
- Multi-user/multi-tenant support (personal use only)
- Mobile app
- Voice/image generation

---

## 1. Platform Strategy

### Keep: QQ via NapCatQQ

- Protocol: OneBot v11 over HTTP
- Risk level: Medium (NTQQ-based, most active implementation)
- Current implementation in `platforms/qq.py` is functional
- Enhancements: WebSocket support (instead of polling), reconnect logic

### Add: Telegram

- Protocol: Official Bot API (zero ban risk)
- Library: `python-telegram-bot` v20+
- Features: private chat, group chat, inline keyboards, command menu, Markdown formatting
- Serves dual purpose: bot platform + system alerting channel

### Remove: WeChat

- Delete `platforms/wechat.py` and related config
- Keep `BasePlatform` abstraction clean for future re-addition
- Document the decision in code comments

### Platform Abstraction

```
BasePlatform (platforms/base.py)
├── QQPlatform (platforms/qq.py)
└── TelegramPlatform (platforms/telegram.py)
```

Each platform adapter:
- Receives messages via its native mechanism
- Normalizes to `UnifiedMessage`
- Calls `handle_callback()` from base class
- Sends replies via `send_long_message()` (split + delay)

---

## 2. Security System

This is the highest priority component. Three-layer defense:

### Layer 1: Behavior Simulation

| Feature | Description | Status |
|---------|-------------|--------|
| Message splitting | Split long messages at paragraph/sentence boundaries | Done |
| Human delay | Random delay + typing time simulation | Done |
| Typing pause | Random mid-message pauses (simulate "typing...") | New |
| Active time window | Only respond during configurable hours (e.g., 8:00-23:00) | New |
| Response length variance | Vary reply length for similar questions (±20%) | New |
| Read delay | Wait before "reading" a message (1-5s random) | New |

### Layer 2: Rate Limiting

Enhance `utils/rate_limiter.py` with three tiers:

```
Per-user limit:   max N messages per minute per user
Per-group limit:  max M messages per minute per group
Global limit:     max K messages per hour total
Cooldown:         after hitting limit, wait X minutes before resuming
```

Additional controls:
- **Conversation depth limit**: max 20 consecutive exchanges per session with same user, reset when session expires or user is idle > 30 minutes
- **Daily message cap**: configurable total daily message limit (e.g., 500/day)
- **Repeat content detection**: if bot sends similar content 3+ times, inject variation
- **URL frequency limit**: max 5 URLs per hour (URLs trigger more scrutiny)

### Layer 3: Circuit Breaker & Monitoring

```
Circuit States: CLOSED (normal) → OPEN (blocked) → HALF_OPEN (testing)

Triggers to OPEN:
- Send failure rate > 30% in 5 minutes
- Account receives warning signal
- Rate limit hit 3+ times in 10 minutes

OPEN behavior:
- Stop all proactive messages
- Only respond to direct commands from owner
- Send alert to Telegram

HALF_OPEN (after 10 minutes):
- Allow 1 message through
- If success → CLOSED
- If failure → stay OPEN, extend timer
```

### Sensitive Content Filter

- Configurable sensitive word list (loaded from file)
- Replace sensitive words with `***` before sending
- Log filtered messages for review
- Optional: skip sending entirely for high-severity words

---

## 3. Message Pipeline Architecture

### Current Flow (Linear)

```
Platform.receive → callback → LLM → Platform.send
```

### New Flow (Middleware Pipeline)

```
Platform.receive
    → [before_receive middleware chain]
    → MessageRouter (command check)
    → [before_llm middleware chain]
    → LLMClient.process
    → [after_llm middleware chain]
    → [before_send middleware chain]
    → Platform.send_long_message
```

### Middleware Interface

```python
class Middleware:
    async def process(self, ctx: MessageContext, next) -> Optional[str]:
        # ctx contains: message, session, metadata
        # return None to stop pipeline, or call next(ctx) to continue
        ...
```

### Built-in Middleware

| Name | Stage | Purpose |
|------|-------|---------|
| `FilterMiddleware` | before_receive | Block messages from blocked users, filter spam |
| `RateLimitMiddleware` | before_receive | Enforce rate limits |
| `ActiveWindowMiddleware` | before_receive | Check if within active hours |
| `MemoryMiddleware` | before_llm | Inject relevant memories into context |
| `CharacterMiddleware` | before_llm | Inject character prompt |
| `ContentFilterMiddleware` | after_llm | Filter sensitive content from response |
| `LengthVarianceMiddleware` | after_llm | Adjust response length variability |
| `SafetyMiddleware` | before_send | Final safety check, circuit breaker |
| `SplitMiddleware` | before_send | Split long messages |
| `DelayMiddleware` | before_send | Add human-like delays |

### MessageContext

```python
@dataclass
class MessageContext:
    message: UnifiedMessage
    session: Session
    character: Character
    memories: List[Memory]
    metadata: dict  # middleware can attach data here
    response: Optional[str] = None
```

---

## 4. Multi-LLM Support

### Architecture

```
LLMClient (llm/client.py)
├── Provider: DeepSeek (primary)
├── Provider: Qwen (backup)
├── Provider: OpenAI-compatible (configurable)
└── Provider: Ollama (local, optional)
```

### Provider Interface

```python
class LLMProvider:
    async def chat(self, messages: List[dict], **kwargs) -> str
    async def health_check(self) -> bool
    def count_tokens(self, text: str) -> int
```

### Failover Logic

```
1. Try primary provider (DeepSeek)
2. If timeout/error after 2 retries → try backup provider
3. If all providers fail → return cached fallback message
4. Log failover events for monitoring
```

### Model Routing (Optional Enhancement)

- Simple greetings/commands → lightweight model (faster, cheaper)
- Complex reasoning/creative tasks → strong model
- Configurable routing rules per character or topic

### Token Budget Management

- Track daily token usage per provider
- Warn when approaching budget limit
- Auto-switch to cheaper model when budget low
- Token usage dashboard in web panel

---

## 5. Memory System Enhancement

### Current System

- Short-term: conversation buffer (in-memory)
- Long-term: ChromaDB semantic search
- Entity: SQLite entity extraction

### Enhancements

#### Short-term Memory

- **Auto-summarization trigger**: summarize when conversation exceeds 20 messages (currently exists, tune threshold)
- **Summary quality**: use structured prompt for better summaries
- **Context window management**: sliding window with summary anchor

#### Long-term Memory (ChromaDB)

- **Relevance threshold**: minimum similarity score for retrieval (configurable, default 0.7)
- **Deduplication**: prevent storing near-duplicate memories
- **Memory importance scoring**: tag memories as high/medium/low importance
- **Retrieval limit**: max 5 relevant memories per query (prevent context overflow)

#### Entity Memory

- **Relation graph**: track relationships between entities (e.g., "Alice mentioned Bob in context of project X")
- **Temporal tracking**: when entities were last mentioned, frequency
- **Auto-cleanup**: entities not mentioned in 30 days get archived

#### Forgetting Mechanism

- Memories accessed < 3 times in 30 days → low priority
- Memories accessed 0 times in 60 days → archived (not deleted, just excluded from search)
- User can manually trigger memory cleanup via command or web panel

---

## 6. Telegram Platform Adapter

### Implementation

```python
class TelegramPlatform(BasePlatform):
    name = "telegram"

    async def start(self):
        # Initialize python-telegram-bot Application
        # Register handlers for private chat and group messages
        # Start polling or webhook mode

    async def send_message(self, user_id, content, group_id=None):
        # Send via Telegram Bot API
        # Support Markdown formatting
        # Split long messages (Telegram limit: 4096 chars)

    async def send_image(self, user_id, image_path, group_id=None):
        # Send photo via Telegram Bot API
```

### Features

- Private chat and group chat support
- `/start`, `/help` commands
- `/character <name>` to switch character
- `/memory` to view/manage memories
- `/status` to see system status
- Markdown formatting for rich responses
- Inline keyboard for interactive options (character selection, etc.)
- Message reply threading
- Alert channel: send system alerts to owner's Telegram

### Configuration

```yaml
telegram:
  token: "BOT_TOKEN"
  owner_id: "OWNER_USER_ID"  # for admin commands and alerts
  allowed_users: []  # empty = allow all, or whitelist user IDs
  group_mode: "mention"  # "mention" (respond when @mentioned) or "always"
  parse_mode: "Markdown"
```

---

## 7. Web Management Dashboard

### Tech Stack

- Backend: FastAPI (already in project)
- Frontend: Simple HTML + vanilla JS + TailwindCSS (no build step needed)
- Real-time: WebSocket for live updates

### Pages

#### Dashboard (Home)

- System status: uptime, memory usage, CPU
- Message stats: today's count, hourly chart
- LLM stats: token usage, cost estimate, provider status
- Active sessions count
- Circuit breaker status

#### Conversations

- Live conversation feed (WebSocket)
- Filter by platform, user, group
- Search messages
- Export conversation history

#### Characters

- List all characters with stats
- Edit character cards
- Set default character
- Per-group character binding

#### Memory

- View short-term memory (active sessions)
- Browse long-term memories (searchable)
- View entity graph visualization (simple)
- Manual cleanup controls
- Memory statistics

#### Security

- Rate limiter status
- Circuit breaker state and history
- Filtered messages log
- Sensitive word list editor
- Active time window config
- Alert history

#### Settings

- LLM provider configuration
- Platform configuration
- All security parameters
- General settings (bot name, language, etc.)

### Implementation

```
web/
├── app.py          # FastAPI app with routes
├── api/
│   ├── dashboard.py
│   ├── conversations.py
│   ├── characters.py
│   ├── memory.py
│   ├── security.py
│   └── settings.py
├── static/
│   ├── css/
│   ├── js/
│   └── index.html
└── websocket.py    # Real-time updates
```

---

## 8. Health Monitoring & Alerting

### Metrics Collected

| Metric | Source | Frequency |
|--------|--------|-----------|
| Messages received | Platform adapters | Per message |
| Messages sent | Platform adapters | Per message |
| Send failures | Platform adapters | Per attempt |
| LLM latency | LLM client | Per call |
| LLM token usage | LLM client | Per call |
| Memory query latency | Memory system | Per query |
| Circuit breaker state | Security system | On change |
| Rate limit hits | Rate limiter | On hit |

### Alerting

- Alerts sent to owner via Telegram
- Alert levels: INFO, WARNING, CRITICAL
- Configurable thresholds
- Alert history stored in SQLite

### Alert Triggers

| Condition | Level | Action |
|-----------|-------|--------|
| Circuit breaker OPEN | CRITICAL | Notify immediately |
| Send failure rate > 20% | WARNING | Notify + log |
| Daily token budget 80% used | WARNING | Notify |
| LLM provider down | WARNING | Notify + switch provider |
| Rate limit hit 3+ times | INFO | Log |

---

## 9. Configuration System

### Unified Config (config.yaml)

```yaml
# Platform
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

# LLM
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

# Security
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
  active_hours:               # Global setting, applies to all platforms
    start: "08:00"
    end: "23:00"
    timezone: "Asia/Shanghai"
  sensitive_words_file: "config/sensitive_words.txt"
  circuit_breaker:
    failure_threshold: 0.3
    window_seconds: 300
    recovery_seconds: 600

# Memory
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

# Characters
characters:
  default: "default"
  dir: "characters/"

# Web Dashboard
web:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  auth_token: ""  # Static bearer token for all API requests, empty = no auth (local only)

# Logging
logging:
  level: "INFO"
  file: "logs/chatbot.log"
  max_size_mb: 50
  backup_count: 5
```

### Hot Reload

- Watch config file for changes
- Reload non-critical settings without restart
- Critical settings (platform connections, LLM keys) require restart
- Web panel can trigger config reload

---

## 10. File Structure (Target)

```
chatbot/
├── config/
│   ├── config.yaml           # Main config
│   └── sensitive_words.txt   # Sensitive word list
├── core/
│   ├── __init__.py
│   ├── pipeline.py           # Message pipeline + middleware
│   ├── middleware/            # Built-in middleware
│   │   ├── __init__.py
│   │   ├── rate_limit.py
│   │   ├── filter.py
│   │   ├── active_window.py
│   │   ├── memory.py
│   │   ├── character.py
│   │   ├── content_filter.py
│   │   ├── safety.py
│   │   ├── split.py
│   │   └── delay.py
│   ├── session.py
│   ├── command_parser.py
│   ├── prompt_builder.py
│   ├── router.py
│   └── group_manager.py
├── platforms/
│   ├── __init__.py
│   ├── base.py
│   ├── models.py
│   ├── qq.py
│   └── telegram.py           # New
├── llm/
│   ├── __init__.py
│   ├── client.py             # Multi-provider client
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── deepseek.py
│   │   ├── openai_compat.py
│   │   └── ollama.py
│   └── token_counter.py
├── memory/
│   ├── __init__.py
│   ├── short_term.py
│   ├── long_term.py
│   ├── entity_memory.py
│   ├── summarizer.py
│   └── forgetting.py         # New
├── security/
│   ├── __init__.py
│   ├── rate_limiter.py       # Enhanced
│   ├── circuit_breaker.py    # New
│   ├── content_filter.py     # New
│   └── alerting.py           # New
├── characters/
│   ├── __init__.py
│   └── manager.py
├── styles/
│   ├── __init__.py
│   ├── engine.py
│   └── presets.yaml
├── learning/
│   ├── __init__.py
│   ├── analyzer.py
│   ├── evolution.py
│   └── scheduler.py
├── plugins/
│   ├── __init__.py
│   ├── base.py
│   ├── manager.py
│   └── web_search.py
├── web/
│   ├── __init__.py
│   ├── app.py
│   ├── api/
│   │   ├── dashboard.py
│   │   ├── conversations.py
│   │   ├── characters.py
│   │   ├── memory.py
│   │   ├── security.py
│   │   └── settings.py
│   ├── websocket.py
│   └── static/
│       ├── index.html
│       ├── css/
│       └── js/
├── database/
│   ├── __init__.py
│   ├── db.py
│   └── models.py
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   ├── config.py             # New: centralized config
│   ├── rate_limiter.py       # Move to security/
│   ├── message_splitter.py
│   ├── message_handler.py
│   └── delay.py
├── tests/
│   └── ...
├── docs/
│   └── ...
├── requirements.txt
├── Dockerfile
├── docker-compose.yaml
└── main.py
```

---

## 11. Implementation Priority

### Phase 1: Security Core (Highest Priority)

1. Security system: rate limiter, circuit breaker, content filter
2. Active time window
3. Behavior simulation enhancements (typing pause, read delay)
4. Alerting system (Telegram notifications)

### Phase 2: Architecture Refactor

5. Message pipeline with middleware pattern
6. Centralized configuration system
7. Refactor existing code into middleware components

### Phase 3: Telegram + Multi-LLM

8. Telegram platform adapter
9. Multi-LLM provider support with failover
10. Token budget management

### Phase 4: Memory & Features

11. Memory system enhancements (forgetting, relevance threshold, entity relations)
12. Enhanced command system
13. Plugin system improvements

### Phase 5: Web Dashboard

14. Web dashboard backend (FastAPI routes)
15. Web dashboard frontend
16. Real-time WebSocket updates
17. Health monitoring dashboard

### Phase 6: Polish

18. Docker deployment improvements
19. Documentation
20. Performance optimization
21. Test coverage expansion

---

## 12. Dependencies (New)

```txt
# Already in project
fastapi
uvicorn
httpx
chromadb
pyyaml

# New additions
python-telegram-bot>=20.0    # Telegram Bot API
aiofiles                     # Async file operations for web
python-multipart             # File upload support
jinja2                       # Web dashboard templates
```

---

## 13. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| QQ account ban | Medium | High | 3-layer security system, circuit breaker |
| NapCatQQ breaks after QQ update | Medium | High | Monitor NapCatQQ repo, have fallback plan |
| LLM API downtime | Low | Medium | Multi-provider failover |
| ChromaDB corruption | Low | Medium | Regular backups, SQLite fallback |
| Config error breaks system | Low | Low | Config validation, hot reload with rollback |
