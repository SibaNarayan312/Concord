import asyncio
from backend.db.mongo import connect_mongo, close_mongo, get_bookings_collection, get_products_collection, get_idempotency_collection

async def inspect():
    await connect_mongo()
    bookings = get_bookings_collection()
    products = get_products_collection()
    idemp = get_idempotency_collection()

    confirmed = await bookings.find({"status": "CONFIRMED"}).sort("created_at", 1).to_list(100)
    print(f"Total Confirmed Bookings in MongoDB: {len(confirmed)}\n")
    for b in confirmed:
        p = await products.find_one({"_id": b["product_id"]})
        pname = p["name"] if p else "[Product Deleted]"
        print(f"Booking ID: {b['_id']}")
        print(f"  Product       : {pname} (ID: {b['product_id']})")
        print(f"  Quantity      : {b['quantity']}")
        print(f"  Idemp Key     : {b.get('idempotency_key')}")
        print(f"  Created At    : {b.get('created_at')}")
        print(f"  Confirmed At  : {b.get('confirmed_at')}\n")

    # Also count bookings grouped by test prefix
    all_bookings = await bookings.find({}).to_list(500)
    print(f"Total All Bookings in MongoDB: {len(all_bookings)}")
    status_counts = {}
    test_origins = {"seed": 0, "load_test": 0, "pytest": 0, "live_idemp": 0, "other": 0}
    for b in all_bookings:
        st = b.get("status")
        status_counts[st] = status_counts.get(st, 0) + 1
        key = b.get("idempotency_key") or ""
        if "seed" in key:
            test_origins["seed"] += 1
        elif "loadtest" in key:
            test_origins["load_test"] += 1
        elif "contention-test" in key or "test-" in key or "key-" in key:
            test_origins["pytest"] += 1
        elif "idemp-key" in key:
            test_origins["live_idemp"] += 1
        else:
            test_origins["other"] += 1

    print(f"Bookings by status: {status_counts}")
    print(f"Bookings by origin: {test_origins}")

    total_prods = await products.count_documents({})
    total_idemp = await idemp.count_documents({})
    print(f"Total Products in Catalog: {total_prods}")
    print(f"Total Idempotency Records: {total_idemp}")

    await close_mongo()

if __name__ == "__main__":
    asyncio.run(inspect())
