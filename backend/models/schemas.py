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
