import pytest
import asyncio
import httpx
import uuid
from backend.services.recovery_service import RecoveryService

@pytest.mark.asyncio
async def test_single_reservation_and_confirmation(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]

    # Acquire hold
    key = f"key-single-{uuid.uuid4()}"
    res = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 3,
        "idempotency_key": key
    })
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "HELD"
    assert data["quantity"] == 3
    booking_id = data["booking_id"]

    # Verify stock decremented by 3
    prod_res = await client.get(f"/products/{pid}")
    assert prod_res.json()["available_stock"] == 7

    # Confirm booking
    conf_res = await client.post(f"/reservations/{booking_id}/confirm")
    assert conf_res.status_code == 200
    assert conf_res.json()["status"] == "CONFIRMED"

    # Confirming again is idempotent
    conf_again = await client.post(f"/reservations/{booking_id}/confirm")
    assert conf_again.status_code == 200

@pytest.mark.asyncio
async def test_insufficient_stock_rejection(client: httpx.AsyncClient, test_product):
    pid = test_product["id"] # Initial stock is 10

    # Request 15 units
    res = await client.post("/reservations", json={
        "product_id": pid,
        "quantity": 15,
        "idempotency_key": f"key-excess-{uuid.uuid4()}"
    })
    assert res.status_code == 400
    assert "Insufficient" in res.json()["detail"]

    # Verify stock was not decremented
    prod_res = await client.get(f"/products/{pid}")
    assert prod_res.json()["available_stock"] == 10

@pytest.mark.asyncio
async def test_mandatory_100_concurrent_requests_on_10_units(client: httpx.AsyncClient):
    """
    MANDATORY CONTENTION TEST:
    Initialize a product with 10 units. Send 100 concurrent 1-unit reservation requests.
    Asserts:
    1. Exactly 10 units are held/confirmed in total.
    2. Exactly 10 requests return 201 Created.
    3. Exactly 90 requests return 400 Bad Request.
    4. Available inventory never becomes negative (equals 0).
    5. Final inventory invariant holds: available (0) + active_holds (10) == initial (10).
    """
    # Create clean product with exactly 10 units
    prod_resp = await client.post("/products", json={
        "name": f"Contention GPU {uuid.uuid4().hex[:6]}",
        "description": "High contention 100-client stress test product",
        "initial_stock": 10
    })
    assert prod_resp.status_code == 201
    prod = prod_resp.json()
    product_id = prod["id"]

    total_requests = 100
    tasks = []

    # Fire 100 concurrent requests simultaneously
    for i in range(total_requests):
        idemp_key = f"contention-test-{product_id}-{i}"
        tasks.append(client.post("/reservations", json={
            "product_id": product_id,
            "quantity": 1,
            "hold_ttl_seconds": 60,
            "idempotency_key": idemp_key
        }))

    responses = await asyncio.gather(*tasks)

    success_responses = [r for r in responses if r.status_code == 201]
    rejected_responses = [r for r in responses if r.status_code == 400]
    other_responses = [r for r in responses if r.status_code not in (201, 400)]

    assert len(other_responses) == 0, f"Unexpected response codes: {[r.status_code for r in other_responses]}"
    assert len(success_responses) == 10, f"Expected exactly 10 successes, got {len(success_responses)}"
    assert len(rejected_responses) == 90, f"Expected exactly 90 rejections, got {len(rejected_responses)}"

    # Check live stock in Redis and Mongo
    updated_prod = (await client.get(f"/products/{product_id}")).json()
    assert updated_prod["available_stock"] == 0, f"Available stock should be 0, got {updated_prod['available_stock']}"
    assert updated_prod["active_holds"] == 10, f"Active holds should be 10, got {updated_prod['active_holds']}"

    # Invariant verification
    invariants = await RecoveryService.verify_inventory_invariants(product_id)
    assert len(invariants) == 1
    assert invariants[0]["invariant_satisfied"] is True
    assert invariants[0]["discrepancy"] == 0
    assert invariants[0]["available_stock"] == 0
    assert invariants[0]["total_accounted"] == 10
