import pytest
import asyncio
import httpx
import uuid
import hmac
import hashlib
import base64
import json
from backend.config import settings
from backend.services.recovery_service import RecoveryService

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

@pytest.mark.asyncio
async def test_woocommerce_mapping_lifecycle(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    wc_pid = 5001

    # 1. Create Mapping
    create_resp = await client.post("/woocommerce/mappings", json={
        "concord_product_id": pid,
        "wc_product_id": wc_pid,
        "wc_variation_id": 0,
        "sku": "WC-TEST-SKU",
        "sync_enabled": True
    })
    assert create_resp.status_code == 201, create_resp.text
    mapping = create_resp.json()
    assert mapping["concord_product_id"] == pid
    assert mapping["wc_product_id"] == wc_pid
    assert mapping["concord_available_stock"] == 10
    mapping_id = mapping["id"]

    # 2. List Mappings
    list_resp = await client.get("/woocommerce/mappings")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert any(m["id"] == mapping_id for m in items)

    # 3. Status Check
    status_resp = await client.get("/woocommerce/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["total_mappings"] >= 1

    # 4. Clean up Mapping
    del_resp = await client.delete(f"/woocommerce/mappings/{mapping_id}")
    assert del_resp.status_code == 204

@pytest.mark.asyncio
async def test_woocommerce_webhook_hmac_signature_verification(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    wc_pid = 6001

    # Map product
    map_resp = await client.post("/woocommerce/mappings", json={
        "concord_product_id": pid,
        "wc_product_id": wc_pid,
        "sync_enabled": True
    })
    assert map_resp.status_code == 201

    order_payload = {
        "id": 9001,
        "status": "pending",
        "line_items": [{"id": 1, "product_id": wc_pid, "quantity": 1}]
    }
    raw_body = json.dumps(order_payload).encode("utf-8")

    # A. Invalid Signature -> Must Return 401
    bad_resp = await client.post(
        "/woocommerce/webhooks",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "x-wc-webhook-signature": "invalid_bogus_signature==",
            "x-wc-webhook-topic": "order.created",
            "x-wc-webhook-delivery-id": "del-sig-fail"
        }
    )
    # If signature verification is enabled, 401 is required
    if settings.WC_VERIFY_WEBHOOK_SIGNATURE and settings.WC_WEBHOOK_SECRET:
        assert bad_resp.status_code == 401
    else:
        # Pass-through in dev mode without secret
        assert bad_resp.status_code in (200, 401)

    # B. Valid Signature with configured secret
    secret = settings.WC_WEBHOOK_SECRET or "test_secret_key"
    orig_secret = settings.WC_WEBHOOK_SECRET
    settings.WC_WEBHOOK_SECRET = secret
    settings.WC_VERIFY_WEBHOOK_SIGNATURE = True

    try:
        valid_sig = generate_wc_signature(raw_body, secret)
        ok_resp = await client.post(
            "/woocommerce/webhooks",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "x-wc-webhook-signature": valid_sig,
                "x-wc-webhook-topic": "order.created",
                "x-wc-webhook-delivery-id": f"del-sig-ok-{uuid.uuid4().hex[:6]}"
            }
        )
        assert ok_resp.status_code == 200
        assert ok_resp.json()["status"] == "SUCCESS"
    finally:
        settings.WC_WEBHOOK_SECRET = orig_secret

@pytest.mark.asyncio
async def test_woocommerce_order_lifecycle_hold_confirm_cancel(client: httpx.AsyncClient, test_product):
    pid = test_product["id"] # stock = 10
    wc_pid = int(str(uuid.uuid4().int)[:5])

    # Map product
    await client.post("/woocommerce/mappings", json={
        "concord_product_id": pid,
        "wc_product_id": wc_pid,
        "sync_enabled": True
    })

    order_id = int(str(uuid.uuid4().int)[:6])
    line_item_id = 11

    # 1. Order Created (Pending Payment) -> Should acquire Concord Hold
    created_payload = {
        "id": order_id,
        "status": "pending",
        "line_items": [
            {"id": line_item_id, "product_id": wc_pid, "quantity": 2}
        ]
    }
    raw_created = json.dumps(created_payload).encode("utf-8")
    resp_created = await client.post(
        "/woocommerce/webhooks",
        content=raw_created,
        headers=make_wc_headers(raw_created, topic="order.created", delivery_id=f"del-{uuid.uuid4()}")
    )
    assert resp_created.status_code == 200
    data_created = resp_created.json()
    assert data_created["status"] == "SUCCESS"
    outcomes = data_created["outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "HELD"
    booking_id = outcomes[0]["booking_id"]

    # Verify live stock decremented from 10 to 8
    p_info = (await client.get(f"/products/{pid}")).json()
    assert p_info["available_stock"] == 8
    assert p_info["active_holds"] == 2

    # 2. Duplicate Webhook delivery -> Exactly-Once Protection
    dup_delivery_id = f"del-dup-{uuid.uuid4()}"
    dup_resp_1 = await client.post(
        "/woocommerce/webhooks",
        content=raw_created,
        headers=make_wc_headers(raw_created, topic="order.created", delivery_id=dup_delivery_id)
    )
    assert dup_resp_1.status_code == 200

    dup_resp_2 = await client.post(
        "/woocommerce/webhooks",
        content=raw_created,
        headers=make_wc_headers(raw_created, topic="order.created", delivery_id=dup_delivery_id)
    )
    assert dup_resp_2.status_code == 200
    assert dup_resp_2.json()["status"] == "DUPLICATE_DELIVERY_ACKNOWLEDGED"

    # Stock should STILL be 8 (no double deduction)
    p_info = (await client.get(f"/products/{pid}")).json()
    assert p_info["available_stock"] == 8

    # 3. Order Updated to 'processing' (Payment Captured) -> Transitions HELD to CONFIRMED
    paid_payload = {
        "id": order_id,
        "status": "processing",
        "line_items": [
            {"id": line_item_id, "product_id": wc_pid, "quantity": 2}
        ]
    }
    raw_paid = json.dumps(paid_payload).encode("utf-8")
    resp_paid = await client.post(
        "/woocommerce/webhooks",
        content=raw_paid,
        headers=make_wc_headers(raw_paid, topic="order.updated", delivery_id=f"del-paid-{uuid.uuid4()}")
    )
    assert resp_paid.status_code == 200
    paid_outcomes = resp_paid.json()["outcomes"]
    assert paid_outcomes[0]["status"] == "CONFIRMED"

    # Verify stock stats: available = 8, confirmed = 2, active_holds = 0
    p_info = (await client.get(f"/products/{pid}")).json()
    assert p_info["available_stock"] == 8
    assert p_info["active_holds"] == 0
    assert p_info["confirmed_units"] == 2

@pytest.mark.asyncio
async def test_woocommerce_order_cancellation_releases_stock(client: httpx.AsyncClient, test_product):
    pid = test_product["id"] # stock = 10
    wc_pid = int(str(uuid.uuid4().int)[:5])

    await client.post("/woocommerce/mappings", json={
        "concord_product_id": pid,
        "wc_product_id": wc_pid,
        "sync_enabled": True
    })

    order_id = int(str(uuid.uuid4().int)[:6])
    # Create hold of 4 units
    hold_payload = {
        "id": order_id,
        "status": "pending",
        "line_items": [{"id": 1, "product_id": wc_pid, "quantity": 4}]
    }
    raw_hold = json.dumps(hold_payload).encode("utf-8")
    await client.post(
        "/woocommerce/webhooks",
        content=raw_hold,
        headers=make_wc_headers(raw_hold, topic="order.created")
    )

    # Stock should be 6
    assert (await client.get(f"/products/{pid}")).json()["available_stock"] == 6

    # Cancel order -> customer abandoned checkout
    cancel_payload = {
        "id": order_id,
        "status": "cancelled",
        "line_items": [{"id": 1, "product_id": wc_pid, "quantity": 4}]
    }
    raw_cancel = json.dumps(cancel_payload).encode("utf-8")
    cancel_resp = await client.post(
        "/woocommerce/webhooks",
        content=raw_cancel,
        headers=make_wc_headers(raw_cancel, topic="order.updated")
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["outcomes"][0]["status"] == "CANCELLED"

    # Stock should be restored back to 10
    p_info = (await client.get(f"/products/{pid}")).json()
    assert p_info["available_stock"] == 10
    assert p_info["active_holds"] == 0

@pytest.mark.asyncio
async def test_woocommerce_stock_synchronization(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    wc_pid = 8001

    # Map product
    await client.post("/woocommerce/mappings", json={
        "concord_product_id": pid,
        "wc_product_id": wc_pid,
        "sync_enabled": True
    })

    # Trigger stock sync for single product
    sync_resp = await client.post(f"/woocommerce/sync/{pid}")
    assert sync_resp.status_code == 200
    data = sync_resp.json()
    assert data["success"] is True
    assert data["stock_quantity"] == 10

    # Trigger stock sync for all mapped products
    sync_all_resp = await client.post("/woocommerce/sync-all")
    assert sync_all_resp.status_code == 200
    assert isinstance(sync_all_resp.json(), list)

@pytest.mark.asyncio
async def test_woocommerce_simulate_checkout(client: httpx.AsyncClient, test_product):
    pid = test_product["id"]
    wc_pid = 8002

    await client.post("/woocommerce/mappings", json={
        "concord_product_id": pid,
        "wc_product_id": wc_pid,
        "sync_enabled": True
    })

    sim_resp = await client.post("/woocommerce/simulate-checkout", json={
        "wc_product_id": wc_pid,
        "quantity": 3,
        "auto_complete_payment": True
    })
    assert sim_resp.status_code == 200
    data = sim_resp.json()
    assert data["hold_step"]["status"] == "SUCCESS"
    assert data["confirm_step"]["status"] == "SUCCESS"

    # Verify available stock is now 7
    p_info = (await client.get(f"/products/{pid}")).json()
    assert p_info["available_stock"] == 7
    assert p_info["confirmed_units"] == 3

@pytest.mark.asyncio
async def test_mandatory_100_concurrent_woocommerce_orders_on_10_units(client: httpx.AsyncClient):
    """
    MANDATORY CONTENTION TEST:
    10 units available on a WooCommerce-mapped product.
    100 simultaneous concurrent purchase webhook requests dispatched.
    Guarantees:
    1. Exactly 10 orders secure holds (success).
    2. Exactly 90 orders are rejected for INSUFFICIENT_STOCK.
    3. Final available stock is 0 (never negative).
    4. Zero overselling invariant is strictly preserved across Redis and MongoDB.
    """
    # 1. Create product with initial stock 10
    prod_resp = await client.post("/products", json={
        "name": f"WooCommerce Flash Sale Item {uuid.uuid4().hex[:6]}",
        "description": "High contention 100-shopper race",
        "initial_stock": 10
    })
    assert prod_resp.status_code == 201
    prod = prod_resp.json()
    pid = prod["id"]
    wc_pid = int(str(uuid.uuid4().int)[:6])

    # 2. Map WooCommerce product ID to Concord product ID
    await client.post("/woocommerce/mappings", json={
        "concord_product_id": pid,
        "wc_product_id": wc_pid,
        "sync_enabled": True
    })

    total_shoppers = 100
    tasks = []

    # 3. Dispatch 100 concurrent checkout order requests simultaneously
    batch_id = int(str(uuid.uuid4().int)[:5])
    for i in range(total_shoppers):
        order_id = batch_id * 1000 + i
        order_payload = {
            "id": order_id,
            "status": "pending",
            "line_items": [
                {"id": 1, "product_id": wc_pid, "quantity": 1}
            ]
        }
        delivery_id = f"wc-contention-del-{i}-{uuid.uuid4().hex[:4]}"
        raw_order = json.dumps(order_payload).encode("utf-8")
        tasks.append(client.post(
            "/woocommerce/webhooks",
            content=raw_order,
            headers=make_wc_headers(raw_order, topic="order.created", delivery_id=delivery_id)
        ))

    responses = await asyncio.gather(*tasks)

    # 4. Analyze outcomes
    assert all(r.status_code == 200 for r in responses), f"Unexpected status codes: {[r.status_code for r in responses if r.status_code != 200]}"

    held_orders = []
    rejected_orders = []

    for r in responses:
        body = r.json()
        outcomes = body.get("outcomes", [])
        if outcomes and outcomes[0].get("status") == "HELD":
            held_orders.append(body["order_id"])
        elif outcomes and outcomes[0].get("status") == "REJECTED_STOCK":
            rejected_orders.append(body["order_id"])

    # 5. Strict Assertions
    assert len(held_orders) == 10, f"Expected exactly 10 successful holds, got {len(held_orders)}"
    assert len(rejected_orders) == 90, f"Expected exactly 90 rejections, got {len(rejected_orders)}"

    # 6. Verify live inventory invariants
    p_info = (await client.get(f"/products/{pid}")).json()
    assert p_info["available_stock"] == 0, f"Stock should be 0, got {p_info['available_stock']}"
    assert p_info["active_holds"] == 10, f"Active holds should be 10, got {p_info['active_holds']}"

    invariants = await RecoveryService.verify_inventory_invariants(pid)
    assert len(invariants) == 1
    assert invariants[0]["invariant_satisfied"] is True
    assert invariants[0]["available_stock"] == 0
    assert invariants[0]["total_accounted"] == 10
    assert invariants[0]["discrepancy"] == 0
