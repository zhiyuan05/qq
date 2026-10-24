"""Admin-only natural-language operator for personas, config and server actions."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

import yaml

from characters.manager import CharacterCard, validate_card_name
from core.reminders import format_time, next_clock_time, parse_duration
from learning.persona_evolution import _extract_json_object
from platforms.models import MessageType, UnifiedMessage
from utils.config import AppConfig
from utils.logger import setup_logger

logger = setup_logger("admin_agent")

_DANGEROUS = {
    "delete_card",
    "set_config",
    "service_restart",
    "service_rebuild",
    "service_migrate",
    "write_file",
    "run_command",
    "send_message",
    "post_qzone",
    "delete_qzone",
    "set_group_name",
    "group_ban",
    "group_kick",
    "group_whole_ban",
    "group_admin",
    "group_notice",
    "send_file",
    "set_group_portrait",
}

_STATUS_CODES = {
    "online": (10, 0),
    "在线": (10, 0),
    "away": (30, 0),
    "离开": (30, 0),
    "invisible": (40, 0),
    "隐身": (40, 0),
    "busy": (50, 0),
    "忙碌": (50, 0),
    "qme": (60, 0),
    "q我吧": (60, 0),
    "dnd": (70, 0),
    "请勿打扰": (70, 0),
    "listening": (10, 1028),
    "听歌中": (10, 1028),
}

_ALLOWED_CONFIG_ROOTS = {
    "bot",
    "llm",
    "rate_limit",
    "security",
    "delay",
    "memory",
    "humanize",
    "stickers",
    "voice",
    "context",
    "persona",
    "usage",
    "media",
    "learning",
    "platforms",
}
_FORBIDDEN_CONFIG_KEYS = {
    "api_key",
    "token",
    "access_token",
    "auth_token",
    "executor_token",
    "secret",
    "password",
    "database_url",
}

_SYSTEM_PROMPT = """你是部署在服务器上的 QQ 机器人的管理员操作代理。
只输出一个 JSON 对象，不要解释。格式：
{"action": "...", "args": {...}}

可用 action：
- list_cards {}
- create_card {"name","description","body","rules":[],"greeting"}
- update_card {"name","description"?,"body"?,"rules"?,"greeting"?}
- delete_card {"name"}
- activate_card {"name"}
- set_avatar {}  # 用本条消息附带的图片设置机器人 QQ 头像
- set_nickname {"nickname"}
- set_signature {"text"}
- set_online_status {"status":"online|away|busy|dnd|invisible|qme|listening"}
- send_message {"target_type":"user|group","target_id":"QQ号或群号","text":"..."}
- send_like {"user_id","times"}
- post_qzone {"content","images"?:["file://路径或URL"],"ugc_right"?:1|4|16|64|128,"target_uins"?:["QQ号"]}
- delete_qzone {"tid"}
- list_friends {}
- list_groups {}
- group_members {"group_id"}
- set_group_card {"group_id","card"}  # 设置机器人在群里的名片
- set_group_name {"group_id","group_name"}
- group_ban {"group_id","user_id","duration"}  # duration 秒，0 解除
- group_kick {"group_id","user_id"}
- group_whole_ban {"group_id","enable"}
- group_admin {"group_id","user_id","enable"}
- group_notice {"group_id","content"}  # 附带图片会自动作为公告图
- set_group_portrait {"group_id"}  # 用本条消息附带的图片设置群头像
- send_file {"target_type":"user|group","target_id","path":"data/logs/chatbot.log","name"?}
- set_friend_remark {"user_id","remark"}
- add_reminder {"text","minutes"?:10,"at"?:"09:00","repeat"?:"once|daily","target_type"?:"user|group","target_id"?}
- list_reminders {}
- delete_reminder {"id"}
- add_persona_trait {"scope":"global|user","category":"speech_style|preference|opinion|relationship|emotional_pattern|boundary","trait_key","value","confidence"}
- set_config {"path":"humanize.min_delay","value":...}
- service_status {}
- service_logs {"lines":100}
- service_restart {}
- service_rebuild {}
- service_migrate {}
- service_test {}  # 运行容器内语法检查
- read_file {"path":"core/engine.py","max_bytes":8000}
- write_file {"path":"core/foo.py","content":"..."}
- run_command {"command":"git status"}
- reply {"text":"..."}

