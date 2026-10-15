import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import HTTPException
import logging
from pymongo import ReturnDocument

from backend.config import settings
from backend.db.mongo import (
    get_products_collection,
    get_bookings_collection,
    get_operations_collection
)
from backend.db.redis_client import redis_manager
from backend.models.schemas import (
    ReservationRequest,
    ReservationResponse,
    BookingActionResponse,
    BookingResponse
)
from backend.services.idempotency_service import IdempotencyService

logger = logging.getLogger("concord.reservation_service")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_iso() -> str:
    return utc_now().isoformat()

class ReservationService:
    @staticmethod
    async def create_reservation(req: ReservationRequest) -> ReservationResponse:
        # Step 1: Idempotency check with payload fingerprint
        fingerprint_data = {
            "product_id": req.product_id,
            "quantity": req.quantity
        }
        is_new, cached = await IdempotencyService.acquire_or_get_cached(
            req.idempotency_key, fingerprint_data
        )
        if not is_new and cached:
            cached_data = dict(cached)
            cached_data["cached"] = True
            return ReservationResponse(**cached_data)

        # Step 2: Validate product exists in MongoDB
        products_coll = get_products_collection()
        product = await products_coll.find_one({"_id": req.product_id})
        if not product:
            await IdempotencyService.mark_failed(req.idempotency_key, "Product not found")
            raise HTTPException(status_code=404, detail="Product not found")

        ttl_seconds = req.hold_ttl_seconds or settings.DEFAULT_HOLD_TTL_SECONDS
        booking_id = str(uuid.uuid4())
        operation_id = str(uuid.uuid4())
        now = utc_now()
        now_str = now.isoformat()
        expires_at_str = (now + timedelta(seconds=ttl_seconds)).isoformat()

        # Step 3: Record operation intent in MongoDB reservation_operations
        ops_coll = get_operations_collection()
        await ops_coll.insert_one({
            "_id": operation_id,
            "booking_id": booking_id,
            "product_id": req.product_id,
            "quantity": req.quantity,
            "operation_type": "RESERVE",
            "status": "PENDING_REDIS",
            "attempt_count": 1,
            "created_at": now_str,
            "updated_at": now_str,
            "last_error": None
        })

        # Step 4: Atomic Redis reservation using Lua script
        success, reason, remaining_stock, stream_id = await redis_manager.execute_reserve(
            product_id=req.product_id,
            booking_id=booking_id,
            quantity=req.quantity,
            ttl_seconds=ttl_seconds,
            timestamp=now_str
        )

        if not success:
            await ops_coll.update_one(
                {"_id": operation_id},
                {"$set": {"status": f"FAILED_REDIS_{reason}", "updated_at": utc_now_iso()}}
            )
            # Remove in-progress idempotency key so retry can occur cleanly
            await IdempotencyService.mark_failed(req.idempotency_key, reason)

            if reason == "INSUFFICIENT_STOCK":
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient inventory available for product {req.product_id}. Available: {remaining_stock}"
                )
            raise HTTPException(status_code=400, detail=f"Reservation failed: {reason}")

        # Step 5: Redis hold succeeded -> update operation status
        await ops_coll.update_one(
            {"_id": operation_id},
            {"$set": {"status": "REDIS_HELD", "updated_at": utc_now_iso()}}
        )

        # Step 6: Persist durable booking in MongoDB
        bookings_coll = get_bookings_collection()
        booking_doc = {
            "_id": booking_id,
            "product_id": req.product_id,
            "quantity": req.quantity,
            "status": "HELD",
            "idempotency_key": req.idempotency_key,
            "created_at": now_str,
            "updated_at": now_str,
            "expires_at": expires_at_str,
            "confirmed_at": None
        }

        try:
            await bookings_coll.insert_one(booking_doc)
            # Step 7: Operation completed
            await ops_coll.update_one(
                {"_id": operation_id},
                {"$set": {"status": "COMPLETED", "updated_at": utc_now_iso()}}
            )
        except Exception as e:
            # Cross-database compensation: If MongoDB write fails, rollback Redis hold immediately
            logger.error(f"MongoDB persistence failed for booking {booking_id}: {e}. Rolling back Redis hold...")
            await redis_manager.execute_release(
                product_id=req.product_id,
                booking_id=booking_id,
                reason="EXPIRED",
                timestamp=utc_now_iso()
            )
            await ops_coll.update_one(
                {"_id": operation_id},
                {"$set": {"status": "COMPENSATED_ROLLED_BACK", "last_error": str(e), "updated_at": utc_now_iso()}}
            )
            await IdempotencyService.mark_failed(req.idempotency_key, str(e))
            raise HTTPException(
                status_code=500,
                detail="Durable persistence failure. Redis inventory hold rolled back safely."
            )

        # Step 8: Complete idempotency record
        response_dict = {
            "booking_id": booking_id,
            "product_id": req.product_id,
            "quantity": req.quantity,
            "status": "HELD",
            "expires_at": expires_at_str,
            "idempotency_key": req.idempotency_key,
            "message": f"Reserved {req.quantity} units successfully. Hold expires at {expires_at_str}.",
            "cached": False,
            "remaining_stock": remaining_stock
        }
        await IdempotencyService.mark_completed(
            idempotency_key=req.idempotency_key,
            booking_id=booking_id,
            response_payload=response_dict,
            response_code=200
        )

        return ReservationResponse(**response_dict)

    @staticmethod
    async def confirm_booking(booking_id: str) -> BookingActionResponse:
        bookings_coll = get_bookings_collection()
        booking = await bookings_coll.find_one({"_id": booking_id})
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")

        # Idempotent confirmation if already confirmed
        if booking["status"] == "CONFIRMED":
            return BookingActionResponse(
                booking_id=booking_id,
                product_id=booking["product_id"],
                quantity=booking["quantity"],
                status="CONFIRMED",
                message="Booking is already confirmed."
            )

        if booking["status"] in ("EXPIRED", "CANCELLED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot confirm booking in '{booking['status']}' state."
            )

        # Atomic transition guard: only transition if status is still 'HELD'
        now_str = utc_now_iso()
        updated = await bookings_coll.find_one_and_update(
            {"_id": booking_id, "status": "HELD"},
            {"$set": {"status": "CONFIRMED", "confirmed_at": now_str, "updated_at": now_str}},
            return_document=ReturnDocument.AFTER
        )

        if not updated:
            # Race condition occurred (e.g. expired or cancelled concurrently)
            curr = await bookings_coll.find_one({"_id": booking_id})
            status = curr["status"] if curr else "UNKNOWN"
            if status == "CONFIRMED":
                return BookingActionResponse(
                    booking_id=booking_id,
                    product_id=booking["product_id"],
                    quantity=booking["quantity"],
                    status="CONFIRMED",
                    message="Booking confirmed successfully."
                )
            raise HTTPException(
                status_code=409,
                detail=f"Confirmation race conflict: booking status transitioned to '{status}'."
            )

        # Update Redis hold state and emit event
        await redis_manager.execute_confirm(booking_id, now_str)

        return BookingActionResponse(
            booking_id=booking_id,
            product_id=updated["product_id"],
            quantity=updated["quantity"],
            status="CONFIRMED",
            message="Booking confirmed successfully."
        )

    @staticmethod
    async def cancel_booking(booking_id: str) -> BookingActionResponse:
        bookings_coll = get_bookings_collection()
        booking = await bookings_coll.find_one({"_id": booking_id})
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")

        if booking["status"] == "CANCELLED":
            return BookingActionResponse(
                booking_id=booking_id,
                product_id=booking["product_id"],
                quantity=booking["quantity"],
                status="CANCELLED",
                message="Booking is already cancelled."
            )

        if booking["status"] in ("CONFIRMED", "EXPIRED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel booking in '{booking['status']}' state."
            )

        now_str = utc_now_iso()
        updated = await bookings_coll.find_one_and_update(
            {"_id": booking_id, "status": "HELD"},
            {"$set": {"status": "CANCELLED", "updated_at": now_str}},
            return_document=ReturnDocument.AFTER
        )

        if not updated:
            curr = await bookings_coll.find_one({"_id": booking_id})
            status = curr["status"] if curr else "UNKNOWN"
            if status == "CANCELLED":
                return BookingActionResponse(
                    booking_id=booking_id,
                    product_id=booking["product_id"],
                    quantity=booking["quantity"],
                    status="CANCELLED",
                    message="Booking already cancelled."
                )
            raise HTTPException(
                status_code=409,
                detail=f"Cancellation race conflict: booking status transitioned to '{status}'."
            )

        # Restore inventory and update Redis atomically via Lua
        await redis_manager.execute_release(
            product_id=booking["product_id"],
            booking_id=booking_id,
            reason="CANCELLED",
            timestamp=now_str
        )

        return BookingActionResponse(
            booking_id=booking_id,
            product_id=booking["product_id"],
            quantity=booking["quantity"],
            status="CANCELLED",
            message="Booking cancelled and inventory returned to available stock."
        )

    @staticmethod
    async def get_booking(booking_id: str) -> BookingResponse:
        bookings_coll = get_bookings_collection()
        doc = await bookings_coll.find_one({"_id": booking_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Booking not found")
        return BookingResponse(
            id=doc["_id"],
            product_id=doc["product_id"],
            quantity=doc["quantity"],
            status=doc["status"],
            idempotency_key=doc.get("idempotency_key"),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            expires_at=doc.get("expires_at"),
            confirmed_at=doc.get("confirmed_at")
        )

    @staticmethod
    async def list_bookings(
        product_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0
    ) -> List[BookingResponse]:
        bookings_coll = get_bookings_collection()
        query: Dict[str, Any] = {}
        if product_id:
            query["product_id"] = product_id
        if status:
            query["status"] = status

        cursor = bookings_coll.find(query).sort("created_at", -1).skip(skip).limit(limit)
        items = []
        async for doc in cursor:
            items.append(BookingResponse(
                id=doc["_id"],
                product_id=doc["product_id"],
                quantity=doc["quantity"],
                status=doc["status"],
                idempotency_key=doc.get("idempotency_key"),
                created_at=doc["created_at"],
                updated_at=doc["updated_at"],
                expires_at=doc.get("expires_at"),
                confirmed_at=doc.get("confirmed_at")
            ))
        return items
