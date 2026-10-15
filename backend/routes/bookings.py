from fastapi import APIRouter, Query
from typing import List, Optional
from backend.models.schemas import BookingResponse
from backend.services.reservation_service import ReservationService

router = APIRouter(prefix="/bookings", tags=["Bookings"])

@router.get("", response_model=List[BookingResponse])
async def list_bookings(
    product_id: Optional[str] = Query(None, description="Filter by product ID"),
    status: Optional[str] = Query(None, description="Filter by status (HELD, CONFIRMED, EXPIRED, CANCELLED)"),
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0)
):
    return await ReservationService.list_bookings(
        product_id=product_id,
        status=status,
        limit=limit,
        skip=skip
    )

@router.get("/{booking_id}", response_model=BookingResponse)
async def get_booking(booking_id: str):
    return await ReservationService.get_booking(booking_id)
