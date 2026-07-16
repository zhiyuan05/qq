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

# Start in console mode (no platform needed)
python main.py --console

# Start normally (requires QQ/Telegram config)
python main.py
```

## Architecture

**Message flow**: Platform adapter → `Engine.process_message()` → Router (dedup + command parse) → LLM chat or command handler → response

**Engine** (`core/engine.py`) is the central orchestrator. It owns all subsystems: SessionManager, MessageRouter, PromptBuilder, CharacterManager, StyleEngine, LLM client, and all three memory layers. Platform adapters (QQ, Telegram) call `engine.process_message()` and get a string response back.

**Two config systems coexist**:
- `AppConfig` (Pydantic models in `utils/config.py`) — used by Engine, loaded from `config.yaml` + `.env`
- `Config` (dot-notation wrapper, also in `utils/config.py`) — used by new security/pipeline/Telegram components, loaded from `config/config.yaml`

**Security layer** (`security/`): `RateLimiter` (3-tier: per-user, per-group, global + daily cap + depth control), `CircuitBreaker` (CLOSED→OPEN→HALF_OPEN), `ContentFilter` (sensitive word replacement), `AlertManager` (callback-based alerting).

**Message pipeline** (`core/pipeline.py`): 4-stage middleware chain (on_receive → on_llm → on_response → on_send). Built-in middleware in `core/middleware/`: rate_limit, filter, active_window, safety, content_filter, split, delay. Pipeline is wired but Engine still uses direct flow — middleware integration is incremental.

**LLM providers** (`llm/providers/`): `ProviderRegistry` with primary + backup failover. `DeepSeekClient` wraps the registry for backward compatibility. `LLMClient` is the new multi-provider client.

**Memory system**: Short-term (in-session buffer via `SessionManager`), Long-term (ChromaDB vectors in `memory/long_term.py`), Entity (SQLite-backed user profiles in `memory/entity_memory.py`). `ConversationSummarizer` compresses history when session exceeds threshold.

**Platform adapters** inherit `BasePlatform` (`platforms/base.py`). `handle_callback()` in base class routes through `send_long_message()` which handles splitting + human-like delays. `UnifiedMessage` (`platforms/models.py`) is the normalized message format.

## Key Conventions

- All tests use `pytest` + `pytest-asyncio`. Async tests use `@pytest.mark.asyncio`.
- Windows dev: use `encoding="utf-8"` when writing test temp files with Chinese content.
- Chinese UI strings throughout (error messages, bot responses, logs).
- `platforms/wechat.py` was removed due to account ban risk. WeChat is not currently supported.
- LLM calls return friendly error strings on failure rather than raising exceptions.
- Session key format: `{platform}:{group_id}` for groups, `{platform}:{user_id}` for private chat.
