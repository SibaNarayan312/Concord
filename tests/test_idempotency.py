import pytest
import asyncio
import httpx
import uuid

@pytest.mark.asyncio
async def test_idempotent_reservation_retry(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    idemp_key = f"idemp-retry-{uuid.uuid4()}"

    # First attempt
    res1 = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 2,
        "idempotency_key": idemp_key
    })
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["cached"] is False
    booking_id = data1["booking_id"]

    # Verify stock decremented to 8
    p1 = (await client.get(f"/products/{pid}")).json()
    assert p1["available_stock"] == 8

    # Second attempt with exact same payload
    res2 = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 2,
        "idempotency_key": idemp_key
    })
    assert res2.status_code == 201 or res2.status_code == 200
    data2 = res2.json()
    assert data2["cached"] is True
    assert data2["booking_id"] == booking_id

    # Crucial check: stock MUST STILL BE 8 (NOT 6!)
    p2 = (await client.get(f"/products/{pid}")).json()
    assert p2["available_stock"] == 8

@pytest.mark.asyncio
async def test_idempotency_key_payload_conflict(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    idemp_key = f"idemp-conflict-{uuid.uuid4()}"

    # Initial request reserving 2 units
    res1 = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 2,
        "idempotency_key": idemp_key
    })
    assert res1.status_code == 201

    # Second request reusing the same key but asking for 4 units (different payload!)
    res2 = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 4,
        "idempotency_key": idemp_key
    })
    assert res2.status_code == 409
    assert "different request payload" in res2.json()["detail"]

@pytest.mark.asyncio
async def test_concurrent_requests_same_idempotency_key(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    idemp_key = f"idemp-race-{uuid.uuid4()}"

    # Fire 5 concurrent requests with identical key
    tasks = [
        client.post("/reservations", json={
            "product_id": pid,
            "quantity": 1,
            "idempotency_key": idemp_key
        })
        for _ in range(5)
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter successful responses
    successful = [r for r in responses if isinstance(r, httpx.Response) and r.status_code in (200, 201)]
    assert len(successful) >= 1

    # Ensure all successful responses point to the EXACT same booking_id
    booking_ids = {r.json()["booking_id"] for r in successful}
    assert len(booking_ids) == 1, "Concurrent idempotent requests must not create different bookings"
