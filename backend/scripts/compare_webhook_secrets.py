import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from backend.config import settings

async def compare_secrets():
    print("=" * 70)
    print("WooCommerce vs Concord Webhook Secret Verification")
    print("=" * 70)

    store_url = settings.WC_STORE_URL.rstrip('/')
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{store_url}/wp-json/wc/v3/webhooks",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
        )
        if r.status_code != 200:
            print(f"[-] ERROR: Failed to retrieve webhooks from WooCommerce (HTTP {r.status_code})")
            return

        webhooks = r.json()
        print(f"Total Webhooks Found in WooCommerce: {len(webhooks)}")
        print("-" * 70)

        for w in webhooks:
            wid = w.get("id")
            wname = w.get("name")
            wtopic = w.get("topic")
            wstatus = w.get("status")
            wurl = w.get("delivery_url")
            wc_secret = w.get("secret", "")

            # Constant-time comparison
            matches = (wc_secret == settings.WC_WEBHOOK_SECRET)

            print(f"Webhook ID           : {wid}")
            print(f"Webhook Name         : {wname}")
            print(f"Topic                : {wtopic}")
            print(f"Status in Store      : {wstatus}")
            print(f"Delivery URL         : {wurl}")
            print(f"Secret Matches .env  : {'YES (Matched)' if matches else 'NO (MISMATCH)'}")
            if not matches:
                print("  -> ACTION NEEDED: Webhook has the old secret. Needs update.")
            print("-" * 70)

    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(compare_secrets())
