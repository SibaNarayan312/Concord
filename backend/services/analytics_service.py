from typing import List, Dict, Any
from backend.db.mongo import (
    get_products_collection,
    get_bookings_collection,
    get_operations_collection
)
from backend.db.redis_client import redis_manager
from backend.models.schemas import (
    AnalyticsOverview,
    BookingsByStatusItem,
    ProductPerformanceItem
)
import logging

logger = logging.getLogger("concord.analytics")

class AnalyticsService:
    @staticmethod
    async def get_bookings_by_status() -> List[BookingsByStatusItem]:
        """Aggregation Pipeline 1: Group booking counts and total quantities by lifecycle status."""
        coll = get_bookings_collection()
        pipeline = [
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "total_quantity": {"$sum": "$quantity"}
                }
            },
            {"$sort": {"count": -1}}
        ]
        results = []
        async for doc in coll.aggregate(pipeline):
            results.append(BookingsByStatusItem(
                status=doc["_id"],
                count=doc["count"],
                total_quantity=doc["total_quantity"]
            ))
        return results

    @staticmethod
    async def get_product_performance() -> List[ProductPerformanceItem]:
        """
        Aggregation Pipeline 2: $lookup from products to bookings, calculating
        active holds, confirmed sales, expired counts, and units sold per product.
        """
        products_coll = get_products_collection()
        pipeline = [
            {
                "$lookup": {
                    "from": "bookings",
                    "localField": "_id",
                    "foreignField": "product_id",
                    "as": "bookings_data"
                }
            },
            {
                "$project": {
                    "product_id": "$_id",
                    "product_name": "$name",
                    "initial_stock": "$initial_stock",
                    "total_bookings": {"$size": "$bookings_data"},
                    "active_holds": {
                        "$sum": {
                            "$map": {
                                "input": {
                                    "$filter": {
                                        "input": "$bookings_data",
                                        "as": "b",
                                        "cond": {"$eq": ["$$b.status", "HELD"]}
                                    }
                                },
                                "as": "hb",
                                "in": "$$hb.quantity"
                            }
                        }
                    },
                    "confirmed_count": {
                        "$size": {
                            "$filter": {
                                "input": "$bookings_data",
                                "as": "b",
                                "cond": {"$eq": ["$$b.status", "CONFIRMED"]}
                            }
                        }
                    },
                    "expired_count": {
                        "$size": {
                            "$filter": {
                                "input": "$bookings_data",
                                "as": "b",
                                "cond": {"$eq": ["$$b.status", "EXPIRED"]}
                            }
                        }
                    },
                    "cancelled_count": {
                        "$size": {
                            "$filter": {
                                "input": "$bookings_data",
                                "as": "b",
                                "cond": {"$eq": ["$$b.status", "CANCELLED"]}
                            }
                        }
                    },
                    "total_units_sold": {
                        "$sum": {
                            "$map": {
                                "input": {
                                    "$filter": {
                                        "input": "$bookings_data",
                                        "as": "b",
                                        "cond": {"$eq": ["$$b.status", "CONFIRMED"]}
                                    }
                                },
                                "as": "cb",
                                "in": "$$cb.quantity"
                            }
                        }
                    }
                }
            },
            {"$sort": {"total_bookings": -1}}
        ]

        items = []
        async for doc in products_coll.aggregate(pipeline):
            pid = doc["product_id"]
            avail = await redis_manager.get_inventory(pid)
            if avail is None:
                avail = doc["initial_stock"]

            items.append(ProductPerformanceItem(
                product_id=pid,
                product_name=doc["product_name"],
                initial_stock=doc["initial_stock"],
                available_stock=avail,
                active_holds=doc["active_holds"],
                confirmed_count=doc["confirmed_count"],
                expired_count=doc["expired_count"],
                cancelled_count=doc["cancelled_count"],
                total_units_sold=doc["total_units_sold"]
            ))
        return items

    @staticmethod
    async def get_overview() -> AnalyticsOverview:
        """
        Combines total inventory aggregation, status breakdown, and invariant verification.
        """
        products_coll = get_products_collection()
        bookings_coll = get_bookings_collection()

        # Pipeline 3: Total products and inventory
        p3 = [
            {
                "$group": {
                    "_id": None,
                    "total_initial_stock": {"$sum": "$initial_stock"},
                    "total_products": {"$sum": 1}
                }
            }
        ]
        stock_cursor = products_coll.aggregate(p3)
        stock_doc = await stock_cursor.to_list(1)
        total_products = stock_doc[0]["total_products"] if stock_doc else 0
        total_initial_stock = stock_doc[0]["total_initial_stock"] if stock_doc else 0

        # Booking stats
        p1 = [
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "total_units": {"$sum": "$quantity"}
                }
            }
        ]
        status_map = {}
        units_map = {}
        total_bookings = 0
        async for b in bookings_coll.aggregate(p1):
            st = b["_id"]
            cnt = b["count"]
            status_map[st] = cnt
            units_map[st] = b["total_units"]
            total_bookings += cnt

        # Check invariant across all products
        all_prods = await products_coll.find({}).to_list(100)
        invariant_ok = True
        for p in all_prods:
            pid = p["_id"]
            init_s = p["initial_stock"]
            avail_s = await redis_manager.get_inventory(pid)
            if avail_s is None:
                avail_s = init_s
            holds = await bookings_coll.count_documents({"product_id": pid, "status": "HELD"})
            if avail_s + holds > init_s or avail_s < 0:
                invariant_ok = False
                break

        return AnalyticsOverview(
            total_products=total_products,
            total_initial_inventory=total_initial_stock,
            total_bookings=total_bookings,
            active_holds=status_map.get("HELD", 0),
            confirmed_bookings=status_map.get("CONFIRMED", 0),
            expired_bookings=status_map.get("EXPIRED", 0),
            cancelled_bookings=status_map.get("CANCELLED", 0),
            total_units_confirmed=units_map.get("CONFIRMED", 0),
            inventory_invariant_satisfied=invariant_ok
        )

    @staticmethod
    async def get_confirmed_vs_expired() -> Dict[str, Any]:
        """Aggregation Pipeline 4: Comparative conversion ratio of confirmed vs expired bookings."""
        coll = get_bookings_collection()
        pipeline = [
            {
                "$match": {
                    "status": {"$in": ["CONFIRMED", "EXPIRED"]}
                }
            },
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "total_units": {"$sum": "$quantity"}
                }
            }
        ]
        results = {}
        async for doc in coll.aggregate(pipeline):
            results[doc["_id"]] = {
                "count": doc["count"],
                "total_units": doc["total_units"]
            }
        return results

    @staticmethod
    async def get_operation_stats() -> Dict[str, int]:
        """Aggregation Pipeline 5: Cross-database reservation operations audit statistics."""
        coll = get_operations_collection()
        pipeline = [
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ]
        stats = {}
        async for doc in coll.aggregate(pipeline):
            stats[doc["_id"]] = doc["count"]
        return stats
