"""角色卡管理器测试"""
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

    mgr = CharacterManager(str(cards_dir))
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
