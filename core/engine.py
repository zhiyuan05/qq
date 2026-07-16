"""核心引擎"""
import json
from typing import Optional

from utils.config import AppConfig
from utils.logger import setup_logger
from platforms.models import UnifiedMessage
from core.session import SessionManager, Session
from core.router import MessageRouter
from core.prompt_builder import PromptBuilder
from core.command_parser import ParsedCommand
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
    def __init__(self, config: AppConfig):
        self.config = config

        self.character_manager = CharacterManager()
        self.style_engine = StyleEngine()
        self.session_manager = SessionManager(
            max_messages=config.memory.short_term.max_messages,
            summarize_after=config.memory.short_term.summarize_after,
        )
        self.router = MessageRouter(
            command_prefix=config.bot.command_prefix,
            admin_users=config.security.admin_users,
        )
        self.group_manager = GroupManager()
        self.entity_memory = EntityMemory()
        self.long_term_memory = LongTermMemory(top_k=config.memory.long_term.top_k) if config.memory.long_term.enabled else None
        self.short_term_memory = ShortTermMemory(max_messages=config.memory.short_term.max_messages)
        self.prompt_builder = PromptBuilder(entity_memory=self.entity_memory)
        self.summarizer = ConversationSummarizer()

        self._llm: Optional[DeepSeekClient] = None
        self._on_conversation_end = None  # callback for learning scheduler
        self._platforms = []  # registered platforms for broadcast
        self._init_pipeline(config)

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
        # 创建管道上下文
        ctx = PipelineContext(
            user_id=msg.user_id,
            content=msg.content,
            platform=msg.platform.value,
            group_id=msg.group_id,
            is_owner=self.router.is_admin(msg.user_id),
        )

        # ═══ PIPELINE: on_receive ═══ (限流、空消息过滤、活跃时段)
        if not await self._run_pipeline_stage(ctx, "on_receive"):
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
        elif cmd == "settings":
            return self._handle_settings(session, sub, parsed.args)
        elif cmd == "admin":
            return await self._handle_admin(msg, session, sub, parsed.args)
        elif cmd == "search":
            return await self._handle_search(parsed.args)
        elif cmd == "group":
            return self._handle_group_command(msg, sub, parsed.args)

        return f"未知指令: /{cmd}，输入 /help 查看帮助"

    def _handle_card_command(self, session: Session, sub: str, args: list) -> str:
        if sub == "list":
            cards = self.character_manager.list_cards()
            current = session.character_card
            lines = [f"{'→ ' if c == current else '  '}{c}" for c in cards]
            return "角色卡列表:\n" + "\n".join(lines)
        elif sub == "show":
            name = args[0] if args else session.character_card
            card = self.character_manager.get(name)
            if not card:
                return f"角色卡不存在: {name}"
            return (f"角色卡: {card.name}\n"
                    f"描述: {card.description}\n"
                    f"技能: {card.skills}\n"
                    f"规则: {', '.join(card.rules)}\n"
                    f"问候语: {card.greeting}\n"
                    f"学习版本: {card.learned_traits.get('learning_version', 0)}")
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
        return "用法: /card list | /card show [名称] | /card use <名称> | /card create <名称>"

    def _handle_style_command(self, session: Session, sub: str, args: list) -> str:
        if sub == "list":
            styles = self.style_engine.list_styles()
            current = session.style
            lines = [f"{'→ ' if s == current else '  '}{s}" for s in styles]
            return "风格列表:\n" + "\n".join(lines)
        elif sub == "show":
            name = args[0] if args else session.style
            style = self.style_engine.get(name)
            if not style:
                return f"风格不存在: {name}"
            return (f"风格: {style.name}\n"
                    f"描述: {style.description}\n"
                    f"提示词: {style.prompt_modifier[:100]}...")
        elif sub == "use" and args:
            name = args[0]
            if self.style_engine.get(name):
                session.style = name
                return f"已切换风格: {name}"
            return f"风格不存在: {name}"
        return "用法: /style list | /style show [名称] | /style use <名称>"

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
            return await self._run_learning(session)
        elif sub == "status":
            return f"当前角色卡: {session.character_card}\n学习间隔: {self.config.learning.trigger_interval} 条对话"
        elif sub == "history":
            from learning.evolution import EvolutionManager
            em = EvolutionManager()
            history = em.get_evolution_history(session.character_card)
            if not history:
                return "暂无学习历史"
            lines = []
            for h in history[-5:]:
                lines.append(f"- {h.get('summary', '无摘要')}")
            return "学习历史:\n" + "\n".join(lines)
        elif sub == "reset":
            from learning.evolution import EvolutionManager
            em = EvolutionManager()
            if em.reset_card(session.character_card):
                return f"已重置角色卡 {session.character_card} 的学习结果"
            return "重置失败"
        return "用法: /learn now | /learn status | /learn history | /learn reset"

    async def run_learning_all(self):
        """对所有活跃会话执行学习（供调度器调用）"""
        sessions = self.session_manager.list_sessions()
        count = 0
        for sid in sessions:
            session = self.session_manager.get(sid)
            if session and len(session.messages) >= 10:
                try:
                    await self._run_learning(session)
                    count += 1
                except Exception as e:
                    logger.error(f"会话 {sid} 学习失败: {e}")
        logger.info(f"批量学习完成，处理了 {count} 个会话")

    async def _run_learning(self, session: Session) -> str:
        """执行完整学习流程"""
        try:
            from learning.analyzer import LearningAnalyzer
            from learning.evolution import EvolutionManager

            # 收集近期对话
            conversations = []
            for m in session.messages:
                conversations.append({"role": m.role, "content": m.content})

            if not conversations:
                return "没有对话记录可供学习"

            # 分析
            analyzer = LearningAnalyzer(llm_client=self._get_llm())
            traits = await analyzer.analyze(conversations)

            if not traits:
                return "学习分析未产生结果"

            # 备份 + 应用
            evolution = EvolutionManager()
            evolution.backup_card(session.character_card)

            if self.config.learning.auto_apply:
                evolution.apply_learned_traits(session.character_card, traits)

            # 保存报告
            report = {
                "character_card": session.character_card,
                "conversations_analyzed": len(conversations),
                "traits_learned": traits,
                "summary": traits.get("summary", ""),
            }
            evolution.save_report(session.character_card, report)

            summary = traits.get("summary", "学习完成")
            return f"学习完成！分析了 {len(conversations)} 条对话。\n摘要: {summary}"

        except Exception as e:
            logger.error(f"学习失败: {e}")
            return f"学习失败: {e}"

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

    def _handle_settings(self, session: Session, sub: str, args: list) -> str:
        if sub == "show":
            return f"当前设置:\n角色卡: {session.character_card}\n风格: {session.style}"
        elif sub == "character" and args:
            name = args[0]
            if self.character_manager.get(name):
                session.character_card = name
                return f"默认角色卡已设为: {name}"
            return f"角色卡不存在: {name}"
        elif sub == "style" and args:
            name = args[0]
            if self.style_engine.get(name):
                session.style = name
                return f"默认风格已设为: {name}"
            return f"风格不存在: {name}"
        return "用法: /settings show | /settings character <名称> | /settings style <名称>"

    async def _handle_admin(self, msg: UnifiedMessage, session: Session, sub: str, args: list) -> str:
        if not self.router.is_admin(msg.user_id):
            return "权限不足，仅管理员可用"

        if sub == "users":
            sessions = self.session_manager.list_sessions()
            return f"活跃会话数: {len(sessions)}\n" + "\n".join(sessions[:20])
        elif sub == "logs":
            try:
                with open(self.config.logging.file, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-20:]
                return "".join(lines)
            except Exception:
                return "无法读取日志"
        elif sub == "reload":
            from utils.config import load_config
            try:
                self.config = load_config()
                return "配置已重载"
            except Exception as e:
                return f"重载失败: {e}"
        elif sub == "broadcast" and args:
            text = " ".join(args)
            count = 0
            for sid in self.session_manager.list_sessions():
                session = self.session_manager.get(sid)
                if session:
                    for platform in self._platforms:
                        try:
                            await platform.send_message(session.user_id, text)
                            count += 1
                            break
                        except Exception:
                            continue
            return f"广播已发送到 {count} 个会话"
        return "用法: /admin users | /admin logs | /admin reload | /admin broadcast <消息>"

    def _handle_group_command(self, msg: UnifiedMessage, sub: str, args: list) -> str:
        if not msg.group_id:
            return "此指令仅在群聊中可用"

        group = self.group_manager.get_or_create(msg.platform.value, msg.group_id)

        if sub == "info":
            return (f"群配置:\n"
                    f"角色卡: {group.character_card or '默认'}\n"
                    f"风格: {group.style or '默认'}\n"
                    f"仅@回复: {'是' if group.mention_only else '否'}\n"
                    f"管理员: {', '.join(group.admins) or '无'}")
        elif sub == "mention":
            if not self.router.is_admin(msg.user_id):
                return "权限不足"
            mode = args[0] if args else "on"
            group.mention_only = mode.lower() in ("on", "true", "1")
            return f"仅@回复模式: {'开启' if group.mention_only else '关闭'}"
        elif sub == "character" and args:
            name = args[0]
            if self.character_manager.get(name):
                group.character_card = name
                return f"群角色卡已设为: {name}"
            return f"角色卡不存在: {name}"
        elif sub == "style" and args:
            name = args[0]
            if self.style_engine.get(name):
                group.style = name
                return f"群风格已设为: {name}"
            return f"风格不存在: {name}"
        return "用法: /group info | /group mention on/off | /group character <名称> | /group style <名称>"

    async def _handle_search(self, args: list) -> str:
        if not args:
            return "用法: /search <关键词>"
        query = " ".join(args)
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1},
                    timeout=10,
                )
                if resp.status_code != 200:
                    return "搜索失败"
                data = resp.json()
                results = []
                if data.get("Abstract"):
                    results.append(f"📖 {data.get('Heading', '')}\n{data['Abstract']}\n{data.get('AbstractURL', '')}")
                for topic in data.get("RelatedTopics", [])[:3]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append(f"🔗 {topic['Text'][:100]}\n{topic.get('FirstURL', '')}")
                return "\n\n".join(results) if results else f"未找到关于「{query}」的结果"
        except Exception as e:
            return f"搜索失败: {e}"

    def _help_text(self) -> str:
        return """可用指令:
/card list - 列出角色卡
/card show [名称] - 查看角色卡详情
/card use <名称> - 切换角色卡
/style list - 列出风格
/style show [名称] - 查看风格详情
/style use <名称> - 切换风格
/memory profile - 查看用户画像
/memory recall <关键词> - 搜索记忆
/memory clear - 清空记忆
/learn now - 触发学习
/learn status - 学习状态
/search <关键词> - 联网搜索
/group info - 群配置(群聊)
/group mention on/off - @才回复(群聊)
/settings show - 查看设置
/context - 查看会话状态
/history [n] - 查看历史
/undo - 撤销上轮对话
/new - 新会话
/help - 帮助"""
