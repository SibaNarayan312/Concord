from motor.motor_asyncio import AsyncIOMotorDatabase
import pymongo
import logging
from typing import Dict, Any, List

logger = logging.getLogger("concord.indexes")

INDEX_DEFINITIONS = {
    "products": [
        {
            "keys": [("name", pymongo.ASCENDING)],
            "unique": True,
            "name": "idx_products_name_unique",
            "justification": "Ensures unique product names and accelerates lookup by product name."
        }
    ],
    "bookings": [
        {
            "keys": [("product_id", pymongo.ASCENDING), ("status", pymongo.ASCENDING)],
            "unique": False,
            "name": "idx_bookings_product_status",
            "justification": "Supports high-frequency queries filtering bookings by product and lifecycle status (HELD, CONFIRMED)."
        },
        {
            "keys": [("status", pymongo.ASCENDING), ("expires_at", pymongo.ASCENDING)],
            "unique": False,
            "name": "idx_bookings_status_expires",
            "justification": "Optimizes background expiration worker queries searching for active HELD records past their expiration deadline."
        },
        {
            "keys": [("idempotency_key", pymongo.ASCENDING)],
            "unique": True,
            "sparse": True,
            "name": "idx_bookings_idempotency_key_unique",
            "justification": "Guarantees no duplicate bookings are created with the same idempotency key while allowing null values."
        },
        {
            "keys": [("created_at", pymongo.DESCENDING)],
            "unique": False,
            "name": "idx_bookings_created_desc",
            "justification": "Provides fast reverse-chronological sorting for booking listings and audit logs."
        }
    ],
    "idempotency_records": [
        {
            "keys": [("idempotency_key", pymongo.ASCENDING)],
            "unique": True,
            "name": "idx_idempotency_key_unique",
            "justification": "Enforces strict exactly-once semantics by guaranteeing idempotency key uniqueness across all requests."
        },
        {
            "keys": [("created_at", pymongo.DESCENDING)],
            "unique": False,
            "name": "idx_idempotency_created_desc",
            "justification": "Facilitates temporal queries and potential archival/cleanup of stale idempotency keys."
        }
    ],
    "reservation_operations": [
        {
            "keys": [("status", pymongo.ASCENDING), ("created_at", pymongo.ASCENDING)],
            "unique": False,
            "name": "idx_operations_status_created",
            "justification": "Enables the reconciliation worker to rapidly poll pending or failed operations in FIFO order."
        },
        {
            "keys": [("booking_id", pymongo.ASCENDING)],
            "unique": False,
            "name": "idx_operations_booking_id",
            "justification": "Allows immediate lookup of operation history and retry attempt counts for any booking."
        }
    ],
    "woocommerce_mappings": [
        {
            "keys": [("wc_product_id", pymongo.ASCENDING), ("wc_variation_id", pymongo.ASCENDING)],
            "unique": True,
            "name": "idx_wc_mappings_product_variation_unique",
            "justification": "Guarantees exactly one authoritative mapping between a WooCommerce product/variation and a Concord product."
        },
        {
            "keys": [("concord_product_id", pymongo.ASCENDING)],
            "unique": False,
            "name": "idx_wc_mappings_concord_product",
            "justification": "Enables instant reverse lookup of store mappings when Concord inventory changes."
        }
    ],
    "woocommerce_orders": [
        {
            "keys": [("order_id", pymongo.ASCENDING)],
            "unique": True,
            "name": "idx_wc_orders_order_id_unique",
            "justification": "Enforces single tracking record per WooCommerce order to prevent duplicate processing."
        },
        {
            "keys": [("booking_ids", pymongo.ASCENDING)],
            "unique": False,
            "name": "idx_wc_orders_booking_ids",
            "justification": "Accelerates lookup of WooCommerce orders associated with specific Concord booking IDs."
        },
        {
            "keys": [("status", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
            "unique": False,
            "name": "idx_wc_orders_status_created",
            "justification": "Facilitates filtering and reconciliation queries across order statuses."
        }
    ],
    "woocommerce_webhooks": [
        {
            "keys": [("delivery_id", pymongo.ASCENDING)],
            "unique": True,
            "sparse": True,
            "name": "idx_wc_webhooks_delivery_id_unique",
            "justification": "Deduplicates webhook deliveries from WooCommerce to prevent re-processing identical webhook events."
        },
        {
            "keys": [("created_at", pymongo.DESCENDING)],
            "unique": False,
            "name": "idx_wc_webhooks_created_desc",
            "justification": "Provides chronological ordering for the WooCommerce webhook audit log."
        }
    ]
}

async def ensure_indexes(db: AsyncIOMotorDatabase) -> Dict[str, List[Dict[str, Any]]]:
    """
    Creates and validates all required MongoDB indexes across collections.
    Safe to run repeatedly.
    """
    results = {}
    for collection_name, index_specs in INDEX_DEFINITIONS.items():
        collection = db[collection_name]
        results[collection_name] = []
        for spec in index_specs:
            keys = spec["keys"]
            options = {k: v for k, v in spec.items() if k not in ("keys", "justification")}
            try:
                created_name = await collection.create_index(keys, **options)
                results[collection_name].append({
                    "name": created_name,
                    "keys": keys,
                    "unique": spec.get("unique", False),
                    "justification": spec["justification"]
                })
                logger.info(f"Index [{created_name}] verified on collection [{collection_name}]")
            except Exception as e:
                logger.error(f"Failed to create index {spec.get('name')} on {collection_name}: {e}")
                raise e
    return results

async def inspect_indexes(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Inspects all indexes currently active in the database."""
    summary = {}
    for coll_name in INDEX_DEFINITIONS.keys():
        coll = db[coll_name]
        raw_indexes = await coll.index_information()
        summary[coll_name] = raw_indexes
    return summary
