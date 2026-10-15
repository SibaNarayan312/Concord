import asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import Optional
from backend.config import settings
import logging

logger = logging.getLogger("concord.mongo")

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None

async def connect_mongo() -> AsyncIOMotorDatabase:
    global _client, _db
    current_loop = asyncio.get_running_loop()
    if _client is not None:
        try:
            if _client.get_io_loop() != current_loop:
                _client.close()
                _client = None
                _db = None
        except Exception:
            _client = None
            _db = None

    if _client is None:
        logger.info(f"Connecting to MongoDB at {settings.MONGO_URI}...")
        _client = AsyncIOMotorClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=5000,
            uuidRepresentation="standard"
        )
        _db = _client[settings.MONGO_DB_NAME]
        await _client.admin.command('ping')
        logger.info(f"MongoDB connected successfully. Database: {settings.MONGO_DB_NAME}")
    return _db

async def close_mongo():
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed.")

def get_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _client is not None:
        try:
            current_loop = asyncio.get_running_loop()
            if _client.get_io_loop() != current_loop:
                _client.close()
                _client = AsyncIOMotorClient(
                    settings.MONGO_URI,
                    serverSelectionTimeoutMS=5000,
                    uuidRepresentation="standard"
                )
                _db = _client[settings.MONGO_DB_NAME]
        except RuntimeError:
            pass
    if _db is None:
        _client = AsyncIOMotorClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=5000,
            uuidRepresentation="standard"
        )
        _db = _client[settings.MONGO_DB_NAME]
    return _db

def get_client() -> AsyncIOMotorClient:
    get_db()
    return _client

# Collection helpers
def get_products_collection():
    return get_db()["products"]

def get_bookings_collection():
    return get_db()["bookings"]

def get_idempotency_collection():
    return get_db()["idempotency_records"]

def get_operations_collection():
    return get_db()["reservation_operations"]

def get_wc_mappings_collection():
    return get_db()["woocommerce_mappings"]

def get_wc_orders_collection():
    return get_db()["woocommerce_orders"]

def get_wc_webhooks_collection():
    return get_db()["woocommerce_webhooks"]