规则：
1. 管理员要求直接执行；不确定时用 reply 询问。
2. 人设卡正文只写身份、性格、语气、关系、边界，不写工具用法。
3. 修改配置、写文件、删除人设、重启/重建/迁移/命令、发消息、发说说、群管理必须由系统二次确认，你只提出动作。
4. 除非管理员明确要求，不要读取 .env、密钥或令牌文件；永远不要在回复中输出密钥内容。
5. run_command 会在宿主机执行，服务器已启用完整权限模式；优先使用 docker compose、systemctl、git、python、nginx 等常规工具。删除数据、格式化磁盘、修改 SSH 配置等高风险命令必须先说明影响，系统会要求确认。
6. 配置路径不得包含 api_key/token/secret/password/database_url。
7. 本条消息附带图片时，post_qzone 可以不传 images，系统会自动附图；设置头像用 set_avatar。"""


class ExecutorClient:
    def __init__(self, socket_path: str, token: str, timeout: float = 600.0):
        self.socket_path = socket_path
        self.token = token
        self.timeout = timeout

    async def call(self, action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("管理员执行器令牌未配置")
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
                try:
                    request = {"token": self.token, "action": action, "args": args or {}}
                    writer.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
                    await writer.drain()
                    line = await reader.readline()
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(f"管理员执行器不可用 ({type(exc).__name__})") from exc
        if not line:
            raise RuntimeError("管理员执行器无响应")
        try:
            payload = json.loads(line.decode("utf-8"))
        except ValueError as exc:
            raise RuntimeError("管理员执行器返回无效 JSON") from exc
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error", "执行失败"))[:500])
        result = payload.get("result")
        return result if isinstance(result, dict) else {"output": result}


class AdminAgent:
    def __init__(self, engine, config):
        self.engine = engine
        self.config = config
        self.prefix = config.trigger_prefix or "#"
        self.executor = ExecutorClient(config.executor_socket, config.executor_token)
        self._pending: dict[str, tuple[float, str, dict[str, Any]]] = {}

    async def handle(self, msg: UnifiedMessage) -> str:
        content = msg.content.strip()
        if content.startswith(self.prefix):
            content = content[len(self.prefix) :].strip()
        if not content:
            return "用法：# <你的要求>，例如「# 新建一个人设叫冷面助手，性格高冷，说话简短」"

        key = msg.principal_key
        pending = self._pending.get(key)
        if pending is not None:
            expires, code, action = pending
            if time.monotonic() > expires:
                self._pending.pop(key, None)
            elif content in {"取消", "cancel"}:
                self._pending.pop(key, None)
                return "已取消待执行操作。"
            elif content.replace("确认", "").strip() == code:
                self._pending.pop(key, None)
                return await self._execute(action, msg)

        images = [
            item.path for item in msg.attachments if item.kind is MessageType.IMAGE and item.path
        ]
        user_content = content
        if images:
            user_content = f"{content}\n（本条消息附带图片，可使用 set_avatar 设为头像）"

        try:
            llm = self.engine._get_llm()
            raw = await llm.chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ]
            )
        except Exception as exc:
            return f"管理员 Agent 调用失败：{type(exc).__name__}"

        action = _extract_json_object(raw or "")
        if not action or not isinstance(action.get("action"), str):
            return "没听懂这个管理请求，请换一种说法，或加上更明确的目标。"
        name = action["action"].strip()
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        if name in _DANGEROUS:
            stored_args = dict(args)
            if name == "post_qzone" and not stored_args.get("images"):
                # Attachments are gone by the time the confirmation arrives; keep file URIs now.
                stored_args["images"] = self._qzone_images({}, msg)
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._pending[key] = (
                time.monotonic() + self.config.confirmation_ttl_seconds,
                code,
                {"action": name, "args": stored_args},
            )
            return self._confirmation_prompt(
                name,
                args,
                code,
                self.config.confirmation_ttl_seconds,
            )
        return await self._execute({"action": name, "args": args}, msg)

    @staticmethod
    def _confirmation_prompt(action: str, args: dict[str, Any], code: str, ttl: int) -> str:
        detail = json.dumps(args, ensure_ascii=False)[:500]
        return (
            f"将执行高危操作：{action}\n参数：{detail}\n"
            f"确认请在 {ttl} 秒内回复：#确认 {code}\n回复「#取消」可放弃。"
        )

    async def _execute(self, action: dict[str, Any], msg: UnifiedMessage) -> str:
        name = action.get("action", "")
        args = action.get("args") or {}
        try:
            if name == "reply":
                return str(args.get("text", ""))[:4000] or "收到。"
            if name == "list_cards":
                manager = self.engine.character_manager
                return "当前人设卡：\n" + "\n".join(
                    f"- {card}" for card in manager.list_cards()
                )
            if name == "create_card":
                return self._create_card(args)
            if name == "update_card":
                return self._update_card(args)
            if name == "delete_card":
                name_value = validate_card_name(str(args.get("name", "")))
                return "已删除人设" if self.engine.character_manager.delete(name_value) else "人设不存在"
            if name == "activate_card":
                return await self._activate_card(str(args.get("name", "")), msg)
            if name == "set_avatar":
                image_path = next(
                    (item.path for item in msg.attachments if item.kind is MessageType.IMAGE and item.path),
                    None,
                )
                if not image_path:
                    return "这条消息没有附带图片，请发送图片并在文字里说明要设置头像。"
                return await self.engine.set_qq_avatar(image_path)
            if name == "set_nickname":
                return await self._set_nickname(args)
            if name == "set_signature":
                return await self._set_signature(args)
            if name == "set_online_status":
                return await self._set_online_status(args)
            if name == "send_message":
                return await self._send_message(args)
            if name == "send_like":
                return await self._send_like(args)
            if name == "post_qzone":
                return await self._post_qzone(args, msg)
            if name == "delete_qzone":
                return await self._delete_qzone(args)
            if name == "add_reminder":
                return await self._add_reminder(args, msg)
            if name == "list_reminders":
                return await self._list_reminders(msg)
            if name == "delete_reminder":
                return await self._delete_reminder(args, msg)
            if name == "list_friends":
                return await self._list_friends()
            if name == "list_groups":
                return await self._list_groups()
            if name == "group_members":
                return await self._group_members(args)
            if name == "set_group_card":
                return await self._set_group_card(args)
            if name == "set_group_portrait":
                return await self._set_group_portrait(args, msg)
            if name == "send_file":
                return await self._send_file(args, msg)
            if name in {
                "set_group_name",
                "group_ban",
                "group_kick",
                "group_whole_ban",
                "group_admin",
                "group_notice",
                "set_friend_remark",
            }:
                return await self._social_action(name, args, msg)
            if name == "add_persona_trait":
                return await self._add_trait(args)
            if name == "set_config":
                return await self._set_config(args)
            if name == "service_status":
                return await self._executor_text("status", {})
            if name == "service_logs":
                return await self._executor_text("logs", {"lines": int(args.get("lines", 100))})
            if name == "service_restart":
                return await self._executor_text("restart", {"delay_seconds": 3})
            if name == "service_rebuild":
                return await self._executor_text("rebuild", {"delay_seconds": 3})
            if name == "service_migrate":
                return await self._executor_text("migrate", {})
            if name == "service_test":
                return await self._executor_text("test", {})
            if name == "read_file":
                return await self._executor_text("read_file", {"path": args.get("path", ""), "max_bytes": int(args.get("max_bytes", 8000))})
            if name == "write_file":
                return await self._executor_text("write_file", {"path": args.get("path", ""), "content": args.get("content", "")})
            if name == "run_command":
                return await self._executor_text("run_command", {"command": args.get("command", "")})
        except Exception as exc:
            logger.warning("admin action %s failed (%s)", name, type(exc).__name__)
            return f"执行失败：{type(exc).__name__}: {str(exc)[:300]}"
        return f"不支持的管理动作：{name}"

    @staticmethod
    def _digits(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text.isdigit():
            raise ValueError(f"{label}必须是数字")
        return text

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "开启", "是"}

    async def _add_reminder(self, args: dict[str, Any], msg: UnifiedMessage) -> str:
        text = str(args.get("text") or "").strip()[:500]
        if not text:
            return "提醒内容不能为空。"
        repeat = "daily" if str(args.get("repeat") or "").strip().lower() in {"daily", "每天"} else "once"
        at = str(args.get("at") or "").strip()
        minutes = args.get("minutes")
        if minutes not in (None, ""):
            try:
                due_at = time.time() + max(1.0, float(minutes) * 60)
            except (TypeError, ValueError):
                return "minutes 必须是数字。"
        elif at:
            delay = parse_duration(at)
            if delay is not None:
                due_at = time.time() + delay
            else:
                due_at = next_clock_time(at)
                if due_at is None:
                    return "时间格式不支持，示例：09:00 或 30m。"
                if not str(args.get("repeat") or "").strip():
                    repeat = "daily"
        else:
            return "请提供 minutes 或 at。"

        target_type = "group" if str(args.get("target_type") or "").lower() == "group" else "user"
        target_id = str(args.get("target_id") or "").strip()
        if target_type == "group":
            target_id = target_id or str(msg.group_id or "")
            if not target_id:
                return "群提醒需要 target_id（群号）。"
        else:
            target_id = target_id or str(msg.user_id)

        reminder = await self.engine.add_reminder(
            target_type=target_type,
            target_id=target_id,
            text=text,
            due_at=due_at,
            repeat=repeat,
            created_by=str(msg.user_id),
        )
        label = "每天" if reminder.repeat == "daily" else "一次"
        return f"已设置提醒 {reminder.id}：{format_time(reminder.due_at)}（{label}）{reminder.text}"

    async def _list_reminders(self, msg: UnifiedMessage) -> str:
        items = await self.engine.reminder_store.list_for("user", str(msg.user_id))
        if not items:
            return "当前没有提醒。"
        lines = [
            f"- {item.id} {format_time(item.due_at)} {'每天' if item.repeat == 'daily' else '一次'} {item.text}"
            for item in items
        ]
        return "提醒列表：\n" + "\n".join(lines)

    async def _delete_reminder(self, args: dict[str, Any], msg: UnifiedMessage) -> str:
        reminder_id = str(args.get("id") or "").strip().lstrip("#")
        if not reminder_id:
            return "缺少提醒 id。"
        items = await self.engine.reminder_store.list_for("user", str(msg.user_id))
        if not any(item.id == reminder_id for item in items):
            return f"提醒不存在：{reminder_id}"
        await self.engine.reminder_store.remove(reminder_id)
        return f"已删除提醒 {reminder_id}。"

    async def _set_nickname(self, args: dict[str, Any]) -> str:
        nickname = str(args.get("nickname") or "").strip()[:36]
        if not nickname:
            return "昵称不能为空。"
        await self.engine.call_qq_action("set_qq_profile", {"nickname": nickname})
        return f"昵称已改为「{nickname}」。"

    async def _set_signature(self, args: dict[str, Any]) -> str:
        text = str(args.get("text") or "").strip()[:200]
        await self.engine.call_qq_action("set_self_longnick", {"longNick": text})
        return "个性签名已更新。"

    async def _set_online_status(self, args: dict[str, Any]) -> str:
        key = str(args.get("status") or "").strip().lower()
        codes = _STATUS_CODES.get(key)
        if codes is None:
            return "支持的状态：在线/离开/忙碌/请勿打扰/隐身/Q我吧/听歌中。"
        await self.engine.call_qq_action(
            "set_online_status",
            {"status": codes[0], "ext_status": codes[1], "battery_status": 0},
        )
        return f"在线状态已切换为「{key}」。"

    async def _send_message(self, args: dict[str, Any]) -> str:
        target_type = str(args.get("target_type") or "user").strip().lower()
        target_id = self._digits(args.get("target_id"), "目标")
        text = str(args.get("text") or "").strip()[:2000]
        if not text:
            return "消息内容不能为空。"
        group_id = target_id if target_type == "group" else None
        await self.engine.send_qq_text(target_id, text, group_id=group_id)
        return f"已发送到{'群' if group_id else '好友'} {target_id}。"

    async def _send_like(self, args: dict[str, Any]) -> str:
        user_id = self._digits(args.get("user_id"), "QQ号")
        times = max(1, min(10, int(args.get("times") or 1)))
        await self.engine.call_qq_action("send_like", {"user_id": user_id, "times": times})
        return f"已给 {user_id} 点赞 {times} 次。"

    def _qzone_images(self, args: dict[str, Any], msg: UnifiedMessage) -> list[str]:
        images = [str(item).strip() for item in (args.get("images") or []) if str(item).strip()]
        for item in msg.attachments:
            if item.kind is MessageType.IMAGE and item.path:
                images.append(item.path)
        cleaned: list[str] = []
        for image in images:
            if image.startswith(("http://", "https://", "file://", "base64://")):
                cleaned.append(image)
            elif Path(image).is_file():
                cleaned.append(Path(image).resolve().as_uri())
        return cleaned[:9]

    async def _post_qzone(self, args: dict[str, Any], msg: UnifiedMessage) -> str:
        content = str(args.get("content") or "").strip()[:2000]
        images = self._qzone_images(args, msg)
        if not content and not images:
            return "说说内容为空，请提供文字或附上一张图片。"
        right = int(args.get("ugc_right") or 1)
        if right not in {1, 4, 16, 64, 128}:
            return "查看权限只能是 1(所有人)/4(好友)/16(部分好友)/64(仅自己)/128(部分不可见)。"
        targets = [str(item) for item in (args.get("target_uins") or []) if str(item).isdigit()]
        if right in {16, 128} and not targets:
            return "该权限需要提供 target_uins 列表。"
        data = await self.engine.call_qq_action(
            "send_qzone_msg",
            {"content": content, "images": images, "ugc_right": right, "target_uins": targets},
        )
        tid = str((data or {}).get("tid", ""))
        return f"说说已发布{f'（tid={tid}）' if tid else ''}。"

    async def _delete_qzone(self, args: dict[str, Any]) -> str:
        tid = str(args.get("tid") or "").strip()[:64]
        if not tid:
            return "缺少说说 tid。"
        await self.engine.call_qq_action("delete_qzone_msg", {"tid": tid})
        return "说说已删除。"

    async def _list_friends(self) -> str:
        data = await self.engine.call_qq_action("get_friend_list", {})
        rows = data if isinstance(data, list) else []
        lines = [
            f"- {row.get('user_id')} {row.get('remark') or row.get('nickname') or ''}".strip()
            for row in rows[:100]
        ]
        return f"好友共 {len(rows)} 个：\n" + "\n".join(lines) if lines else "好友列表为空。"

    async def _list_groups(self) -> str:
        data = await self.engine.call_qq_action("get_group_list", {})
        rows = data if isinstance(data, list) else []
        lines = [
            f"- {row.get('group_id')} {row.get('group_name', '')}（{row.get('member_count', '?')}人）"
            for row in rows[:100]
        ]
        return f"群聊共 {len(rows)} 个：\n" + "\n".join(lines) if lines else "群列表为空。"

    async def _group_members(self, args: dict[str, Any]) -> str:
        group_id = self._digits(args.get("group_id"), "群号")
        data = await self.engine.call_qq_action("get_group_member_list", {"group_id": group_id})
        rows = data if isinstance(data, list) else []
        lines = [
            f"- {row.get('user_id')} {row.get('card') or row.get('nickname') or ''}（{row.get('role', '')}）"
            for row in rows[:100]
        ]
        return f"群 {group_id} 共 {len(rows)} 人：\n" + "\n".join(lines) if lines else "没有获取到群成员。"

    async def _self_id(self) -> str:
        platform = self.engine.qq_platform()
        if platform is not None and platform.self_id:
            return platform.self_id
        data = await self.engine.call_qq_action("get_login_info", {})
        return str((data or {}).get("user_id", ""))

    async def _set_group_card(self, args: dict[str, Any]) -> str:
        group_id = self._digits(args.get("group_id"), "群号")
        card = str(args.get("card") or "").strip()[:30]
        user_id = await self._self_id()
        if not user_id:
            return "无法确定机器人 QQ 号。"
        await self.engine.call_qq_action(
            "set_group_card",
            {"group_id": group_id, "user_id": user_id, "card": card},
        )
        return f"群 {group_id} 的机器人名片已更新。"

    async def _send_file(self, args: dict[str, Any], msg: UnifiedMessage) -> str:
        target_type = "group" if str(args.get("target_type") or "").lower() == "group" else "user"
        target_id = str(args.get("target_id") or "").strip()
        if target_type == "group":
            target_id = target_id or str(msg.group_id or "")
            if not target_id:
                return "群文件需要 target_id（群号）。"
        else:
            target_id = target_id or str(msg.user_id)
        path = str(args.get("path") or "").strip()
        if not path:
            return "请提供要发送的文件路径（仅限 data/logs 或 data/media）。"
        name = str(args.get("name") or "").strip()[:80]
        await self.engine.send_qq_file(
            target_type=target_type,
            target_id=target_id,
            source_path=path,
            name=name,
        )
        return f"文件已发送到{'群' if target_type == 'group' else '好友'} {target_id}。"

    async def _set_group_portrait(self, args: dict[str, Any], msg: UnifiedMessage) -> str:
        group_id = self._digits(args.get("group_id"), "群号")
        image_path = next(
            (item.path for item in msg.attachments if item.kind is MessageType.IMAGE and item.path),
            None,
        )
        if not image_path:
            return "请在同一条消息里附带一张图片再设置群头像。"
        await self.engine.call_qq_action(
            "set_group_portrait",
            {"group_id": group_id, "file": Path(image_path).resolve().as_uri()},
        )
        return f"群 {group_id} 的头像已更新。"

    async def _social_action(
        self,
        name: str,
        args: dict[str, Any],
        msg: UnifiedMessage | None = None,
    ) -> str:
        if name == "set_friend_remark":
            user_id = self._digits(args.get("user_id"), "QQ号")
            remark = str(args.get("remark") or "").strip()[:30]
            await self.engine.call_qq_action("set_friend_remark", {"user_id": user_id, "remark": remark})
            return f"好友 {user_id} 的备注已更新。"
        group_id = self._digits(args.get("group_id"), "群号")
        if name == "set_group_name":
            group_name = str(args.get("group_name") or "").strip()[:30]
            if not group_name:
                return "群名不能为空。"
            await self.engine.call_qq_action("set_group_name", {"group_id": group_id, "group_name": group_name})
            return "群名已更新。"
        if name == "group_ban":
            user_id = self._digits(args.get("user_id"), "QQ号")
            duration = max(0, min(2_592_000, int(args.get("duration") or 0)))
            await self.engine.call_qq_action(
                "set_group_ban",
                {"group_id": group_id, "user_id": user_id, "duration": duration},
            )
            return "已解除禁言。" if duration == 0 else f"已禁言 {duration} 秒。"
        if name == "group_kick":
            user_id = self._digits(args.get("user_id"), "QQ号")
            await self.engine.call_qq_action("set_group_kick", {"group_id": group_id, "user_id": user_id})
            return f"已将 {user_id} 移出群聊。"
        if name == "group_whole_ban":
            enable = self._as_bool(args.get("enable"))
            await self.engine.call_qq_action("set_group_whole_ban", {"group_id": group_id, "enable": enable})
            return "已开启全员禁言。" if enable else "已关闭全员禁言。"
        if name == "group_admin":
            user_id = self._digits(args.get("user_id"), "QQ号")
            enable = self._as_bool(args.get("enable"))
            await self.engine.call_qq_action(
                "set_group_admin",
                {"group_id": group_id, "user_id": user_id, "enable": enable},
            )
            return "已设置管理员。" if enable else "已取消管理员。"
        if name == "group_notice":
            content = str(args.get("content") or "").strip()[:1000]
            if not content:
                return "公告内容不能为空。"
            payload: dict[str, Any] = {"group_id": group_id, "content": content}
            if msg is not None:
                image_path = next(
                    (item.path for item in msg.attachments if item.kind is MessageType.IMAGE and item.path),
                    None,
                )
                if image_path:
                    payload["image"] = Path(image_path).resolve().as_uri()
            await self.engine.call_qq_action("_send_group_notice", payload)
            return "群公告已发布。"
        return f"不支持的管理动作：{name}"

    def _create_card(self, args: dict[str, Any]) -> str:
        name = validate_card_name(str(args.get("name", "")).strip())
        body = str(args.get("body", "") or "").strip()
        description = str(args.get("description", "") or "").strip()[:4000]
        rules = args.get("rules") or []
        if isinstance(rules, str):
            rules = [line.strip() for line in rules.splitlines() if line.strip()]
        card = CharacterCard(
            {
                "name": name,
                "description": description,
                "system_prompt": body or f"你是{name}。{description}".strip(),
                "rules": [str(rule)[:1000] for rule in rules][:100],
                "greeting": str(args.get("greeting", "") or "")[:4000],
            }
        )
        self.engine.character_manager.save(name, card)
        return f"已创建人设「{name}」，可通过 /card use {name} 切换。"

    def _update_card(self, args: dict[str, Any]) -> str:
        name = validate_card_name(str(args.get("name", "")).strip())
        existing = self.engine.character_manager.get(name)
        if existing is None:
            return f"人设「{name}」不存在"
        self.engine.character_manager.backup_card(name)
        data = existing.to_dict()
        if "description" in args:
            data["description"] = str(args.get("description") or "")[:4000]
        if "body" in args:
            data["system_prompt"] = str(args.get("body") or "").strip()
        if "rules" in args:
            rules = args.get("rules") or []
            if isinstance(rules, str):
                rules = [line.strip() for line in rules.splitlines() if line.strip()]
            data["rules"] = [str(rule)[:1000] for rule in rules][:100]
        if "greeting" in args:
            data["greeting"] = str(args.get("greeting") or "")[:4000]
        self.engine.character_manager.save(name, CharacterCard(data))
        return f"已更新人设「{name}」。"

    async def _activate_card(self, name: str, msg: UnifiedMessage) -> str:
        name = validate_card_name(name)
        if self.engine.character_manager.get(name) is None:
            return f"人设「{name}」不存在"
        session = self.engine.session_manager.get_or_create(
            msg,
            default_character=self.engine.config.bot.default_character,
            default_style=self.engine.config.bot.default_style,
        )
        session.character_card = name
        await self.engine._commit_response(session, None)
        return f"当前会话已切换到人设「{name}」。"

    async def _add_trait(self, args: dict[str, Any]) -> str:
        store = self.engine.persona_store
        if store is None:
            return "人格演化未启用"
        scope = str(args.get("scope", "global"))
        category = str(args.get("category", ""))
        trait_key = str(args.get("trait_key", ""))
        value = str(args.get("value", ""))
        try:
            confidence = float(args.get("confidence", 0.95))
        except (TypeError, ValueError):
            confidence = 0.95
        ok = await store.add_admin_trait(
            category,
            trait_key,
            value,
            scope=scope,
            user_key="global",
            confidence=confidence,
        )
        if not ok:
            return "特质不合法或包含敏感内容，未写入。"
        return f"已写入{('全局' if scope == 'global' else '用户')}人格特质：{trait_key}: {value}"

    async def _set_config(self, args: dict[str, Any]) -> str:
        path = str(args.get("path", "")).strip()
        if not path:
            return "缺少配置路径"
        parts = path.split(".")
        if parts[0] not in _ALLOWED_CONFIG_ROOTS:
            return "该配置项不在允许修改的白名单内"
        if any(part.lower() in _FORBIDDEN_CONFIG_KEYS for part in parts):
            return "不能通过管理员 Agent 修改密钥或数据库凭据"
        config_path = Path("/app/config.yaml")
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            return f"读取配置失败：{type(exc).__name__}"
        node = data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = args.get("value")
        try:
            AppConfig(**data)
        except Exception as exc:
            return f"配置校验失败：{str(exc)[:300]}"
        content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        result = await self.executor.call("write_file", {"path": "config.yaml", "content": content})
        return f"配置已写入并备份：{path}\n{result.get('output', '')}\n重启后生效，可发送 #重启 或再次让我执行。"

    async def _executor_text(self, action: str, args: dict[str, Any]) -> str:
        result = await self.executor.call(action, args)
        output = str(result.get("output", ""))
        return output[:6000] or "执行完成（无输出）"
