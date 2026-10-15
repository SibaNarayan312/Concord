import asyncio
import uuid
from datetime import datetime, timezone, timedelta
import logging

from backend.db.mongo import connect_mongo, close_mongo, get_products_collection, get_bookings_collection, get_operations_collection
from backend.db.redis_client import connect_redis, close_redis, redis_manager
from backend.db.indexes import ensure_indexes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("concord.seed")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_iso() -> str:
    return utc_now().isoformat()

SAMPLE_PRODUCTS = [
    {
        "_id": "prod-rtx-4090",
        "name": "NVIDIA GeForce RTX 4090 24GB",
        "description": "High-demand flagship GPU (Target for High-Contention Stress Tests)",
        "initial_stock": 10
    },
    {
        "_id": "prod-concert-vip",
        "name": "Coldplay Music of the Spheres VIP Ticket",
        "description": "Ultra high-concurrency ticket release with short hold expiration",
        "initial_stock": 50
    },
    {
        "_id": "prod-ps5-pro",
        "name": "Sony PlayStation 5 Pro Console 2TB",
        "description": "Limited batch console release",
        "initial_stock": 25
    },
    {
        "_id": "prod-retro-sneakers",
        "name": "Air Jordan 1 Retro High OG 'Chicago'",
        "description": "Exclusive collector edition sneaker drop",
        "initial_stock": 5
    }
]

async def seed_database():
    logger.info("Connecting to MongoDB and Redis for database seeding...")
    db = await connect_mongo()
    await connect_redis()

    # Step 1: Ensure indexes
    await ensure_indexes(db)

    products_coll = get_products_collection()
    bookings_coll = get_bookings_collection()
    now_dt = utc_now()
    now_str = now_dt.isoformat()

    logger.info("Seeding products...")
    for p in SAMPLE_PRODUCTS:
        pid = p["_id"]
        doc = {
            "_id": pid,
            "name": p["name"],
            "description": p["description"],
            "initial_stock": p["initial_stock"],
            "created_at": now_str,
            "updated_at": now_str
        }
        await products_coll.replace_one({"_id": pid}, doc, upsert=True)
        # Initialize Redis inventory
        await redis_manager.set_inventory(pid, p["initial_stock"])
        logger.info(f"Seeded product '{p['name']}' with {p['initial_stock']} units in Mongo and Redis.")

    # Step 2: Seed a few sample historical bookings for demonstration
    # Product: PS5 Pro (has 1 confirmed booking of 2 units)
    ps5_id = "prod-ps5-pro"
    b1_id = "book-seed-confirmed-01"
    b1_doc = {
        "_id": b1_id,
        "product_id": ps5_id,
        "quantity": 2,
        "status": "CONFIRMED",
        "idempotency_key": "idemp-seed-ps5-01",
        "created_at": (now_dt - timedelta(minutes=30)).isoformat(),
        "updated_at": (now_dt - timedelta(minutes=25)).isoformat(),
        "expires_at": (now_dt - timedelta(minutes=20)).isoformat(),
        "confirmed_at": (now_dt - timedelta(minutes=25)).isoformat()
    }
    await bookings_coll.replace_one({"_id": b1_id}, b1_doc, upsert=True)

    # Product: Concert Ticket (has 1 expired booking of 4 units)
    ticket_id = "prod-concert-vip"
    b2_id = "book-seed-expired-01"
    b2_doc = {
        "_id": b2_id,
        "product_id": ticket_id,
        "quantity": 4,
        "status": "EXPIRED",
        "idempotency_key": "idemp-seed-ticket-01",
        "created_at": (now_dt - timedelta(hours=2)).isoformat(),
        "updated_at": (now_dt - timedelta(hours=2)).isoformat(),
        "expires_at": (now_dt - timedelta(hours=2, minutes=-5)).isoformat(),
        "confirmed_at": None
    }
    await bookings_coll.replace_one({"_id": b2_id}, b2_doc, upsert=True)

    # Adjust Redis inventory for confirmed ps5 booking (2 units sold)
    await redis_manager.set_inventory(ps5_id, 23)

    logger.info("Database seeding completed successfully.")
    await close_redis()
    await close_mongo()

if __name__ == "__main__":
    asyncio.run(seed_database())
