import asyncio
import logging
from backend.config import settings
from backend.services.recovery_service import RecoveryService

logger = logging.getLogger("concord.reconciliation_worker")

async def run_reconciliation_worker():
    """
    Background worker that continuously scans for and resolves incomplete cross-database operations.
    """
    logger.info(f"Starting Reconciliation Worker (interval: {settings.RECONCILIATION_WORKER_INTERVAL_SECONDS}s)...")
    while True:
        try:
            reconciled = await RecoveryService.reconcile_incomplete_operations()
            if reconciled > 0:
                logger.info(f"Reconciliation worker resolved {reconciled} incomplete operation(s).")
        except asyncio.CancelledError:
            logger.info("Reconciliation worker task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in reconciliation worker loop: {e}")

        await asyncio.sleep(settings.RECONCILIATION_WORKER_INTERVAL_SECONDS)

if __name__ == "__main__":
    from backend.db.mongo import connect_mongo
    from backend.db.redis_client import connect_redis

    async def main():
        await connect_mongo()
        await connect_redis()
        await run_reconciliation_worker()

    asyncio.run(main())
