import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from backend.config import settings

async def check_wc_webhooks():
    store_url = settings.WC_STORE_URL.rstrip('/')
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        r = await client.get(
            f"{store_url}/wp-json/wc/v3/webhooks",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
        )
        print("=" * 70)
        print("WooCommerce Store Webhooks")
        print("=" * 70)
        print(f"HTTP Status: {r.status_code}")
        if r.status_code == 200:
            webhooks = r.json()
            print(f"Total Webhooks Configured on WooCommerce: {len(webhooks)}")
            for w in webhooks:
                print(f"ID: {w.get('id')} | Name: {w.get('name')} | Topic: {w.get('topic')} | Status: {w.get('status')}")
                print(f"Delivery URL: {w.get('delivery_url')}")
                print("-" * 70)
        else:
            print(f"Error: {r.text[:300]}")
        print("=" * 70)

if __name__ == "__main__":
    asyncio.run(check_wc_webhooks())
