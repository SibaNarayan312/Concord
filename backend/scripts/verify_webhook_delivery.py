import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.mongo import connect_mongo, close_mongo, get_wc_webhooks_collection, get_wc_orders_collection

async def check_delivery(limit=5):
    await connect_mongo()
    w_coll = get_wc_webhooks_collection()
    o_coll = get_wc_orders_collection()

    webhooks = await w_coll.find({}).sort("created_at", -1).limit(limit).to_list(limit)
    orders = await o_coll.find({}).sort("updated_at", -1).limit(limit).to_list(limit)

    print("=" * 80)
    print("CONCORD WEBHOOK AUDIT LOG & DELIVERY VERIFICATION")
    print("=" * 80)
    print(f"Recent Webhook Ingestion Records (Last {len(webhooks)}):")
    print("-" * 80)
    if not webhooks:
        print("No incoming webhooks recorded yet.")
    for w in webhooks:
        print(f"Log ID       : {w.get('_id')}")
        print(f"Delivery ID  : {w.get('delivery_id')}")
        print(f"Order ID     : {w.get('order_id')}")
        print(f"Topic        : {w.get('topic')}")
        print(f"Order Status : {w.get('order_status')}")
        print(f"Audit Status : {w.get('status')}")
        print(f"Timestamp    : {w.get('created_at')}")
        print("-" * 80)

    print("\nRecent Tracked WooCommerce Orders:")
    print("-" * 80)
    if not orders:
        print("No WooCommerce orders tracked yet.")
    for o in orders:
        print(f"Order ID     : {o.get('order_id')}")
        print(f"Status       : {o.get('status')}")
        print(f"Booking IDs  : {o.get('booking_ids')}")
        print(f"Items Count  : {len(o.get('items', []))}")
        print(f"Outcomes     : {o.get('outcomes')}")
        print(f"Last Updated : {o.get('updated_at')}")
        print("-" * 80)

    await close_mongo()

if __name__ == "__main__":
    asyncio.run(check_delivery())
