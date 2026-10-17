import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
from backend.db.mongo import connect_mongo, close_mongo, get_products_collection, get_bookings_collection
from backend.db.redis_client import connect_redis, close_redis, redis_manager

async def inspect_products():
    await connect_mongo()
    await connect_redis()

    products_coll = get_products_collection()
    bookings_coll = get_bookings_collection()

    products = await products_coll.find({}).to_list(200)

    print("=" * 105)
    print(f"{'Concord Product ID':<38} | {'Product Name':<35} | {'Initial':<7} | {'Avail (Redis)':<13}")
    print("=" * 105)

    # Sort so known flagship products or non-test products are listed first
    def sort_key(p):
        name = p.get("name", "")
        if name.startswith("Test Product"):
            return (2, name)
        elif name.startswith("WooCommerce"):
            return (1, name)
        return (0, name)

    products.sort(key=sort_key)

    for p in products:
        pid = p["_id"]
        name = p.get("name", "Unnamed")[:35]
        initial = p.get("initial_stock", 0)
        avail = await redis_manager.get_inventory(pid)
        print(f"{pid:<38} | {name:<35} | {initial:<7} | {str(avail):<13}")

    print("=" * 105)
    print(f"Total Products in Catalog: {len(products)}")

    from backend.db.mongo import get_wc_mappings_collection
    mappings_coll = get_wc_mappings_collection()
    mappings = await mappings_coll.find({}).to_list(100)
    print("\n" + "=" * 105)
    print(f"EXISTING WOOCOMMERCE PRODUCT MAPPINGS (Total: {len(mappings)})")
    print("=" * 105)
    if not mappings:
        print("No product mappings currently exist in Concord.")
    for m in mappings:
        print(f"Concord Product ID: {m.get('concord_product_id')} -> WooCommerce ID: {m.get('wc_product_id')} | Sync Enabled: {m.get('sync_enabled')}")
    print("=" * 105)

    await close_redis()
    await close_mongo()

if __name__ == "__main__":
    asyncio.run(inspect_products())
