import asyncio
import logging
from backend.db.mongo import connect_mongo, close_mongo
from backend.db.redis_client import connect_redis, close_redis
from backend.db.indexes import ensure_indexes, inspect_indexes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("concord.init_db")

async def init_db():
    logger.info("Initializing Concord database and indexes...")
    db = await connect_mongo()
    await connect_redis()

    created_indexes = await ensure_indexes(db)
    logger.info(f"Verified indexes across {len(created_indexes)} collections.")

    inspected = await inspect_indexes(db)
    for coll, idxs in inspected.items():
        logger.info(f"Collection [{coll}] active indexes: {list(idxs.keys())}")

    await close_redis()
    await close_mongo()
    logger.info("Database initialization finished successfully.")

if __name__ == "__main__":
    asyncio.run(init_db())
