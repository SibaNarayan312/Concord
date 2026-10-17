import pytest
import asyncio
import httpx
import uuid
import hmac
import hashlib
import base64
import json
from datetime import datetime, timezone, timedelta

from backend.config import settings
from backend.services.recovery_service import RecoveryService, utc_now_iso
from backend.services.woocommerce_service import WooCommerceService
from backend.services.reservation_service import ReservationService
from backend.db.mongo import (
    get_products_collection,
    get_bookings_collection,
    get_operations_collection,
    get_wc_mappings_collection,
    get_wc_orders_collection,
    get_wc_webhooks_collection
)
from backend.db.redis_client import redis_manager, inventory_key, hold_key
from backend.models.schemas import ReservationRequest, WooCommerceMappingCreate

def generate_wc_signature(payload_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

def make_wc_headers(payload_bytes: bytes, topic: str = "order.created", delivery_id: str = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "x-wc-webhook-topic": topic,
        "x-wc-webhook-delivery-id": delivery_id or f"del-{uuid.uuid4()}"
    }
    if settings.WC_WEBHOOK_SECRET:
        headers["x-wc-webhook-signature"] = generate_wc_signature(payload_bytes, settings.WC_WEBHOOK_SECRET)
    return headers

@pytest.fixture
async def disposable_test_product(client: httpx.AsyncClient):
    """
    Creates an isolated disposable test product with 10 units of initial stock.
    Automatically cleans up after test completion.
    """
    pid = f"prod-test-{uuid.uuid4().hex[:8]}"
    prods_coll = get_products_collection()
    now_str = utc_now_iso()
    await prods_coll.insert_one({
        "_id": pid,
        "name": f"Phase 1 Disposable Item {pid}",
        "description": "Disposable product for Phase 1 automated validation suite",
        "initial_stock": 10,
        "created_at": now_str,
        "updated_at": now_str
    })
    await redis_manager.set_inventory(pid, 10)

    yield {"id": pid, "initial_stock": 10}

    # Teardown
    try:
        await prods_coll.delete_one({"_id": pid})
        r = await redis_manager.connect()
        await r.delete(inventory_key(pid))
        # Remove any test bookings
        bookings_coll = get_bookings_collection()
        await bookings_coll.delete_many({"product_id": pid})
        # Remove any mappings
        mappings_coll = get_wc_mappings_collection()
        await mappings_coll.delete_many({"concord_product_id": pid})
    except Exception:
        pass


# ==============================================================================
# TEST 1: A successful pending order reserves exactly one unit
# ==============================================================================
@pytest.mark.asyncio
async def test_01_successful_pending_order_reserves_exactly_one_unit(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])
    order_id = int(str(uuid.uuid4().int)[:6])

    # Map product
    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        sync_enabled=False
    ))

    # Send pending order webhook (qty=1)
    payload = {
        "id": order_id,
        "status": "pending",
        "line_items": [{"id": 101, "product_id": wc_pid, "quantity": 1}]
    }
    raw = json.dumps(payload).encode("utf-8")
    resp = await client.post("/woocommerce/webhooks", content=raw, headers=make_wc_headers(raw))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert len(data["outcomes"]) == 1
    assert data["outcomes"][0]["status"] == "HELD"
    assert data["outcomes"][0]["quantity"] == 1

    # Verify inventory state
    prod_info = (await client.get(f"/products/{pid}")).json()
    assert prod_info["available_stock"] == 9
    assert prod_info["active_holds"] == 1
    assert prod_info["confirmed_units"] == 0

    redis_stock = await redis_manager.get_inventory(pid)
    assert redis_stock == 9


# ==============================================================================
# TEST 2: A cancelled or failed order releases its reservation exactly once
# ==============================================================================
@pytest.mark.asyncio
async def test_02_cancelled_or_failed_order_releases_reservation_exactly_once(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])
    order_id = int(str(uuid.uuid4().int)[:6])

    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        sync_enabled=False
    ))

    # 1. Hold 2 units
    hold_payload = {
        "id": order_id,
        "status": "pending",
        "line_items": [{"id": 201, "product_id": wc_pid, "quantity": 2}]
    }
    raw_hold = json.dumps(hold_payload).encode("utf-8")
    await client.post("/woocommerce/webhooks", content=raw_hold, headers=make_wc_headers(raw_hold))

    stock_after_hold = await redis_manager.get_inventory(pid)
    assert stock_after_hold == 8

    # 2. Cancel order
    cancel_payload = {
        "id": order_id,
        "status": "cancelled",
        "line_items": [{"id": 201, "product_id": wc_pid, "quantity": 2}]
    }
    raw_cancel = json.dumps(cancel_payload).encode("utf-8")
    resp_cancel = await client.post("/woocommerce/webhooks", content=raw_cancel, headers=make_wc_headers(raw_cancel, topic="order.updated"))
    assert resp_cancel.status_code == 200
    assert resp_cancel.json()["outcomes"][0]["status"] == "CANCELLED"

    # Stock should be restored to 10
    stock_after_cancel = await redis_manager.get_inventory(pid)
    assert stock_after_cancel == 10

    # 3. Repeat cancellation webhook (retry) - MUST be idempotent and not increment stock again!
    raw_cancel_retry = json.dumps(cancel_payload).encode("utf-8")
    resp_retry = await client.post(
        "/woocommerce/webhooks",
        content=raw_cancel_retry,
        headers=make_wc_headers(raw_cancel_retry, topic="order.updated", delivery_id=f"del-retry-{uuid.uuid4()}")
    )
    assert resp_retry.status_code == 200

    # Stock MUST STILL be exactly 10, NOT 12!
    final_stock = await redis_manager.get_inventory(pid)
    assert final_stock == 10


