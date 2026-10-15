import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from backend.config import settings
from backend.db.mongo import connect_mongo, close_mongo, get_db
from backend.db.redis_client import connect_redis, close_redis
from backend.db.indexes import ensure_indexes
from backend.routes import health, products, reservations, bookings, analytics, events
from backend.workers.expiration_worker import run_expiration_worker
from backend.workers.reconciliation_worker import run_reconciliation_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("concord.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing Concord Service...")
    db = await connect_mongo()
    await connect_redis()
    index_results = await ensure_indexes(db)
    logger.info(f"Initialized indexes: {list(index_results.keys())}")

    # Launch background workers
    expiration_task = asyncio.create_task(run_expiration_worker())
    reconciliation_task = asyncio.create_task(run_reconciliation_worker())

    yield

    # Shutdown
    logger.info("Shutting down Concord Service...")
    expiration_task.cancel()
    reconciliation_task.cancel()
    await asyncio.gather(expiration_task, reconciliation_task, return_exceptions=True)
    await close_redis()
    await close_mongo()
    logger.info("Concord Service shutdown complete.")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="High-Contention Inventory Reservation Service with Exactly-Once Booking Semantics and Live Contention Stream",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health.router)
app.include_router(products.router)
app.include_router(reservations.router)
app.include_router(bookings.router)
app.include_router(analytics.router)
app.include_router(events.router)

# Serve Frontend static assets
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

@app.get("/", include_in_schema=False)
async def serve_root():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Concord API is running. Access /docs for interactive Swagger UI."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.HOST, port=settings.PORT, reload=True)
