from observability import RuntimeMetrics


def test_runtime_metrics_tracks_http_and_websocket_activity():
    metrics = RuntimeMetrics()
    metrics.request_started()
    metrics.request_finished(503, 0.025)
    metrics.websocket_connected()
    metrics.websocket_message()

    snapshot = metrics.snapshot()
    assert snapshot["http"] == {
        "requests_total": 1,
        "requests_in_flight": 0,
        "server_errors": 1,
        "average_latency_ms": 25.0,
        "status_codes": {"503": 1},
    }
    assert snapshot["websocket"] == {"active_connections": 1, "messages_total": 1}


def test_runtime_metrics_connection_counters_do_not_go_negative():
    metrics = RuntimeMetrics()
    metrics.request_finished(200, 0)
    metrics.websocket_disconnected()

    snapshot = metrics.snapshot()
    assert snapshot["http"]["requests_in_flight"] == 0
    assert snapshot["websocket"]["active_connections"] == 0


def test_domain_counters_are_exposed():
    metrics = RuntimeMetrics()
    metrics.increment("stickers_sent")
    metrics.increment("stickers_sent", 2)
    metrics.increment("outbound_blocked")

    assert metrics.snapshot()["domain"] == {"stickers_sent": 3, "outbound_blocked": 1}
