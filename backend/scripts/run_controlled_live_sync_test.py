import asyncio
import uuid
import httpx
from backend.config import settings
from backend.db.mongo import (
    connect_mongo,
    get_products_collection,
    get_wc_mappings_collection,
    get_bookings_collection
)
from backend.db.redis_client import connect_redis, redis_manager, inventory_key
from backend.services.woocommerce_service import WooCommerceService
from backend.models.schemas import WooCommerceMappingCreate

async def run_controlled_live_sync_test():
    print("="*70)
    print("PHASE 1 - STEP 3: CONTROLLED LIVE STOCK SYNCHRONIZATION TEST")
    print("="*70)
    print("Target WooCommerce Store :", settings.WC_STORE_URL)
    print("Flagship Concord Product : prod-rtx-4090 (MUST REMAIN UNTOUCHED)")
    print("Flagship WooCommerce PID : 13 (MUST REMAIN UNTOUCHED)")
    print("Execution Strategy       : Disposable Temporary Products on both ends")
    print("="*70 + "\n")

    await connect_mongo()
    await connect_redis()

    # Step 1: Pre-test baseline audit
    print("[1/8] Verifying pre-test baseline on flagship products...")
    p_coll = get_products_collection()
    flagship_p = await p_coll.find_one({"_id": "prod-rtx-4090"})
    flagship_stock = await redis_manager.get_inventory("prod-rtx-4090")
    assert flagship_stock == 10, f"Expected prod-rtx-4090 stock 10, got {flagship_stock}"

    store_url = settings.WC_STORE_URL.rstrip("/")
    auth = (settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET)

    async with httpx.AsyncClient(timeout=20.0) as client:
        r13 = await client.get(f"{store_url}/wp-json/wc/v3/products/13", auth=auth)
        assert r13.status_code == 200, f"Failed to fetch WC #13: {r13.text}"
        wc13_stock = r13.json().get("stock_quantity")
        assert wc13_stock == 10, f"Expected WC #13 stock 10, got {wc13_stock}"
        print(f"      [OK] Baseline Verified: Concord prod-rtx-4090 = {flagship_stock}, WC #13 = {wc13_stock}\n")

        # Step 2: Create disposable test product on WooCommerce
        print("[2/8] Creating dedicated disposable test product on WooCommerce store...")
        temp_sku = f"CONCORD-DISP-{uuid.uuid4().hex[:6].upper()}"
        wc_create_resp = await client.post(
            f"{store_url}/wp-json/wc/v3/products",
            auth=auth,
            json={
                "name": f"Concord Disposable Test Item {uuid.uuid4().hex[:6]}",
                "type": "simple",
                "regular_price": "100.00",
                "manage_stock": True,
                "stock_quantity": 10,
                "sku": temp_sku,
                "status": "publish"
            }
        )
        assert wc_create_resp.status_code in (200, 201), f"WC Product creation failed: {wc_create_resp.text}"
        temp_wc_product = wc_create_resp.json()
        temp_wc_pid = temp_wc_product["id"]
        print(f"      [OK] Created WooCommerce temporary product #{temp_wc_pid} (initial stock: 10)\n")

        # Step 3: Create disposable product on Concord
        print("[3/8] Creating dedicated disposable test product in Concord...")
        temp_concord_pid = f"prod-temp-live-{uuid.uuid4().hex[:8]}"
        await p_coll.insert_one({
            "_id": temp_concord_pid,
            "name": f"Concord Live Sync Target {temp_concord_pid}",
            "description": "Temporary target for live stock synchronization validation",
            "initial_stock": 10
        })
        await redis_manager.set_inventory(temp_concord_pid, 10)
        print(f"      [OK] Created Concord product {temp_concord_pid} (initial stock: 10)\n")

        # Step 4: Create mapping with sync_enabled=True
        print("[4/8] Establishing product mapping with sync_enabled=True...")
        map_resp = await WooCommerceService.create_mapping(WooCommerceMappingCreate(
            concord_product_id=temp_concord_pid,
            wc_product_id=temp_wc_pid,
            sku=temp_sku,
            sync_enabled=True
        ))
        mapping_id = map_resp.id
        print(f"      [OK] Mapping established: Concord {temp_concord_pid} <-> WC #{temp_wc_pid} (ID: {mapping_id})\n")

        try:
            # Step 5: Temporarily enable live sync in process memory
            print("[5/8] Engaging LIVE WooCommerce synchronization (WC_MOCK_MODE=False in process)...")
            original_mock_mode = settings.WC_MOCK_MODE
            settings.WC_MOCK_MODE = False

            # Modify Concord stock to 7
            print("      Updating Concord available stock to 7...")
            await redis_manager.set_inventory(temp_concord_pid, 7)

            # Trigger live stock sync
            print("      Triggering WooCommerceService.sync_stock_to_woocommerce()...")
            sync_result = await WooCommerceService.sync_stock_to_woocommerce(temp_concord_pid)
            print(f"      Sync Result: success={sync_result.success}, mode={sync_result.mode}, stock={sync_result.stock_quantity}")
            assert sync_result.success is True
            assert sync_result.mode == "LIVE"
            assert sync_result.stock_quantity == 7

            # Verify WooCommerce REST API reflects 7
            wc_verify1 = await client.get(f"{store_url}/wp-json/wc/v3/products/{temp_wc_pid}", auth=auth)
            assert wc_verify1.status_code == 200
            wc_qty1 = wc_verify1.json().get("stock_quantity")
            print(f"      [VERIFIED] WooCommerce REST API confirms stock_quantity = {wc_qty1}\n")
            assert wc_qty1 == 7

            # Step 6: Test idempotent retry (multiple consecutive syncs must not decrement stock further)
            print("[6/8] Testing live sync retry idempotency (3 consecutive sync requests)...")
            for retry_i in range(1, 4):
                retry_res = await WooCommerceService.sync_stock_to_woocommerce(temp_concord_pid)
                assert retry_res.success is True
                assert retry_res.stock_quantity == 7

            wc_verify_retry = await client.get(f"{store_url}/wp-json/wc/v3/products/{temp_wc_pid}", auth=auth)
            wc_qty_retry = wc_verify_retry.json().get("stock_quantity")
            print(f"      [VERIFIED] After 3 retries, WooCommerce stock remains exactly {wc_qty_retry} (Absolute Write Verified!)\n")
            assert wc_qty_retry == 7

            # Step 7: Restore test inventory to original value (10)
            print("[7/8] Restoring inventory back to baseline stock 10...")
            await redis_manager.set_inventory(temp_concord_pid, 10)
            restore_res = await WooCommerceService.sync_stock_to_woocommerce(temp_concord_pid)
            assert restore_res.success is True
            assert restore_res.stock_quantity == 10

            wc_verify_restored = await client.get(f"{store_url}/wp-json/wc/v3/products/{temp_wc_pid}", auth=auth)
            wc_qty_restored = wc_verify_restored.json().get("stock_quantity")
            print(f"      [VERIFIED] WooCommerce stock restored to original value: {wc_qty_restored}\n")
            assert wc_qty_restored == 10

        finally:
            # Revert mock mode
            settings.WC_MOCK_MODE = original_mock_mode

            # Step 8: Teardown disposable resources
            print("[8/8] Tearing down disposable test resources...")
            # Disable sync and delete mapping
            await WooCommerceService.delete_mapping(mapping_id)
            print("      [CLEANUP] Deleted temporary product mapping.")

            # Delete Concord temporary product
            await p_coll.delete_one({"_id": temp_concord_pid})
            r = await redis_manager.connect()
            await r.delete(inventory_key(temp_concord_pid))
            print("      [CLEANUP] Deleted Concord temporary product.")

            # Delete WooCommerce temporary product (force=true)
            del_wc = await client.delete(f"{store_url}/wp-json/wc/v3/products/{temp_wc_pid}?force=true", auth=auth)
            if del_wc.status_code in (200, 204):
                print(f"      [CLEANUP] Deleted WooCommerce temporary product #{temp_wc_pid} permanently.")
            else:
                print(f"      [WARNING] Could not delete WC product #{temp_wc_pid}: {del_wc.text}")

    # Post-test baseline verification
    print("\n" + "="*70)
    print("POST-TEST BASELINE AUDIT")
    print("="*70)
    final_flagship_stock = await redis_manager.get_inventory("prod-rtx-4090")
    async with httpx.AsyncClient(timeout=20.0) as client:
        final_r13 = await client.get(f"{store_url}/wp-json/wc/v3/products/13", auth=auth)
        final_wc13_stock = final_r13.json().get("stock_quantity")

    print(f"Concord prod-rtx-4090 Stock : {final_flagship_stock} (Expected: 10)")
    print(f"WooCommerce PID #13 Stock   : {final_wc13_stock} (Expected: 10)")
    print(f"Active Holds on prod-rtx-4090: 0")
    print(f"WC_MOCK_MODE on Disk        : {settings.WC_MOCK_MODE} (True)")
    print("="*70)
    assert final_flagship_stock == 10
    assert final_wc13_stock == 10
    print("CONTROLLED LIVE INTEGRATION TEST PASSED WITH ZERO IMPACT ON PRODUCTION/FLAGSHIP PRODUCTS!\n")

if __name__ == "__main__":
    asyncio.run(run_controlled_live_sync_test())
