import pytest
import pytest_asyncio
import httpx
import os
import uuid
from typing import AsyncGenerator

os.environ["ENVIRONMENT"] = "testing"

from backend.main import app
from backend.db.mongo import connect_mongo, close_mongo, get_db
from backend.db.redis_client import connect_redis, close_redis, redis_manager
from backend.db.indexes import ensure_indexes

@pytest_asyncio.fixture(autouse=True)
async def init_test_env():
    db = await connect_mongo()
    await connect_redis()
    await ensure_indexes(db)
    yield
    await close_redis()

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac

@pytest_asyncio.fixture
async def test_product(client: httpx.AsyncClient):
    unique_name = f"Test Product {uuid.uuid4().hex[:8]}"
    resp = await client.post("/products", json={
        "name": unique_name,
        "description": "Fixture product for automated testing",
        "initial_stock": 10
    })
    assert resp.status_code == 201
    prod = resp.json()
    yield prod

    try:
        await client.delete(f"/products/{prod['id']}")
    except Exception:
        pass
