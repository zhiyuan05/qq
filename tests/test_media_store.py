import io
import os
from datetime import datetime, timedelta

import pytest
from PIL import Image

from media.images import prepare_for_vision, sniff_image_mime
from media.net import MediaFetchError, _is_global_address, fetch_bytes
from media.store import MediaStore
from utils.config import MediaConfig


def _png_bytes(size=(64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_private_addresses_are_rejected():
    assert _is_global_address("8.8.8.8") is True
    assert _is_global_address("127.0.0.1") is False
    assert _is_global_address("10.1.2.3") is False
    assert _is_global_address("100.64.0.1") is False
    assert _is_global_address("::1") is False
    assert _is_global_address("169.254.169.254") is False
    assert _is_global_address("not-an-ip") is False


@pytest.mark.asyncio
async def test_fetch_rejects_unsafe_urls_without_network():
    with pytest.raises(MediaFetchError):
        await fetch_bytes("ftp://example.com/a.jpg", max_bytes=100)
    with pytest.raises(MediaFetchError):
        await fetch_bytes("http://user:pass@example.com/a.jpg", max_bytes=100)
    with pytest.raises(MediaFetchError):
        await fetch_bytes("http://localhost/a.jpg", max_bytes=100)


@pytest.mark.asyncio
async def test_fetch_rejects_private_resolution():
    def resolver(host, port):
        raise MediaFetchError("目标地址不是公网地址")

    with pytest.raises(MediaFetchError):
        await fetch_bytes("https://cdn.example/a.jpg", max_bytes=100, resolver=resolver)


@pytest.mark.asyncio
async def test_store_is_content_addressed_and_deduplicated(tmp_path):
    config = MediaConfig(storage_dir=str(tmp_path / "media"))
    store = MediaStore(config)
    data = _png_bytes()

    first = await store.store_image(data)
    second = await store.store_image(data)

    assert first is not None and second is not None
    assert first.path == second.path
    assert first.sha256 == second.sha256
    assert sniff_image_mime(data) == "image/png"
    assert (tmp_path / "media").exists()


@pytest.mark.asyncio
async def test_store_rejects_non_image_payload(tmp_path):
    store = MediaStore(MediaConfig(storage_dir=str(tmp_path / "media")))
    assert await store.store_image(b"not an image") is None


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_files(tmp_path):
    config = MediaConfig(storage_dir=str(tmp_path / "media"), retention_days=1)
    store = MediaStore(config)
    attachment = await store.store_image(_png_bytes())
    assert attachment is not None

    old = datetime.now() - timedelta(days=5)
    os.utime(attachment.path, (old.timestamp(), old.timestamp()))

    removed = await store.cleanup(keep_paths=set())
    assert removed == 1


def test_prepare_for_vision_resizes_large_image():
    payload = _png_bytes((3000, 2000))

    prepared = prepare_for_vision(payload, max_side=640, max_bytes=200_000)

    assert prepared is not None
    data, mime = prepared
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as image:
        assert max(image.size) <= 640
    assert len(data) <= 200_000


def test_prepare_for_vision_rejects_unknown_payload():
    assert prepare_for_vision(b"plain text", max_side=100, max_bytes=100) is None
