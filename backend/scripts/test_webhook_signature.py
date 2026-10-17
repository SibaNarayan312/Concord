import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from backend.config import settings

def make_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

async def test_webhook_pipeline():
    print("=" * 70)
    print("Concord Webhook Signature & Pipeline Self-Test")
    print("=" * 70)

    url = "https://ripping-yelling-kinsman.ngrok-free.dev/woocommerce/webhooks"
    secret = settings.WC_WEBHOOK_SECRET
    print(f"Target Public Webhook Endpoint : {url}")
    print(f"Secret Present in Settings     : {'YES' if secret else 'NO'}")
    print(f"Signature Verification Enabled : {settings.WC_VERIFY_WEBHOOK_SIGNATURE}")
    print("-" * 70)

    payload = {"webhook_id": 9999}
    body = json.dumps(payload).encode("utf-8")

    # Test 1: Invalid Signature
    bad_sig = "d3Jvbmdfc2lnbmF0dXJlX2Zvcl90ZXN0aW5n"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r_bad = await client.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-wc-webhook-signature": bad_sig,
                "x-wc-webhook-topic": "action.woocommerce_webhook_ping",
                "x-wc-webhook-delivery-id": "test-del-bad-sig"
            }
        )
        print(f"Test 1 (Invalid Signature) -> HTTP Status: {r_bad.status_code}")
        assert r_bad.status_code == 401, f"Expected 401, got {r_bad.status_code}"
        print("    [+] Correctly rejected with 401 Unauthorized!")

    # Test 2: Valid Signature with Rotated Secret
    good_sig = make_signature(body, secret)
    async with httpx.AsyncClient(timeout=10.0) as client:
        r_good = await client.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-wc-webhook-signature": good_sig,
                "x-wc-webhook-topic": "action.woocommerce_webhook_ping",
                "x-wc-webhook-delivery-id": "test-del-good-sig"
            }
        )
        print(f"Test 2 (Valid Signature)   -> HTTP Status: {r_good.status_code}")
        print(f"    Response Body: {r_good.json()}")
        assert r_good.status_code == 200, f"Expected 200, got {r_good.status_code}"
        print("    [+] Correctly accepted and processed with 200 OK!")

    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_webhook_pipeline())
