import asyncio
import json
from backend.db.mongo import connect_mongo, close_mongo, get_products_collection, get_bookings_collection
from backend.db.redis_client import connect_redis, close_redis, redis_manager

async def verify_all():
    await connect_mongo()
    await connect_redis()
    products_coll = get_products_collection()
    bookings_coll = get_bookings_collection()

    products = await products_coll.find({}).to_list(100)
    print(f"\n==========================================================================================")
    print(f"CONCORD INVENTORY INVARIANT VERIFICATION REPORT (MongoDB + Redis State)")
    print(f"==========================================================================================")
    print(f"Total Products in Catalog: {len(products)}\n")
    header = f"{'Product Name':<38} | {'Initial':<7} | {'Redis Avail':<11} | {'Holds':<6} | {'Confirmed':<9} | {'Total':<6} | {'Status':<8}"
    print(header)
    print("-" * len(header))

    all_passed = True
    for p in products:
        pid = p["_id"]
        name = p["name"][:36]
        initial = p["initial_stock"]
        avail = await redis_manager.get_inventory(pid)
        if avail is None:
            avail = initial

        pipeline = [
            {"$match": {"product_id": pid}},
            {"$group": {"_id": "$status", "qty": {"$sum": "$quantity"}}}
        ]
        status_counts = {}
        async for doc in bookings_coll.aggregate(pipeline):
            status_counts[doc["_id"]] = doc["qty"]

        holds = status_counts.get("HELD", 0)
        confirmed = status_counts.get("CONFIRMED", 0)
        total_accounted = avail + holds + confirmed
        passed = (total_accounted == initial) and (avail >= 0)
        if not passed:
            all_passed = False

        row = f"{name:<38} | {initial:<7} | {avail:<11} | {holds:<6} | {confirmed:<9} | {total_accounted:<6} | {('PASS' if passed else 'FAIL'):<8}"
        print(row)

    print("-" * len(header))
    print(f"Invariant Formula : Available (Redis) + Active Holds (Mongo) + Confirmed (Mongo) == Initial Stock (Mongo)")
    print(f"Invariant Result  : {'PASS - ZERO OVERSELLING DETECTED' if all_passed else 'FAIL - INVARIANT VIOLATION'}\n")

    await close_redis()
    await close_mongo()
    return all_passed

if __name__ == "__main__":
    asyncio.run(verify_all())
