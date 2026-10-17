from fastapi import APIRouter
from typing import Dict, Any
import time

from backend.db.mongo import get_db
from backend.db.redis_client import redis_manager
from backend.config import settings

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check() -> Dict[str, Any]:
    start_time = time.time()
    mongo_status = "DOWN"
    redis_status = "DOWN"
    details = {}

    # Check MongoDB
    try:
        db = get_db()
        await db.command("ping")
        mongo_status = "UP"
        details["mongo_database"] = settings.MONGO_DB_NAME
    except Exception as e:
        details["mongo_error"] = str(e)

    # Check Redis
    try:
        r = await redis_manager.connect()
        await r.ping()
        redis_status = "UP"
        if "@" in settings.REDIS_URI:
            sanitized_host = settings.REDIS_URI.split("@")[-1]
            details["redis_uri"] = f"redis://***@{sanitized_host}"
        else:
            details["redis_uri"] = settings.REDIS_URI
    except Exception as e:
        details["redis_error"] = str(e)

    all_healthy = (mongo_status == "UP" and redis_status == "UP")
    latency_ms = round((time.time() - start_time) * 1000, 2)

    return {
        "status": "HEALTHY" if all_healthy else "UNHEALTHY",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "latency_ms": latency_ms,
        "components": {
            "mongodb": mongo_status,
            "redis": redis_status
        },
        "details": details
    }
