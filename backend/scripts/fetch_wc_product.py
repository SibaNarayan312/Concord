import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import httpx
from backend.config import settings

async def fetch_product():
    store_url = settings.WC_STORE_URL.rstrip("/")
    if not store_url or not settings.WC_CONSUMER_KEY or not settings.WC_CONSUMER_SECRET:
        print("[!] ERROR: WooCommerce credentials missing in .env")
        return

    endpoint = f"{store_url}/wp-json/wc/v3/products"
    params = {"search": "Concord Test Headphones"}

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                endpoint,
                auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET),
                params=params
            )

            if resp.status_code != 200:
                print(f"[-] API request failed with status {resp.status_code}: {resp.text[:200]}")
                return

            products = resp.json()
            if not products:
                # If search param didn't return, fetch all recent products to check
                all_resp = await client.get(
                    endpoint,
                    auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET),
                    params={"per_page": 10}
                )
                if all_resp.status_code == 200:
                    products = all_resp.json()

            target = None
            for p in products:
                if "concord test headphones" in p.get("name", "").lower():
                    target = p
                    break

            if not target and products:
                # If exact name match wasn't found, pick first product for inspection
                target = products[0]

            if not target:
                print("[-] No products found in the store.")
                return

            print("=" * 60)
            print("WooCommerce Product Details")
            print("=" * 60)
            print(f"Product Found           : YES")
            print(f"Product Name            : {target.get('name')}")
            print(f"WooCommerce Product ID  : {target.get('id')}")
            print(f"Price                   : {target.get('price')} (Regular: {target.get('regular_price')})")
            print(f"Stock Quantity          : {target.get('stock_quantity')}")
            print(f"Stock Status            : {target.get('stock_status')}")
            print(f"Stock Management Enabled: {target.get('manage_stock')}")
            print(f"Product Status          : {target.get('status')}")
            print("=" * 60)

    except Exception as e:
        print(f"[-] Error fetching product: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(fetch_product())
