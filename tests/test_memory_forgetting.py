"""遗忘策略测试"""
import time
import pytest
from memory.forgetting import MemoryForgetting


def test_should_not_archive_recent():
    mf = MemoryForgetting()
    memory = {"last_accessed": time.time() - 100, "access_count": 10}
    assert mf.should_archive(memory) is False


def test_should_archive_old_unused():
    mf = MemoryForgetting()
    # should_archive 要求 access_count == 0 且超过归档天数
    memory = {"last_accessed": time.time() - 9999999, "access_count": 0}
    assert mf.should_archive(memory) is True


def test_should_mark_low_priority():
    mf = MemoryForgetting()
    memory = {"last_accessed": time.time() - 50000, "access_count": 2}
    assert mf.is_low_priority(memory) is True


def test_should_not_mark_active_as_low():
    mf = MemoryForgetting()
    memory = {"last_accessed": time.time() - 100, "access_count": 50}
    assert mf.is_low_priority(memory) is False


def test_get_cleanup_report():
    mf = MemoryForgetting()
    now = time.time()
    memories = [
        {"id": "1", "last_accessed": now - 100, "access_count": 10},      # active
        {"id": "2", "last_accessed": now - 9999999, "access_count": 2},   # low_priority (access_count < 3)
        {"id": "3", "last_accessed": now - 9999999, "access_count": 0},   # archive
    ]
    report = mf.get_cleanup_report(memories)
    assert "archive_count" in report
    assert "low_priority_count" in report
    assert report["archive_count"] >= 1
    assert report["low_priority_count"] >= 1
