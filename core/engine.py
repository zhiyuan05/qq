from __future__ import annotations
"""核心引擎"""
import json
import uuid
from typing import Optional

from utils.config import AppConfig
from utils.logger import setup_logger
from platforms.models import UnifiedMessage
from core.session import SessionManager, Session
from core.router import MessageRouter
from core.prompt_builder import PromptBuilder
from core.command_parser import ParsedCommand
from core.commands import CommandHandler
from core.group_manager import GroupManager
from core.pipeline import Pipeline, PipelineContext
from core.middleware.rate_limit import RateLimitMiddleware
from core.middleware.filter import FilterMiddleware
from core.middleware.active_window import ActiveWindowMiddleware
from core.middleware.safety import SafetyMiddleware
from core.middleware.content_filter import ContentFilterMiddleware
from core.middleware.split import SplitMiddleware
from core.middleware.delay import DelayMiddleware
from characters.manager import CharacterManager
from styles.engine import StyleEngine
from llm.client import DeepSeekClient
from llm.token_counter import count_tokens
from memory.short_term import ShortTermMemory
from memory.entity_memory import EntityMemory
from memory.long_term import LongTermMemory
from memory.summarizer import ConversationSummarizer
from security.rate_limiter import RateLimiter
from security.circuit_breaker import CircuitBreaker
from security.content_filter import ContentFilter

logger = setup_logger("engine")


