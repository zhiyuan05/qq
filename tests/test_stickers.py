import io
import random
from types import SimpleNamespace

import pytest
from PIL import Image

import media.stickers as stickers_module
from media.stickers import StickerLibrary
from utils.config import StickersConfig


def _image_bytes(size=(64, 64), fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(30, 160, 80)).save(buffer, format=fmt)
    return buffer.getvalue()


def _library(tmp_path, **overrides) -> StickerLibrary:
    config = StickersConfig(
        library_dir=str(tmp_path / "stickers"),
        **overrides,
    )
    return StickerLibrary(
        config,
        database_path=str(tmp_path / "stickers.db"),
        rng=random.Random(7),
    )


@pytest.mark.asyncio
async def test_index_discovers_library_images(tmp_path):
    library = _library(tmp_path)
    library.root.mkdir(parents=True)
    (library.root / "开心.png").write_bytes(_image_bytes())

    assert await library.index() == 1
    assert await library.index() == 0
    stickers = await library.list("active")
    assert len(stickers) == 1
    assert "默认" in stickers[0].tags


@pytest.mark.asyncio
async def test_top_tags_excludes_default_and_caches(tmp_path):
    library = _library(tmp_path)
    library.root.mkdir(parents=True)
    (library.root / "开心.png").write_bytes(_image_bytes())
    (library.root / "开心猫.png").write_bytes(_image_bytes((48, 48)))
    await library.index()

    tags = await library.top_tags()

    assert "开心" in tags
    assert "默认" not in tags
    assert await library.top_tags() == tags


@pytest.mark.asyncio
async def test_first_pick_is_not_blocked_by_system_uptime(tmp_path, monkeypatch):
    library = _library(tmp_path, cooldown_seconds=60, repeat_window_seconds=600)
    library.root.mkdir(parents=True)
    (library.root / "开心.png").write_bytes(_image_bytes())
    await library.index()

    # A freshly booted host/container has a monotonic clock near zero.
    monkeypatch.setattr(stickers_module, "time", SimpleNamespace(monotonic=lambda: 5.0))

    assert library._pick_blocking("开心", "qq:1") is not None


@pytest.mark.asyncio
async def test_collect_accepts_gifs_and_small_images(tmp_path):
    library = _library(tmp_path)
    small = tmp_path / "small.png"
    small.write_bytes(_image_bytes())
    big = tmp_path / "big.png"
    big.write_bytes(_image_bytes((1024, 1024)))
    gif = tmp_path / "anim.gif"
    gif.write_bytes(_image_bytes(fmt="GIF"))

    assert await library.collect(str(small)) is True
    assert await library.collect(str(big)) is False
    assert await library.collect(str(gif)) is True
    assert len(await library.list("active")) == 2


@pytest.mark.asyncio
async def test_review_queue_holds_candidates_until_approved(tmp_path):
    library = _library(tmp_path, review_required=True)
    source = tmp_path / "candidate.png"
    source.write_bytes(_image_bytes())

    assert await library.collect(str(source)) is True
    candidates = await library.list("candidate")
    assert len(candidates) == 1
    assert await library.pick("默认", "qq:1") is None

    assert await library.approve(candidates[0].hash) is True
    assert await library.pick("默认", "qq:1") is not None


@pytest.mark.asyncio
async def test_pick_respects_tags_and_cooldowns(tmp_path):
    library = _library(tmp_path, cooldown_seconds=60, repeat_window_seconds=600)
    library.root.mkdir(parents=True)
    (library.root / "开心.png").write_bytes(_image_bytes())
    (library.root / "难过.png").write_bytes(_image_bytes((80, 80)))
    await library.index()

    picked = await library.pick("开心", "qq:1")
    assert picked is not None
    assert "开心" in picked.tags
    assert await library.pick("开心", "qq:1") is None

    other = await library.pick("开心", "qq:2")
    assert other is None or other.hash != picked.hash or "开心" not in other.tags


@pytest.mark.asyncio
async def test_rejected_sticker_is_not_selected(tmp_path):
    library = _library(tmp_path)
    source = tmp_path / "rejected.png"
    source.write_bytes(_image_bytes())
    await library.collect(str(source))
    stickers = await library.list("active")
    assert await library.reject(stickers[0].hash) is True
    assert await library.pick("默认", "qq:1") is None


@pytest.mark.asyncio
async def test_ai_tagging_merges_tags_and_marks_sticker(tmp_path):
    library = _library(tmp_path)
    library.root.mkdir(parents=True)
    (library.root / "开心.png").write_bytes(_image_bytes())
    await library.index()

    calls = 0

    async def tagger(path):
        nonlocal calls
        calls += 1
        return ["微笑", "高兴"]

    library._tagger = tagger
    assert await library.tag_missing(limit=10) == 1
    assert await library.tag_missing(limit=10) == 0
    stickers = await library.list("active")
    assert {"开心", "微笑", "高兴"} <= set(stickers[0].tags)
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_tagging_stops_after_three_attempts(tmp_path):
    library = _library(tmp_path)
    library.root.mkdir(parents=True)
    (library.root / "开心.png").write_bytes(_image_bytes())
    await library.index()

    calls = 0

    async def tagger(path):
        nonlocal calls
        calls += 1
        return []

    library._tagger = tagger
    for _ in range(4):
        assert await library.tag_missing(limit=10) == 0
    assert calls == 3


@pytest.mark.asyncio
async def test_missing_library_file_is_disabled_on_reindex(tmp_path):
    library = _library(tmp_path)
    library.root.mkdir(parents=True)
    path = library.root / "开心.png"
    path.write_bytes(_image_bytes())
    await library.index()
    assert await library.pick("开心", "qq:1") is not None

    path.unlink()
    await library.index()

    assert await library.pick("开心", "qq:2") is None
