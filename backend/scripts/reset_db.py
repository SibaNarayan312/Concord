import asyncio
import logging
from backend.db.mongo import (
    connect_mongo,
    close_mongo,
    get_products_collection,
    get_bookings_collection,
    get_idempotency_collection,
    get_operations_collection
)
from backend.db.redis_client import connect_redis, close_redis, redis_manager
from backend.scripts.seed_data import seed_database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("concord.reset_db")

async def reset_db():
    logger.warning("RESETTING CONCORD DATABASE (Safe Test Data Reset)...")
    await connect_mongo()
    r = await connect_redis()

    # Clear MongoDB collections
    await get_products_collection().delete_many({})
    await get_bookings_collection().delete_many({})
    await get_idempotency_collection().delete_many({})
    await get_operations_collection().delete_many({})

    # Clear Redis keys starting with concord:
    cursor = "0"
    while cursor != 0:
        cursor, keys = await r.scan(cursor=cursor, match="concord:*", count=100)
        if keys:
            await r.delete(*keys)

    logger.info("MongoDB collections and Redis keys cleared. Re-seeding baseline data...")
    await close_redis()
    await close_mongo()

    await seed_database()
    logger.info("Database reset and re-seeding complete.")

if __name__ == "__main__":
    asyncio.run(reset_db())
