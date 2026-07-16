"""风格引擎测试"""
from styles.engine import StyleEngine


def test_load_presets():
    engine = StyleEngine("styles/presets.yaml")
    styles = engine.list_styles()
    assert "casual" in styles
    assert "professional" in styles
    assert "creative" in styles
    assert "concise" in styles
    assert "socratic" in styles


def test_get_style():
    engine = StyleEngine("styles/presets.yaml")
    casual = engine.get("casual")
    assert casual is not None
    assert casual.name == "随性闲聊"
    assert casual.emoji_density == 0.3


def test_create_style():
    engine = StyleEngine("styles/presets.yaml")
    style = engine.create("custom", "自定义", "自定义的回复风格")
    assert style.name == "custom"
    assert engine.get("custom") is not None


def test_mix_styles():
    engine = StyleEngine("styles/presets.yaml")
    mixed = engine.mix("casual", "professional")
    assert mixed is not None
    assert "casual+professional" == mixed.name