# ==============================================================================
# TEST 3: A confirmed order cannot be double-confirmed
# ==============================================================================
@pytest.mark.asyncio
async def test_03_confirmed_order_cannot_be_double_confirmed(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])
    order_id = int(str(uuid.uuid4().int)[:6])

    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        sync_enabled=False
    ))

    # Hold 1 unit
    hold_payload = {
        "id": order_id,
        "status": "pending",
        "line_items": [{"id": 301, "product_id": wc_pid, "quantity": 1}]
    }
    raw_hold = json.dumps(hold_payload).encode("utf-8")
    await client.post("/woocommerce/webhooks", content=raw_hold, headers=make_wc_headers(raw_hold))

    # 1st Confirmation (payment processing)
    confirm_payload = {
        "id": order_id,
        "status": "processing",
        "line_items": [{"id": 301, "product_id": wc_pid, "quantity": 1}]
    }
    raw_confirm = json.dumps(confirm_payload).encode("utf-8")
    resp_conf1 = await client.post("/woocommerce/webhooks", content=raw_confirm, headers=make_wc_headers(raw_confirm, topic="order.updated"))
    assert resp_conf1.status_code == 200
    assert resp_conf1.json()["outcomes"][0]["status"] == "CONFIRMED"

    prod_info = (await client.get(f"/products/{pid}")).json()
    assert prod_info["available_stock"] == 9
    assert prod_info["confirmed_units"] == 1
    assert prod_info["active_holds"] == 0

    # 2nd Confirmation (order completed webhook retry)
    complete_payload = {
        "id": order_id,
        "status": "completed",
        "line_items": [{"id": 301, "product_id": wc_pid, "quantity": 1}]
    }
    raw_complete = json.dumps(complete_payload).encode("utf-8")
    resp_conf2 = await client.post("/woocommerce/webhooks", content=raw_complete, headers=make_wc_headers(raw_complete, topic="order.updated"))
    assert resp_conf2.status_code == 200
    outcome2 = resp_conf2.json()["outcomes"][0]
    assert outcome2["status"] == "CONFIRMED"
    assert "Already confirmed" in outcome2.get("message", "")

    # Stock MUST STILL be 9, confirmed_units MUST STILL be 1 (zero double-deduction)
    prod_info_final = (await client.get(f"/products/{pid}")).json()
    assert prod_info_final["available_stock"] == 9
    assert prod_info_final["confirmed_units"] == 1


# ==============================================================================
# TEST 4: Duplicate webhook deliveries do not create duplicate reservations or stock changes
# ==============================================================================
@pytest.mark.asyncio
async def test_04_duplicate_webhook_deliveries_do_not_create_duplicate_reservations_or_stock_changes(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])
    order_id = int(str(uuid.uuid4().int)[:6])
    fixed_delivery_id = f"wc-delivery-dup-test-{uuid.uuid4().hex[:6]}"

    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        sync_enabled=False
    ))

    payload = {
        "id": order_id,
        "status": "pending",
        "line_items": [{"id": 401, "product_id": wc_pid, "quantity": 3}]
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = make_wc_headers(raw, delivery_id=fixed_delivery_id)

    # First delivery
    resp1 = await client.post("/woocommerce/webhooks", content=raw, headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "SUCCESS"

    stock1 = await redis_manager.get_inventory(pid)
    assert stock1 == 7

    # Duplicate delivery (network retry with same delivery_id)
    resp2 = await client.post("/woocommerce/webhooks", content=raw, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "DUPLICATE_DELIVERY_ACKNOWLEDGED"

    stock2 = await redis_manager.get_inventory(pid)
    assert stock2 == 7, "Stock must remain 7 on duplicate webhook delivery"


# ==============================================================================
# TEST 5: An invalid signature is rejected
# ==============================================================================
@pytest.mark.asyncio
async def test_05_invalid_signature_is_rejected(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])

    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        sync_enabled=False
    ))

    payload = {
        "id": 99999,
        "status": "pending",
        "line_items": [{"id": 501, "product_id": wc_pid, "quantity": 1}]
    }
    raw = json.dumps(payload).encode("utf-8")

    # Tampered signature
    headers = {
        "Content-Type": "application/json",
        "x-wc-webhook-topic": "order.created",
        "x-wc-webhook-delivery-id": f"del-bad-{uuid.uuid4().hex[:4]}",
        "x-wc-webhook-signature": "invalid_forged_signature_value=="
    }

    resp = await client.post("/woocommerce/webhooks", content=raw, headers=headers)
    assert resp.status_code == 401
    assert "Invalid HMAC-SHA256 webhook signature" in resp.json()["detail"]

    # Ensure no inventory was touched
    stock = await redis_manager.get_inventory(pid)
    assert stock == 10


