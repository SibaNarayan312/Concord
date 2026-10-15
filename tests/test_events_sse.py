import pytest
import httpx
import uuid
from backend.services.stream_service import StreamService

@pytest.mark.asyncio
async def test_redis_streams_lifecycle_events(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    idemp_key = f"idemp-stream-{uuid.uuid4()}"

    # 1. Create reservation -> emits HOLD_CREATED
    res = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 1,
        "idempotency_key": idemp_key
    })
    assert res.status_code == 201
    booking_id = res.json()["booking_id"]

    # 2. Confirm booking -> emits BOOKING_CONFIRMED
    conf = await client.post(f"/reservations/{booking_id}/confirm")
    assert conf.status_code == 200

    # 3. Query recent events from Redis Stream
    recent_events = await StreamService.get_recent_events(count=10)
    assert len(recent_events) >= 2

    event_types = [e["event_type"] for e in recent_events]
    assert "HOLD_CREATED" in event_types
    assert "BOOKING_CONFIRMED" in event_types

@pytest.mark.asyncio
async def test_recent_events_endpoint(client: httpx.AsyncClient):
    resp = await client.get("/events/recent?count=5")
    assert resp.status_code == 200
    events = resp.json()
    assert isinstance(events, list)
