import asyncio
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from backend.config import settings
from backend.db.mongo import connect_mongo, close_mongo, get_wc_webhooks_collection, get_wc_orders_collection, get_bookings_collection
from backend.db.redis_client import connect_redis, close_redis, redis_manager
from backend.services.reservation_service import ReservationService

async def run_full_verification():
    print("=" * 80)
    print("CONCORD <-> WOOCOMMERCE REAL WEBHOOK DELIVERY & AUDIT VERIFICATION")
    print("=" * 80)

    await connect_mongo()
    await connect_redis()

    concord_pid = "prod-rtx-4090"
    wc_pid = 13

    # Step 1: Initial Stock Baseline
    initial_stock = await redis_manager.get_inventory(concord_pid)
    print(f"[*] Step 1: Initial Stock for '{concord_pid}': {initial_stock} units")

    store_url = settings.WC_STORE_URL.rstrip('/')
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        # Step 2: Create a non-paid test order in WooCommerce
        print("\n[*] Step 2: Creating a non-paid test order in WooCommerce (status: pending)...")
        order_payload = {
            "status": "pending",
            "billing": {
                "first_name": "Antigravity",
                "last_name": "Tester",
                "email": "test-webhook@concord.local"
            },
            "line_items": [
                {
                    "product_id": wc_pid,
                    "quantity": 1
                }
            ]
        }

        r_create = await client.post(
            f"{store_url}/wp-json/wc/v3/orders",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET),
            json=order_payload
        )
        if r_create.status_code not in (200, 201):
            print(f"[-] Failed to create test order: {r_create.status_code} - {r_create.text[:200]}")
            await close_redis()
            await close_mongo()
            return

        order_data = r_create.json()
        order_id = order_data.get("id")
        print(f"[+] Order created in WooCommerce: ID #{order_id}")

        # Step 3: Trigger WP-Cron to dispatch the queued webhook immediately
        print("\n[*] Step 3: Triggering WooCommerce WP-Cron to dispatch webhook...")
        await client.get(f"{store_url}/wp-cron.php")
        time.sleep(3.0)

        # Step 4: Verify Webhook Receipt in ngrok & Concord
        print("\n[*] Step 4: Checking Concord webhook reception...")
        r_ngrok = await client.get("http://127.0.0.1:4040/api/requests/http?limit=5")
        ngrok_reqs = r_ngrok.json().get("requests", [])
        matched = None
        for n in ngrok_reqs:
            headers = n.get("request", {}).get("headers", {})
            if "Hookshot" in str(headers.get("User-Agent", "")) and headers.get("X-Wc-Webhook-Topic") == ["order.created"]:
                matched = n
                break

        if matched:
            status_code = matched.get("response", {}).get("status_code")
            delivery_id = matched.get("request", {}).get("headers", {}).get("X-Wc-Webhook-Delivery-Id", [""])[0]
            has_sig = bool(matched.get("request", {}).get("headers", {}).get("X-Wc-Webhook-Signature"))
            print(f"[+] WooCommerce Webhook Delivered to Concord:")
            print(f"    - Delivery ID              : {delivery_id}")
            print(f"    - HTTP Response Code       : {status_code} ({'SUCCESS' if status_code == 200 else 'FAILED'})")
            print(f"    - Signature Header Present : {has_sig}")
            print(f"    - HMAC Signature Verified  : {'PASS (HTTP 200)' if status_code == 200 else 'FAIL'}")
        else:
            print("[-] Note: Webhook not found in top ngrok requests; checking MongoDB directly.")

        # Step 5: Check Concord MongoDB Audit Logs
        print(f"\n[*] Step 5: Inspecting Concord MongoDB Audit Collections for Order #{order_id}...")
        w_coll = get_wc_webhooks_collection()
        o_coll = get_wc_orders_collection()
        bookings_coll = get_bookings_collection()

        w_doc = await w_coll.find_one({"order_id": order_id})
        o_doc = await o_coll.find_one({"order_id": order_id})

        if w_doc:
            print(f"[+] Audit Entry in 'woocommerce_webhooks':")
            print(f"    - Log ID        : {w_doc.get('_id')}")
            print(f"    - Delivery ID   : {w_doc.get('delivery_id')}")
            print(f"    - Topic         : {w_doc.get('topic')}")
            print(f"    - Audit Status  : {w_doc.get('status')}")
            print(f"    - Outcomes      : {w_doc.get('outcomes')}")
        else:
            print(f"[-] No record in 'woocommerce_webhooks' for order #{order_id}")

        if o_doc:
            print(f"[+] Audit Entry in 'woocommerce_orders':")
            print(f"    - Order ID      : {o_doc.get('order_id')}")
            print(f"    - Order Status  : {o_doc.get('status')}")
            print(f"    - Booking IDs   : {o_doc.get('booking_ids')}")

        # Step 6: Verify Stock Protection & Cleanup
        print(f"\n[*] Step 6: Cleaning up & asserting stock remains unchanged...")
        # Release hold
        if o_doc and o_doc.get("booking_ids"):
            for b_id in o_doc.get("booking_ids", []):
                b = await bookings_coll.find_one({"_id": b_id})
                if b and b.get("status") == "HELD":
                    await ReservationService.cancel_booking(b_id)
                    print(f"    - Cancelled Concord reservation hold #{b_id} : YES")

        # Delete order in WooCommerce
        del_r = await client.delete(
            f"{store_url}/wp-json/wc/v3/orders/{order_id}?force=true",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
        )
        print(f"    - Deleted WooCommerce test order #{order_id} : {'YES' if del_r.status_code == 200 else 'NO'}")

        # Verify stock invariant
        final_stock = await redis_manager.get_inventory(concord_pid)
        print(f"    - Initial Stock: {initial_stock} | Final Stock: {final_stock}")
        print(f"    - Stock Unchanged Invariant: {'PASS' if final_stock == initial_stock else 'FAIL'}")

    print("=" * 80)
    await close_redis()
    await close_mongo()

if __name__ == "__main__":
    asyncio.run(run_full_verification())
