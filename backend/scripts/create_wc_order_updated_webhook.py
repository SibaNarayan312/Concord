import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from backend.config import settings

async def setup_order_updated_webhook():
    print("=" * 75)
    print("WOOCOMMERCE 'ORDER UPDATED' WEBHOOK SETUP")
    print("=" * 75)

    store_url = settings.WC_STORE_URL.rstrip('/')
    delivery_url = "https://ripping-yelling-kinsman.ngrok-free.dev/woocommerce/webhooks"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        # Check existing webhooks to prevent duplicate
        r_list = await client.get(
            f"{store_url}/wp-json/wc/v3/webhooks",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
        )
        if r_list.status_code != 200:
            print(f"[-] ERROR: Failed to list existing webhooks ({r_list.status_code})")
            return None

        webhooks = r_list.json()
        for w in webhooks:
            if w.get("topic") == "order.updated" or w.get("name") == "Concord Order Updated":
                print(f"[!] Webhook already exists: ID #{w.get('id')} - {w.get('name')} (Status: {w.get('status')})")
                return w

        # Create the webhook
        print("[*] Creating 'Concord Order Updated' webhook via REST API...")
        payload = {
            "name": "Concord Order Updated",
            "topic": "order.updated",
            "delivery_url": delivery_url,
            "secret": settings.WC_WEBHOOK_SECRET,
            "status": "active"
        }

        r_create = await client.post(
            f"{store_url}/wp-json/wc/v3/webhooks",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET),
            json=payload
        )

        if r_create.status_code in (200, 201):
            created = r_create.json()
            print(f"[+] SUCCESS: Webhook created successfully in WooCommerce!")
            print(f"    - Webhook ID   : {created.get('id')}")
            print(f"    - Name         : {created.get('name')}")
            print(f"    - Topic        : {created.get('topic')}")
            print(f"    - Status       : {created.get('status')}")
            print(f"    - Delivery URL : {created.get('delivery_url')}")
            print(f"    - Secret Match : Configured directly with current WC_WEBHOOK_SECRET")
            print("=" * 75)
            return created
        else:
            print(f"[-] ERROR creating webhook: {r_create.status_code} - {r_create.text[:200]}")
            print("=" * 75)
            return None

if __name__ == "__main__":
    asyncio.run(setup_order_updated_webhook())