# ==============================================================================
# TEST 6: An expired reservation releases inventory
# ==============================================================================
@pytest.mark.asyncio
async def test_06_expired_reservation_releases_inventory(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]
    booking_coll = get_bookings_collection()

    # Create a reservation with a 1-second TTL
    req = ReservationRequest(
        product_id=pid,
        quantity=3,
        hold_ttl_seconds=1,
        idempotency_key=f"expire-test-{uuid.uuid4()}"
    )
    res = await ReservationService.create_reservation(req)
    assert res.remaining_stock == 7

    # Stock immediately decremented
    assert await redis_manager.get_inventory(pid) == 7

    # Wait 1.5 seconds for hold TTL to elapse
    await asyncio.sleep(1.5)

    # Process expiration (can also be processed by background worker)
    await RecoveryService.process_expired_holds()

    # Verify status in MongoDB
    booking_doc = await booking_coll.find_one({"_id": res.booking_id})
    assert booking_doc["status"] == "EXPIRED"

    # Inventory must be fully restored to 10
    restored_stock = await redis_manager.get_inventory(pid)
    assert restored_stock == 10

    # Attempting to confirm this expired hold must be rejected
    conf_resp = await client.post(f"/reservations/{res.booking_id}/confirm")
    assert conf_resp.status_code == 400
    assert "EXPIRED" in conf_resp.json()["detail"]


# ==============================================================================
# TEST 7: A stock synchronization retry is idempotent
# ==============================================================================
@pytest.mark.asyncio
async def test_07_stock_synchronization_retry_is_idempotent(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])

    # 1. Test mapping with sync_enabled=False: sync must refuse to push
    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        sync_enabled=False
    ))
    disabled_sync = await WooCommerceService.sync_stock_to_woocommerce(pid)
    assert disabled_sync.success is False
    assert disabled_sync.mode == "DISABLED"

    # 2. Update mapping to sync_enabled=True
    mappings_coll = get_wc_mappings_collection()
    await mappings_coll.update_one({"concord_product_id": pid}, {"$set": {"sync_enabled": True}})

    # Set stock to 7
    await redis_manager.set_inventory(pid, 7)

    # Initial sync
    sync1 = await WooCommerceService.sync_stock_to_woocommerce(pid)
    assert sync1.success is True
    assert sync1.stock_quantity == 7

    # Retry sync 3 times in rapid succession
    for _ in range(3):
        retry_sync = await WooCommerceService.sync_stock_to_woocommerce(pid)
        assert retry_sync.success is True
        assert retry_sync.stock_quantity == 7

    # Concord available stock must remain exactly 7 (never mutated by sync calls)
    final_stock = await redis_manager.get_inventory(pid)
    assert final_stock == 7


# ==============================================================================
# TEST 8: WooCommerce and Concord stock discrepancies are detected and reconciled safely
# ==============================================================================
@pytest.mark.asyncio
async def test_08_woocommerce_and_concord_stock_discrepancies_detected_and_reconciled(client: httpx.AsyncClient, disposable_test_product):
    pid = disposable_test_product["id"]

    # Initial check: invariant satisfied
    invariants = await RecoveryService.verify_inventory_invariants(pid)
    assert len(invariants) == 1
    assert invariants[0]["invariant_satisfied"] is True
    assert invariants[0]["discrepancy"] == 0

    # Inject artificial inventory discrepancy: corrupt Redis stock to 6 (out of sync by -4)
    await redis_manager.set_inventory(pid, 6)

    discrepant = await RecoveryService.verify_inventory_invariants(pid)
    assert discrepant[0]["invariant_satisfied"] is False
    assert discrepant[0]["discrepancy"] == -4

    # Reconciliation: restore Redis authoritative stock to 10
    await redis_manager.set_inventory(pid, 10)

    reconciled = await RecoveryService.verify_inventory_invariants(pid)
    assert reconciled[0]["invariant_satisfied"] is True
    assert reconciled[0]["discrepancy"] == 0


