import asyncio
import redis.asyncio as aioredis
from redis.exceptions import NoScriptError
from typing import Optional, Dict, Any, Tuple
from backend.config import settings
import logging

logger = logging.getLogger("concord.redis")

_redis: Optional[aioredis.Redis] = None

# Key Formatters
def inventory_key(product_id: str) -> str:
    return f"concord:inventory:{product_id}"

def hold_key(booking_id: str) -> str:
    return f"concord:hold:{booking_id}"

def stream_key() -> str:
    return settings.STREAM_KEY

# Lua Scripts
RESERVE_LUA = """
local inv_key = KEYS[1]
local hold_key = KEYS[2]
local stream_key = KEYS[3]

local product_id = ARGV[1]
local booking_id = ARGV[2]
local quantity = tonumber(ARGV[3])
local ttl_seconds = tonumber(ARGV[4])
local timestamp = ARGV[5]
local max_stream_len = tonumber(ARGV[6])

-- 1. Check if product inventory exists
local stock_raw = redis.call('get', inv_key)
if not stock_raw then
    return {0, "PRODUCT_NOT_INITIALIZED", "0"}
end

local current_stock = tonumber(stock_raw)

-- 2. Validate quantity
if quantity <= 0 then
    return {0, "INVALID_QUANTITY", tostring(current_stock)}
end

-- 3. Check sufficient stock
if current_stock < quantity then
    -- Emit RESERVATION_REJECTED event
    redis.call('xadd', stream_key, 'MAXLEN', '~', max_stream_len, '*',
        'event_type', 'RESERVATION_REJECTED',
        'product_id', product_id,
        'booking_id', booking_id,
        'quantity', tostring(quantity),
        'timestamp', timestamp,
        'outcome', 'INSUFFICIENT_STOCK',
        'available_stock', tostring(current_stock)
    )
    return {0, "INSUFFICIENT_STOCK", tostring(current_stock)}
end

-- 4. Atomically decrement stock
local remaining_stock = redis.call('decrby', inv_key, quantity)

-- 5. Store hold data in Redis hash
redis.call('hset', hold_key,
    'booking_id', booking_id,
    'product_id', product_id,
    'quantity', tostring(quantity),
    'status', 'HELD',
    'created_at', timestamp,
    'ttl_seconds', tostring(ttl_seconds)
)
redis.call('expire', hold_key, ttl_seconds + 120)

-- 6. Emit HOLD_CREATED to Redis Stream
local stream_id = redis.call('xadd', stream_key, 'MAXLEN', '~', max_stream_len, '*',
    'event_type', 'HOLD_CREATED',
    'product_id', product_id,
    'booking_id', booking_id,
    'quantity', tostring(quantity),
    'timestamp', timestamp,
    'outcome', 'SUCCESS',
    'remaining_stock', tostring(remaining_stock)
)

return {1, "HOLD_CREATED", tostring(remaining_stock), stream_id}
"""

RELEASE_LUA = """
local inv_key = KEYS[1]
local hold_key = KEYS[2]
local stream_key = KEYS[3]

local release_reason = ARGV[1]
local timestamp = ARGV[2]
local max_stream_len = tonumber(ARGV[3])
local booking_id = ARGV[4]

-- 1. Check if hold exists
local exists = redis.call('exists', hold_key)
if exists == 0 then
    return {0, "HOLD_NOT_FOUND", "0"}
end

-- 2. Check hold current status
local status = redis.call('hget', hold_key, 'status')
if status ~= 'HELD' then
    return {0, "HOLD_NOT_ACTIVE", status or "UNKNOWN"}
end

local quantity = tonumber(redis.call('hget', hold_key, 'quantity') or "0")
local product_id = redis.call('hget', hold_key, 'product_id') or ""

-- 3. Restore inventory atomically
local restored_stock = redis.call('incrby', inv_key, quantity)

-- 4. Update hold status
redis.call('hset', hold_key, 'status', release_reason, 'resolved_at', timestamp)

-- 5. Emit event
local event_type = "HOLD_EXPIRED"
if release_reason == "CANCELLED" then
    event_type = "HOLD_CANCELLED"
end

local stream_id = redis.call('xadd', stream_key, 'MAXLEN', '~', max_stream_len, '*',
    'event_type', event_type,
    'product_id', product_id,
    'booking_id', booking_id,
    'quantity', tostring(quantity),
    'timestamp', timestamp,
    'outcome', release_reason,
    'current_stock', tostring(restored_stock)
)

return {1, release_reason, tostring(restored_stock), stream_id}
"""

CONFIRM_LUA = """
local hold_key = KEYS[1]
local stream_key = KEYS[2]
local timestamp = ARGV[1]
local max_stream_len = tonumber(ARGV[2])
local booking_id = ARGV[3]

local exists = redis.call('exists', hold_key)
if exists == 0 then
    return {0, "HOLD_NOT_FOUND"}
end

local status = redis.call('hget', hold_key, 'status')
if status == 'CONFIRMED' then
    return {1, "ALREADY_CONFIRMED"}
end
if status ~= 'HELD' then
    return {0, "INVALID_STATE", status or "UNKNOWN"}
end

redis.call('hset', hold_key, 'status', 'CONFIRMED', 'confirmed_at', timestamp)

local product_id = redis.call('hget', hold_key, 'product_id') or ""
local quantity = redis.call('hget', hold_key, 'quantity') or "0"

local stream_id = redis.call('xadd', stream_key, 'MAXLEN', '~', max_stream_len, '*',
    'event_type', 'BOOKING_CONFIRMED',
    'product_id', product_id,
    'booking_id', booking_id,
    'quantity', tostring(quantity),
    'timestamp', timestamp,
    'outcome', 'CONFIRMED'
)

return {1, "CONFIRMED", stream_id}
"""

