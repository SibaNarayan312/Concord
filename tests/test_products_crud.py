import pytest
import httpx
import uuid

@pytest.mark.asyncio
async def test_product_lifecycle_crud(client: httpx.AsyncClient):
    unique_name = f"CRUD Item {uuid.uuid4().hex[:6]}"
    
    # 1. Create Product
    create_resp = await client.post("/products", json={
        "name": unique_name,
        "description": "Integration test item",
        "initial_stock": 25
    })
    assert create_resp.status_code == 201
    prod = create_resp.json()
    product_id = prod["id"]
    assert prod["name"] == unique_name
    assert prod["initial_stock"] == 25
    assert prod["available_stock"] == 25

    # 2. Prevent Duplicate Name
    dup_resp = await client.post("/products", json={
        "name": unique_name,
        "initial_stock": 10
    })
    assert dup_resp.status_code == 400

    # 3. Read Product
    get_resp = await client.get(f"/products/{product_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == product_id

    # 4. Update Product
    update_resp = await client.put(f"/products/{product_id}", json={
        "description": "Updated description text"
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["description"] == "Updated description text"

    # 5. Safe Deletion Blocked when active hold exists
    reserve_resp = await client.post("/reservations", json={
        "product_id": product_id,
        "quantity": 2,
        "idempotency_key": f"test-del-hold-{uuid.uuid4()}"
    })
    assert reserve_resp.status_code == 201
    booking_id = reserve_resp.json()["booking_id"]

    # Attempt deletion while hold is active
    del_blocked_resp = await client.delete(f"/products/{product_id}")
    assert del_blocked_resp.status_code == 400
    assert "active holds" in del_blocked_resp.json()["detail"]

    # Cancel hold to free product
    cancel_resp = await client.post(f"/reservations/{booking_id}/cancel")
    assert cancel_resp.status_code == 200

    # 6. Delete Product succeeds after hold released
    del_resp = await client.delete(f"/products/{product_id}")
    assert del_resp.status_code == 204

    # Verify not found
    get_after = await client.get(f"/products/{product_id}")
    assert get_after.status_code == 404
