import hashlib
import json
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from fastapi import HTTPException
import logging
from pymongo.errors import DuplicateKeyError

from backend.db.mongo import get_idempotency_collection

logger = logging.getLogger("concord.idempotency")

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def compute_fingerprint(data: Dict[str, Any]) -> str:
    # Deterministic JSON string
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

class IdempotencyService:
    @staticmethod
    async def acquire_or_get_cached(
        idempotency_key: str,
        request_payload: Dict[str, Any]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Returns (is_new_request, cached_response_data).
        - If False, cached_response_data is returned (original outcome).
        - If True, this request holds the lock (in progress) and should proceed.
        - Raises HTTPException 409 if key is reused with a different payload.
        """
        fingerprint = compute_fingerprint(request_payload)
        coll = get_idempotency_collection()
        now = utc_now_iso()

        try:
            # Attempt to insert an IN_PROGRESS record atomically
            await coll.insert_one({
                "idempotency_key": idempotency_key,
                "request_fingerprint": fingerprint,
                "status": "IN_PROGRESS",
                "booking_id": None,
                "response_code": None,
                "response_payload": None,
                "created_at": now,
                "updated_at": now
            })
            # Successfully claimed key
            return True, None
        except DuplicateKeyError:
            # Key already exists, query current record
            record = await coll.find_one({"idempotency_key": idempotency_key})
            if not record:
                raise HTTPException(status_code=500, detail="Idempotency state inconsistency.")

            # Validate fingerprint
            if record.get("request_fingerprint") != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key conflict: This key was previously used with a different request payload."
                )

            # If already completed, return cached response
            if record.get("status") == "COMPLETED":
                logger.info(f"Returning cached idempotent response for key: {idempotency_key}")
                return False, record.get("response_payload")

            # If still IN_PROGRESS, wait briefly for in-flight request
            for _ in range(5):
                await asyncio.sleep(0.1)
                record = await coll.find_one({"idempotency_key": idempotency_key})
                if record and record.get("status") == "COMPLETED":
                    return False, record.get("response_payload")

            raise HTTPException(
                status_code=409,
                detail="A concurrent reservation request with this idempotency key is currently being processed. Please retry shortly."
            )

    @staticmethod
    async def mark_completed(
        idempotency_key: str,
        booking_id: str,
        response_payload: Dict[str, Any],
        response_code: int = 200
    ):
        coll = get_idempotency_collection()
        await coll.update_one(
            {"idempotency_key": idempotency_key},
            {"$set": {
                "status": "COMPLETED",
                "booking_id": booking_id,
                "response_payload": response_payload,
                "response_code": response_code,
                "updated_at": utc_now_iso()
            }}
        )

    @staticmethod
    async def mark_failed(idempotency_key: str, error_detail: str):
        coll = get_idempotency_collection()
        await coll.delete_one({"idempotency_key": idempotency_key})