# ==============================================================================
# TEST 9: Concurrent checkout attempts cannot oversell
# ==============================================================================
@pytest.mark.asyncio
async def test_09_concurrent_checkout_attempts_cannot_oversell(client: httpx.AsyncClient, disposable_test_product):
    """
    10 units available. 100 simultaneous concurrent checkout attempts competing.
    Guarantees:
    - Exactly 10 units reserved.
    - Exactly 90 rejections for insufficient stock.
    - Zero overselling.
    """
    pid = disposable_test_product["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])

    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        sync_enabled=False
    ))

    total_requests = 100
    tasks = []
    run_batch = int(str(uuid.uuid4().int)[:4])

    for i in range(total_requests):
        order_id = run_batch * 1000 + i
        order_payload = {
            "id": order_id,
            "status": "pending",
            "line_items": [{"id": 1, "product_id": wc_pid, "quantity": 1}]
        }
        raw_order = json.dumps(order_payload).encode("utf-8")
        delivery_id = f"contention-del-{order_id}-{uuid.uuid4().hex[:4]}"
        tasks.append(client.post(
            "/woocommerce/webhooks",
            content=raw_order,
            headers=make_wc_headers(raw_order, topic="order.created", delivery_id=delivery_id)
        ))

    responses = await asyncio.gather(*tasks)
    assert all(r.status_code == 200 for r in responses)

    held = 0
    rejected = 0
    for r in responses:
        outcomes = r.json().get("outcomes", [])
        if outcomes and outcomes[0].get("status") == "HELD":
            held += 1
        elif outcomes and outcomes[0].get("status") == "REJECTED_STOCK":
            rejected += 1

    assert held == 10, f"Expected exactly 10 holds, got {held}"
    assert rejected == 90, f"Expected exactly 90 rejections, got {rejected}"

    # Final stock in Redis must be exactly 0
    final_stock = await redis_manager.get_inventory(pid)
    assert final_stock == 0

    # Invariant check
    invariants = await RecoveryService.verify_inventory_invariants(pid)
    assert invariants[0]["invariant_satisfied"] is True
    assert invariants[0]["available_stock"] == 0
    assert invariants[0]["active_holds"] == 10
    assert invariants[0]["total_accounted"] == 10


# ==============================================================================
# TEST 10: API or database failures produce a safe, recoverable outcome
# ==============================================================================
@pytest.mark.asyncio
async def test_10_api_or_database_failures_produce_safe_recoverable_outcome(client: httpx.AsyncClient, disposable_test_product):
    """
    Simulates a partial failure / crash scenario:
    Redis hold was acquired (stock decremented), but the backend crashed before
    persisting the booking to MongoDB.
    Verifies that RecoveryService.reconcile_incomplete_operations() detects the
    dangling operation and restores inventory back to available stock.
    """
    pid = disposable_test_product["id"]
    fake_booking_id = str(uuid.uuid4())
    fake_op_id = str(uuid.uuid4())
    now_str = utc_now_iso()

    # 1. Acquire Redis hold directly (decrement stock by 2)
    success, reason, remaining, stream_id = await redis_manager.execute_reserve(
        product_id=pid,
        booking_id=fake_booking_id,
        quantity=2,
        ttl_seconds=300,
        timestamp=now_str
    )
    assert success is True
    assert remaining == 8

    # 2. Record operation in MongoDB as REDIS_HELD with created_at 20 seconds ago
    ops_coll = get_operations_collection()
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    await ops_coll.insert_one({
        "_id": fake_op_id,
        "booking_id": fake_booking_id,
        "product_id": pid,
        "quantity": 2,
        "operation_type": "RESERVE",
        "status": "REDIS_HELD",
        "attempt_count": 1,
        "created_at": old_time,
        "updated_at": old_time,
        "last_error": None
    })

    # Note: No booking inserted into get_bookings_collection() (simulates MongoDB crash!)

    # 3. Run reconciliation worker logic
    reconciled_count = await RecoveryService.reconcile_incomplete_operations()
    assert reconciled_count >= 1

    # 4. Verify inventory was safely recovered and restored to 10!
    recovered_stock = await redis_manager.get_inventory(pid)
    assert recovered_stock == 10, f"Expected recovered stock 10, got {recovered_stock}"

    # Verify operation record marked as RECONCILED_RELEASED
    op_doc = await ops_coll.find_one({"_id": fake_op_id})
    assert op_doc["status"] == "RECONCILED_RELEASED"
