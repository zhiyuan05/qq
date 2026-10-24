import json
import re

import pytest

from characters.manager import CharacterManager
from core.admin_agent import AdminAgent
from database.db import dispose_db, init_db
from memory.persona import PersonaStore
from platforms.models import Attachment, MessageType, Platform, UnifiedMessage
from utils.config import AdminAgentConfig, PersonaConfig


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return json.dumps(self.payload, ensure_ascii=False)


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    async def call(self, action, args=None):
        self.calls.append((action, args))
        return {"output": "ok"}


class _FakeEngine:
    def __init__(self, tmp_path):
        self.character_manager = CharacterManager(
            str(tmp_path / "cards"),
            str(tmp_path / "roles"),
        )
        self.persona_store = None
        self.llm = None
        self.avatar_paths = []
        self.qq_actions = []
        self.config = type("Config", (), {"bot": type("Bot", (), {"default_character": "default", "default_style": "casual"})()})()

    def _get_llm(self):
        return self.llm

    async def set_qq_avatar(self, path):
        self.avatar_paths.append(path)
        return "头像已更新"

    def qq_platform(self):
        return None

    async def call_qq_action(self, action, params):
        self.qq_actions.append((action, params))
        if action == "send_qzone_msg":
            return {"tid": "tid-1"}
        if action == "get_login_info":
            return {"user_id": "10001"}
        return None

    async def send_qq_text(self, target_id, text, *, group_id=None):
        self.qq_actions.append(("send_text", {"target_id": target_id, "text": text, "group_id": group_id}))


def _message(content="# 你好"):
    return UnifiedMessage(platform=Platform.QQ, user_id="2277837533", content=content)


@pytest.mark.asyncio
async def test_admin_agent_creates_markdown_card(tmp_path):
    engine = _FakeEngine(tmp_path)
    engine.llm = _FakeLLM(
        {
            "action": "create_card",
            "args": {
                "name": "冷面助手",
                "description": "话少但可靠",
                "body": "# 身份\n\n你是冷面助手。\n\n## 语气\n\n简短。",
                "rules": ["回答简短"],
            },
        }
    )
    agent = AdminAgent(engine, AdminAgentConfig(enabled=True))

    result = await agent.handle(_message("# 新建冷面助手"))

    assert "已创建" in result
    assert (tmp_path / "roles" / "冷面助手.md").is_file()
    loaded = engine.character_manager.get("冷面助手")
    assert loaded is not None
    assert "冷面助手" in loaded.get_system_prompt()


@pytest.mark.asyncio
async def test_dangerous_action_requires_confirmation(tmp_path):
    engine = _FakeEngine(tmp_path)
    engine.llm = _FakeLLM(
        {
            "action": "write_file",
            "args": {"path": "core/example.py", "content": "print('x')\n"},
        }
    )
    agent = AdminAgent(engine, AdminAgentConfig(enabled=True))
    agent.executor = _FakeExecutor()

    proposal = await agent.handle(_message("# 写一个文件"))

    assert "确认" in proposal
    assert agent.executor.calls == []
    code = re.search(r"#确认 (\d{6})", proposal).group(1)

    result = await agent.handle(_message(f"#确认 {code}"))

    assert result == "ok"
    assert agent.executor.calls == [("write_file", {"path": "core/example.py", "content": "print('x')\n"})]


@pytest.mark.asyncio
async def test_admin_agent_adds_persona_trait(tmp_path):
    db_path = str(tmp_path / "persona.db")
    await init_db(db_path)
    engine = _FakeEngine(tmp_path)
    engine.persona_store = PersonaStore(db_path, config=PersonaConfig())
    engine.llm = _FakeLLM(
        {
            "action": "add_persona_trait",
            "args": {
                "scope": "global",
                "category": "speech_style",
                "trait_key": "语气",
                "value": "直接",
                "confidence": 0.95,
            },
        }
    )
    agent = AdminAgent(engine, AdminAgentConfig(enabled=True))
    try:
        result = await agent.handle(_message("# 让机器人说话直接一点"))
        assert "已写入" in result
        traits = await engine.persona_store.active_traits("global", "global")
        assert [trait.value for trait in traits] == ["直接"]
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_admin_agent_sets_avatar_from_attached_image(tmp_path):
    image = tmp_path / "avatar.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    engine = _FakeEngine(tmp_path)
    engine.llm = _FakeLLM({"action": "set_avatar", "args": {}})
    agent = AdminAgent(engine, AdminAgentConfig(enabled=True))
    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="2277837533",
        content="# 把这张图设置成你的头像",
        attachments=[Attachment(kind=MessageType.IMAGE, path=str(image), mime="image/png")],
    )

    result = await agent.handle(message)

    assert result == "头像已更新"
    assert engine.avatar_paths == [str(image)]


@pytest.mark.asyncio
async def test_admin_agent_updates_nickname(tmp_path):
    engine = _FakeEngine(tmp_path)
    engine.llm = _FakeLLM({"action": "set_nickname", "args": {"nickname": "小助手"}})
    agent = AdminAgent(engine, AdminAgentConfig(enabled=True))

    result = await agent.handle(_message("# 昵称改成小助手"))

    assert "小助手" in result
    assert engine.qq_actions == [("set_qq_profile", {"nickname": "小助手"})]


@pytest.mark.asyncio
async def test_admin_agent_posts_qzone_with_attached_image(tmp_path):
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    engine = _FakeEngine(tmp_path)
    engine.llm = _FakeLLM(
        {"action": "post_qzone", "args": {"content": "今天天气不错", "ugc_right": 4}}
    )
    agent = AdminAgent(engine, AdminAgentConfig(enabled=True))
    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="2277837533",
        content="# 发条说说",
        attachments=[Attachment(kind=MessageType.IMAGE, path=str(image), mime="image/png")],
    )

    proposal = await agent.handle(message)
    code = re.search(r"#确认 (\d{6})", proposal).group(1)
    result = await agent.handle(_message(f"#确认 {code}"))

    assert "说说已发布" in result
    action, params = engine.qq_actions[0]
    assert action == "send_qzone_msg"
    assert params["content"] == "今天天气不错"
    assert params["ugc_right"] == 4
    assert params["images"] == [image.resolve().as_uri()]
