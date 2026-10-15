from fastapi import APIRouter, status
from backend.models.schemas import (
    ReservationRequest,
    ReservationResponse,
    BookingActionResponse,
    BookingResponse
)
from backend.services.reservation_service import ReservationService

router = APIRouter(prefix="/reservations", tags=["Reservations"])

@router.post("", response_model=ReservationResponse, status_code=status.HTTP_201_CREATED)
async def create_reservation(req: ReservationRequest):
    return await ReservationService.create_reservation(req)

@router.get("/{booking_id}", response_model=BookingResponse)
async def get_reservation_hold(booking_id: str):
    return await ReservationService.get_booking(booking_id)

@router.post("/{booking_id}/confirm", response_model=BookingActionResponse)
async def confirm_reservation(booking_id: str):
    return await ReservationService.confirm_booking(booking_id)

@router.post("/{booking_id}/cancel", response_model=BookingActionResponse)
async def cancel_reservation(booking_id: str):
    return await ReservationService.cancel_booking(booking_id)
