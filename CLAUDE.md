# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_security.py -v

# Run a single test
python -m pytest tests/test_security.py::test_per_user_rate_limit -v

# Run tests matching a keyword
python -m pytest tests/ -k "test_memory" -v

# Lint (ruff is the project gate: E9 + F rules)
python -m ruff check .

# Start in console mode (no platform config needed)
python main.py --console

# Install dependencies (dev includes pytest, pytest-asyncio, ruff)
pip install -r requirements-dev.txt

# Apply database migrations (same as `chatbot-migrate`)
python -m database.migrate

# PostgreSQL multi-instance tests (skipped unless set)
TEST_POSTGRES_URL=postgresql+asyncpg://... python -m pytest tests/test_postgres_integration.py -v

# Deploy (Linux/Docker; reads .env and runs migrations before startup)
./deploy.sh
./deploy.sh --postgres
./deploy.sh --voice

# Docker: build & run (use the same isolated project name as .env)
docker compose -p qqbot up -d --build

# Docker: view logs / restart
docker compose -p qqbot logs -f chatbot
docker compose -p qqbot restart chatbot

# Docker: PostgreSQL multi-instance override
docker compose -p qqbot -f docker-compose.yml -f docker-compose.postgres.yml up -d
docker compose -p qqbot -f docker-compose.yml -f docker-compose.postgres.yml run --rm --no-deps chatbot python -m database.migrate

# Validate front-end assets after editing web/static
node --check web/static/admin.js
node --check web/static/chat.js
node --check web/static/login.js
```

## CI (`.github/workflows/`)

- `test.yml` runs on Python 3.11/3.12/3.13/3.14: `ruff check .`, `pytest tests/ -v`, import smoke checks, and on 3.14 a wheel build + wheel-content/README-independent packaging assertions (`tests/test_packaging.py` also enforces requirements/pyproject/version sync). Run these locally before pushing.
- The same workflow has a `postgres` job (postgres:16 service, `TEST_POSTGRES_URL`) for `tests/test_postgres_integration.py`, and a `container` job that validates `docker compose config`, builds the image, and asserts the runtime is non-root.
- `lint.yml` is intentionally non-blocking (`continue-on-error: true`); the `test.yml` static check is the real gate.

## Architecture

### Message Flow

```
Platform adapter → Engine.process_message()
  → Plugins.on_message (pre-process)
  → Pipeline.run_stage("on_receive") — rate limit, filter, active window
  → Router (dedup 5s + auth + command parse)
  → Pipeline.run_stage("on_llm") — safety/breaker
  → CommandHandler / _handle_chat (LLM call → _finish_chat)
  → Pipeline.run_stage("on_response") — content filter
  → Pipeline.run_stage("on_send") — breaker, delay
  → Plugins.on_response (post-process)
  → return response → Platform.send_long_message()
```

**Humanized delivery** (QQ private chat):
```
QQPlatform → Engine.process_message_plan()
  → same pipeline → _handle_chat → _finish_response_pipeline
  → Humanizer.plan() → ReplyPlan
  → _finish_chat(plan.history_text) → QQPlatform.deliver_reply()
      → reply segment + bubbles/delays + sticker + voice
```
`PlatformManager` uses `process_message_plan`; commands and non-QQ platforms fall back to plain strings. `process_message()` remains the raw-string entry used by Web chat and existing tests. `ReplyPlan` tracks delivered bubbles/media so a duplicate event resumes rather than re-sending.

**Streaming flow** (WebSocket `/ws/chat`):
```
WS client → Engine.process_message_stream()
  → same pipeline + LLM streaming → yield chunks → WS push {chunk: "..."}
  → _finish_chat (save to session, extract entities, summarize)
  → WS push {done: true, full: "..."}