class Engine:
    def __init__(self, config: AppConfig, **deps):
        """依赖注入支持：传入 deps 覆盖自动创建的子系统"""
        self.config = config

        self.character_manager = deps.get("character_manager") or CharacterManager()
        self.style_engine = deps.get("style_engine") or StyleEngine()
        self.session_manager = deps.get("session_manager") or SessionManager(
            max_messages=config.memory.short_term.max_messages,
            summarize_after=config.memory.short_term.summarize_after,
        )
        self.router = deps.get("router") or MessageRouter(
            command_prefix=config.bot.command_prefix,
            admin_users=config.security.admin_users,
        )
        self.group_manager = deps.get("group_manager") or GroupManager()
        self.entity_memory = deps.get("entity_memory") or EntityMemory()
        self.long_term_memory = deps.get("long_term_memory") or (
            LongTermMemory(top_k=config.memory.long_term.top_k) if config.memory.long_term.enabled else None
        )
        self.short_term_memory = deps.get("short_term_memory") or ShortTermMemory(
            max_messages=config.memory.short_term.max_messages
        )
        self.prompt_builder = deps.get("prompt_builder") or PromptBuilder(entity_memory=self.entity_memory)
        self.summarizer = deps.get("summarizer") or ConversationSummarizer()

        self._llm: Optional[DeepSeekClient] = deps.get("llm_client")
        self._on_conversation_end = None  # callback for learning scheduler
        self._platforms = []  # registered platforms for broadcast
        self._pending_llm_calls = 0  # 优雅关闭用
        self._init_pipeline(config)
        self.command_handler = deps.get("command_handler") or CommandHandler(self)

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
            # 只包装一次
            original_chat = self._llm.chat

            async def tracked_chat(messages, **kwargs):
                self._pending_llm_calls += 1
                try:
                    return await original_chat(messages, **kwargs)
                finally:
                    self._pending_llm_calls -= 1

            self._llm.chat = tracked_chat
        return self._llm

    async def wait_for_pending_calls(self, timeout: float = 10.0):
        """等待进行中的 LLM 调用完成（优雅关闭用）"""
        import asyncio
        waited = 0.0
        while self._pending_llm_calls > 0 and waited < timeout:
            await asyncio.sleep(0.1)
            waited += 0.1
        if self._pending_llm_calls > 0:
            logger.warning(f"关闭超时，仍有 {self._pending_llm_calls} 个 LLM 调用未完成")

    # ── Pipeline ──────────────────────────────────────────────
    def _init_pipeline(self, config: AppConfig):
        rate_limiter = RateLimiter(
            per_user_per_minute=config.rate_limit.max_per_minute,
            per_group_per_minute=60,
            global_per_hour=config.rate_limit.max_per_hour,
        )
        breaker = CircuitBreaker()

        middleware = [
            RateLimitMiddleware(rate_limiter),
            FilterMiddleware(),
            ActiveWindowMiddleware(),
            SafetyMiddleware(breaker),
        ]

        if config.security.content_filter:
            cf = ContentFilter()
            middleware.append(ContentFilterMiddleware(cf))

        middleware.append(SplitMiddleware(max_length=config.security.max_message_length))
        middleware.append(DelayMiddleware())

        self.pipeline = Pipeline(middleware)

    async def _run_pipeline_stage(self, ctx: PipelineContext, stage: str) -> bool:
        """Run a single stage across all middleware in order."""
        for mw in self.pipeline._middleware:
            method = getattr(mw, stage, None)
            if method and not await method(ctx):
                return False
        return True

    async def process_message(self, msg: UnifiedMessage) -> Optional[str]:
        # 请求追踪 ID
        request_id = uuid.uuid4().hex[:12]
        logger.debug(f"[{request_id}] process_message from {msg.user_id}@{msg.platform.value}: {msg.content[:50]}")

        # 创建管道上下文
        ctx = PipelineContext(
            user_id=msg.user_id,
            content=msg.content,
            platform=msg.platform.value,
            group_id=msg.group_id,
            is_owner=self.router.is_admin(msg.user_id),
        )
        ctx.metadata["request_id"] = request_id

        # ═══ PIPELINE: on_receive ═══ (限流、空消息过滤、活跃时段)
        if not await self._run_pipeline_stage(ctx, "on_receive"):
            logger.info(f"[{request_id}] blocked by pipeline: {ctx.block_reason}")
            return ctx.block_reason if ctx.blocked else None

        # 群聊 @mention 检测
        if msg.group_id:
            is_mentioned, clean_content = self._check_mention(msg)
            group_cfg = self.group_manager.get(msg.platform.value, msg.group_id)
            if group_cfg and group_cfg.mention_only and not is_mentioned:
                return None  # mention_only 模式，未被@不回复
            if is_mentioned:
                msg = UnifiedMessage(
                    platform=msg.platform,
                    user_id=msg.user_id,
                    content=clean_content,
                    message_type=msg.message_type,
                    group_id=msg.group_id,
                    nickname=msg.nickname,
                    message_id=msg.message_id,
                )

        should_process, reason, parsed = self.router.route(msg)
        if not should_process:
            if reason == "重复消息":
                return None
            return reason

        # ═══ PIPELINE: on_llm ═══ (LLM 调用前钩子)
        if not await self._run_pipeline_stage(ctx, "on_llm"):
            return ctx.block_reason if ctx.blocked else None

        # 处理消息
        if parsed and parsed.is_command:
            ctx.response = await self._handle_command(msg, parsed)
        else:
            ctx.response = await self._handle_chat(msg)

        logger.debug(f"[{request_id}] response generated: {len(ctx.response or '')} chars")

        # ═══ PIPELINE: on_response ═══ (内容过滤、消息拆分)
        if not await self._run_pipeline_stage(ctx, "on_response"):
            return ctx.block_reason if ctx.blocked else None

        # ═══ PIPELINE: on_send ═══ (熔断检查、发送延迟)
        if not await self._run_pipeline_stage(ctx, "on_send"):
            return ctx.block_reason if ctx.blocked else None

        return ctx.response

    def _check_mention(self, msg: UnifiedMessage) -> tuple:
        """检测消息是否@了机器人，返回 (is_mentioned, clean_content)"""
        content = msg.content
        # QQ @格式: [CQ:at,qq=12345] 或 @用户名
        # 微信 @格式: @用户名
        bot_name = self.config.bot.name
        mentioned = False

        if f"@{bot_name}" in content:
            mentioned = True
            content = content.replace(f"@{bot_name}", "").strip()
        elif "[CQ:at," in content:
            # QQ CQ码格式
            import re
            at_pattern = r'\[CQ:at,qq=(\d+)\]'
            matches = re.findall(at_pattern, content)
            if matches:
                mentioned = True  # 群里有人被@就认为可能是在@机器人
                content = re.sub(at_pattern, '', content).strip()

        return mentioned, content

    async def _handle_chat(self, msg: UnifiedMessage) -> str:
        is_new_session = msg.session_key not in self.session_manager._sessions
        session = self.session_manager.get_or_create(
            msg,
            default_character=self.config.bot.default_character,
            default_style=self.config.bot.default_style,
        )

        # 群聊使用群专属配置覆盖会话配置
        char_name = session.character_card
        style_name = session.style
        if msg.group_id:
            group_cfg = self.group_manager.get(msg.platform.value, msg.group_id)
            if group_cfg:
                if group_cfg.character_card:
                    char_name = group_cfg.character_card
                if group_cfg.style:
                    style_name = group_cfg.style

        character = self.character_manager.get(char_name) or self.character_manager.get_default()
        style = self.style_engine.get(style_name) or self.style_engine.get_default()

        memory_results = []
        if self.long_term_memory:
            memory_results = self.long_term_memory.search(msg.content, user_id=msg.user_id)

        history = session.get_context_messages(self.config.memory.short_term.max_messages)
        messages = self.prompt_builder.build(
            character=character,
            style=style,
            user_message=msg.content,
            history=history,
            memory_results=memory_results,
            user_id=msg.user_id,
        )

        llm = self._get_llm()
        response = await llm.chat(messages)

        user_tokens = count_tokens([{"role": "user", "content": msg.content}])
        assistant_tokens = count_tokens([{"role": "assistant", "content": response}])
        session.add_message("user", msg.content, token_count=user_tokens)
        session.add_message("assistant", response, token_count=assistant_tokens)

        if self.long_term_memory:
            self.long_term_memory.store(
                text=f"用户: {msg.content}\n助手: {response}",
                metadata={"user_id": msg.user_id, "session": session.session_id},
            )

        if self.config.memory.entity.auto_extract:
            await self._extract_entities(msg.user_id, msg.content, response)

        if self.session_manager.should_summarize(session):
            await self._summarize_session(session)

        if self._on_conversation_end:
            self._on_conversation_end()

        # 新会话时附加角色问候语
        if is_new_session and character.greeting:
            response = f"{character.greeting}\n\n{response}"

        return response

    async def _handle_command(self, msg: UnifiedMessage, parsed: ParsedCommand) -> str:
        session = self.session_manager.get_or_create(
            msg,
            default_character=self.config.bot.default_character,
            default_style=self.config.bot.default_style,
        )
        return await self.command_handler.handle(msg, parsed, session)

    async def run_learning_all(self):
        """对所有活跃会话执行学习（供调度器调用）"""
        sessions = self.session_manager.list_sessions()
        count = 0
        for sid in sessions:
            session = self.session_manager.get(sid)
            if session and len(session.messages) >= 10:
                try:
                    await self.command_handler._run_learning(session)
                    count += 1
                except Exception as e:
                    logger.error(f"会话 {sid} 学习失败: {e}")
        logger.info(f"批量学习完成，处理了 {count} 个会话")

    async def _extract_entities(self, user_id: str, user_msg: str, assistant_msg: str):
        try:
            llm = self._get_llm()
            prompt = [
                {"role": "system", "content": "从对话中提取用户信息，返回JSON格式: {\"entities\": [{\"type\": \"preference/fact/skill/person\", \"key\": \"...\", \"value\": \"...\"}]}。如果没有值得记录的信息，返回空列表。只返回JSON。"},
                {"role": "user", "content": f"用户: {user_msg}\n助手: {assistant_msg}"},
            ]
            result = await llm.chat(prompt)
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
        try:
            old_messages = session.get_context_messages()
            summary = await self.summarizer.summarize(old_messages)
            session.summary = summary
            keep = self.config.memory.short_term.max_messages // 2
            session.messages = session.messages[-keep:]
            logger.info(f"会话 {session.session_id} 已摘要，保留 {keep} 条消息")
        except Exception as e:
            logger.error(f"摘要失败: {e}")
