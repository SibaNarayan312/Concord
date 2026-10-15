import pytest
import httpx

@pytest.mark.asyncio
async def test_health_check_healthy(client: httpx.AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "HEALTHY"
    assert data["components"]["mongodb"] == "UP"
    assert data["components"]["redis"] == "UP"
    assert "latency_ms" in data
