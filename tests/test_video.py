from io import BytesIO

from PIL import Image

from media.video import _montage, video_extension
from platforms.models import MessageType
from platforms.qq_segments import parse_message


def test_video_extension_sniffs_containers():
    assert video_extension("video/mp4") == "mp4"
    assert video_extension("", b"\x00\x00\x00\x18ftypmp42") == "mp4"
    assert video_extension("", b"\x1a\x45\xdf\xa3webm-payload") == "webm"
    assert video_extension("image/png", b"\x89PNG\r\n\x1a\n") is None


def test_parse_message_extracts_video_segment():
    parsed = parse_message(
        {
            "message": [
                {"type": "video", "data": {"url": "https://example.com/v.mp4", "file": "v.mp4"}}
            ]
        },
        self_id="1",
    )
    assert parsed.model_text == "[视频]"
    assert len(parsed.media) == 1
    assert parsed.media[0].kind is MessageType.VIDEO
    assert parsed.media[0].url == "https://example.com/v.mp4"


def test_montage_tiles_frames():
    frames = []
    for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        buffer = BytesIO()
        Image.new("RGB", (640, 360), color).save(buffer, "JPEG")
        frames.append(buffer.getvalue())

    montage = _montage(frames)
    assert montage is not None
    image = Image.open(BytesIO(montage))
    assert image.width == 1440
    assert image.height == 270
