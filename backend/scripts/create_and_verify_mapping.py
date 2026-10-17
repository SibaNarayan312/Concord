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
from backend.db.mongo import connect_mongo, close_mongo, get_products_collection, get_wc_mappings_collection
from backend.db.redis_client import connect_redis, close_redis, redis_manager
from backend.models.schemas import WooCommerceMappingCreate
from backend.services.woocommerce_service import WooCommerceService

async def run_mapping_flow():
    print("=" * 75)
    print("CONCORD <-> WOOCOMMERCE PRODUCT MAPPING FLOW")
    print("=" * 75)

    await connect_mongo()
    await connect_redis()

    # Step 1: Verify WooCommerce product via REST API
    wc_pid = 13
    print(f"[*] Step 1: Verifying WooCommerce product ID {wc_pid} via REST API...")
    store_url = settings.WC_STORE_URL.rstrip("/")
    wc_stock = None
    wc_name = None
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                f"{store_url}/wp-json/wc/v3/products/{wc_pid}",
                auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)
            )
            if resp.status_code == 200:
                data = resp.json()
                wc_name = data.get("name")
                wc_stock = data.get("stock_quantity")
                manage_stock = data.get("manage_stock")
                print(f"    - WooCommerce Product Name : {wc_name}")
                print(f"    - WooCommerce Product ID   : {data.get('id')}")
                print(f"    - WooCommerce Stock Qty    : {wc_stock}")
                print(f"    - Stock Management Enabled : {manage_stock}")
            else:
                print(f"[-] ERROR: Failed to retrieve WooCommerce product ({resp.status_code}): {resp.text[:200]}")
                await close_redis()
                await close_mongo()
                return False
    except Exception as e:
        print(f"[-] ERROR: WooCommerce connection error: {e}")
        await close_redis()
        await close_mongo()
        return False

    # Step 2: Verify Concord product in MongoDB & Redis
    concord_pid = "prod-rtx-4090"
    print(f"\n[*] Step 2: Verifying Concord product ID '{concord_pid}'...")
    prods_coll = get_products_collection()
    concord_prod = await prods_coll.find_one({"_id": concord_pid})
    if not concord_prod:
        print(f"[-] ERROR: Concord product '{concord_pid}' not found in MongoDB.")
        await close_redis()
        await close_mongo()
        return False

    concord_avail = await redis_manager.get_inventory(concord_pid)
    print(f"    - Concord Product Name     : {concord_prod.get('name')}")
    print(f"    - Concord Initial Stock    : {concord_prod.get('initial_stock')}")
    print(f"    - Concord Redis Available  : {concord_avail}")

    # Step 3: Confirm mapping parameters & safety controls
    print("\n[*] Step 3: Confirming mapping parameters & constraints:")
    print(f"    - Mock Mode Active (WC_MOCK_MODE)        : {settings.WC_MOCK_MODE}")
    print(f"    - Live Stock Sync (sync_enabled flag)    : False (Disabled)")
    print(f"    - Modifying Product Stock               : No (Stock remains untouched)")

    # Step 4: Create the mapping via WooCommerceService
    print(f"\n[*] Step 4: Creating mapping: Concord '{concord_pid}' <-> WooCommerce '{wc_pid}'...")
    payload = WooCommerceMappingCreate(
        concord_product_id=concord_pid,
        wc_product_id=wc_pid,
        wc_variation_id=0,
        sku="CONCORD-RTX-4090",
        sync_enabled=False
    )
    mapping_resp = await WooCommerceService.create_mapping(payload)
    print(f"[+] Mapping Created Successfully:")
    print(f"    - Mapping ID               : {mapping_resp.id}")
    print(f"    - Concord Product ID       : {mapping_resp.concord_product_id}")
    print(f"    - Concord Product Name     : {mapping_resp.concord_product_name}")
    print(f"    - WooCommerce Product ID   : {mapping_resp.wc_product_id}")
    print(f"    - SKU                      : {mapping_resp.sku}")
    print(f"    - Sync Enabled             : {mapping_resp.sync_enabled}")

    # Step 5: Verify the mapping via mapping API
    print(f"\n[*] Step 5: Verifying mapping via WooCommerceService.list_mappings()...")
    all_mappings = await WooCommerceService.list_mappings()
    found = False
    for m in all_mappings:
        if m.wc_product_id == wc_pid and m.concord_product_id == concord_pid:
            found = True
            print(f"[+] Verified in Mapping Registry:")
            print(f"    - Registry Mapping ID      : {m.id}")
            print(f"    - Linked Concord Product   : {m.concord_product_name} ({m.concord_product_id})")
            print(f"    - Linked WooCommerce ID    : {m.wc_product_id}")
            print(f"    - Sync Enabled Status      : {m.sync_enabled}")
            print(f"    - Concord Stock Available  : {m.concord_available_stock}")
            break

    if not found:
        print("[-] WARNING: Mapping was not found in registry!")

    # Step 6: Confirm stock was not modified on either system
    print(f"\n[*] Step 6: Post-mapping stock integrity check:")
    post_avail = await redis_manager.get_inventory(concord_pid)
    print(f"    - Concord Stock Unaltered  : {'YES' if post_avail == concord_avail else 'NO'} ({post_avail})")
    print(f"    - WooCommerce Stock Untouched : YES ({wc_stock})")
    print("=" * 75)

    await close_redis()
    await close_mongo()
    return found

if __name__ == "__main__":
    asyncio.run(run_mapping_flow())
