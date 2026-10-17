import hmac
import hashlib
import base64
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import httpx
from fastapi import HTTPException

from backend.config import settings
from backend.db.mongo import (
    get_products_collection,
    get_bookings_collection,
    get_wc_mappings_collection,
    get_wc_orders_collection,
    get_wc_webhooks_collection
)
from backend.db.redis_client import redis_manager, stream_key
from backend.models.schemas import (
    ReservationRequest,
    WooCommerceMappingCreate,
    WooCommerceMappingResponse,
    WooCommerceSyncResult,
    WooCommerceStatusResponse
)
from backend.services.reservation_service import ReservationService

logger = logging.getLogger("concord.woocommerce")

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class WooCommerceService:
    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
        """
        Validates the HMAC-SHA256 signature sent by WooCommerce in the 'x-wc-webhook-signature' header.
        Uses constant-time comparison to prevent timing attacks.
        """
        secret = settings.WC_WEBHOOK_SECRET
        if not secret or not settings.WC_VERIFY_WEBHOOK_SIGNATURE:
            # When signature verification is disabled in dev/testing, permit request
            return True

        if not signature_header:
            logger.warning("WooCommerce webhook missing 'x-wc-webhook-signature' header.")
            return False

        computed_digest = hmac.new(
            key=secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256
        ).digest()
        computed_b64 = base64.b64encode(computed_digest).decode("utf-8")

        is_valid = hmac.compare_digest(computed_b64, signature_header.strip())
        if not is_valid:
            logger.warning("WooCommerce webhook signature mismatch.")
        return is_valid

    @staticmethod
    async def create_mapping(payload: WooCommerceMappingCreate) -> WooCommerceMappingResponse:
        """
        Establishes an authoritative 1:1 mapping between a WooCommerce product/variation
        and a Concord inventory product.
        """
        prods_coll = get_products_collection()
        mappings_coll = get_wc_mappings_collection()

        # 1. Validate Concord product exists
        concord_prod = await prods_coll.find_one({"_id": payload.concord_product_id})
        if not concord_prod:
            raise HTTPException(status_code=404, detail=f"Concord product '{payload.concord_product_id}' not found.")

        # 2. Check uniqueness of (wc_product_id, wc_variation_id)
        existing = await mappings_coll.find_one({
            "wc_product_id": payload.wc_product_id,
            "wc_variation_id": payload.wc_variation_id or 0
        })
        now = utc_now_iso()

        if existing:
            # Update existing mapping
            await mappings_coll.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "concord_product_id": payload.concord_product_id,
                    "sku": payload.sku,
                    "sync_enabled": payload.sync_enabled,
                    "updated_at": now
                }}
            )
            mapping_id = existing["_id"]
            created_at = existing["created_at"]
        else:
            mapping_id = str(uuid.uuid4())
            await mappings_coll.insert_one({
                "_id": mapping_id,
                "concord_product_id": payload.concord_product_id,
                "wc_product_id": payload.wc_product_id,
                "wc_variation_id": payload.wc_variation_id or 0,
                "sku": payload.sku,
                "sync_enabled": payload.sync_enabled,
                "last_synced_stock": None,
                "last_synced_at": None,
                "created_at": now,
                "updated_at": now
            })
            created_at = now

        # 3. Fetch live Redis stock
        avail = await redis_manager.get_inventory(payload.concord_product_id)
        if avail is None:
            avail = concord_prod["initial_stock"]

        # 4. Perform immediate initial stock sync if enabled
        if payload.sync_enabled and settings.WC_STOCK_SYNC_ENABLED:
            try:
                await WooCommerceService.sync_stock_to_woocommerce(payload.concord_product_id)
            except Exception as e:
                logger.warning(f"Initial stock sync for mapping {mapping_id} failed: {e}")

        return WooCommerceMappingResponse(
            id=mapping_id,
            concord_product_id=payload.concord_product_id,
            concord_product_name=concord_prod["name"],
            wc_product_id=payload.wc_product_id,
            wc_variation_id=payload.wc_variation_id or 0,
            sku=payload.sku,
            sync_enabled=payload.sync_enabled,
            concord_available_stock=avail,
            last_synced_stock=avail,
            last_synced_at=now,
            created_at=created_at,
            updated_at=now
        )

    @staticmethod
    async def list_mappings() -> List[WooCommerceMappingResponse]:
        mappings_coll = get_wc_mappings_collection()
        prods_coll = get_products_collection()

        results = []
        cursor = mappings_coll.find({}).sort("created_at", -1)
        async for doc in cursor:
            c_prod = await prods_coll.find_one({"_id": doc["concord_product_id"]})
            prod_name = c_prod["name"] if c_prod else "Unknown Product"
            avail = await redis_manager.get_inventory(doc["concord_product_id"])
            if avail is None and c_prod:
                avail = c_prod["initial_stock"]

            results.append(WooCommerceMappingResponse(
                id=doc["_id"],
                concord_product_id=doc["concord_product_id"],
                concord_product_name=prod_name,
                wc_product_id=doc["wc_product_id"],
                wc_variation_id=doc.get("wc_variation_id", 0),
                sku=doc.get("sku"),
                sync_enabled=doc.get("sync_enabled", True),
                concord_available_stock=avail,
                last_synced_stock=doc.get("last_synced_stock"),
                last_synced_at=doc.get("last_synced_at"),
                created_at=doc["created_at"],
                updated_at=doc["updated_at"]
            ))
        return results

    @staticmethod
    async def get_mapping_by_wc(wc_product_id: int, wc_variation_id: int = 0) -> Optional[Dict[str, Any]]:
        mappings_coll = get_wc_mappings_collection()
        return await mappings_coll.find_one({
            "wc_product_id": wc_product_id,
            "wc_variation_id": wc_variation_id or 0
        })

    @staticmethod
    async def delete_mapping(mapping_id: str):
        mappings_coll = get_wc_mappings_collection()
        res = await mappings_coll.delete_one({"_id": mapping_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Mapping not found")

    @staticmethod
    async def auto_map_first_products() -> List[WooCommerceMappingResponse]:
        """
        Convenience bootstrapper: maps existing Concord products to virtual WooCommerce product IDs (1001, 1002, ...)
        if no mappings currently exist, enabling immediate end-to-end testing.
        """
        prods_coll = get_products_collection()
        mappings_coll = get_wc_mappings_collection()

        prods = await prods_coll.find({}).to_list(10)
        seeded = []
        base_wc_id = 1001

        for idx, p in enumerate(prods):
            wc_id = base_wc_id + idx
            existing = await mappings_coll.find_one({"wc_product_id": wc_id, "wc_variation_id": 0})
            if not existing:
                resp = await WooCommerceService.create_mapping(WooCommerceMappingCreate(
                    concord_product_id=p["_id"],
                    wc_product_id=wc_id,
                    wc_variation_id=0,
                    sku=f"CONCORD-SKU-{p['name'][:6].upper()}",
                    sync_enabled=True
                ))
                seeded.append(resp)
        return seeded

    @staticmethod
    async def process_order_webhook(
        payload: Dict[str, Any],
        topic: str = "order.updated",
        delivery_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authoritative webhook handler for WooCommerce orders.
        Coordinates inventory reservations and order states with exactly-once / idempotent semantics.
        """
        now_str = utc_now_iso()
        webhooks_coll = get_wc_webhooks_collection()
        orders_coll = get_wc_orders_collection()

        # Handle WooCommerce webhook verification ping
        if "ping" in topic.lower() or ("webhook_id" in payload and not payload.get("id")):
            webhook_id = payload.get("webhook_id", "ping")
            logger.info(f"WooCommerce webhook ping received (webhook_id={webhook_id}).")
            return {
                "status": "SUCCESS",
                "message": "Webhook ping acknowledged.",
                "webhook_id": webhook_id
            }

        order_id = payload.get("id")
        if not order_id:
            raise HTTPException(status_code=400, detail="Missing order 'id' in WooCommerce webhook payload.")

        order_status = str(payload.get("status", "pending")).lower()
        line_items = payload.get("line_items", [])

        # Step 1: Webhook deduplication using delivery_id
        if delivery_id:
            existing_delivery = await webhooks_coll.find_one({"delivery_id": delivery_id})
            if existing_delivery and existing_delivery.get("status") == "PROCESSED":
                logger.info(f"Duplicate WooCommerce webhook delivery received (delivery_id={delivery_id}). Returning cached ack.")
                return {
                    "status": "DUPLICATE_DELIVERY_ACKNOWLEDGED",
                    "order_id": order_id,
                    "delivery_id": delivery_id,
                    "message": "Webhook delivery already processed successfully."
                }

        actual_delivery_id = delivery_id or f"gen-{uuid.uuid4()}"
        webhook_log_id = str(uuid.uuid4())
        await webhooks_coll.insert_one({
            "_id": webhook_log_id,
            "delivery_id": actual_delivery_id,
            "order_id": order_id,
            "order_status": order_status,
            "topic": topic,
            "status": "PROCESSING",
            "created_at": now_str
        })

        # Step 2: Resolve mapped line items
        resolved_items = []
        for item in line_items:
            wc_pid = item.get("product_id")
            wc_var_id = item.get("variation_id", 0)
            qty = int(item.get("quantity", 1))

            mapping = await WooCommerceService.get_mapping_by_wc(wc_pid, wc_var_id)
            if mapping:
                resolved_items.append({
                    "item_id": item.get("id", str(uuid.uuid4())),
                    "wc_product_id": wc_pid,
                    "wc_variation_id": wc_var_id,
                    "concord_product_id": mapping["concord_product_id"],
                    "quantity": qty,
                    "name": item.get("name", "Item")
                })
            else:
                logger.info(f"WooCommerce product {wc_pid} (var: {wc_var_id}) not mapped in Concord. Skipping item.")

        # Step 3: Handle Order Lifecycle States
        order_record = await orders_coll.find_one({"order_id": order_id})
        booking_ids = order_record.get("booking_ids", []) if order_record else []
        action_outcomes = []

        # --- A. ORDER CREATION / PENDING PAYMENT: CREATE RESERVATION HOLD ---
        if order_status in ("pending", "on-hold", "checkout-draft", "pending-payment"):
            for item in resolved_items:
                # Idempotency key scoped to order ID and line item ID
                idemp_key = f"wc-order-{order_id}-item-{item['item_id']}"
                req = ReservationRequest(
                    product_id=item["concord_product_id"],
                    quantity=item["quantity"],
                    idempotency_key=idemp_key,
                    hold_ttl_seconds=settings.WC_HOLD_TTL_SECONDS
                )
                try:
                    res_resp = await ReservationService.create_reservation(req)
                    if res_resp.booking_id not in booking_ids:
                        booking_ids.append(res_resp.booking_id)

                    action_outcomes.append({
                        "item_id": item["item_id"],
                        "product_id": item["concord_product_id"],
                        "booking_id": res_resp.booking_id,
                        "status": "HELD",
                        "quantity": item["quantity"]
                    })

                    # Emit WooCommerce-specific reservation event to Redis Stream only for new holds
                    is_new_hold = not getattr(res_resp, "cached", False)
                    if is_new_hold:
                        r = await redis_manager.connect()
                        await r.xadd(
                            stream_key(),
                            {
                                "event_type": "WOOCOMMERCE_ORDER_RESERVED",
                                "order_id": str(order_id),
                                "booking_id": res_resp.booking_id,
                                "product_id": item["concord_product_id"],
                                "quantity": str(item["quantity"]),
                                "timestamp": now_str,
                                "outcome": "HELD"
                            },
                            maxlen=settings.STREAM_MAX_LEN,
                            approximate=True
                        )
                    else:
                        logger.info(
                            f"Idempotent duplicate hold detected for order {order_id}, "
                            f"booking {res_resp.booking_id}. Skipping duplicate stream event."
                        )

                except HTTPException as e:
                    logger.warning(f"Reservation hold failed for order {order_id}, item {item['item_id']}: {e.detail}")
                    action_outcomes.append({
                        "item_id": item["item_id"],
                        "product_id": item["concord_product_id"],
                        "status": "REJECTED_STOCK",
                        "error": e.detail
                    })
                    # Emit rejection to stream
                    r = await redis_manager.connect()
                    await r.xadd(
                        stream_key(),
                        {
                            "event_type": "WOOCOMMERCE_RESERVATION_REJECTED",
                            "order_id": str(order_id),
                            "product_id": item["concord_product_id"],
                            "quantity": str(item["quantity"]),
                            "timestamp": now_str,
                            "outcome": "INSUFFICIENT_STOCK",
                            "error": str(e.detail)
                        },
                        maxlen=settings.STREAM_MAX_LEN,
                        approximate=True
                    )

            # Persist or update order record
            await orders_coll.update_one(
                {"order_id": order_id},
                {"$set": {
                    "order_id": order_id,
                    "status": order_status,
                    "booking_ids": booking_ids,
                    "items": resolved_items,
                    "outcomes": action_outcomes,
                    "updated_at": now_str
                }, "$setOnInsert": {
                    "created_at": now_str
                }},
                upsert=True
            )

        # --- B. ORDER PAYMENT COMPLETED: CONFIRM RESERVATION ---
        elif order_status in ("processing", "completed"):
            bookings_coll = get_bookings_collection()

            # If no booking exists yet (e.g. checkout hook was bypassed directly to completed webhook),
            # attempt immediate reservation first
            if not booking_ids and resolved_items:
                for item in resolved_items:
                    idemp_key = f"wc-order-{order_id}-item-{item['item_id']}"
                    try:
                        res_resp = await ReservationService.create_reservation(ReservationRequest(
                            product_id=item["concord_product_id"],
                            quantity=item["quantity"],
                            idempotency_key=idemp_key,
                            hold_ttl_seconds=60
                        ))
                        booking_ids.append(res_resp.booking_id)
                    except HTTPException as e:
                        logger.error(f"Failed direct hold for completed order {order_id}: {e.detail}")

            for b_id in booking_ids:
                b_doc = await bookings_coll.find_one({"_id": b_id})
                if not b_doc:
                    continue

                if b_doc["status"] == "CONFIRMED":
                    action_outcomes.append({"booking_id": b_id, "status": "CONFIRMED", "message": "Already confirmed"})
                    continue

                if b_doc["status"] == "HELD":
                    conf_res = await ReservationService.confirm_booking(b_id)
                    action_outcomes.append({"booking_id": b_id, "status": "CONFIRMED", "message": conf_res.message})

                    r = await redis_manager.connect()
                    await r.xadd(
                        stream_key(),
                        {
                            "event_type": "WOOCOMMERCE_ORDER_CONFIRMED",
                            "order_id": str(order_id),
                            "booking_id": b_id,
                            "product_id": b_doc["product_id"],
                            "quantity": str(b_doc["quantity"]),
                            "timestamp": now_str,
                            "outcome": "CONFIRMED"
                        },
                        maxlen=settings.STREAM_MAX_LEN,
                        approximate=True
                    )

                elif b_doc["status"] in ("EXPIRED", "CANCELLED"):
                    # Distributed edge case: Customer paid after hold expired!
                    # Compensation protocol: attempt re-reservation and immediate confirmation
                    logger.warning(f"Order {order_id} booking {b_id} expired prior to payment! Initiating compensation...")
                    comp_key = f"wc-compensate-{order_id}-{b_id}"
                    try:
                        re_res = await ReservationService.create_reservation(ReservationRequest(
                            product_id=b_doc["product_id"],
                            quantity=b_doc["quantity"],
                            idempotency_key=comp_key,
                            hold_ttl_seconds=30
                        ))
                        await ReservationService.confirm_booking(re_res.booking_id)
                        action_outcomes.append({
                            "booking_id": re_res.booking_id,
                            "status": "COMPENSATED_CONFIRMED",
                            "message": "Hold had expired but units re-reserved and confirmed successfully."
                        })
                    except Exception as comp_err:
                        logger.critical(f"OVERSELL ALERT for WooCommerce order {order_id}: stock exhausted while holding! {comp_err}")
                        action_outcomes.append({
                            "booking_id": b_id,
                            "status": "OVERSELL_RISK_DETECTED",
                            "error": str(comp_err)
                        })
                        r = await redis_manager.connect()
                        await r.xadd(
                            stream_key(),
                            {
                                "event_type": "WOOCOMMERCE_OVERSELL_ALERT",
                                "order_id": str(order_id),
                                "product_id": b_doc["product_id"],
                                "timestamp": now_str,
                                "outcome": "OVERSELL_RISK_DETECTED",
                                "error": f"Payment captured on expired hold; inventory depleted ({comp_err})"
                            },
                            maxlen=settings.STREAM_MAX_LEN,
                            approximate=True
                        )

            await orders_coll.update_one(
                {"order_id": order_id},
                {"$set": {
                    "status": order_status,
                    "booking_ids": booking_ids,
                    "outcomes": action_outcomes,
                    "updated_at": now_str
                }}
            )

        # --- C. ORDER CANCELLED / FAILED / REFUNDED: RELEASE RESERVATION HOLD ---
        elif order_status in ("cancelled", "failed", "refunded", "trash"):
            bookings_coll = get_bookings_collection()
            for b_id in booking_ids:
                b_doc = await bookings_coll.find_one({"_id": b_id})
                if b_doc and b_doc["status"] == "HELD":
                    cancel_res = await ReservationService.cancel_booking(b_id)
                    action_outcomes.append({"booking_id": b_id, "status": "CANCELLED", "message": cancel_res.message})

                    r = await redis_manager.connect()
                    await r.xadd(
                        stream_key(),
                        {
                            "event_type": "WOOCOMMERCE_ORDER_CANCELLED",
                            "order_id": str(order_id),
                            "booking_id": b_id,
                            "product_id": b_doc["product_id"],
                            "quantity": str(b_doc["quantity"]),
                            "timestamp": now_str,
                            "outcome": "CANCELLED"
                        },
                        maxlen=settings.STREAM_MAX_LEN,
                        approximate=True
                    )

            await orders_coll.update_one(
                {"order_id": order_id},
                {"$set": {
                    "status": order_status,
                    "outcomes": action_outcomes,
                    "updated_at": now_str
                }}
            )

        # Mark webhook log as completed
        await webhooks_coll.update_one(
            {"_id": webhook_log_id},
            {"$set": {"status": "PROCESSED", "outcomes": action_outcomes, "updated_at": utc_now_iso()}}
        )

        return {
            "status": "SUCCESS",
            "order_id": order_id,
            "order_status": order_status,
            "delivery_id": delivery_id,
            "items_processed": len(resolved_items),
            "outcomes": action_outcomes
        }

    @staticmethod
    async def sync_stock_to_woocommerce(concord_product_id: str) -> WooCommerceSyncResult:
        """
        Pushes Concord's authoritative available stock to WooCommerce for all mapped store products.
        Supports live WooCommerce REST API v3 as well as an integrated Mock mode for offline testing.
        """
        mappings_coll = get_wc_mappings_collection()
        mapping = await mappings_coll.find_one({"concord_product_id": concord_product_id})
        if not mapping:
            return WooCommerceSyncResult(
                wc_product_id=0,
                concord_product_id=concord_product_id,
                stock_quantity=0,
                success=False,
                message="No WooCommerce mapping found for this product.",
                mode="none"
            )

        wc_pid = mapping["wc_product_id"]
        wc_var_id = mapping.get("wc_variation_id", 0)

        # Guard: Check mapping-level sync_enabled flag
        if not mapping.get("sync_enabled", True):
            return WooCommerceSyncResult(
                wc_product_id=wc_pid,
                wc_variation_id=wc_var_id,
                concord_product_id=concord_product_id,
                stock_quantity=0,
                success=False,
                message="Stock synchronization is disabled for this product mapping.",
                mode="DISABLED"
            )

        # Retrieve current available stock from Redis
        stock = await redis_manager.get_inventory(concord_product_id)
        if stock is None:
            prods_coll = get_products_collection()
            p = await prods_coll.find_one({"_id": concord_product_id})
            stock = p["initial_stock"] if p else 0

        now_str = utc_now_iso()

        # Check if live WooCommerce connection is configured
        has_credentials = bool(
            settings.WC_STORE_URL
            and settings.WC_CONSUMER_KEY
            and settings.WC_CONSUMER_SECRET
            and not settings.WC_MOCK_MODE
        )

        if not has_credentials:
            # Mock / Test Mode
            await mappings_coll.update_one(
                {"_id": mapping["_id"]},
                {"$set": {
                    "last_synced_stock": stock,
                    "last_synced_at": now_str,
                    "updated_at": now_str
                }}
            )
            # Emit sync event to stream
            r = await redis_manager.connect()
            await r.xadd(
                stream_key(),
                {
                    "event_type": "WOOCOMMERCE_STOCK_SYNCED",
                    "product_id": concord_product_id,
                    "wc_product_id": str(wc_pid),
                    "stock_quantity": str(stock),
                    "timestamp": now_str,
                    "mode": "MOCK",
                    "outcome": "SUCCESS"
                },
                maxlen=settings.STREAM_MAX_LEN,
                approximate=True
            )
            return WooCommerceSyncResult(
                wc_product_id=wc_pid,
                wc_variation_id=wc_var_id,
                concord_product_id=concord_product_id,
                stock_quantity=stock,
                success=True,
                message="Stock synchronized successfully in Mock/Test Mode.",
                mode="MOCK"
            )

        # Live WooCommerce REST API request
        target_endpoint = f"{settings.WC_STORE_URL.rstrip('/')}/wp-json/wc/v3/products/{wc_pid}"
        if wc_var_id and wc_var_id > 0:
            target_endpoint = f"{settings.WC_STORE_URL.rstrip('/')}/wp-json/wc/v3/products/{wc_pid}/variations/{wc_var_id}"

        payload = {
            "manage_stock": True,
            "stock_quantity": stock
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.put(
                    target_endpoint,
                    auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET),
                    json=payload
                )
                if resp.status_code in (200, 201):
                    await mappings_coll.update_one(
                        {"_id": mapping["_id"]},
                        {"$set": {
                            "last_synced_stock": stock,
                            "last_synced_at": now_str,
                            "updated_at": now_str
                        }}
                    )
                    r = await redis_manager.connect()
                    await r.xadd(
                        stream_key(),
                        {
                            "event_type": "WOOCOMMERCE_STOCK_SYNCED",
                            "product_id": concord_product_id,
                            "wc_product_id": str(wc_pid),
                            "stock_quantity": str(stock),
                            "timestamp": now_str,
                            "mode": "LIVE",
                            "outcome": "SUCCESS"
                        },
                        maxlen=settings.STREAM_MAX_LEN,
                        approximate=True
                    )
                    return WooCommerceSyncResult(
                        wc_product_id=wc_pid,
                        wc_variation_id=wc_var_id,
                        concord_product_id=concord_product_id,
                        stock_quantity=stock,
                        success=True,
                        message=f"Live WooCommerce stock updated to {stock}.",
                        mode="LIVE"
                    )
                else:
                    logger.error(f"Failed to update WooCommerce stock ({resp.status_code}): {resp.text}")
                    return WooCommerceSyncResult(
                        wc_product_id=wc_pid,
                        wc_variation_id=wc_var_id,
                        concord_product_id=concord_product_id,
                        stock_quantity=stock,
                        success=False,
                        message=f"WooCommerce API error: {resp.status_code} - {resp.text}",
                        mode="LIVE"
                    )
        except Exception as e:
            logger.error(f"WooCommerce API network exception: {e}")
            return WooCommerceSyncResult(
                wc_product_id=wc_pid,
                wc_variation_id=wc_var_id,
                concord_product_id=concord_product_id,
                stock_quantity=stock,
                success=False,
                message=f"Network error connecting to WooCommerce: {str(e)}",
                mode="LIVE"
            )

    @staticmethod
    async def sync_all_mapped_products() -> List[WooCommerceSyncResult]:
        mappings_coll = get_wc_mappings_collection()
        results = []
        cursor = mappings_coll.find({"sync_enabled": True})
        async for m in cursor:
            res = await WooCommerceService.sync_stock_to_woocommerce(m["concord_product_id"])
            results.append(res)
        return results

    @staticmethod
    async def get_status() -> WooCommerceStatusResponse:
        mappings_coll = get_wc_mappings_collection()
        orders_coll = get_wc_orders_collection()
        webhooks_coll = get_wc_webhooks_collection()

        mapping_count = await mappings_coll.count_documents({})
        order_count = await orders_coll.count_documents({})
        webhook_count = await webhooks_coll.count_documents({})

        has_creds = bool(settings.WC_STORE_URL and settings.WC_CONSUMER_KEY and settings.WC_CONSUMER_SECRET)

        return WooCommerceStatusResponse(
            store_url=settings.WC_STORE_URL or "Not configured (Mock / Test Mode Active)",
            connected=has_creds and not settings.WC_MOCK_MODE,
            mock_mode=settings.WC_MOCK_MODE,
            stock_sync_enabled=settings.WC_STOCK_SYNC_ENABLED,
            verify_signatures=settings.WC_VERIFY_WEBHOOK_SIGNATURE,
            total_mappings=mapping_count,
            total_tracked_orders=order_count,
            recent_webhooks_count=webhook_count
        )
