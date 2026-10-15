from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, description="Product display name")
    description: Optional[str] = Field(None, max_length=500, description="Optional description")
    initial_stock: int = Field(..., ge=0, description="Initial non-negative inventory count")

class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)

class ProductResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    initial_stock: int
    available_stock: int
    active_holds: int = 0
    confirmed_units: int = 0
    created_at: str
    updated_at: str

class ReservationRequest(BaseModel):
    product_id: str = Field(..., description="ID of product to reserve")
    quantity: int = Field(1, gt=0, le=100, description="Units to reserve (1 to 100)")
    idempotency_key: str = Field(..., min_length=1, max_length=128, description="Unique client-generated idempotency key")
    hold_ttl_seconds: Optional[int] = Field(None, ge=1, le=3600, description="Custom hold duration in seconds")

class ReservationResponse(BaseModel):
    booking_id: str
    product_id: str
    quantity: int
    status: str
    expires_at: str
    idempotency_key: str
    message: str
    cached: bool = False
    remaining_stock: Optional[int] = None

class BookingActionResponse(BaseModel):
    booking_id: str
    product_id: str
    quantity: int
    status: str
    message: str

class BookingResponse(BaseModel):
    id: str
    product_id: str
    quantity: int
    status: str
    idempotency_key: Optional[str] = None
    created_at: str
    updated_at: str
    expires_at: Optional[str] = None
    confirmed_at: Optional[str] = None

class BookingsByStatusItem(BaseModel):
    status: str
    count: int
    total_quantity: int

class ProductPerformanceItem(BaseModel):
    product_id: str
    product_name: str
    initial_stock: int
    available_stock: int
    active_holds: int
    confirmed_count: int
    expired_count: int
    cancelled_count: int
    total_units_sold: int

class AnalyticsOverview(BaseModel):
    total_products: int
    total_initial_inventory: int
    total_bookings: int
    active_holds: int
    confirmed_bookings: int
    expired_bookings: int
    cancelled_bookings: int
    total_units_confirmed: int
    inventory_invariant_satisfied: bool

class StreamEvent(BaseModel):
    event_id: str
    event_type: str
    product_id: str
    booking_id: Optional[str] = None
    quantity: Optional[int] = None
    timestamp: str
    outcome: str
    data: Dict[str, Any] = {}

class WooCommerceMappingCreate(BaseModel):
    concord_product_id: str = Field(..., description="Target Concord product UUID")
    wc_product_id: int = Field(..., ge=1, description="WooCommerce Product ID")
    wc_variation_id: int = Field(0, ge=0, description="Optional WooCommerce Variation ID (0 for simple product)")
    sku: Optional[str] = Field(None, max_length=64, description="Optional product SKU")
    sync_enabled: bool = Field(True, description="Whether stock sync is active for this product")

class WooCommerceMappingResponse(BaseModel):
    id: str
    concord_product_id: str
    concord_product_name: Optional[str] = None
    wc_product_id: int
    wc_variation_id: int = 0
    sku: Optional[str] = None
    sync_enabled: bool = True
    concord_available_stock: Optional[int] = None
    last_synced_stock: Optional[int] = None
    last_synced_at: Optional[str] = None
    created_at: str
    updated_at: str

class WooCommerceSyncResult(BaseModel):
    wc_product_id: int
    wc_variation_id: int = 0
    concord_product_id: str
    stock_quantity: int
    success: bool
    message: str
    mode: str

class WooCommerceStatusResponse(BaseModel):
    store_url: str
    connected: bool
    mock_mode: bool
    stock_sync_enabled: bool
    verify_signatures: bool
    total_mappings: int
    total_tracked_orders: int
    recent_webhooks_count: int

class WooCommerceOrderSimulation(BaseModel):
    wc_product_id: int = Field(..., description="WooCommerce product ID to simulate purchase for")
    wc_variation_id: int = Field(0, description="Optional variation ID")
    quantity: int = Field(1, ge=1, le=100, description="Quantity to purchase")
    auto_complete_payment: bool = Field(True, description="Whether to simulate payment confirmation immediately")
    customer_email: Optional[str] = Field("shopper@example.com", description="Simulated customer email")
