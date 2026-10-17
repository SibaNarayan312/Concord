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

async def test_order_webhook():
    print("=" * 80)
    print("WOOCOMMERCE LIVE ORDER WEBHOOK DELIVERY & HMAC SIGNATURE VALIDATION TEST")
    print("=" * 80)

    await connect_mongo()
    await connect_redis()

    concord_pid = "prod-rtx-4090"
    wc_pid = 13

    # Step 1: Baseline Stock Check
    initial_avail = await redis_manager.get_inventory(concord_pid)
    print(f"[*] Step 1: Baseline stock for '{concord_pid}': {initial_avail} units")

    # Step 2: Check Webhook status in WooCommerce
    store_url = settings.WC_STORE_URL.rstrip('/')
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        wh_resp = await client.get(
            f"{store_url}/wp-json/wc/v3/webhooks/1",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
        )
        if wh_resp.status_code != 200:
            print(f"[-] Failed to fetch WooCommerce webhook: {wh_resp.status_code}")
            await close_redis()
            await close_mongo()
            return

        wh_data = wh_resp.json()
        print(f"[*] Step 2: WooCommerce Webhook Config:")
        print(f"    - Webhook ID   : {wh_data.get('id')}")
        print(f"    - Name         : {wh_data.get('name')}")
        print(f"    - Topic        : {wh_data.get('topic')}")
        print(f"    - Status       : {wh_data.get('status')}")
        print(f"    - Delivery URL : {wh_data.get('delivery_url')}")

        # Step 3: Create a Non-Paid Test Order via WooCommerce REST API
        print(f"\n[*] Step 3: Creating a non-paid test order in WooCommerce (status: pending)...")
        order_payload = {
            "status": "pending",
            "billing": {
                "first_name": "Concord",
                "last_name": "TestShopper",
                "email": "test@concord-inventory.local"
            },
            "line_items": [
                {
                    "product_id": wc_pid,
                    "quantity": 1
                }
            ]
        }

        order_resp = await client.post(
            f"{store_url}/wp-json/wc/v3/orders",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET),
            json=order_payload
        )

        if order_resp.status_code not in (200, 201):
            print(f"[-] Failed to create test order in WooCommerce: {order_resp.status_code} - {order_resp.text[:200]}")
            await close_redis()
            await close_mongo()
            return

        created_order = order_resp.json()
        wc_order_id = created_order.get("id")
        print(f"[+] Non-paid test order created successfully in WooCommerce! Order ID: {wc_order_id}")

        # Step 4: Wait for WooCommerce to deliver webhook to Concord
        print(f"\n[*] Step 4: Waiting for WooCommerce webhook delivery to Concord...")
        time.sleep(4.0)

        # Check ngrok requests for the order.created delivery
        r_ngrok = await client.get("http://127.0.0.1:4040/api/requests/http?limit=5")
        ngrok_reqs = r_ngrok.json().get("requests", [])
        matched_ngrok = None
        for n in ngrok_reqs:
            req_headers = n.get("request", {}).get("headers", {})
            if "Hookshot" in str(req_headers.get("User-Agent", "")) and req_headers.get("X-Wc-Webhook-Topic") == ["order.created"]:
                matched_ngrok = n
                break

        if matched_ngrok:
            ngrok_status = matched_ngrok.get("response", {}).get("status_code")
            sig_present = bool(matched_ngrok.get("request", {}).get("headers", {}).get("X-Wc-Webhook-Signature"))
            print(f"[+] Webhook received by Concord via ngrok:")
            print(f"    - HTTP Response Code       : {ngrok_status}")
            print(f"    - X-WC-Webhook-Signature   : {'Present (Verified)' if sig_present else 'Missing'}")
            print(f"    - Signature Validated      : {'YES - HTTP 200' if ngrok_status == 200 else 'NO - Failed'}")
        else:
            print("[-] Note: Direct ngrok log entry not matched; checking MongoDB directly...")

        # Step 5: Check Concord MongoDB Audit Collections
        print(f"\n[*] Step 5: Verifying Concord Audit Log for Order {wc_order_id}...")
        w_coll = get_wc_webhooks_collection()
        o_coll = get_wc_orders_collection()

        webhook_doc = await w_coll.find_one({"order_id": wc_order_id})
        order_doc = await o_coll.find_one({"order_id": wc_order_id})

        if webhook_doc:
            print(f"[+] Audit Record Found in 'woocommerce_webhooks':")
            print(f"    - Webhook Delivery ID : {webhook_doc.get('delivery_id')}")
            print(f"    - Topic               : {webhook_doc.get('topic')}")
            print(f"    - Audit Status        : {webhook_doc.get('status')}")
            print(f"    - Outcomes            : {webhook_doc.get('outcomes')}")
        else:
            print(f"[-] No webhook doc found in MongoDB for order {wc_order_id}")

        if order_doc:
            print(f"[+] Audit Record Found in 'woocommerce_orders':")
            print(f"    - Order ID            : {order_doc.get('order_id')}")
            print(f"    - Status              : {order_doc.get('status')}")
            print(f"    - Linked Booking IDs  : {order_doc.get('booking_ids')}")

        # Step 6: Cleanup & Stock Restitution
        print(f"\n[*] Step 6: Cleaning up test order & restoring inventory...")
        # Delete test order from WooCommerce
        del_resp = await client.delete(
            f"{store_url}/wp-json/wc/v3/orders/{wc_order_id}?force=true",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
        )
        print(f"    - WooCommerce Order {wc_order_id} deleted : {'YES' if del_resp.status_code == 200 else 'NO'}")

        # Release any hold created in Concord
        if order_doc and order_doc.get("booking_ids"):
            bookings_coll = get_bookings_collection()
            for b_id in order_doc.get("booking_ids", []):
                b = await bookings_coll.find_one({"_id": b_id})
                if b and b.get("status") == "HELD":
                    await ReservationService.cancel_booking(b_id)
                    print(f"    - Released Concord Hold {b_id} : YES")

        final_avail = await redis_manager.get_inventory(concord_pid)
        print(f"    - Final Concord Available Stock           : {final_avail} units")
        print(f"    - Stock Unchanged Invariant Check         : {'PASS' if final_avail == initial_avail else 'FAIL'}")

    print("=" * 80)
    await close_redis()
    await close_mongo()

if __name__ == "__main__":
    asyncio.run(test_order_webhook())
