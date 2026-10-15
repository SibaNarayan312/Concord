import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple, Optional
import logging
from pymongo import ReturnDocument

from backend.db.mongo import (
    get_bookings_collection,
    get_products_collection,
    get_operations_collection
)
from backend.db.redis_client import redis_manager

logger = logging.getLogger("concord.recovery")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_iso() -> str:
    return utc_now().isoformat()

class RecoveryService:
    @staticmethod
    async def process_expired_holds() -> int:
        """
        Scans for expired HELD bookings in MongoDB, atomically updates their status to EXPIRED,
        and releases their inventory in Redis via atomic Lua script.
        Returns the count of successfully released holds.
        """
        bookings_coll = get_bookings_collection()
        now_str = utc_now_iso()

        # Find bookings where status == 'HELD' and expires_at <= now
        cursor = bookings_coll.find({
            "status": "HELD",
            "expires_at": {"$lte": now_str}
        })

        released_count = 0
        async for booking in cursor:
            booking_id = booking["_id"]
            product_id = booking["product_id"]

            # Guarded atomic transition: only update if status is STILL 'HELD'
            updated = await bookings_coll.find_one_and_update(
                {"_id": booking_id, "status": "HELD"},
                {"$set": {"status": "EXPIRED", "updated_at": now_str}},
                return_document=ReturnDocument.AFTER
            )

            if updated:
                # Inventory restoration in Redis via atomic Lua
                success, status_text, current_stock, stream_id = await redis_manager.execute_release(
                    product_id=product_id,
                    booking_id=booking_id,
                    reason="EXPIRED",
                    timestamp=now_str
                )
                released_count += 1
                logger.info(
                    f"Expired hold {booking_id} for product {product_id}. "
                    f"Restored inventory. Redis status: {status_text}, Available: {current_stock}"
                )

        return released_count

    @staticmethod
    async def reconcile_incomplete_operations() -> int:
        """
        Detects dangling or abandoned operations (e.g. backend crashed after Redis hold
        before MongoDB booking persisted) and cleans up Redis holds to prevent leaks.
        """
        ops_coll = get_operations_collection()
        bookings_coll = get_bookings_collection()
        cutoff = (utc_now() - timedelta(seconds=15)).isoformat()
        now_str = utc_now_iso()

        # Find operations stuck in REDIS_HELD older than 15 seconds
        cursor = ops_coll.find({
            "status": "REDIS_HELD",
            "created_at": {"$lte": cutoff}
        })

        reconciled = 0
        async for op in cursor:
            booking_id = op["booking_id"]
            product_id = op["product_id"]

            # Check if booking document actually exists in MongoDB
            booking = await bookings_coll.find_one({"_id": booking_id})
            if not booking:
                # Dangling Redis hold without durable booking! Release it
                logger.warning(f"Reconciling orphan hold for operation {op['_id']}, booking {booking_id}")
                await redis_manager.execute_release(
                    product_id=product_id,
                    booking_id=booking_id,
                    reason="EXPIRED",
                    timestamp=now_str
                )
                await ops_coll.update_one(
                    {"_id": op["_id"]},
                    {"$set": {"status": "RECONCILED_RELEASED", "updated_at": now_str}}
                )
                reconciled += 1
            else:
                # Booking exists, mark operation completed
                await ops_coll.update_one(
                    {"_id": op["_id"]},
                    {"$set": {"status": "COMPLETED", "updated_at": now_str}}
                )

        return reconciled

    @staticmethod
    async def verify_inventory_invariants(product_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Verifies that for each product:
        available_stock (Redis) + active_holds (Mongo) + confirmed_units (Mongo) == initial_stock (Mongo)
        """
        products_coll = get_products_collection()
        bookings_coll = get_bookings_collection()

        query = {"_id": product_id} if product_id else {}
        cursor = products_coll.find(query)
        reports = []

        async for prod in cursor:
            pid = prod["_id"]
            initial_stock = prod["initial_stock"]
            available_stock = await redis_manager.get_inventory(pid)
            if available_stock is None:
                available_stock = initial_stock

            pipeline = [
                {"$match": {"product_id": pid}},
                {"$group": {
                    "_id": "$status",
                    "total_quantity": {"$sum": "$quantity"}
                }}
            ]
            stats = {item["_id"]: item["total_quantity"] async for item in bookings_coll.aggregate(pipeline)}

            active_holds = stats.get("HELD", 0)
            confirmed_units = stats.get("CONFIRMED", 0)
            total_accounted = available_stock + active_holds + confirmed_units
            is_valid = (total_accounted == initial_stock) and (available_stock >= 0)

            reports.append({
                "product_id": pid,
                "product_name": prod["name"],
                "initial_stock": initial_stock,
                "available_stock": available_stock,
                "active_holds": active_holds,
                "confirmed_units": confirmed_units,
                "total_accounted": total_accounted,
                "invariant_satisfied": is_valid,
                "discrepancy": total_accounted - initial_stock
            })

        return reports
