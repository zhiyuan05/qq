"""Memory enhancement tests."""
import pytest
import time
from memory.forgetting import MemoryForgetting


@pytest.fixture
def forgetting():
    return MemoryForgetting(
        low_access_threshold=3,
        low_access_days=30,
        archive_after_days=60,
    )


def test_should_not_archive_recent(forgetting):
    memory = {"id": "1", "access_count": 5, "last_access": time.time()}
    assert forgetting.should_archive(memory) is False


def test_should_archive_old_unused(forgetting):
    old_time = time.time() - 61 * 86400
    memory = {"id": "1", "access_count": 0, "last_access": old_time}
    assert forgetting.should_archive(memory) is True


def test_should_mark_low_priority(forgetting):
    old_time = time.time() - 31 * 86400
    memory = {"id": "1", "access_count": 2, "last_access": old_time}
    assert forgetting.is_low_priority(memory) is True


def test_should_not_mark_active_as_low(forgetting):
    memory = {"id": "1", "access_count": 10, "last_access": time.time()}
    assert forgetting.is_low_priority(memory) is False


def test_get_cleanup_report(forgetting):
    memories = [
        {"id": "1", "access_count": 0, "last_access": time.time() - 61 * 86400},
        {"id": "2", "access_count": 5, "last_access": time.time()},
        {"id": "3", "access_count": 1, "last_access": time.time() - 31 * 86400},
    ]
    report = forgetting.get_cleanup_report(memories)
    assert report["archive_count"] == 1
    assert report["low_priority_count"] == 1
    assert report["active_count"] == 1
