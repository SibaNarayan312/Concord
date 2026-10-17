import pytest
import asyncio
import httpx
import uuid
from backend.services.recovery_service import RecoveryService

@pytest.mark.asyncio
async def test_automatic_hold_expiration_and_inventory_restitution(client: httpx.AsyncClient, test_product):
    pid = test_product["id"] # Initial stock 10
    idemp_key = f"idemp-expire-{uuid.uuid4()}"

    # Create a 2-second hold
    res = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 3,
        "hold_ttl_seconds": 2,
        "idempotency_key": idemp_key
    })
    assert res.status_code == 201
    booking_id = res.json()["booking_id"]

    # Immediately after reservation, available stock must be 7
    p1 = (await client.get(f"/products/{pid}")).json()
    assert p1["available_stock"] == 7

    # Wait 2.5 seconds for hold to expire
    await asyncio.sleep(2.5)

    # Trigger expiration worker scan (or it may have already been processed by background worker)
    released = await RecoveryService.process_expired_holds()
    # At least one must be true: either released in this call, or already expired by background worker

    # Check booking status in MongoDB
    booking_resp = await client.get(f"/bookings/{booking_id}")
    assert booking_resp.status_code == 200
    assert booking_resp.json()["status"] == "EXPIRED"

    # Crucial check: stock MUST be restored back to 10!
    p2 = (await client.get(f"/products/{pid}")).json()
    assert p2["available_stock"] == 10

    # Confirmation after expiration must be rejected
    conf_resp = await client.post(f"/reservations/{booking_id}/confirm")
    assert conf_resp.status_code == 400
    assert "EXPIRED" in conf_resp.json()["detail"]
