"""自然语言人设切换测试。"""
from core.persona_switch import PersonaSwitchAgent


ALIASES = {
    "猫娘": "猫娘",
    "学姐": "学姐",
    "御姐": "御姐",
    "温柔姐姐": "温柔姐姐",
    "默认助手": "default",
    "default": "default",
}


def test_switch_phrases():
    agent = PersonaSwitchAgent()

    assert agent.detect("切换到猫娘", ALIASES) == "猫娘"
    assert agent.detect("换成人设学姐", ALIASES) == "学姐"
    assert agent.detect("切到御姐吧", ALIASES) == "御姐"
    assert agent.detect("用温柔姐姐人设", ALIASES) == "温柔姐姐"
    assert agent.detect("扮演猫娘", ALIASES) == "猫娘"
    assert agent.detect("让猫娘来陪我", ALIASES) == "猫娘"
    assert agent.detect("从现在开始你就是学姐", ALIASES) == "学姐"
    assert agent.detect("切换到默认助手", ALIASES) == "default"
    assert agent.detect("switch to 猫娘", ALIASES) == "猫娘"


def test_plain_chat_is_not_a_switch():
    agent = PersonaSwitchAgent()

    assert agent.detect("猫娘好可爱", ALIASES) is None
    assert agent.detect("用猫娘的语气说话", ALIASES) is None
    assert agent.detect("不要切换到猫娘", ALIASES) is None
    assert agent.detect("别用猫娘人设", ALIASES) is None
    assert agent.detect("改天再聊猫娘", ALIASES) is None
    assert agent.detect("我不认识这个角色", ALIASES) is None


def test_unknown_long_and_empty_messages_are_ignored():
    agent = PersonaSwitchAgent()

    assert agent.detect("切换到不存在的人", ALIASES) is None
    assert agent.detect("", ALIASES) is None
    assert agent.detect("切换到猫娘" + "啊" * 100, ALIASES) is None


def test_longest_alias_wins():
    agent = PersonaSwitchAgent()

    aliases = {"姐姐": "姐姐", "温柔姐姐": "温柔姐姐"}
    assert agent.detect("切换到温柔姐姐", aliases) == "温柔姐姐"
