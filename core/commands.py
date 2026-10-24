from __future__ import annotations
"""命令处理器 - 从 Engine 拆分"""
import asyncio
from typing import List

from core.session import Session
from core.command_parser import ParsedCommand
from core.links import extract_urls, fetch_link_summary
from utils.weather import weather_report
from platforms.models import MessageType, UnifiedMessage
from utils.logger import setup_logger

logger = setup_logger("commands")


class CommandHandler:
    """处理所有 / 开头的指令，不依赖 Engine 内部状态"""

    def __init__(self, engine):
        self._engine = engine

    @property
    def config(self):
        return self._engine.config

    @property
    def character_manager(self):
        return self._engine.character_manager

    @property
    def style_engine(self):
        return self._engine.style_engine

    @property
    def session_manager(self):
        return self._engine.session_manager

    @property
    def entity_memory(self):
        return self._engine.entity_memory

    @property
    def long_term_memory(self):
        return self._engine.long_term_memory

    @property
    def group_manager(self):
        return self._engine.group_manager

    @property
    def persona_store(self):
        return getattr(self._engine, "persona_store", None)

    @property
    def sticker_library(self):
        return getattr(self._engine, "sticker_library", None)

    @property
    def router(self):
        return self._engine.router

    async def handle(self, msg: UnifiedMessage, parsed: ParsedCommand, session: Session) -> str:
        cmd = parsed.command
        sub = parsed.subcommand

        handlers = {
            "help": lambda: self._help_text(),
            "new": lambda: self._cmd_new(session),
            "undo": lambda: self._cmd_undo(session),
            "card": lambda: self._handle_card(msg, session, sub, parsed.args),
            "style": lambda: self._handle_style(session, sub, parsed.args),
            "memory": lambda: self._handle_memory(msg, sub, parsed.args),
            "learn": lambda: self._handle_learn(msg, session, sub, parsed.args),
            "persona": lambda: self._handle_persona(msg, session, sub, parsed.args),
            "sticker": lambda: self._handle_sticker(msg, sub, parsed.args),
            "context": lambda: self._cmd_context(session),
            "token": lambda: self._cmd_token(session),
            "history": lambda: self._cmd_history(session, parsed.args),
            "settings": lambda: self._handle_settings(session, sub, parsed.args),
            "admin": lambda: self._handle_admin(msg, session, sub, parsed.args),
            "search": lambda: self._handle_search(parsed.args),
            "read": lambda: self._handle_read(parsed.args),
            "weather": lambda: self._handle_weather(parsed.args),
            "group": lambda: self._handle_group(msg, sub, parsed.args),
            "avatar": lambda: self._handle_avatar(msg),
            "remind": lambda: self._handle_remind(msg, sub, parsed.args),
        }
        handler = handlers.get(cmd)
        if handler:
            result = handler()
            if hasattr(result, "__await__"):
                return await result
            return result
        return f"未知指令: /{cmd}，输入 /help 查看帮助"

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
/persona show - 查看人格特质
/persona approve <id> - 审核全局特质(管理员)
/persona forget <关键词> - 删除特质
/persona rollback [n] - 回滚人格
/persona reset - 重置人格档案
/sticker list [status] - 查看表情包
/sticker approve <hash前缀> - 审核表情包(管理员)
/sticker reject <hash前缀> - 拒绝表情包(管理员)
/avatar - 用附带图片设置机器人QQ头像(管理员)
/remind 10m 喝水 - 10分钟后提醒
/remind 09:00 吃药 - 每天9点提醒
/remind list - 查看提醒
/remind del <id> - 删除提醒
/search <关键词> - 联网搜索
/read <链接> - 读取网页内容摘要
/weather <城市> - 查询实时天气
/group info - 群配置(群聊)
/group mention on/off - @才回复(群聊)
/settings show - 查看设置
/context - 查看会话状态
/history [n] - 查看历史
/undo - 撤销上轮对话
/new - 新会话
/help - 帮助"""

    def _cmd_new(self, session: Session) -> str:
        self.session_manager.clear(session.session_id)
        return "已开启新会话。"

    def _cmd_undo(self, session: Session) -> str:
        session.undo_last_exchange()
        return "已撤销上一轮对话。"

    def _cmd_context(self, session: Session) -> str:
        return f"当前会话: {len(session.messages)} 条消息\n角色卡: {session.character_card}\n风格: {session.style}"

    def _cmd_token(self, session: Session) -> str:
        total = sum(m.token_count for m in session.messages)
        return f"当前会话 token 估算: {total}"

    def _cmd_history(self, session: Session, args: List[str]) -> str:
        n = int(args[0]) if args else 10
        recent = session.get_recent_messages(n)
        if not recent:
            return "暂无对话历史"
        lines = []
        for m in recent:
            role = "我" if m["role"] == "user" else "AI"
            lines.append(f"[{role}] {m['content'][:100]}")
        return "\n".join(lines)

    def _handle_card(self, msg: UnifiedMessage, session: Session, sub: str, args: List[str]) -> str:
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
            if not self.router.is_admin(msg.user_id, msg.platform.value):
                return "权限不足，仅管理员可创建共享角色卡"
            name = args[0]
            from characters.manager import CharacterCard
            card = CharacterCard({"name": name, "system_prompt": f"你是{name}。", "rules": ["以中文输出为主"]})
            self.character_manager.save(name, card)
            return f"已创建角色卡: {name}"
        return "用法: /card list | /card show [名称] | /card use <名称> | /card create <名称>"

    def _handle_style(self, session: Session, sub: str, args: List[str]) -> str:
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

    async def _handle_memory(self, msg: UnifiedMessage, sub: str, args: List[str]) -> str:
        if sub == "profile":
            if not self.config.memory.entity.enabled:
                return "实体记忆功能已关闭"
            profile = await self.entity_memory.get_profile_text(msg.principal_key)
            return profile or "暂无用户画像"
        elif sub == "recall" and args:
            query = " ".join(args)
            if self.long_term_memory:
                results = await asyncio.to_thread(
                    self.long_term_memory.search,
                    query,
                    user_id=msg.principal_key,
                )
                if results:
                    return "\n".join(f"- {r['text'][:100]}" for r in results)
            return "未找到相关记忆"
        elif sub == "forget" and args:
            if not self.config.memory.entity.enabled:
                return "实体记忆功能已关闭"
            key = " ".join(args)
            if await self.entity_memory.forget(msg.principal_key, key):
                return f"已删除记忆: {key}"
            return f"未找到记忆: {key}"
        elif sub == "clear":
            await self.entity_memory.clear(msg.principal_key)
            if self.long_term_memory:
                await asyncio.to_thread(self.long_term_memory.clear, msg.principal_key)
            return "已清空所有记忆"
        return "用法: /memory profile | /memory recall <关键词> | /memory forget <主题> | /memory clear"

    async def _handle_learn(
        self,
        msg: UnifiedMessage,
        session: Session,
        sub: str,
        args: List[str],
    ) -> str:
        if sub == "now":
            if not self.router.is_admin(msg.user_id, msg.platform.value):
                return "权限不足，仅管理员可触发学习"
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
            if not self.router.is_admin(msg.user_id, msg.platform.value):
                return "权限不足，仅管理员可重置学习结果"
            from learning.evolution import EvolutionManager
            em = EvolutionManager()
            if em.reset_card(session.character_card):
                return f"已重置角色卡 {session.character_card} 的学习结果"
            return "重置失败"
        return "用法: /learn now | /learn status | /learn history | /learn reset"

    async def _run_learning(self, session: Session) -> str:
        try:
            evolution = getattr(self._engine, "persona_evolution", None)
            if evolution:
                character = self.character_manager.get(session.character_card)
                result = await evolution.evolve_session(
                    session.session_id,
                    f"{session.platform}:{session.user_id}",
                    base_persona=character.get_system_prompt() if character else "",
                    force=True,
                )
                return (
                    f"人格演化完成：提取 {result['extracted']} 条，"
                    f"生效 {result['applied']} 条，候选 {result['candidates']} 条"
                )
            from learning.analyzer import LearningAnalyzer
            from learning.evolution import EvolutionManager
            conversations = [{"role": m.role, "content": m.content} for m in session.messages]
            if not conversations:
                return "没有对话记录可供学习"
            analyzer = LearningAnalyzer(llm_client=self._engine._get_llm())
            traits = await analyzer.analyze(conversations)
            if not traits:
                return "学习分析未产生结果"
            evolution = EvolutionManager()
            evolution.backup_card(session.character_card)
            if self.config.learning.auto_apply:
                evolution.apply_learned_traits(session.character_card, traits)
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

    def _can_manage_reminders(self, msg: UnifiedMessage) -> bool:
        if msg.group_id is None:
            return True
        return self.router.is_admin(msg.user_id, msg.platform.value) or self.group_manager.is_admin(
            msg.platform.value,
            msg.group_id,
            msg.user_id,
        )

    async def _handle_remind(self, msg: UnifiedMessage, sub: str, args: List[str]) -> str:
        import time as _time

        from core.reminders import format_time, next_clock_time, parse_duration

        if not self._can_manage_reminders(msg):
            return "只有管理员可以在群里设置提醒"
        target_type = "group" if msg.group_id else "user"
        target_id = str(msg.group_id or msg.user_id)
        store = self._engine.reminder_store

        if sub in {"list", "列表"}:
            items = await store.list_for(target_type, target_id)
            if not items:
                return "当前没有提醒"
            lines = [
                f"- {item.id} {format_time(item.due_at)} {'每天' if item.repeat == 'daily' else '一次'} {item.text}"
                for item in items
            ]
            return "提醒列表:\n" + "\n".join(lines)

        if sub in {"del", "delete", "删除"}:
            if not args:
                return "用法: /remind del <id>"
            reminder_id = args[0].strip().lstrip("#")
            items = await store.list_for(target_type, target_id)
            if not any(item.id == reminder_id for item in items):
                return f"提醒不存在: {reminder_id}"
            await store.remove(reminder_id)
            return f"已删除提醒 {reminder_id}"

        repeat = "once"
        rest = list(args)
        if sub in {"daily", "每天"}:
            repeat = "daily"
            spec = rest[0] if rest else ""
            text = " ".join(rest[1:])
        elif sub in {"once", "一次"}:
            spec = rest[0] if rest else ""
            text = " ".join(rest[1:])
        else:
            spec = sub
            text = " ".join(rest)
        if not spec or not text.strip():
            return "用法: /remind 10m 喝水 | /remind 09:00 吃药 | /remind list | /remind del <id>"

        delay = parse_duration(spec)
        if delay is not None:
            due_at = _time.time() + delay
        else:
            due_at = next_clock_time(spec)
            if due_at is None:
                return "时间格式不支持，示例：10m、1h30m、09:00、daily 09:00"
            if sub not in {"once", "一次", "daily", "每天"}:
                repeat = "daily"
        reminder = await self._engine.add_reminder(
            target_type=target_type,
            target_id=target_id,
            text=text.strip(),
            due_at=due_at,
            repeat=repeat,
            created_by=str(msg.user_id),
        )
        repeat_label = "每天" if reminder.repeat == "daily" else "一次"
        return f"已设置提醒 {reminder.id}：{format_time(reminder.due_at)}（{repeat_label}）{reminder.text}"

    async def _handle_avatar(self, msg: UnifiedMessage) -> str:
        if not self.router.is_admin(msg.user_id, msg.platform.value):
            return "只有管理员可以设置机器人头像"
        image_path = next(
            (item.path for item in msg.attachments if item.kind is MessageType.IMAGE and item.path),
            None,
        )
        if not image_path:
            return "请在同一条消息里附带图片再发送 /avatar"
        return await self._engine.set_qq_avatar(image_path)

    async def _handle_persona(
        self,
        msg: UnifiedMessage,
        session: Session,
        sub: str,
        args: List[str],
    ) -> str:
        store = self.persona_store
        if store is None:
            return "人格演化未启用"
        user_key = msg.principal_key
        is_admin = self.router.is_admin(msg.user_id, msg.platform.value)

        def render(traits, title: str) -> List[str]:
            lines = [title]
            if not traits:
                lines.append("- 无")
            lines.extend(
                f"- #{trait.id} [{trait.status}/{trait.tier}] {trait.category}: {trait.value}"
                f"（{trait.confidence:.2f}）"
                for trait in traits
            )
            return lines

        if sub in {"", "show"}:
            lines = render(await store.show("user", user_key), "个人人格特质:")
            if is_admin:
                lines.extend(render(await store.show("global", "global"), "全局人格特质:"))
            return "\n".join(lines)

        if sub == "approve":
            if not is_admin:
                return "权限不足，仅管理员可审核全局特质"
            if not args or not args[0].isdigit():
                return "用法: /persona approve <特质ID>"
            approved = await store.approve("global", "global", int(args[0]))
            return "已批准该全局特质" if approved else "该特质不满足启用条件或不存在"

        if sub == "forget":
            keyword = " ".join(args).strip()
            if not keyword:
                return "用法: /persona forget <关键词>"
            removed = await store.forget("user", user_key, keyword)
            if is_admin:
                removed += await store.forget("global", "global", keyword)
            return f"已删除 {removed} 条特质"

        if sub == "rollback":
            steps = int(args[0]) if args and args[0].isdigit() else 1
            rolled = await store.rollback("user", user_key, steps)
            if is_admin:
                rolled = await store.rollback("global", "global", steps) or rolled
            return "已回滚人格档案" if rolled else "没有可回滚的版本"

        if sub == "reset":
            await store.reset("user", user_key)
            if is_admin:
                await store.reset("global", "global")
            return "已重置人格档案"

        return "用法: /persona show | approve <id> | forget <关键词> | rollback [n] | reset"

    async def _handle_sticker(self, msg: UnifiedMessage, sub: str, args: List[str]) -> str:
        library = self.sticker_library
        if library is None:
            return "表情包功能未启用"

        if sub in {"", "list", "pending"}:
            status = "candidate" if sub == "pending" else (args[0] if args else "")
            rows = await library.list(status)
            lines = [f"表情包（{len(rows)} 张）:"]
            if not rows:
                lines.append("- 无")
            lines.extend(
                f"- {sticker.hash[:12]} [{sticker.status}] {','.join(sticker.tags)}"
                f" 使用 {sticker.used_count} 次"
                for sticker in rows[:20]
            )
            return "\n".join(lines)

        if sub in {"approve", "reject"}:
            if not self.router.is_admin(msg.user_id, msg.platform.value):
                return "权限不足，仅管理员可审核表情包"
            if not args:
                return f"用法: /sticker {sub} <hash前缀>"
            prefix = args[0]
            rows = await library.list("")
            match = next((sticker for sticker in rows if sticker.hash.startswith(prefix)), None)
            if match is None:
                return "未找到匹配的表情包"
            changed = await (library.approve(match.hash) if sub == "approve" else library.reject(match.hash))
            return "已更新表情包状态" if changed else "更新失败"

        return "用法: /sticker list | pending | approve <hash前缀> | reject <hash前缀>"

    def _handle_settings(self, session: Session, sub: str, args: List[str]) -> str:
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

    async def _handle_admin(self, msg: UnifiedMessage, session: Session, sub: str, args: List[str]) -> str:
        if not self.router.is_admin(msg.user_id, msg.platform.value):
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
                self._engine.config = load_config()
                return "配置已重载"
            except Exception as e:
                return f"重载失败: {e}"
        elif sub == "broadcast" and args:
            text = " ".join(args)
            count = 0
            for sid in self.session_manager.list_sessions():
                s = self.session_manager.get(sid)
                if s:
                    for platform in self._engine.platforms:
                        try:
                            await platform.send_message(s.user_id, text)
                            count += 1
                            break
                        except Exception:
                            continue
            return f"广播已发送到 {count} 个会话"
        return "用法: /admin users | /admin logs | /admin reload | /admin broadcast <消息>"

    def _handle_group(self, msg: UnifiedMessage, sub: str, args: List[str]) -> str:
        if not msg.group_id:
            return "此指令仅在群聊中可用"
        group = self.group_manager.get_or_create(msg.platform.value, msg.group_id)
        can_manage = self.router.is_admin(
            msg.user_id,
            msg.platform.value,
        ) or self.group_manager.is_admin(msg.platform.value, msg.group_id, msg.user_id)
        if sub == "info":
            return (f"群配置:\n"
                    f"角色卡: {group.character_card or '默认'}\n"
                    f"风格: {group.style or '默认'}\n"
                    f"仅@回复: {'是' if group.mention_only else '否'}\n"
                    f"管理员: {', '.join(group.admins) or '无'}")
        elif sub == "mention":
            if not can_manage:
                return "权限不足"
            mode = args[0] if args else "on"
            group.mention_only = mode.lower() in ("on", "true", "1")
            self.group_manager.save()
            return f"仅@回复模式: {'开启' if group.mention_only else '关闭'}"
        elif sub == "character" and args:
            if not can_manage:
                return "权限不足"
            name = args[0]
            if self.character_manager.get(name):
                group.character_card = name
                self.group_manager.save()
                return f"群角色卡已设为: {name}"
            return f"角色卡不存在: {name}"
        elif sub == "style" and args:
            if not can_manage:
                return "权限不足"
            name = args[0]
            if self.style_engine.get(name):
                group.style = name
                self.group_manager.save()
                return f"群风格已设为: {name}"
            return f"风格不存在: {name}"
        return "用法: /group info | /group mention on/off | /group character <名称> | /group style <名称>"

    async def _handle_search(self, args: List[str]) -> str:
        if not args:
            return "用法: /search <关键词>"
        try:
            from utils.search import duckduckgo_search, format_search_results
            query = " ".join(args)
            results = await duckduckgo_search(query)
            return format_search_results(query, results)
        except Exception as e:
            return f"搜索失败: {e}"

    async def _handle_read(self, args: List[str]) -> str:
        urls = extract_urls(" ".join(args))
        if not urls and args:
            urls = [args[0]] if args[0].startswith(("http://", "https://")) else []
        if not urls:
            return "用法: /read <链接>"
        try:
            summary = await fetch_link_summary(urls[0], max_chars=2500)
        except Exception as e:
            return f"读取失败: {e}"
        if not summary:
            return "无法读取该链接（可能需要登录、被屏蔽或不是网页）。"
        return summary[:3500]

    async def _handle_weather(self, args: List[str]) -> str:
        city = " ".join(args).strip()
        if not city:
            return "用法: /weather <城市>"
        report = await weather_report(city)
        return report or f"没有找到「{city}」的天气信息，试试更具体的城市名。"
