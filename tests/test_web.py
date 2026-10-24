"""Web面板测试"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport
from web.app import app, get_metrics, set_engine


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert data["check"] == "liveness"
    assert resp.headers["x-request-id"]


@pytest.mark.asyncio
async def test_readiness_requires_started_engine(client):
    set_engine(None)
    resp = await client.get("/api/health/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"


@pytest.mark.asyncio
async def test_readiness_and_metrics_for_started_engine(client):
    fake_engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="")),
        is_ready=True,
        session_store=None,
        runtime_stats=lambda: {"ready": True, "active_sessions": 2},
    )
    set_engine(fake_engine)
    try:
        ready = await client.get("/api/v1/health/ready", headers={"X-Request-ID": "request123"})
        assert ready.status_code == 200
        assert ready.headers["x-request-id"] == "request123"

        response = await client.get("/api/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["engine"]["ready"] is True
        assert data["runtime"]["http"]["requests_total"] >= 2
        assert data["persistence"]["backend"] == "disabled"
        assert get_metrics().snapshot()["http"]["requests_in_flight"] == 0
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_readiness_fails_when_persistence_is_unavailable(client):
    store = AsyncMock()
    store.health_check.return_value = False
    fake_engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="")),
        is_ready=True,
        session_store=store,
    )
    set_engine(fake_engine)
    try:
        response = await client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {"engine": True, "persistence": False}
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_stats_endpoint(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "session_count" in data


@pytest.mark.asyncio
async def test_platform_status_endpoint_exposes_lifecycle_without_sensitive_error_text(client):
    fake_engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="")),
        platform_statuses=lambda: [
            {
                "name": "qq",
                "state": "running",
                "running": True,
                "ready": False,
                "connected": False,
                "last_error": "ConnectionClosedError",
                "queue_depth": 2,
                "stats": {"reconnects": 3},
            }
        ],
    )
    set_engine(fake_engine)
    try:
        response = await client.get("/api/v1/platforms/status")
        assert response.status_code == 200
        assert response.json() == {
            "platforms": [
                {
                    "name": "qq",
                    "running": True,
                    "state": "running",
                    "ready": False,
                    "connected": False,
                    "last_error": "ConnectionClosedError",
                    "queue_depth": 2,
                    "offset": None,
                    "stats": {"reconnects": 3},
                }
            ]
        }
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_sessions_endpoint(client):
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cards_endpoint(client):
    resp = await client.get("/api/cards")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_card_detail_returns_unrendered_prompt_for_lossless_editing(client):
    raw_prompt = "你是 {{name}}。" + ("原始配置" * 100)
    card = SimpleNamespace(
        name="测试角色",
        description="描述",
        rules=["规则"],
        system_prompt=raw_prompt,
        greeting="你好",
    )
    manager = SimpleNamespace(get=lambda name: card if name == "test" else None)
    engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="")),
        character_manager=manager,
    )
    set_engine(engine)
    try:
        response = await client.get("/api/v1/cards/test")
        assert response.status_code == 200
        assert response.json()["system_prompt"] == raw_prompt
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_styles_endpoint(client):
    resp = await client.get("/api/styles")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_index_page(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "聊天机器人" in resp.text
    assert '/static/admin.css' in resp.text
    assert '/static/admin.js' in resp.text
    assert '<style' not in resp.text
    assert '<script>' not in resp.text
    assert 'onclick=' not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/sessions",
        "/sessions/example-session",
        "/cards",
        "/cards/edit",
        "/cards/example-card",
        "/persona",
        "/stickers",
        "/usage",
        "/settings",
        "/logs",
    ],
)
async def test_management_pages_share_external_admin_shell(client, path):
    response = await client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '/static/admin.css' in response.text
    assert '/static/admin.js' in response.text
    assert '<style' not in response.text
    assert '<script>' not in response.text
    assert 'onclick=' not in response.text


@pytest.mark.asyncio
async def test_csp_disallows_inline_scripts_and_styles(client):
    response = await client.get("/")
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "unsafe-inline" not in policy


@pytest.mark.asyncio
async def test_chat_and_login_use_external_assets(client):
    chat = await client.get("/chat")
    login = await client.get("/login")
    assert chat.status_code == login.status_code == 200
    assert '/static/chat.css' in chat.text
    assert '/static/chat.js' in chat.text
    assert '<style' not in chat.text
    assert 'onclick=' not in chat.text
    assert '/static/login.css' in login.text
    assert '/static/login.js' in login.text
    assert '<style' not in login.text
    assert '<script>' not in login.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset", "content_type"),
    [
        ("login.css", "text/css"),
        ("admin.css", "text/css"),
        ("admin.js", "text/javascript"),
    ],
)
async def test_static_assets_are_public_and_security_hardened(client, asset, content_type):
    fake_engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="a-secure-token-with-32-characters"))
    )
    set_engine(fake_engine)
    try:
        response = await client.get(f"/static/{asset}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-type"].startswith(content_type)
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_sessions_page(client):
    resp = await client.get("/sessions")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cards_page(client):
    resp = await client.get("/cards")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_settings_page(client):
    resp = await client.get("/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_protects_management_routes(client):
    fake_engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="a-secure-token-with-32-characters"))
    )
    set_engine(fake_engine)
    try:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/stats")).status_code == 401
        assert (await client.get("/")).status_code == 303
        assert (await client.post("/api/auth/login", json={"token": "wrong"})).status_code == 403

        login = await client.post(
            "/api/auth/login",
            json={"token": "a-secure-token-with-32-characters"},
        )
        assert login.status_code == 200
        assert (await client.get("/")).status_code == 200
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_bearer_token_authentication(client):
    token = "another-secure-token-32-characters"
    fake_engine = SimpleNamespace(config=SimpleNamespace(web=SimpleNamespace(auth_token=token)))
    set_engine(fake_engine)
    try:
        response = await client.get("/", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.headers["x-frame-options"] == "DENY"
    finally:
        set_engine(None)


def test_openapi_publishes_complete_v1_contract_only():
    schema = app.openapi()
    expected_paths = {
        "/api/v1/auth/check",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/cards",
        "/api/v1/cards/{card_name}",
        "/api/v1/chat",
        "/api/v1/health",
        "/api/v1/health/ready",
        "/api/v1/learning/{card_name}",
        "/api/v1/logs",
        "/api/v1/metrics",
        "/api/v1/persona",
        "/api/v1/persona/approve/{trait_id}",
        "/api/v1/persona/rollback",
        "/api/v1/persona/traits",
        "/api/v1/platforms/status",
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}",
        "/api/v1/sessions/{session_id}/export",
        "/api/v1/stats",
        "/api/v1/stats/detailed",
        "/api/v1/stickers",
        "/api/v1/stickers/{digest}/approve",
        "/api/v1/stickers/{digest}/reject",
        "/api/v1/styles",
        "/api/v1/usage",
    }
    assert set(schema["paths"]) == expected_paths

    operation_ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert schema["paths"]["/api/v1/health"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/HealthResponse")
    assert schema["paths"]["/api/v1/chat"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/ChatResponse")


@pytest.mark.asyncio
async def test_openapi_schema_is_available_without_cdn_docs_ui(client):
    schema = await client.get("/openapi.json")
    docs = await client.get("/docs")
    assert schema.status_code == 200
    assert schema.json()["info"]["version"] == "2.4.0"
    assert docs.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["cards", "styles", "sessions"])
async def test_legacy_and_v1_read_endpoints_have_parity(client, endpoint):
    set_engine(None)
    legacy = await client.get(f"/api/{endpoint}")
    versioned = await client.get(f"/api/v1/{endpoint}")
    assert legacy.status_code == versioned.status_code == 200
    assert legacy.json() == versioned.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/chat", "/api/v1/chat"])
async def test_http_chat_request_id_is_idempotent(client, path):
    engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="")),
        process_message=AsyncMock(return_value="answer"),
    )
    set_engine(engine)
    request_id = "http-idempotency-legacy" if path == "/api/chat" else "http-idempotency-v1"
    payload = {
        "message": "question",
        "client_id": "http-client",
        "request_id": request_id,
    }
    try:
        first = await client.post(path, json=payload)
        replay = await client.post(path, json=payload)
        assert first.status_code == 200
        assert first.json() == {"response": "answer", "request_id": request_id}
        assert replay.json() == {
            "response": "answer",
            "request_id": request_id,
            "replayed": True,
        }
        engine.process_message.assert_awaited_once()
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_readiness_probes_media_and_sticker_directories(client, tmp_path):
    fake_engine = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(auth_token="")),
        is_ready=True,
        session_store=None,
        runtime_stats=lambda: {"ready": True},
        media_store=SimpleNamespace(
            config=SimpleNamespace(storage_dir=str(tmp_path / "media"))
        ),
        sticker_library=SimpleNamespace(database_path=str(tmp_path / "stickers.db")),
    )
    set_engine(fake_engine)
    try:
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 200
        checks = response.json()["checks"]
        assert checks["media"] is True
        assert checks["stickers"] is True
    finally:
        set_engine(None)
