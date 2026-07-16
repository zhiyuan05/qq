from __future__ import annotations
"""命令处理器 - 从 Engine 拆分"""
from typing import List, Optional

from core.session import Session
from core.command_parser import ParsedCommand
from platforms.models import UnifiedMessage
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
    def router(self):
        return self._engine.router

    async def handle(self, msg: UnifiedMessage, parsed: ParsedCommand, session: Session) -> str:
        cmd = parsed.command
        sub = parsed.subcommand

        handlers = {
            "help": lambda: self._help_text(),
            "new": lambda: self._cmd_new(session),
            "undo": lambda: self._cmd_undo(session),
            "card": lambda: self._handle_card(session, sub, parsed.args),
            "style": lambda: self._handle_style(session, sub, parsed.args),
            "memory": lambda: self._handle_memory(msg, sub, parsed.args),
            "learn": lambda: self._handle_learn(session, sub, parsed.args),
            "context": lambda: self._cmd_context(session),
            "token": lambda: self._cmd_token(session),
            "history": lambda: self._cmd_history(session, parsed.args),
            "settings": lambda: self._handle_settings(session, sub, parsed.args),
            "admin": lambda: self._handle_admin(msg, session, sub, parsed.args),
            "search": lambda: self._handle_search(parsed.args),
            "group": lambda: self._handle_group(msg, sub, parsed.args),
        }
        handler = handlers.get(cmd)
        if handler:
            return await handler() if cmd in ("memory", "learn", "admin", "search") else handler()
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
/search <关键词> - 联网搜索
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

    def _handle_card(self, session: Session, sub: str, args: List[str]) -> str:
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

    async def _handle_learn(self, session: Session, sub: str, args: List[str]) -> str:
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

    async def _run_learning(self, session: Session) -> str:
        try:
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
                    for platform in self._engine._platforms:
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

    async def _handle_search(self, args: List[str]) -> str:
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
