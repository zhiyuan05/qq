import io

import pytest
from PIL import Image

from core.engine import Engine
from platforms.models import Attachment, MessageType, Platform, UnifiedMessage
from utils.config import AppConfig


def _config() -> AppConfig:
    return AppConfig(
        memory={"long_term": {"enabled": False}, "entity": {"auto_extract": False}},
        rate_limit={"enabled": False},
        media={"enabled": True, "vision_max_side": 640, "vision_max_bytes": 200_000},
    )


def _image_file(tmp_path) -> str:
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (1200, 800), color=(20, 120, 220)).save(path, format="JPEG")
    return str(path)


@pytest.mark.asyncio
async def test_image_attachment_becomes_multimodal_content(tmp_path):
    engine = Engine(_config())
    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="1",
        content="",
        attachments=[
            Attachment(kind=MessageType.IMAGE, path=_image_file(tmp_path), mime="image/jpeg", size=1)
        ],
        model_content="[图片] 这是什么",
    )

    content = await engine._build_user_content(message)

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"].startswith("[图片] 这是什么")
    assert "请先理解图片内容" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_missing_image_file_falls_back_to_text(tmp_path):
    engine = Engine(_config())
    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="1",
        content="",
        attachments=[
            Attachment(kind=MessageType.IMAGE, path=str(tmp_path / "missing.jpg"), mime="image/jpeg")
        ],
        model_content="[图片]",
    )

    assert await engine._build_user_content(message) == "[图片]"


def test_strip_image_blocks_keeps_text_and_marks_failure():
    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xxx"}},
            ],
        },
    ]

    stripped = Engine._strip_image_blocks(messages)

    assert stripped[1]["content"] == "看图 [图片未能识别]"


def test_animated_gif_is_sampled_into_a_single_montage(tmp_path):
    from media.images import prepare_for_vision

    frames = [Image.new("RGB", (80, 60), color=(index * 40, 40, 80)) for index in range(4)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)

    prepared = prepare_for_vision(buffer.getvalue(), max_side=1280, max_bytes=500_000)

    assert prepared is not None
    payload, mime = prepared
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(payload)) as image:
        assert image.width >= 400
        assert image.height >= 400
