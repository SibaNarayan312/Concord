import json
import logging
import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Request, Header, HTTPException, status, Query

from backend.models.schemas import (
    WooCommerceMappingCreate,
    WooCommerceMappingResponse,
    WooCommerceSyncResult,
    WooCommerceStatusResponse,
    WooCommerceOrderSimulation
)
from backend.services.woocommerce_service import WooCommerceService
from backend.db.mongo import get_wc_orders_collection

logger = logging.getLogger("concord.routes.woocommerce")

router = APIRouter(prefix="/woocommerce", tags=["WooCommerce Integration"])

@router.get("/status", response_model=WooCommerceStatusResponse)
async def get_woocommerce_status():
    """Returns store integration status, mock mode indicator, and mapping metrics."""
    return await WooCommerceService.get_status()

@router.get("/mappings", response_model=List[WooCommerceMappingResponse])
async def list_product_mappings():
    """Lists all active mappings between WooCommerce products and Concord inventory records."""
    return await WooCommerceService.list_mappings()

@router.post("/mappings", response_model=WooCommerceMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_mapping(payload: WooCommerceMappingCreate):
    """Establishes or updates an authoritative product mapping."""
    return await WooCommerceService.create_mapping(payload)

@router.delete("/mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_mapping(mapping_id: str):
    """Removes a product mapping."""
    await WooCommerceService.delete_mapping(mapping_id)

@router.post("/mappings/auto-map", response_model=List[WooCommerceMappingResponse])
async def auto_map_demo_products():
    """Convenience helper: auto-maps existing Concord products to virtual WooCommerce IDs."""
    return await WooCommerceService.auto_map_first_products()

@router.post("/webhooks")
async def handle_woocommerce_webhook(
    request: Request,
    x_wc_webhook_signature: Optional[str] = Header(None),
    x_wc_webhook_topic: Optional[str] = Header("order.updated"),
    x_wc_webhook_delivery_id: Optional[str] = Header(None)
):
    """
    Primary webhook receiver for WooCommerce order lifecycle events.
    Verifies HMAC-SHA256 signature, deduplicates delivery IDs, and coordinates reservations.
    """
    raw_body = await request.body()

    # 1. Signature Verification
    is_valid = WooCommerceService.verify_webhook_signature(raw_body, x_wc_webhook_signature)
    if not is_valid:
        logger.warning(f"Unauthorized WooCommerce webhook received: signature verification failed.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC-SHA256 webhook signature."
        )

    # 2. Parse payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {str(e)}"
        )

    # 3. Process Order Event
    result = await WooCommerceService.process_order_webhook(
        payload=payload,
        topic=x_wc_webhook_topic or "order.updated",
        delivery_id=x_wc_webhook_delivery_id
    )
    return result

@router.post("/sync/{concord_product_id}", response_model=WooCommerceSyncResult)
async def sync_single_product_stock(concord_product_id: str):
    """Pushes the current authoritative Concord stock for a single product to WooCommerce."""
    return await WooCommerceService.sync_stock_to_woocommerce(concord_product_id)

@router.post("/sync-all", response_model=List[WooCommerceSyncResult])
async def sync_all_products_stock():
    """Pushes Concord stock for all mapped products to WooCommerce."""
    return await WooCommerceService.sync_all_mapped_products()

@router.get("/orders")
async def list_woocommerce_orders(limit: int = Query(25, ge=1, le=100)):
    """Lists tracked WooCommerce orders and their linked Concord bookings."""
    orders_coll = get_wc_orders_collection()
    cursor = orders_coll.find({}).sort("updated_at", -1).limit(limit)
    orders = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        orders.append(doc)
    return orders

@router.post("/simulate-checkout")
async def simulate_woocommerce_checkout(payload: WooCommerceOrderSimulation):
    """
    Simulation harness: creates a mock WooCommerce order, sends it through the checkout
    hold reservation flow, and optionally simulates immediate payment confirmation.
    Used for browser dashboard testing and end-to-end integration demonstration.
    """
    sim_order_id = int(str(uuid.uuid4().int)[:7])
    item_id = int(str(uuid.uuid4().int)[:5])

    order_payload = {
        "id": sim_order_id,
        "status": "pending",
        "currency": "USD",
        "billing": {"email": payload.customer_email},
        "line_items": [
            {
                "id": item_id,
                "product_id": payload.wc_product_id,
                "variation_id": payload.wc_variation_id,
                "name": f"Simulated Product {payload.wc_product_id}",
                "quantity": payload.quantity
            }
        ]
    }

    # Step 1: Simulate order creation / pending hold
    hold_result = await WooCommerceService.process_order_webhook(
        payload=order_payload,
        topic="order.created",
        delivery_id=f"sim-del-hold-{uuid.uuid4().hex[:6]}"
    )

    confirm_result = None
    if payload.auto_complete_payment:
        # Step 2: Simulate payment confirmation (order transitions to 'processing')
        order_payload["status"] = "processing"
        confirm_result = await WooCommerceService.process_order_webhook(
            payload=order_payload,
            topic="order.updated",
            delivery_id=f"sim-del-conf-{uuid.uuid4().hex[:6]}"
        )

    return {
        "simulation_order_id": sim_order_id,
        "hold_step": hold_result,
        "confirm_step": confirm_result
    }
