from fastapi import APIRouter, Request, Query
from sse_starlette.sse import EventSourceResponse
from typing import Optional, List, Dict, Any

from backend.services.stream_service import StreamService

router = APIRouter(prefix="/events", tags=["Live Contention Stream"])

@router.get("/stream")
async def event_stream(
    request: Request,
    replay: bool = Query(True, description="Whether to replay recent events on connection")
):
    """
    Live Server-Sent Events (SSE) endpoint powered by Redis Streams.
    Supports automatic reconnection with 'Last-Event-ID' header.
    """
    last_event_id = request.headers.get("Last-Event-ID")
    return EventSourceResponse(
        StreamService.sse_event_generator(last_event_id=last_event_id, replay_history=replay)
    )

@router.get("/recent")
async def get_recent_events(
    count: int = Query(50, ge=1, le=500, description="Max recent events to fetch")
) -> List[Dict[str, Any]]:
    return await StreamService.get_recent_events(count=count)
