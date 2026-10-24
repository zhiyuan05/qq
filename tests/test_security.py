"""Security system tests: rate limiter, circuit breaker, content filter, alerting."""
import pytest
import time
from security.rate_limiter import RateLimiter
from security.circuit_breaker import CircuitBreaker, CircuitState
from security.content_filter import ContentFilter
from security.alerting import AlertManager, AlertLevel


# === Rate Limiter ===

@pytest.fixture
def limiter():
    return RateLimiter(
        per_user_per_minute=3,
        per_group_per_minute=5,
        global_per_hour=10,
        cooldown_minutes=1,
    )


def test_per_user_rate_limit(limiter):
    for i in range(3):
        allowed, reason = limiter.check("user1", group_id=None)
        assert allowed is True
    allowed, reason = limiter.check("user1", group_id=None)
    assert allowed is False
    assert "用户" in reason


def test_per_group_rate_limit(limiter):
    for i in range(5):
        allowed, _ = limiter.check(f"user{i}", group_id="group1")
        assert allowed is True
    allowed, reason = limiter.check("user99", group_id="group1")
    assert allowed is False
    assert "群" in reason


def test_global_rate_limit(limiter):
    for i in range(10):
        allowed, _ = limiter.check(f"user{i}", group_id=None)
        assert allowed is True
    allowed, reason = limiter.check("user99", group_id=None)
    assert allowed is False
    assert "全局" in reason


def test_per_user_independent(limiter):
    for i in range(3):
        limiter.check("user1", group_id=None)
    allowed, _ = limiter.check("user2", group_id=None)
    assert allowed is True


def test_daily_message_cap():
    limiter = RateLimiter(
        per_user_per_minute=100,
        per_group_per_minute=100,
        global_per_hour=100,
        daily_message_cap=5,
    )
    for i in range(5):
        limiter.check(f"user{i}", group_id=None)
    allowed, reason = limiter.check("user99", group_id=None)
    assert allowed is False
    assert "每日" in reason


def test_url_frequency_limit(limiter):
    for i in range(5):
        limiter.record_url_sent()
    allowed, reason = limiter.check_url_limit()
    assert allowed is False
    assert "URL" in reason


def test_conversation_depth(limiter):
    for i in range(20):
        limiter.record_exchange("user1")
    allowed, reason = limiter.check_depth("user1")
    assert allowed is False
    assert "对话深度" in reason


def test_conversation_depth_reset_on_idle(limiter):
    for i in range(20):
        limiter.record_exchange("user1")
    limiter._depth_timestamps["user1"] = time.time() - 1800
    allowed, _ = limiter.check_depth("user1")
    assert allowed is True


def test_stats(limiter):
    limiter.check("user1", group_id=None)
    stats = limiter.stats()
    assert "user_counts" in stats
    assert "global_count" in stats


# === Circuit Breaker ===

@pytest.fixture
def breaker():
    return CircuitBreaker(
        failure_threshold=0.3,
        window_seconds=60,
        recovery_seconds=10,
    )


def test_initial_state_closed(breaker):
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_records_success(breaker):
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_opens_on_failure_threshold(breaker):
    for _ in range(7):
        breaker.record_success()
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_half_open_after_recovery(breaker):
    for _ in range(10):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    breaker._last_failure_time = time.time() - 11
    assert breaker.allow_request() is True
    assert breaker.state == CircuitState.HALF_OPEN


def test_half_open_success_closes(breaker):
    breaker._force_state(CircuitState.HALF_OPEN)
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_half_open_failure_reopens(breaker):
    breaker._force_state(CircuitState.HALF_OPEN)
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_force_open(breaker):
    breaker.force_open()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_force_close(breaker):
    breaker.force_open()
    breaker.force_close()
    assert breaker.state == CircuitState.CLOSED


def test_breaker_stats(breaker):
    stats = breaker.stats()
    assert stats["state"] == "CLOSED"
    assert "total_requests" in stats


# === Content Filter ===

@pytest.fixture
def content_filter(tmp_path):
    words_file = tmp_path / "sensitive.txt"
    words_file.write_text("赌博\n色情\n# comment\n暴力\n", encoding="utf-8")
    return ContentFilter(str(words_file))


def test_filter_replaces_sensitive_words(content_filter):
    result = content_filter.filter("这是一个关于赌博的故事")
    assert "赌博" not in result
    assert "***" in result


def test_filter_multiple_words(content_filter):
    result = content_filter.filter("赌博和色情和暴力都不好")
    assert result.count("***") == 3


def test_filter_no_sensitive_content(content_filter):
    text = "今天天气真好"
    assert content_filter.filter(text) == text


def test_filter_empty_file(tmp_path):
    words_file = tmp_path / "empty.txt"
    words_file.write_text("", encoding="utf-8")
    cf = ContentFilter(str(words_file))
    assert cf.filter("任何文本") == "任何文本"


def test_filter_missing_file(tmp_path):
    cf = ContentFilter(str(tmp_path / "nonexistent.txt"))
    assert cf.filter("任何文本") == "任何文本"


def test_has_sensitive(content_filter):
    assert content_filter.has_sensitive("这里有赌博内容") is True
    assert content_filter.has_sensitive("正常内容") is False


def test_filter_logs(content_filter):
    content_filter.filter("赌博内容")
    logs = content_filter.get_filter_log()
    assert len(logs) > 0
    assert logs[0]["word_count"] >= 1


# === Alert Manager ===

@pytest.fixture
def alert_manager():
    return AlertManager()


def test_create_alert(alert_manager):
    alert_manager.create_alert(
        level=AlertLevel.WARNING,
        title="Test Alert",
        message="Something happened",
    )
    alerts = alert_manager.get_recent()
    assert len(alerts) == 1
    assert alerts[0]["level"] == "WARNING"
    assert alerts[0]["title"] == "Test Alert"


def test_alert_levels(alert_manager):
    for level in AlertLevel:
        alert_manager.create_alert(level=level, title=f"Test {level.value}", message="msg")
    alerts = alert_manager.get_recent()
    assert len(alerts) == len(AlertLevel)


def test_alert_filter_by_level(alert_manager):
    alert_manager.create_alert(level=AlertLevel.INFO, title="Info", message="msg")
    alert_manager.create_alert(level=AlertLevel.CRITICAL, title="Critical", message="msg")
    critical = alert_manager.get_recent(level=AlertLevel.CRITICAL)
    assert len(critical) == 1
    assert critical[0]["title"] == "Critical"


def test_alert_stats(alert_manager):
    alert_manager.create_alert(level=AlertLevel.INFO, title="1", message="msg")
    alert_manager.create_alert(level=AlertLevel.WARNING, title="2", message="msg")
    alert_manager.create_alert(level=AlertLevel.WARNING, title="3", message="msg")
    stats = alert_manager.stats()
    assert stats["total"] == 3
    assert stats["by_level"]["WARNING"] == 2
    assert stats["by_level"]["INFO"] == 1


def test_conversation_depth_zero_means_unlimited():
    limiter = RateLimiter(max_conversation_depth=0)
    for _ in range(1000):
        limiter.record_exchange("user1")
    allowed, reason = limiter.check_depth("user1")
    assert allowed is True
    assert reason == ""
