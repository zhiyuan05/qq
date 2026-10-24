"""Character Card V2 兼容层测试。"""
import io
import json

import pytest
from PIL import Image

from characters.codec import CardFormatError, decode_card, decode_png_text, encode_png, encode_v2
from characters.manager import CharacterCard


def _card() -> CharacterCard:
    return CharacterCard(
        {
            "name": "小满",
            "description": "住在海边的摄影师",
            "system_prompt": "你是小满，喜欢用照片记录日常。",
            "greeting": "今天的光线很好看。",
            "alternate_greetings": ["你来啦，刚好赶上日落。"],
            "examples": [{"user": "在做什么", "assistant": "在等一朵云路过。"}],
            "personality": {"tone": "安静温柔"},
            "tags": ["日常", "治愈"],
            "scenario": "傍晚的海边",
            "post_history_instructions": "保持短句，不要写旁白。",
            "lorebook": [
                {
                    "keys": ["相机", "照片"],
                    "content": "小满随身带着一台旧胶片相机。",
                    "priority": 120,
                }
            ],
            "creator": "tester",
            "character_version": "1.0",
            "creator_notes": "测试用卡",
        }
    )


def test_v2_roundtrip_preserves_persona_and_lorebook():
    card = _card()
    payload = encode_v2(card)

    assert payload["spec"] == "chara_card_v2"
    assert payload["data"]["first_mes"] == "今天的光线很好看。"
    assert payload["data"]["character_book"]["entries"][0]["keys"] == ["相机", "照片"]

    data = decode_card(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert data["name"] == "小满"
    assert data["greeting"] == "今天的光线很好看。"
    assert data["alternate_greetings"] == ["你来啦，刚好赶上日落。"]
    assert data["examples"] == [{"user": "在做什么", "assistant": "在等一朵云路过。"}]
    assert data["lorebook"][0]["content"] == "小满随身带着一台旧胶片相机。"
    assert data["post_history_instructions"] == "保持短句，不要写旁白。"


def test_v1_payload_uses_description_as_prompt():
    data = decode_card(
        json.dumps(
            {"name": "旧卡", "description": "一个旧格式角色", "first_mes": "你好"},
            ensure_ascii=False,
        ).encode("utf-8")
    )

    assert data["name"] == "旧卡"
    assert "一个旧格式角色" in data["system_prompt"]
    assert data["greeting"] == "你好"


def test_v2_system_prompt_keeps_description():
    payload = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "双重设定",
            "description": "角色的核心人设",
            "system_prompt": "额外的主提示词",
        },
    }
    data = decode_card(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    assert "角色的核心人设" in data["system_prompt"]
    assert "额外的主提示词" in data["system_prompt"]


def test_mes_example_parses_user_and_char_blocks():
    payload = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "示例",
            "mes_example": "<START>\n{{user}}: 你好\n{{char}}: 你好呀\n\n<START>\n{{user}}: 再见\n{{char}}: 路上小心",
        },
    }
    data = decode_card(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    assert data["examples"] == [
        {"user": "你好", "assistant": "你好呀"},
        {"user": "再见", "assistant": "路上小心"},
    ]


def test_png_card_roundtrip():
    png = encode_png(_card())

    assert png.startswith(b"\x89PNG")
    assert "chara" in decode_png_text(png)

    data = decode_card(png)
    assert data["name"] == "小满"
    assert data["lorebook"][0]["keys"] == ["相机", "照片"]


def test_png_without_card_data_is_rejected():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format="PNG")

    with pytest.raises(CardFormatError):
        decode_card(output.getvalue())


def test_missing_name_is_rejected():
    with pytest.raises(CardFormatError):
        decode_card(b'{"description": "no name"}')


def test_invalid_payload_is_rejected():
    with pytest.raises(CardFormatError):
        decode_card(b"not a card")