```

### Engine (`core/engine.py`)

Central orchestrator. Constructor accepts `**deps` for DI — every subsystem can be overridden.

Key methods:
- `process_message(UnifiedMessage) → Optional[str]` — raw-string entry point (Web, Telegram, console, tests)
- `process_message_plan(msg) → str | ReplyPlan | None` — QQ entry point; humanizes private replies, returns strings for commands/blocked messages
- `process_message_stream(msg)` → async event generator — yields `{type: chunk|done, content, replace?}` and runs the same input/output pipeline as normal chat
- `_handle_chat_stream(msg)` → internal raw LLM chunk generator
- `_prepare_chat(msg)` → context assembly + memory/persona retrieval (sync + streaming)
- `_finish_chat(msg, session, response, ...)` — post-LLM: archive append, incremental summary, entity extraction (throttled), persistence, learning callback
- `_archive_turn` / `_summarize_session` / `_record_usage` / `_run_retention` — derived projections; failures never block the reply
- `start()` — restores persisted sessions and flips readiness
- `stop(timeout)` — graceful shutdown (drains LLM + stops memory cleanup)
- `runtime_stats()` — bounded engine, breaker, and rate-limit status

Normal and streaming messages are serialized by `UnifiedMessage.session_key` through `SessionCoordinator` (`database/session_coordinator.py`): an auto-reclaimed local `asyncio.Lock` by default, or a transaction-scoped PostgreSQL advisory lock when `persistence.multi_instance` is on. One session cannot race its history or database snapshot.

All pipeline stage calls use `Pipeline.run_stage(stage_name, ctx)` (public API, not private attrs).

### Prompt assembly (`core/prompt_builder.py`, `core/context_assembler.py`)

`_prepare_chat` builds the prompt through `PromptBuilder`, which combines character/style/memory into a system prompt and delegates history packing to `ContextAssembler` when one is configured (`context.enabled`). It also receives `lore_text` (selected world-book entries) and `understanding_hint` (from `core/understanding.py`): short/deictic/ellipsis messages get a “use history and ask one clarifying question if still ambiguous” hint, with no extra LLM call. `/card use|show` uses `difflib` to suggest a close card name when the exact name is missing. `ContextAssembler` never includes the full transcript: it adds context in priority order under `context.max_prompt_tokens` and never trims the fixed system prompt or the newest user message. Token counts come from `llm/token_counter.py`; history image blocks are bounded by `media.history_image_window`. `Engine` accepts `prompt_builder`/`context_assembler` via `**deps`.

### Persistence (`database/`)

- `db.py` — async SQLAlchemy engine/session factory; `resolve_database()` picks SQLite (`persistence.db_path`) or PostgreSQL (`persistence.database_url` / `CHATBOT_DATABASE_URL`).
- `session_store.py` (`SQLAlchemySessionStore`) — session metadata, message snapshots, and entity profiles. Snapshots carry a monotonically increasing `revision` for optimistic concurrency; a stale snapshot must not overwrite newer state.
- `session_coordinator.py` — per-session ordering (local lock vs. cross-instance advisory lock).
- Multi-instance mode refreshes the DB snapshot before each turn and holds one PG connection for the duration of an LLM response; capacity must account for `pool_size + max_overflow`.
- `Engine.start()` restores up to `persistence.restore_limit` sessions, reading only the newest `persistence.messages_per_session` messages per session (window function). In-memory `SessionManager` (`core/session.py`) is the runtime cache; the DB is the source of truth.
- `conversation_archive` (append-only) is the cursor source for incremental summaries and persona extraction; snapshots and the archive are not written in one transaction. Requests without a platform message id derive a stable hash identity from user/timestamp/content so retries do not duplicate archive rows.
- Entity extraction is throttled by `memory.entity.extract_interval` (default 3 responses) to keep one LLM call from firing on every turn.
- Incremental summaries consume the archive oldest-first in batches of 40 (the summarizer only reads 50). Do not advance `summary_until_id` past rows that were not summarized.
- On the plain `process_message` path a failed snapshot write is best-effort: the reply is still returned and `persistence_failures` is incremented. The durable `commit` callback path remains strict and raises `SessionPersistenceError`.

`application/durable_chat.py` (durable inbound/outbound queues) is **not wired into `main.py`** and imports a currently missing `database.durable_store`; treat it as an in-progress feature rather than live code.

### Pipeline & Middleware (`core/pipeline.py`)

4-stage chain: `on_receive → on_llm → on_response → on_send`. Middleware can abort by returning `False`.

Public API:
- `Pipeline.run_stage(stage, ctx) → bool` — single stage (Engine's use case)
- `Pipeline.process(ctx) → str|None` — all four stages sequentially (simple use)

Built-in middleware:
1. `rate_limit` — per-user, per-group, global, daily cap, conversation depth
2. `filter` — block empty/system messages
3. `active_window` — configured hours only
4. `safety` — circuit breaker
5. `content_filter` — sensitive word replacement (config flag)
6. `split` — logs oversized responses (actual split by platform's send_long_message)
7. `delay` — human-like typing (delegates to shared `HumanDelay`)

Assembly in `Engine._init_pipeline` is conditional: `rate_limit`, `active_window`, and `content_filter` are only added when their config flags are on (`delay` always registers but honours its own `enabled`). Tests must set those flags if they expect the middleware to run.

### Plugins (`plugins/`)

`PluginManager` is wired into `Engine.process_message()`:
- Pre-LLM: `plugin_manager.process_message(msg)` — modify/replace input
- Post-LLM: `plugin_manager.process_response(response, msg)` — modify output

Built-in: `WebSearchPlugin` — Bing HTML search with DuckDuckGo fallback (`utils/search.py`); private messages starting with 搜索/搜一下/查一下 auto-inject results, and city weather phrases auto-inject Open-Meteo data (`utils/weather.py`). `build_context` is token-bounded and gives summary/profile/traits/memory priority before history, while reserving room for the newest exchange.

### Characters, Styles & Groups

- `CharacterManager` (`characters/manager.py`) treats **Markdown cards in `characters/roles/*.md` as canonical**: YAML front matter holds metadata (`name/description/greeting/rules/examples/personality`, plus optional `scenario/tags/alternate_greetings/lorebook/post_history_instructions`), the body is the persona prompt. Same-named legacy YAML cards in `characters/cards/` are still loaded as a fallback; `migrate_legacy_cards()` converts them without overwriting existing `.md` files. The built-in YAML fallbacks are mirrored from the Markdown cards.
- `CharacterCard.get_system_prompt()` interpolates `{{name}}/{{description}}/{{skills}}/{{rules}}`; if the body has no `{{rules}}` slot, rules are appended. `scenario` and `post_history_instructions` are appended after the body. Keep tool/protocol instructions out of persona bodies.
- `characters/codec.py` converts Character Card V2/V3 JSON/PNG cards (PNG `chara` text chunk) to/from the Markdown card model; `CharacterManager.import_card()/export_card()` expose it, and the Web API routes `/api/v1/cards/import` (raw JSON or PNG body, max 5 MB) and `/api/v1/cards/{name}/export?format=v2|png` back it.
- `characters/lorebook.py` selects keyword-triggered `lorebook` entries (CJK substring, ASCII word-boundary, optional secondary keys, constant entries, priority + char budget). `Engine._prepare_chat` passes the selected block to `PromptBuilder` as `lore_text`; it is never part of `get_system_prompt()` so it stays out of static prompts.
- `core/persona_switch.py` is a deterministic natural-language switch agent (no LLM call): private messages like “切换到猫娘 / 换成人设学姐 / 扮演御姐” switch `session.character_card`; group messages do the same through `GroupManager` only for bot/group admins. `Engine._maybe_switch_persona` runs after commands/reminders and before chat; it accepts either the card key or the display name. The regex is intentionally narrow (negations, long messages, and “用猫娘的语气” do not trigger) and is covered by `tests/test_persona_switch.py`.
- Card names are validated before being used as file names (path-traversal guard) — extend `validate_card_name` rather than bypassing it.
- `StyleEngine` (`styles/engine.py`) loads presets from `styles/presets.yaml`.
- Roles/cards and presets are package data (`pyproject.toml`); adding one is a data-file change, not a code change.
- `GroupManager` (`core/group_manager.py`) holds per-group overrides keyed by `(platform, group_id)`: character, style, admins, `enabled`, `mention_only`, welcome message. `_prepare_chat` applies group character/style on top of the session config; `process_message` drops group messages when `mention_only` is on and the bot wasn't mentioned. Group settings are persisted to `${CHATBOT_GROUPS_PATH:-data/groups.json}` (atomic replace, set by compose) and loaded at startup; `/group mention|character|style` and admin changes save immediately.
- `CommandParser` (`core/command_parser.py`) → `ParsedCommand`; `CommandHandler` (`core/commands.py`) implements the commands and owns `_run_learning(session)`; `Router` dedups and authorizes before dispatch.

### Security (`security/`)

- `RateLimiter` — 3-tier (user/group/global) + daily cap + URL limit + depth. Uses `deque` for O(1) cleanup.
- `CircuitBreaker` — CLOSED→OPEN→HALF_OPEN with configurable thresholds.
- `ContentFilter` — word replacement (logs only replacement count, not original text).
- `AlertManager` — async-safe callbacks; wired into Telegram when available.

### LLM Providers (`llm/providers/`)

`LLMClient` is the single Engine-facing interface. `ProviderRegistry` manages the primary + backups with pre-output failover, per-provider cooldown, isolated health checks, usage stats, and deterministic resource shutdown. `DeepSeekClient` remains only as a thin constructor-compatible alias. Providers:
- `DeepSeekProvider` — official API via openai SDK
- `OpenAICompatProvider` — any OpenAI-compatible endpoint
- `OllamaProvider` — local models and native NDJSON streaming

Primary/fallback model variants on the same endpoint share one SDK/httpx connection pool. Once a stream has emitted content, an upstream failure produces an interruption suffix instead of splicing a backup response onto partial output. `InstrumentedLLMClient` records Engine pending-call/circuit-breaker state without replacing methods on injected objects.

### Memory (`memory/`)

Three layers:
- **Short-term**: `SessionManager` in `core/session.py`, restored from the optional async `SessionStore` at startup. The old `ShortTermMemory` is deprecated.
- **Long-term**: `LongTermMemory` — ChromaDB vector store. `_doc_counter` avoids expensive `count()` calls.
- **Entity memory**: `EntityMemory` — async (aiosqlite) user profiles. No in-memory cache (DB is source of truth). The full profile stays on disk; prompt injection uses `get_relevant_profile_text()` (keyword/CJK-bigram relevance, only entities with at least one overlapping term, capped by `memory.entity.inject_top_k`) instead of re-reading every entity each turn. `get_profile_text()` remains for full-profile admin/debug use.

Background cleanup: `Engine._start_memory_cleanup()` runs on the configured interval, updates access metadata, and removes memories selected by `MemoryForgetting`.

### Learning (`learning/`)

Self-learning is enabled by default (`config.learning.enabled`) and runs outside the message path:
- `LearningScheduler` (`learning/scheduler.py`) triggers `Engine.run_learning_all` every `trigger_interval` conversations or on the `trigger_schedule` cron; `main.py` wires `Engine._on_conversation_end` to it and drains it on shutdown.
- `LearningAnalyzer` asks the LLM for persona-evolution suggestions from stored conversations; `EvolutionManager` backs up then rewrites the character card YAML (backups and history under `data/learning/`). `auto_apply` gates applying a proposal; a backup is taken unconditionally — `backup_before_learn` is declared in config but currently unused.
- Because this mutates `characters/cards/`, treat unexpected card changes as learning output before assuming hand edits.

### Media & Humanlike Reply (`media/`, `core/humanizer.py`)

- `platforms/qq_segments.py` parses both the OneBot message array and CQ strings (`text/at/reply/image/mface/record/file`). Do not read `raw_message` as content.
- `media/net.py` performs SSRF-hardened fetches: every hop re-resolves, rejects any non-global address, validates redirects, caps size/time. `media/store.py` stores content-addressed files; `media/images.py` normalizes vision payloads; `media/qq_cdn.py` provides `emoji_id` CDN fallback; `core/links.py` fetches and summarizes public URLs through the same path; `media/video.py` samples ffmpeg keyframes into a montage that is sent to the vision model. `media/voice.py` converts voice to WAV, consumes Whisper segments in a worker thread, and unloads the ASR model when idle.
- `media/stickers.py` indexes `characters/stickers/`, auto-collects (private QQ only), supports a review queue, cooldowns and tag selection. `top_tags()` feeds a short sticker protocol and active tag list into the private-QQ system prompt so the model can emit `[sticker:标签]`; tags are sanitized/blocklisted before prompt insertion.
- `core/humanizer.py` converts model text into `ReplyPlan` (bubbles, delays, quote, sticker, voice), strips chat-unfriendly Markdown outside fenced code (unless the user explicitly asked for formatting), and ignores sticker/voice markers inside code fences. `Engine.process_message_plan()` is the platform entry; `process_message()` and streaming remain string-based.
- `core/links.py` auto-summarizes URLs in private chat/admin groups, `/read <url>` reads a page on demand, and `utils/weather.py` backs both `/weather <城市>` and automatic weather phrases in private chat.
- `platforms/qq.py` delivers plans with segment arrays, attaches `reply` only to the first bubble, enforces outbound quotas (`platforms/delivery.py`) and audits text (`security/outbound_guard.py`).
- Media/TTS files must be visible to NapCat: compose mounts `./data/media` into both containers at `/app/data/media`.
- Platform/domain counters (`media_attachments`, `media_fetch_failed`, `stickers_sent`, `voice_*`, `outbound_text`, `outbound_blocked`) are exposed under `/api/v1/metrics` → `runtime.domain`.
- `ReplyPlan` tracks `delivered_bubbles`/`sticker_sent`/`voice_sent`, so a duplicate event resumes after a partial send instead of re-sending bubbles.

### Persona (`memory/persona.py`, `learning/persona_evolution.py`)

Global/user traits live in `persona_*` tables (migration 006), injected as core + retrieved traits through `ContextAssembler`. Evolution reads the append-only archive after a per-session cursor. User traits activate by confidence; global traits auto-activate after evidence from two sessions when `persona.auto_approve_global` is on (default), otherwise they stay `candidate` until an admin approves them. `persona.enabled` disables the legacy role-card rewrite in `learning/evolution.py`.

### Reminders (`core/reminders.py`)

`ReminderStore` persists one-shot/daily reminders in `data/reminders.json` (atomic replace, asyncio lock); times use `Asia/Shanghai` regardless of the UTC container clock. `ReminderService` is started by `Engine.start()` and polls every 20 s, delivering through `Engine.send_qq_text`; failed sends are retried after 60 s and only marked complete after success. Commands: `/remind 10m 喝水`, `/remind 09:00 吃药` (daily), `/remind list`, `/remind del <id>`; group reminders require group-admin rights. Private QQ also accepts natural phrases like `提醒我10分钟后喝水` / `明早8点提醒我带伞` (strict trigger + adjacent time, rejects third-person/negated/interrogative text). The admin agent exposes `add_reminder`/`list_reminders`/`delete_reminder`.

### Conversation archive (`database/archive.py`)

`conversations` remains a replaceable snapshot (delete + reinsert), so its ids are unstable. `conversation_archive` is append-only with unique `(session_id, request_id, turn_index)` and is the cursor source for summaries and persona extraction. It is never used to build the live prompt.

### Usage ledger (`llm/usage.py`)

Providers expose `last_usage`; `UsageLedger` stores idempotent per-turn token buckets and computed cost (migration 007). Recording failures never fail a reply. `/api/v1/usage` aggregates by session/day. A non-zero `usage.daily_budget_micros` blocks new LLM calls once the daily spend is reached.

Admin pages `/persona`, `/stickers`, and `/usage` use the shared `admin.html` shell and the v1 APIs for trait approval, sticker review, and token/cost viewing. `/api/v1/health/ready` also probes the media and sticker directories when those subsystems are enabled.

### Runtime state directories

Writable state lives under `data/`:

- `chatbot.db` (+ WAL/SHM) for SQLite; `chroma/` for vectors
- `media/` for downloaded images/voice/TTS/outbound copies
- `stickers.db` for the rebuildable sticker index
- `learning/`, `logs/`, `tmp/`, `hf/`, `whisper/`
- `napcat/` for QQ account data; `napcat-config/` for NapCat `webui.json` and `onebot11_<QQ>.json`
- `characters/stickers/collected/` for auto-collected stickers (gitignored)

`ChatBot.__init__` creates these runtime directories so `docker compose up` works even without `deploy.sh`. Containers use a read-only rootfs; only `data/`, `characters/`, and the read-only web asset mounts are writable/visible.

### Platform Adapters (`platforms/`)

All extend `BasePlatform` with shared `httpx.AsyncClient` (connection pooling, not per-call). `send_long_message()` handles splitting + human-like delays. `PlatformManager` (`platforms/manager.py`) registers adapters and orchestrates lifecycle: concurrent start with per-adapter failure isolation, then quiesce-all before drain-all on shutdown.

`UnifiedMessage.session_key` = `"{platform}:{user_id}"` or `"{platform}:{group_id}"`. The same subject key isolates entity and vector memory across platforms by default.

`QQPlatform` parses OneBot array/CQ segments (`platforms/qq_segments.py`), resolves quotes from a bounded cache then `get_msg`, downloads private media, and processes independent events with `platforms.qq.event_concurrency` (default 2; same-session ordering still comes from Engine's coordinator). Humanized delivery is private-only; groups fall back to `send_long_message`.

NapCat integration: `ACCOUNT=${CHATBOT_QQ_ACCOUNT:-你的QQ号}` must match the scanned account or restart loses quick login. OneBot WebSocket server is configured in `data/napcat-config/onebot11_<QQ>.json` on port 3001; `./data/media` is mounted into both containers at `/app/data/media`. `web/` assets are bind-mounted read-only so UI changes only need a container restart.

`Platform.WECHAT` and the `wechatpy` dependency are reserved/legacy only — there is no WeChat adapter under `platforms/`.

### Config

`AppConfig` (Pydantic in `utils/config.py`) — the canonical config. `load_config()` tries `config.yaml` → `config/config.yaml` → defaults. Both YAML files use the same schema. Generic `CHATBOT_LLM_API_KEY` / `CHATBOT_LLM_BASE_URL` override the backward-compatible `DEEPSEEK_*` names.

Persistence env overrides: `CHATBOT_DATABASE_URL` and `CHATBOT_MULTI_INSTANCE`. The shipped root config listens on `0.0.0.0` and the app refuses to start without a `CHATBOT_WEB_AUTH_TOKEN` of at least 16 characters — it does not fall back to an anonymous admin panel.

Feature sections (all `StrictConfigModel`, so unknown keys fail startup): `media`, `stickers`, `humanize`, `voice`, `delivery`, `context`, `persona`, `usage`, `admin_agent`, plus `platforms.qq.event_concurrency`, `memory.entity.extract_interval`/`inject_top_k`, and `persistence.messages_per_session`. `voice.enabled` defaults false; `usage.daily_budget_micros > 0` blocks new LLM calls after the daily budget. Mirrored YAML lives in both `config.yaml` and `config/config.yaml`.

Deployment-isolation envs: `COMPOSE_PROJECT_NAME`, `CHATBOT_CONTAINER_PREFIX`, `CHATBOT_WEB_PORT`, `CHATBOT_ONEBOT_PORT`, `NAPCAT_WEBUI_PORT`, `CHATBOT_MEMORY_LIMIT`, `CHATBOT_QQ_ACCOUNT`, `CHATBOT_INSTALL_VOICE`, `CHATBOT_VOICE_ENABLED`, `CHATBOT_GROUPS_PATH`.

### CLI

`python main.py --console` / `-c` for console mode. `python main.py --version` / `-v` for version. Uses `argparse`.

### WebSocket

- `/ws` — generic broadcast
- `/ws/chat` — authenticated streaming chat: receive JSON `{message, card?, style?, client_id?, request_id?}`, push correlated `{chunk, request_id}` then `{done, full, replace, request_id}`
- A repeated `request_id` coalesces an in-flight request or replays its bounded cached result; the browser retries one disconnect with the same ID. Once `complete()` commits a result it stays replayable even if the final WebSocket send fails, so reconnects never re-run the LLM.
- `client_id` is the stable Web session identity. The remote address is only used to isolate legacy clients that omit it.

### Operations

- `/api/v1/*` is the canonical contract; the same routers are mounted at legacy `/api/*` with `include_in_schema=False`.
- `/api/v1/health` — liveness; `/api/v1/health/ready` — engine + persistence, plus media/sticker directory probes when those subsystems are enabled.
- `/api/v1/metrics` — authenticated runtime/engine/persistence snapshot, including `runtime.domain` counters (`media_attachments`, `media_fetch_failed`, `stickers_sent`, `voice_*`, `outbound_*`, `outbound_blocked`). The collector is `observability/metrics.py` (`RuntimeMetrics`), fed by HTTP/WebSocket middleware and platform calls.
- Persona/sticker/usage admin APIs: `/api/v1/persona*`, `/api/v1/stickers*`, `/api/v1/usage`. They back the `/persona`, `/stickers`, `/usage` admin pages and are protected by the normal Web token.
- `POST /api/v1/cards` rejects an existing name with 409; `PUT /api/v1/cards/{name}` backs the current card up under `characters/roles/.backups/` before writing and preserves V2 fields (`scenario/lorebook/...`) it does not edit. `/api/v1/cards/import` accepts a raw JSON or PNG card body; `/api/v1/cards/{name}/export?format=v2|png` returns the ecosystem format. `/api/v1/logs` reads only the tail in a worker thread.
- `web/routes/` splits `api.py`, `auth.py`, `chat.py`, `pages.py`, `ws.py`; `web/idempotency.py` implements WebSocket `request_id` coalescing/replay (see `/ws/chat`).
- OpenAPI exposes only v1 paths with stable operation IDs and typed response schemas. The CDN-backed docs UI is disabled; use authenticated `/openapi.json`.
- HTTP responses carry `X-Request-ID`; HTTP and WebSocket activity feeds `RuntimeMetrics`.
- `SecurityAndAuthMiddleware` is pure ASGI so streaming bodies are not buffered or wrapped in `BaseHTTPMiddleware` cancellation scopes.
- `scripts/watchdog.sh` (cron every 5 min) checks `/api/v1/health` and the QQ platform's `connected` flag, restarts the container after two consecutive failures, and alerts through optional Telegram/webhook settings in `/etc/qqbot-watchdog.env`.
- `scripts/backup.sh` writes daily local archives and supports optional offsite `rclone`/`rsync` via `/etc/qqbot-backup.env`.
- `logging` uses a 10 MiB × 5 `RotatingFileHandler`, so `data/logs` is bounded.

### Admin Agent (`core/admin_agent.py`, `scripts/admin_executor.py`)

Admin-only natural-language operator triggered by messages starting with `admin_agent.trigger_prefix` (default `#`). Engine intercepts it before normal chat only for `security.admin_users`. The deployment config (edited in place on the server, not committed) enables `admin_agent` and lists the operator in `security.admin_users`.

Private admin messages that match `core/admin_direct.py` action patterns (nickname/signature/status/friends/groups/group card/persona content/service/file/command, etc.) are routed to `AdminAgent` **without** `#`. Questions (“怎么改昵称”) and negations are excluded; dangerous actions still require the confirmation code, and “确认 …/取消” replies are forwarded while a confirmation is pending.

- Safe actions run immediately: `list_cards`, `create_card`, `update_card`, `activate_card`, `set_avatar`, `set_nickname`, `set_signature`, `set_online_status`, `send_like`, `list_friends`, `list_groups`, `group_members`, `set_group_card`, `set_friend_remark`, `add_reminder`, `list_reminders`, `delete_reminder`, `add_persona_trait`, `reply`.
- Dangerous actions normally require a short-lived confirmation code: `delete_card`, `set_config`, `service_restart`, `service_rebuild`, `service_migrate`, `write_file`, `run_command`, `send_message`, `send_file`, `post_qzone`, `delete_qzone`, `set_group_name`, `set_group_portrait`, `group_ban`, `group_kick`, `group_whole_ban`, `group_admin`, `group_notice`. The deployment config sets `admin_agent.require_confirmation: false` for the listed owner, so these execute immediately; the host executor audit log, full-mode rate limit, and `data/admin-executor.disabled` kill switch remain the safeguards.
- QQ profile, Qzone, friend/group actions call NapCat's OneBot extensions through `Engine.call_qq_action`; outgoing text goes through `Engine.send_qq_text`. Avatar and Qzone images are restricted to files inside the shared media directory or `file://`/`http(s)://`/`base64://` URIs.
- Admins can also send an image with a caption containing “头像” (e.g. “换成这个头像 / 设成头像”) in private chat to update the avatar without a `#` command; `/avatar` with an attached image does the same. If none of `设为/设置/设置成/设成/换成/改成/换/改/当作/作为/做/当` matches, the message is treated as normal chat. `QQPlatform.set_avatar` retries with a compressed `base64://` payload if NapCat cannot read the shared-media path.
- Card changes write canonical `characters/roles/*.md` and back up the previous file under `characters/roles/.backups/`.
- Config changes are whitelist-checked, validated through `AppConfig`, written by the host executor with a backup, and require a restart to take effect.
- Server/service/file actions go through `scripts/admin_executor.py`, a **host-side systemd service** listening on `data/admin.sock` (root-owned, group `CHATBOT_GID`, mode 660). It authenticates with `data/admin-executor.token` / `CHATBOT_ADMIN_EXECUTOR_TOKEN`. `ADMIN_EXECUTOR_MODE=safe` (default) exposes only status/logs/restart/rebuild/migrate/syntax-check, path-restricted read/write (never `.env`, `data/`, `.git`), and an allowlisted command set. `ADMIN_EXECUTOR_MODE=full` instead runs `bash -lc` as root with a 30 commands/min cap, 5–120 s timeout and audit log; the kill switch is `data/admin-executor.disabled`. Treat full mode as root access.
- `AdminAgent` never receives the executor token; the chatbot container gets the token through `.env`.
- Audit rows are appended to `data/admin-audit.log`.
- The host executor is outside the Compose project; reinstall after moving the project:
  `systemctl enable --now qqbot-admin-executor` (unit points at `/opt/qq-chatbot/scripts/admin_executor.py`).

### Web assets

- `/login`, `/chat`, and all management routes return files from `web/templates/`; their CSS and JavaScript live in `web/static/`.
- Management routes share a lightweight `admin.html` shell; `admin.js` selects the view from the stable URL and uses DOM APIs rather than interpolating user data into HTML.
- Admin layout: desktop sidebar with inline-SVG icons; mobile bottom nav with four primary links plus a “更多” sheet (`#nav-more` / `#nav-more-toggle`). Theme defaults to the OS preference and is persisted in `localStorage["dark"]`.
- `login.html` and `chat.html` are separate shells with their own CSS/JS; keep the shared teal/neutral palette when adding pages.
- The chat page restores history from `/api/v1/sessions/console:web_<clientId>`, shows a desktop session sidebar (summary/recent messages), supports “新对话”, streaming status, textarea composer, and retry on errors. Web chat sessions use the `console` platform and persist like QQ sessions.
- `docker-compose.yml` bind-mounts `web/templates` and `web/static` read-only. UI edits require only `docker compose -p <project> restart chatbot`; backend edits still require an image rebuild.
- The CSP permits same-origin scripts/styles only and contains no `unsafe-inline` exception.
- Static assets are mounted at public `/static/`, still receive security headers, and use `Cache-Control: no-cache`.
- Internal browser requests target canonical `/api/v1`, never the compatibility prefix.
- Web templates/static files are declared in setuptools package data and verified in wheel builds.

### Container security

- Runtime image pins `PYTHON_VERSION=3.12` (voice wheels) and uses configurable `UID`/`GID`, ending as `USER chatbot:chatbot`.
- ffmpeg is installed in the base runtime image (video keyframes). Voice is opt-in: `INSTALL_VOICE=true` adds `requirements-voice.txt` (`faster-whisper==1.2.1`, `av==13.1.0`, `edge-tts`); models are downloaded to `data/whisper`, not baked into the image. QQ SILK voice is converted by NapCat `get_record(out_format=wav)` before ASR, so no `pilk`/codec build is required.
- Compose enables a read-only root filesystem, all-capability drop, `no-new-privileges`, an init process, and a bounded `/tmp` tmpfs. `TMPDIR`, `HF_HOME`, and `WHISPER_CACHE_DIR` point under `/app/data`.
- Chatbot memory defaults to 1536M; the voice `base` model has been verified within that limit (`small` needs ~2G). NapCat is limited to 512M.
- `./data/media` is mounted into NapCat at the same absolute path so `file://` image/record sends resolve. `./data/napcat` holds QQ account data and `./data/napcat-config` holds NapCat WebUI/OneBot config.
- Writable state must remain under mounted `data/` or `characters/`.
- Coexistence with other Compose projects: set `COMPOSE_PROJECT_NAME`, `CHATBOT_CONTAINER_PREFIX`, and the three host port envs. `deploy.sh` reads `COMPOSE_PROJECT_NAME` and passes `-p`; container names become `${CHATBOT_CONTAINER_PREFIX}chatbot` / `...napcat`. `deploy.sh` also warns if host ports are already listening.
- `build.network: host` is intentional: some servers have a broken Docker default-bridge DNS during image builds. Runtime services still use the project-scoped user-defined network.
- `./deploy.sh --voice` builds the voice extras, warms the model into `data/whisper`, and sets a ~2G memory default. ffmpeg itself is unconditional in the base image.

### Database migrations

- Run migrations with `python -m database.migrate` (entry point `chatbot-migrate`); `main.py` refuses to start on a modified historical migration.
- SQLite connections use WAL, foreign-key checks, busy timeout, and pool pre-ping.
- `_migrations` stores a SHA-256 checksum. Modifying an already-applied migration is a startup error; add a new migration instead.
- SQL scripts are split with SQLite's parser boundary detection, not raw semicolon splitting.
- PostgreSQL table creation at startup is serialized with an advisory lock.
- Current migrations: `001_init`, `002_chat_sessions`, `003_session_revision_and_entity_identity`, `004_durable_execution` (staged for the unwired durable feature), `005_persistence_window_attachments`, `006_persona`, `007_llm_usage`.
- SQLite skips an `ALTER TABLE ... ADD COLUMN` when the column/table already exists (transitional metadata bootstrap); PostgreSQL ignores `.sql` files and needs the matching `create_all` model plus hand-written DDL in `database/db.py`.

### Design docs & planned work

- `docs/api.md` — Web API contract and versioning; `docs/persistence.md` — SQLite/PostgreSQL, migrations, scaling, rollback; `docs/media.md` — media/humanlike reply chain; `docs/voice.md` — opt-in voice deployment; `docs/durable-execution.md` — inbound/outbound durable queue design; `docs/architecture-plan.md` — current baseline and P0/P1 status; `docs/reference-architecture.md`, `docs/platform-roadmap.md` — longer-term direction.
- `docs/superpowers/specs/` — dated design specs, including earlier v2/optimization and comprehensive-improvement designs.
- `docs/superpowers/specs/2026-10-23-qq-humanlike-bot-design.md` — design for QQ private-chat humanlike features: DeepSeek vision, sticker library/auto-collect, voice ASR/TTS, segmented/quote replies, bounded context, and versioned persona evolution. It is now implemented; the module map above is authoritative where the spec and code differ (segment parsing, archive cursors, PostgreSQL DDL, voice opt-in).
- That spec narrows the humanlike feature work to QQ private chat. Existing Telegram support remains, but is not being extended; WeChat is not an active target.

## Key Conventions

- All tests: `pytest` + `pytest-asyncio`. `asyncio_mode = auto` in pyproject.toml (duplicated in `pytest.ini`). `tests/conftest.py` provides `default_config` / `config_with_key` fixtures; ruff ignores `F401`/`F841` under `tests/**`.
- Lint gate: `python -m ruff check .` — only `E9` + `F` are enforced, line length 120, double quotes.
- `requires-python >=3.11`; the Docker image pins 3.12; CI tests 3.11–3.14.
- `utils/version.py` is the single runtime version source and must match `pyproject.toml`; `tests/test_packaging.py` also keeps `requirements.txt` and pyproject dependencies in sync.
- `from __future__ import annotations` in all source files (PEP 604).
- Chinese UI strings throughout.
- LLM calls return friendly error strings, never raise.
- Engine DI via `**deps` — mock any subsystem by passing it in.
- `build/`, `dist/`, and `chatbot.egg-info/` are stale packaging artifacts; never edit them or trust them over source.
- `requirements-voice.txt` (copied unconditionally by the Dockerfile) and `docker-compose.postgres.yml` (used by `deploy.sh --postgres`/CI) must stay tracked even though voice is optional.
- Window dev: `encoding="utf-8"` for temp files with Chinese content.
- When adding middleware: subclass `Middleware`, implement hooks, `Pipeline.add()`.
- When adding a new LLM provider: subclass `LLMProvider`, implement `chat`, preferably `chat_stream`, `health_check`, token counting, and `aclose`; register via `ProviderRegistry`.
- Web UI: keep scripts/styles external (strict CSP has no `unsafe-inline`), use `textContent`/DOM APIs instead of HTML interpolation, and keep mobile states/loading/empty/error covered. `tests/test_web.py` asserts the admin shell, CSP, page routes, and the `/api/v1` OpenAPI path set — update it when adding pages/endpoints.
- New config sections must be added to `AppConfig` and both YAML files; `StrictConfigModel` rejects unknown keys.
- Media/voice output paths must stay under `media.storage_dir`; `VoiceService` disables itself otherwise. Outbound `file://` paths are copied to `media.outbound_dir`, which NapCat reads from the shared volume.
- SQLAlchemy async requires `greenlet`; keep the dependency as `sqlalchemy[asyncio]` in both `requirements.txt` and `pyproject.toml`.
