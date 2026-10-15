import pytest
import uuid
from datetime import datetime, timezone, timedelta
from backend.services.recovery_service import RecoveryService
from backend.db.mongo import get_operations_collection
from backend.db.redis_client import redis_manager

@pytest.mark.asyncio
async def test_cross_database_recovery_of_orphan_hold(test_product):
    pid = test_product["id"] # 10 units
    orphan_booking_id = f"orphan-book-{uuid.uuid4().hex[:8]}"
    orphan_op_id = f"op-{uuid.uuid4().hex[:8]}"

    # Simulate an incomplete operation: Redis hold was created, but backend crashed before MongoDB booking write
    # 1. Acquire Redis hold directly
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    success, reason, remaining, _ = await redis_manager.execute_reserve(
        product_id=pid,
        booking_id=orphan_booking_id,
        quantity=2,
        ttl_seconds=30,
        timestamp=old_time
    )
    assert success is True
    assert remaining == 8

    # 2. Record operation in MongoDB as REDIS_HELD (simulating in-flight crash)
    ops_coll = get_operations_collection()
    await ops_coll.insert_one({
        "_id": orphan_op_id,
        "booking_id": orphan_booking_id,
        "product_id": pid,
        "quantity": 2,
        "operation_type": "RESERVE",
        "status": "REDIS_HELD",
        "attempt_count": 1,
        "created_at": old_time,
        "updated_at": old_time,
        "last_error": None
    })

    # 3. Trigger reconciliation worker
    reconciled = await RecoveryService.reconcile_incomplete_operations()
    assert reconciled >= 1

    # 4. Verify Redis stock was safely restored from 8 back to 10
    restored_stock = await redis_manager.get_inventory(pid)
    assert restored_stock == 10

    # 5. Verify operation status updated in MongoDB
    op_doc = await ops_coll.find_one({"_id": orphan_op_id})
    assert op_doc["status"] == "RECONCILED_RELEASED"

    # 6. Re-running reconciliation must be idempotent (must not restore stock twice)
    reconciled_again = await RecoveryService.reconcile_incomplete_operations()
    assert reconciled_again == 0
    stock_after = await redis_manager.get_inventory(pid)
    assert stock_after == 10
