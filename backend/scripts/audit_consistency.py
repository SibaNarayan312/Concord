import asyncio
import urllib.request
import json
from backend.db.mongo import connect_mongo, close_mongo, get_products_collection, get_bookings_collection, get_idempotency_collection, get_operations_collection

async def audit_consistency():
    print("\n==========================================================================================")
    print("CONCORD CONSISTENCY AUDIT: DASHBOARD METRICS VS RAW MONGODB COLLECTIONS")
    print("==========================================================================================")

    # 1. Fetch dashboard overview metrics via API
    req = urllib.request.Request("http://localhost:8000/analytics/overview")
    with urllib.request.urlopen(req) as resp:
        dashboard_metrics = json.loads(resp.read().decode())

    # 2. Query MongoDB collections directly
    await connect_mongo()
    products_coll = get_products_collection()
    bookings_coll = get_bookings_collection()
    idemp_coll = get_idempotency_collection()
    ops_coll = get_operations_collection()

    raw_total_products = await products_coll.count_documents({})
    raw_held_count = await bookings_coll.count_documents({"status": "HELD"})
    raw_confirmed_count = await bookings_coll.count_documents({"status": "CONFIRMED"})
    raw_expired_count = await bookings_coll.count_documents({"status": "EXPIRED"})
    raw_cancelled_count = await bookings_coll.count_documents({"status": "CANCELLED"})
    raw_total_bookings = await bookings_coll.count_documents({})
    raw_idemp_count = await idemp_coll.count_documents({})
    raw_ops_count = await ops_coll.count_documents({})

    pipeline_stock = [{"$group": {"_id": None, "total": {"$sum": "$initial_stock"}}}]
    res = await products_coll.aggregate(pipeline_stock).to_list(1)
    raw_total_initial_stock = res[0]["total"] if res else 0

    print(f"METRIC COMPARISON AUDIT:")
    print(f"{'Metric Name':<30} | {'Dashboard API Value':<20} | {'Raw MongoDB Count':<18} | {'Match?':<8}")
    print("-" * 84)

    checks = [
        ("total_products", dashboard_metrics["total_products"], raw_total_products),
        ("total_initial_inventory", dashboard_metrics["total_initial_inventory"], raw_total_initial_stock),
        ("total_bookings", dashboard_metrics["total_bookings"], raw_total_bookings),
        ("active_holds (HELD)", dashboard_metrics["active_holds"], raw_held_count),
        ("confirmed_bookings", dashboard_metrics["confirmed_bookings"], raw_confirmed_count),
        ("expired_bookings", dashboard_metrics["expired_bookings"], raw_expired_count),
        ("cancelled_bookings", dashboard_metrics["cancelled_bookings"], raw_cancelled_count)
    ]

    all_matched = True
    for name, api_val, db_val in checks:
        match = (api_val == db_val)
        if not match:
            all_matched = False
        print(f"{name:<30} | {str(api_val):<20} | {str(db_val):<18} | {('PASS' if match else 'FAIL'):<8}")

    print("-" * 84)
    print(f"Total Idempotency Tokens Logged : {raw_idemp_count}")
    print(f"Total Operations Audited        : {raw_ops_count}")
    print(f"Overall Consistency Status      : {'PASS - 100% RECONCILED' if all_matched else 'FAIL - DISCREPANCY DETECTED'}\n")
    print("==========================================================================================\n")

    await close_mongo()
    return all_matched

if __name__ == "__main__":
    asyncio.run(audit_consistency())
