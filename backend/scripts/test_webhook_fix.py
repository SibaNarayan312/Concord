import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
import uuid

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from backend.config import settings

def make_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

async def test_webhook_suite():
    print("=" * 80)
    print("CONCORD WOOCOMMERCE WEBHOOK SUITE: VALIDATION & REGRESSION TESTING")
    print("=" * 80)
    url = "https://ripping-yelling-kinsman.ngrok-free.dev/woocommerce/webhooks"
    secret = settings.WC_WEBHOOK_SECRET

    async with httpx.AsyncClient(timeout=15.0) as client:
        # -------------------------------------------------------------
        # Scenario 1: Exact WooCommerce deliver_ping payload
        # -------------------------------------------------------------
        print("\n[*] Scenario 1: Simulating exact WooCommerce deliver_ping request...")
        ping_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "WooCommerce/11.2.1 Hookshot (WordPress/7.1.3)",
        }
        ping_body = "webhook_id=1"
        r_ping = await client.post(url, content=ping_body, headers=ping_headers)
        print(f"    - HTTP Status Code : {r_ping.status_code}")
        print(f"    - Response Payload : {r_ping.json()}")
        assert r_ping.status_code == 200, f"Expected 200, got {r_ping.status_code}"
        assert r_ping.json().get("status") == "SUCCESS"
        print("    [+] PASS: Reachability ping accepted with HTTP 200 (WooCommerce will mark webhook active)!")

        # -------------------------------------------------------------
        # Scenario 2: Real Order event WITHOUT signature -> Must 401
        # -------------------------------------------------------------
        print("\n[*] Scenario 2: Testing Order Event WITHOUT signature...")
        order_payload = {
            "id": 99999,
            "status": "pending",
            "line_items": [{"id": 1, "product_id": 13, "quantity": 1}]
        }
        order_body = json.dumps(order_payload).encode("utf-8")
        r_nosig = await client.post(
            url,
            content=order_body,
            headers={
                "Content-Type": "application/json",
                "x-wc-webhook-topic": "order.created",
                "x-wc-webhook-delivery-id": f"test-nosig-{uuid.uuid4().hex[:6]}"
            }
        )
        print(f"    - HTTP Status Code : {r_nosig.status_code}")
        assert r_nosig.status_code == 401, f"Expected 401, got {r_nosig.status_code}"
        print("    [+] PASS: Correctly blocked with 401 Unauthorized (Security intact)!")

        # -------------------------------------------------------------
        # Scenario 3: Real Order event with INVALID signature -> Must 401
        # -------------------------------------------------------------
        print("\n[*] Scenario 3: Testing Order Event with INVALID signature...")
        r_badsig = await client.post(
            url,
            content=order_body,
            headers={
                "Content-Type": "application/json",
                "x-wc-webhook-signature": "ZmFrZV9iYWRfc2lnbmF0dXJlX2Zvcl90ZXN0aW5n",
                "x-wc-webhook-topic": "order.created",
                "x-wc-webhook-delivery-id": f"test-badsig-{uuid.uuid4().hex[:6]}"
            }
        )
        print(f"    - HTTP Status Code : {r_badsig.status_code}")
        assert r_badsig.status_code == 401, f"Expected 401, got {r_badsig.status_code}"
        print("    [+] PASS: Correctly blocked with 401 Unauthorized (HMAC tamper protection intact)!")

        # -------------------------------------------------------------
        # Scenario 4: Real Order event with VALID signature -> Must 200
        # -------------------------------------------------------------
        print("\n[*] Scenario 4: Testing Order Event with VALID signature...")
        valid_sig = make_signature(order_body, secret)
        r_goods = await client.post(
            url,
            content=order_body,
            headers={
                "Content-Type": "application/json",
                "x-wc-webhook-signature": valid_sig,
                "x-wc-webhook-topic": "order.created",
                "x-wc-webhook-delivery-id": f"test-goodsig-{uuid.uuid4().hex[:6]}"
            }
        )
        print(f"    - HTTP Status Code : {r_goods.status_code}")
        print(f"    - Response Status  : {r_goods.json().get('status')}")
        assert r_goods.status_code == 200, f"Expected 200, got {r_goods.status_code}"
        print("    [+] PASS: Successfully authenticated and processed with 200 OK!")

    print("\n" + "=" * 80)
    print("ALL 4 SCENARIOS PASSED: REACHABILITY PINKS SUCCEED & HMAC AUTHENTICATION FULLY PRESERVED")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_webhook_suite())