class RedisClientManager:
    def __init__(self):
        self.client: Optional[aioredis.Redis] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._reserve_sha: Optional[str] = None
        self._release_sha: Optional[str] = None
        self._confirm_sha: Optional[str] = None

    async def connect(self) -> aioredis.Redis:
        current_loop = asyncio.get_running_loop()
        if self.client is not None and self._loop == current_loop:
            return self.client

        if self.client is not None and self._loop != current_loop:
            self.client = None
            self._reserve_sha = None
            self._release_sha = None
            self._confirm_sha = None

        logger.info(f"Connecting to Redis at {settings.REDIS_URI}...")
        self.client = aioredis.from_url(
            settings.REDIS_URI,
            decode_responses=True,
            socket_timeout=10.0,
            socket_connect_timeout=10.0,
            max_connections=200
        )
        self._loop = current_loop
        # Register Lua scripts
        self._reserve_sha = await self.client.script_load(RESERVE_LUA)
        self._release_sha = await self.client.script_load(RELEASE_LUA)
        self._confirm_sha = await self.client.script_load(CONFIRM_LUA)
        logger.info("Redis connected and Lua scripts loaded successfully.")
        return self.client

    async def close(self):
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:
                pass
            self.client = None
            self._loop = None
            self._reserve_sha = None
            self._release_sha = None
            self._confirm_sha = None
            logger.info("Redis connection closed.")

    async def execute_reserve(
        self,
        product_id: str,
        booking_id: str,
        quantity: int,
        ttl_seconds: int,
        timestamp: str
    ) -> Tuple[bool, str, int, Optional[str]]:
        r = await self.connect()
        keys = [inventory_key(product_id), hold_key(booking_id), stream_key()]
        args = [product_id, booking_id, quantity, ttl_seconds, timestamp, settings.STREAM_MAX_LEN]
        try:
            res = await r.evalsha(self._reserve_sha, len(keys), *keys, *args)
        except NoScriptError:
            self._reserve_sha = await r.script_load(RESERVE_LUA)
            res = await r.evalsha(self._reserve_sha, len(keys), *keys, *args)

        success = (res[0] == 1)
        reason = res[1]
        stock = int(res[2])
        stream_id = res[3] if len(res) > 3 else None
        return success, reason, stock, stream_id

    async def execute_release(
        self,
        product_id: str,
        booking_id: str,
        reason: str,
        timestamp: str
    ) -> Tuple[bool, str, int, Optional[str]]:
        r = await self.connect()
        keys = [inventory_key(product_id), hold_key(booking_id), stream_key()]
        args = [reason, timestamp, settings.STREAM_MAX_LEN, booking_id]
        try:
            res = await r.evalsha(self._release_sha, len(keys), *keys, *args)
        except NoScriptError:
            self._release_sha = await r.script_load(RELEASE_LUA)
            res = await r.evalsha(self._release_sha, len(keys), *keys, *args)

        success = (res[0] == 1)
        status_text = res[1]
        stock = int(res[2]) if len(res) > 2 else 0
        stream_id = res[3] if len(res) > 3 else None
        return success, status_text, stock, stream_id

    async def execute_confirm(
        self,
        booking_id: str,
        timestamp: str
    ) -> Tuple[bool, str, Optional[str]]:
        r = await self.connect()
        keys = [hold_key(booking_id), stream_key()]
        args = [timestamp, settings.STREAM_MAX_LEN, booking_id]
        try:
            res = await r.evalsha(self._confirm_sha, len(keys), *keys, *args)
        except NoScriptError:
            self._confirm_sha = await r.script_load(CONFIRM_LUA)
            res = await r.evalsha(self._confirm_sha, len(keys), *keys, *args)

        success = (res[0] == 1)
        status_text = res[1]
        stream_id = res[2] if len(res) > 2 else None
        return success, status_text, stream_id

    async def set_inventory(self, product_id: str, stock: int):
        r = await self.connect()
        await r.set(inventory_key(product_id), stock)

    async def get_inventory(self, product_id: str) -> Optional[int]:
        r = await self.connect()
        val = await r.get(inventory_key(product_id))
        return int(val) if val is not None else None

    async def delete_inventory(self, product_id: str):
        r = await self.connect()
        await r.delete(inventory_key(product_id))

    async def get_hold(self, booking_id: str) -> Dict[str, str]:
        r = await self.connect()
        return await r.hgetall(hold_key(booking_id))

redis_manager = RedisClientManager()

async def connect_redis() -> aioredis.Redis:
    return await redis_manager.connect()

async def close_redis():
    await redis_manager.close()
