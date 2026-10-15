import asyncio
import logging
from backend.config import settings
from backend.services.recovery_service import RecoveryService

logger = logging.getLogger("concord.expiration_worker")

async def run_expiration_worker():
    """
    Background worker that continuously scans for and releases expired holds.
    Uses configurable interval (e.g. 2s).
    """
    logger.info(f"Starting Hold Expiration Worker (interval: {settings.EXPIRATION_WORKER_INTERVAL_SECONDS}s)...")
    while True:
        try:
            released = await RecoveryService.process_expired_holds()
            if released > 0:
                logger.info(f"Expiration worker released {released} expired hold(s).")
        except asyncio.CancelledError:
            logger.info("Expiration worker task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in expiration worker loop: {e}")

        await asyncio.sleep(settings.EXPIRATION_WORKER_INTERVAL_SECONDS)

if __name__ == "__main__":
    from backend.db.mongo import connect_mongo
    from backend.db.redis_client import connect_redis

    async def main():
        await connect_mongo()
        await connect_redis()
        await run_expiration_worker()

    asyncio.run(main())
