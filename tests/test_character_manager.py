"""角色卡管理器测试"""
import json

import pytest
import yaml
from characters.manager import CharacterCard, CharacterManager


def test_character_card_system_prompt():
    card = CharacterCard({
        "name": "小助手",
        "description": "一个测试助手",
        "skills": "测试",
        "system_prompt": "你是{{name}}，{{description}}。擅长{{skills}}。\n规则:\n{{rules}}",
        "rules": ["说中文", "简洁"],
    })
    prompt = card.get_system_prompt()
    assert "小助手" in prompt
    assert "测试助手" in prompt
    assert "- 说中文" in prompt
    assert "- 简洁" in prompt


def test_pick_greeting_includes_alternates():
    card = CharacterCard({"name": "助手", "greeting": "你好", "alternate_greetings": ["又见面了"]})

    assert card.pick_greeting() in {"你好", "又见面了"}
    assert CharacterCard({"name": "助手"}).pick_greeting() == ""


def test_character_card_supports_sillytavern_macros():
    card = CharacterCard({"name": "小满", "system_prompt": "你是{{char}}，正在和{{user}}聊天。"})

    prompt = card.get_system_prompt()
    assert "你是小满" in prompt
    assert "和用户聊天" in prompt


def test_character_manager_load(tmp_path):
    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    data = {
        "name": "测试卡",
        "description": "测试",
        "system_prompt": "你是{{name}}",
        "rules": ["规则1"],
    }
    with open(cards_dir / "test.yaml", "w") as f:
        yaml.dump(data, f)

    mgr = CharacterManager(str(cards_dir), str(tmp_path / "roles"))
    assert "test" in mgr.list_cards()
    card = mgr.get("test")
    assert card.name == "测试卡"


def test_character_card_learned_traits():
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}",
        "learned_traits": {
            "communication_style": ["用户喜欢简洁"],
        },
    })
    prompt = card.get_system_prompt()
    assert "用户喜欢简洁" in prompt


def test_save_rejects_path_traversal(tmp_path):
    manager = CharacterManager(str(tmp_path / "cards"), str(tmp_path / "roles"))
    card = CharacterCard({"name": "bad"})

    with pytest.raises(ValueError, match="角色卡名称"):
        manager.save("../outside", card)
    assert not (tmp_path.parent / "outside.yaml").exists()


def test_markdown_card_roundtrip(tmp_path):
    manager = CharacterManager(str(tmp_path / "cards"), str(tmp_path / "roles"))
    card = CharacterCard({
        "name": "测试人格",
        "description": "Markdown 卡",
        "system_prompt": "# 身份\n\n你是测试人格。\n\n## 语气\n\n简洁。",
        "rules": ["说中文"],
        "greeting": "你好",
    })

    manager.save("测试人格", card)

    path = tmp_path / "roles" / "测试人格.md"
    assert path.is_file()
    reloaded = CharacterManager(str(tmp_path / "cards"), str(tmp_path / "roles"))
    loaded = reloaded.get("测试人格")
    assert loaded is not None
    assert "你是测试人格" in loaded.system_prompt
    assert loaded.rules == ["说中文"]
    assert "- 说中文" in loaded.get_system_prompt()


def test_markdown_roundtrip_keeps_lorebook_and_alternate_greetings(tmp_path):
    manager = CharacterManager(str(tmp_path / "cards"), str(tmp_path / "roles"))
    card = CharacterCard(
        {
            "name": "测试人格",
            "description": "Markdown 卡",
            "system_prompt": "你是测试人格。\n{{rules}}",
            "rules": ["说中文"],
            "greeting": "你好",
            "alternate_greetings": ["又见面了"],
            "tags": ["测试"],
            "scenario": "测试场景",
            "post_history_instructions": "保持简短",
            "lorebook": [{"keys": ["测试"], "content": "这是一条设定。", "priority": 90}],
            "voice": {"name": "zh-CN-XiaoyiNeural", "rate": "+8%", "pitch": "+18Hz"},
        }
    )
    manager.save("测试人格", card)

    loaded = CharacterManager(str(tmp_path / "cards"), str(tmp_path / "roles")).get("测试人格")
    assert loaded is not None
    assert loaded.alternate_greetings == ["又见面了"]
    assert loaded.tags == ["测试"]
    assert loaded.scenario == "测试场景"
    assert loaded.post_history_instructions == "保持简短"
    assert loaded.lorebook[0]["content"] == "这是一条设定。"
    assert loaded.voice["name"] == "zh-CN-XiaoyiNeural"


def test_import_and_export_v2_card(tmp_path):
    manager = CharacterManager(str(tmp_path / "cards"), str(tmp_path / "roles"))
    payload = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "导入角色",
            "description": "来自酒馆的卡",
            "first_mes": "你好呀",
            "character_book": {
                "entries": [{"keys": ["海"], "content": "角色住在海边。"}],
            },
        },
    }

    name = manager.import_card(json.dumps(payload, ensure_ascii=False).encode("utf-8"), name="导入角色")
    imported = manager.get(name)
    assert imported is not None
    assert imported.greeting == "你好呀"
    assert imported.lorebook[0]["keys"] == ["海"]

    exported = manager.export_card(name)
    assert exported["data"]["first_mes"] == "你好呀"
    assert exported["data"]["character_book"]["entries"][0]["content"] == "角色住在海边。"

    with pytest.raises(FileExistsError):
        manager.import_card(json.dumps(payload).encode("utf-8"), name="导入角色")
    assert manager.import_card(json.dumps(payload).encode("utf-8"), name="导入角色", overwrite=True) == "导入角色"


def test_legacy_yaml_cards_migrate_to_markdown(tmp_path):
    cards = tmp_path / "cards"
    roles = tmp_path / "roles"
    cards.mkdir()
    (cards / "legacy.yaml").write_text(
        "name: 旧卡\nsystem_prompt: 你是{{name}}\nrules:\n- 规则\n",
        encoding="utf-8",
    )
    manager = CharacterManager(str(cards), str(roles))

    assert manager.migrate_legacy_cards() == 1
    assert (roles / "legacy.md").is_file()
    assert manager.migrate_legacy_cards() == 0
