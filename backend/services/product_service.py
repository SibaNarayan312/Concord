from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from bson import ObjectId
import uuid
import logging
from fastapi import HTTPException

from backend.db.mongo import get_products_collection, get_bookings_collection
from backend.db.redis_client import redis_manager
from backend.models.schemas import ProductCreate, ProductUpdate, ProductResponse

logger = logging.getLogger("concord.product_service")

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class ProductService:
    @staticmethod
    async def create_product(payload: ProductCreate) -> ProductResponse:
        coll = get_products_collection()
        # Check name uniqueness
        existing = await coll.find_one({"name": payload.name})
        if existing:
            raise HTTPException(status_code=400, detail=f"Product with name '{payload.name}' already exists.")

        product_id = str(uuid.uuid4())
        now = utc_now_iso()
        doc = {
            "_id": product_id,
            "name": payload.name,
            "description": payload.description,
            "initial_stock": payload.initial_stock,
            "created_at": now,
            "updated_at": now
        }
        await coll.insert_one(doc)

        # Initialize Redis inventory atomically
        await redis_manager.set_inventory(product_id, payload.initial_stock)
        logger.info(f"Created product '{payload.name}' ({product_id}) with stock {payload.initial_stock}")

        return ProductResponse(
            id=product_id,
            name=payload.name,
            description=payload.description,
            initial_stock=payload.initial_stock,
            available_stock=payload.initial_stock,
            active_holds=0,
            confirmed_units=0,
            created_at=now,
            updated_at=now
        )

    @staticmethod
    async def get_product(product_id: str) -> ProductResponse:
        coll = get_products_collection()
        doc = await coll.find_one({"_id": product_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Product not found")

        # Query Redis for live available stock
        available_stock = await redis_manager.get_inventory(product_id)
        if available_stock is None:
            # Sync with initial_stock if key was evicted or not set
            available_stock = doc["initial_stock"]
            await redis_manager.set_inventory(product_id, available_stock)

        # Compute active holds and confirmed bookings from MongoDB
        bookings_coll = get_bookings_collection()
        pipeline = [
            {"$match": {"product_id": product_id}},
            {"$group": {
                "_id": "$status",
                "total_quantity": {"$sum": "$quantity"}
            }}
        ]
        stats = {item["_id"]: item["total_quantity"] async for item in bookings_coll.aggregate(pipeline)}

        return ProductResponse(
            id=doc["_id"],
            name=doc["name"],
            description=doc.get("description"),
            initial_stock=doc["initial_stock"],
            available_stock=available_stock,
            active_holds=stats.get("HELD", 0),
            confirmed_units=stats.get("CONFIRMED", 0),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"]
        )

    @staticmethod
    async def list_products() -> List[ProductResponse]:
        coll = get_products_collection()
        bookings_coll = get_bookings_collection()

        products = []
        cursor = coll.find({}).sort("created_at", -1)
        async for doc in cursor:
            pid = doc["_id"]
            avail = await redis_manager.get_inventory(pid)
            if avail is None:
                avail = doc["initial_stock"]
                await redis_manager.set_inventory(pid, avail)

            # Aggregate stats for this product
            pipeline = [
                {"$match": {"product_id": pid}},
                {"$group": {
                    "_id": "$status",
                    "total_quantity": {"$sum": "$quantity"}
                }}
            ]
            stats = {item["_id"]: item["total_quantity"] async for item in bookings_coll.aggregate(pipeline)}

            products.append(ProductResponse(
                id=pid,
                name=doc["name"],
                description=doc.get("description"),
                initial_stock=doc["initial_stock"],
                available_stock=avail,
                active_holds=stats.get("HELD", 0),
                confirmed_units=stats.get("CONFIRMED", 0),
                created_at=doc["created_at"],
                updated_at=doc["updated_at"]
            ))
        return products

    @staticmethod
    async def update_product(product_id: str, payload: ProductUpdate) -> ProductResponse:
        coll = get_products_collection()
        doc = await coll.find_one({"_id": product_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Product not found")

        updates: Dict[str, Any] = {"updated_at": utc_now_iso()}
        if payload.name is not None:
            # Check unique name conflict
            conflict = await coll.find_one({"name": payload.name, "_id": {"$ne": product_id}})
            if conflict:
                raise HTTPException(status_code=400, detail=f"Product with name '{payload.name}' already exists.")
            updates["name"] = payload.name
        if payload.description is not None:
            updates["description"] = payload.description

        await coll.update_one({"_id": product_id}, {"$set": updates})
        return await ProductService.get_product(product_id)

    @staticmethod
    async def delete_product(product_id: str):
        coll = get_products_collection()
        doc = await coll.find_one({"_id": product_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Product not found")

        # Invariant check: Prevent unsafe deletion when active holds or confirmed bookings exist
        bookings_coll = get_bookings_collection()
        active_holds_count = await bookings_coll.count_documents({"product_id": product_id, "status": "HELD"})
        confirmed_count = await bookings_coll.count_documents({"product_id": product_id, "status": "CONFIRMED"})

        if active_holds_count > 0 or confirmed_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete product with {active_holds_count} active holds and {confirmed_count} confirmed bookings."
            )

        # Safe to delete
        await coll.delete_one({"_id": product_id})
        await redis_manager.delete_inventory(product_id)
        # Also clean any expired/cancelled bookings if desired
        await bookings_coll.delete_many({"product_id": product_id})
        logger.info(f"Deleted product {product_id}")

    @staticmethod
    async def reset_inventory(product_id: str, new_stock: Optional[int] = None) -> ProductResponse:
        """Controlled reset functionality for testing and demonstrations."""
        coll = get_products_collection()
        doc = await coll.find_one({"_id": product_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Product not found")

        stock = new_stock if new_stock is not None else doc["initial_stock"]
        now = utc_now_iso()

        # Update MongoDB product record
        await coll.update_one(
            {"_id": product_id},
            {"$set": {"initial_stock": stock, "updated_at": now}}
        )

        # Cancel/expire all current active holds in MongoDB for this product
        bookings_coll = get_bookings_collection()
        await bookings_coll.update_many(
            {"product_id": product_id, "status": "HELD"},
            {"$set": {"status": "EXPIRED", "updated_at": now}}
        )

        # Reset Redis stock
        await redis_manager.set_inventory(product_id, stock)
        logger.info(f"Reset inventory for product {product_id} to {stock}")

        return await ProductService.get_product(product_id)
