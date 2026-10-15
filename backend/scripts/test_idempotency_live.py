import asyncio
import uuid
import httpx
from backend.db.mongo import connect_mongo, close_mongo, get_bookings_collection, get_idempotency_collection
from backend.db.redis_client import connect_redis, close_redis, redis_manager

async def test_idempotency_live():
    base_url = "http://localhost:8000"
    print("\n==========================================================================================")
    print("CONCORD EXACTLY-ONCE IDEMPOTENCY LIVE VERIFICATION")
    print("==========================================================================================")

    await connect_mongo()
    await connect_redis()
    bookings_coll = get_bookings_collection()
    idemp_coll = get_idempotency_collection()

    async with httpx.AsyncClient(base_url=base_url) as client:
        # Step 1: Provision a clean test product
        p_resp = await client.post("/products", json={
            "name": f"Idempotency Test Item {uuid.uuid4().hex[:6]}",
            "description": "Verifying exactly-once semantics",
            "initial_stock": 20
        })
        product = p_resp.json()
        pid = product["id"]
        initial_stock = product["initial_stock"]
        print(f"1. Target Product Created: '{product['name']}' (ID: {pid})")
        print(f"   Initial Stock: {initial_stock} units\n")

        # Step 2: Prepare idempotency key and payload
        test_key = f"idemp-key-{uuid.uuid4()}"
        payload = {
            "product_id": pid,
            "quantity": 2,
            "hold_ttl_seconds": 60,
            "idempotency_key": test_key
        }
        print(f"2. Dispatching Request #1 (Initial Attempt)")
        print(f"   Idempotency Key : {test_key}")
        print(f"   Payload Units   : {payload['quantity']}")

        # Step 3: Send first request
        r1 = await client.post("/reservations", json=payload)
        assert r1.status_code in (200, 201), f"Request 1 failed: {r1.text}"
        data1 = r1.json()
        booking_id_1 = data1["booking_id"]
        cached_1 = data1.get("cached", False)
        print(f"   -> HTTP Status  : {r1.status_code} Created")
        print(f"   -> Booking ID   : {booking_id_1}")
        print(f"   -> Cached Flag  : {cached_1}")

        # Check Redis stock after request 1
        stock_after_r1 = await redis_manager.get_inventory(pid)
        print(f"   -> Redis Stock  : {stock_after_r1} (decremented from {initial_stock} to {stock_after_r1})\n")
        assert stock_after_r1 == initial_stock - 2

        # Step 4: Dispatch Request #2 with IDENTICAL key and payload (Retried Request)
        print(f"3. Dispatching Request #2 (Client Retry with SAME Key & SAME Payload)")
        r2 = await client.post("/reservations", json=payload)
        assert r2.status_code in (200, 201), f"Request 2 failed: {r2.text}"
        data2 = r2.json()
        booking_id_2 = data2["booking_id"]
        cached_2 = data2.get("cached", False)
        print(f"   -> HTTP Status  : {r2.status_code} OK")
        print(f"   -> Booking ID   : {booking_id_2}")
        print(f"   -> Cached Flag  : {cached_2}")

        # Crucial Invariant Assertions
        assert booking_id_1 == booking_id_2, "Booking IDs must match exactly!"
        assert cached_2 is True, "Second request must return cached: true"

        # Check Redis stock after request 2
        stock_after_r2 = await redis_manager.get_inventory(pid)
        print(f"   -> Redis Stock  : {stock_after_r2} (Stock remained UNCHANGED: {stock_after_r1} == {stock_after_r2})\n")
        assert stock_after_r2 == stock_after_r1, "Redis stock must NOT be decremented a second time!"

        # Step 5: Check MongoDB Bookings collection
        matching_bookings = await bookings_coll.find({"idempotency_key": test_key}).to_list(10)
        print(f"4. Inspecting MongoDB Durable Bookings Collection:")
        print(f"   -> Total matching documents for key '{test_key}': {len(matching_bookings)}")
        assert len(matching_bookings) == 1, "Exactly one booking document must exist in MongoDB!"
        print(f"   -> Verified: No duplicate logical booking created.\n")

        # Step 6: Dispatch Request #3 with SAME key but MUTATED payload (Conflict Test)
        print(f"5. Dispatching Request #3 (Attempting key reuse with MUTATED quantity = 5)")
        mutated_payload = dict(payload)
        mutated_payload["quantity"] = 5
        r3 = await client.post("/reservations", json=mutated_payload)
        print(f"   -> HTTP Status  : {r3.status_code} Conflict")
        print(f"   -> Error Detail : {r3.json().get('detail')}")
        assert r3.status_code == 409, f"Expected 409 Conflict, got {r3.status_code}"
        print(f"   -> Verified: Payload conflict detection prevented corrupt re-use.\n")

        # Cleanup test product
        await client.post(f"/reservations/{booking_id_1}/cancel")
        await client.delete(f"/products/{pid}")

    print("==========================================================================================")
    print("IDEMPOTENCY VERIFICATION RESULT: PASS (Exactly-Once Semantics Guaranteed)")
    print("==========================================================================================\n")
    await close_redis()
    await close_mongo()

if __name__ == "__main__":
    asyncio.run(test_idempotency_live())
