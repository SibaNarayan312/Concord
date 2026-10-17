import asyncio
import httpx
from backend.config import settings
from backend.db.mongo import connect_mongo, get_products_collection, get_wc_mappings_collection, get_bookings_collection
from backend.db.redis_client import connect_redis, redis_manager

async def inspect():
    await connect_mongo()
    await connect_redis()

    # 1. Concord stock
    p_coll = get_products_collection()
    p = await p_coll.find_one({"_id": "prod-rtx-4090"})
    avail = await redis_manager.get_inventory("prod-rtx-4090")
    b_coll = get_bookings_collection()
    holds = await b_coll.count_documents({"product_id": "prod-rtx-4090", "status": "HELD"})
    confirmed = await b_coll.count_documents({"product_id": "prod-rtx-4090", "status": "CONFIRMED"})

    print("--- Concord Inventory Baseline ---")
    print(f"Product ID         : prod-rtx-4090")
    print(f"Product Name       : {p.get('name') if p else 'N/A'}")
    print(f"Initial Stock      : {p.get('initial_stock') if p else 'N/A'}")
    print(f"Redis Available    : {avail}")
    print(f"Active Holds (HELD): {holds}")
    print(f"Confirmed Orders   : {confirmed}")

    # 2. Mapping
    m_coll = get_wc_mappings_collection()
    m = await m_coll.find_one({"concord_product_id": "prod-rtx-4090"})
    print("\n--- Mapping Configuration ---")
    print(f"Mapping ID         : {m.get('_id') if m else 'N/A'}")
    print(f"WooCommerce PID    : {m.get('wc_product_id') if m else 'N/A'}")
    print(f"sync_enabled       : {m.get('sync_enabled') if m else 'N/A'}")
    print(f"last_synced_stock  : {m.get('last_synced_stock') if m else 'N/A'}")
    print(f"last_synced_at     : {m.get('last_synced_at') if m else 'N/A'}")
    print(f"WC_MOCK_MODE       : {settings.WC_MOCK_MODE}")

    # 3. Live WC Product 13 read (read-only GET)
    store_url = settings.WC_STORE_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{store_url}/wp-json/wc/v3/products/13",
            auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
        )
        print("\n--- WooCommerce Store Baseline ---")
        if resp.status_code == 200:
            wc_data = resp.json()
            print(f"WC Product ID      : {wc_data.get('id')}")
            print(f"WC Product Name    : {wc_data.get('name')}")
            print(f"Manage Stock       : {wc_data.get('manage_stock')}")
            print(f"Stock Quantity     : {wc_data.get('stock_quantity')}")
            print(f"Stock Status       : {wc_data.get('stock_status')}")
            print(f"Price              : {wc_data.get('price')}")
        else:
            print(f"WooCommerce GET failed with HTTP {resp.status_code}: {resp.text}")

if __name__ == "__main__":
    asyncio.run(inspect())
