from fastapi import APIRouter
from typing import List, Dict, Any

from backend.models.schemas import (
    AnalyticsOverview,
    BookingsByStatusItem,
    ProductPerformanceItem
)
from backend.services.analytics_service import AnalyticsService
from backend.services.recovery_service import RecoveryService
from backend.db.mongo import get_db
from backend.db.indexes import inspect_indexes

router = APIRouter(prefix="/analytics", tags=["Analytics & Invariants"])

@router.get("/overview", response_model=AnalyticsOverview)
async def get_overview():
    return await AnalyticsService.get_overview()

@router.get("/bookings-by-status", response_model=List[BookingsByStatusItem])
async def get_bookings_by_status():
    return await AnalyticsService.get_bookings_by_status()

@router.get("/product-performance", response_model=List[ProductPerformanceItem])
async def get_product_performance():
    return await AnalyticsService.get_product_performance()

@router.get("/confirmed-vs-expired")
async def get_confirmed_vs_expired() -> Dict[str, Any]:
    return await AnalyticsService.get_confirmed_vs_expired()

@router.get("/operations")
async def get_operations_stats() -> Dict[str, int]:
    return await AnalyticsService.get_operation_stats()

@router.get("/invariants")
async def verify_invariants():
    """Validates that available_stock + active_holds + confirmed_units == initial_stock."""
    return await RecoveryService.verify_inventory_invariants()

@router.get("/indexes")
async def get_indexes_status():
    """Inspects all created MongoDB indexes and their definitions."""
    db = get_db()
    return await inspect_indexes(db)
