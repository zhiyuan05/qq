"""Web面板测试"""
import pytest
from httpx import AsyncClient, ASGITransport
from web.app import app, set_engine


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


@pytest.mark.asyncio
async def test_stats_endpoint(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "session_count" in data


@pytest.mark.asyncio
async def test_sessions_endpoint(client):
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cards_endpoint(client):
    resp = await client.get("/api/cards")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_styles_endpoint(client):
    resp = await client.get("/api/styles")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_index_page(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "聊天机器人" in resp.text


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
