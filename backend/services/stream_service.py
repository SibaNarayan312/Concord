import asyncio
import json
from typing import AsyncGenerator, Optional, Dict, Any, List
import logging

from backend.config import settings
from backend.db.redis_client import redis_manager, stream_key

logger = logging.getLogger("concord.stream")

class StreamService:
    @staticmethod
    def _format_stream_event(msg_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Formats and projects stream fields into a standardized, backward-compatible event payload."""
        qty = None
        if fields.get("quantity"):
            try:
                qty = int(fields["quantity"])
            except (ValueError, TypeError):
                qty = None

        stock_qty = None
        if fields.get("stock_quantity"):
            try:
                stock_qty = int(fields["stock_quantity"])
            except (ValueError, TypeError):
                stock_qty = None

        return {
            "event_id": msg_id,
            "event_type": fields.get("event_type", "UNKNOWN"),
            "product_id": fields.get("product_id", ""),
            "booking_id": fields.get("booking_id", ""),
            "quantity": qty,
            "timestamp": fields.get("timestamp", ""),
            "outcome": fields.get("outcome", ""),
            "order_id": fields.get("order_id"),
            "error": fields.get("error"),
            "stock_quantity": stock_qty,
            "mode": fields.get("mode"),
            "data": fields
        }

    @staticmethod
    async def get_recent_events(count: int = 50) -> List[Dict[str, Any]]:
        """Returns the most recent events from the Redis Stream."""
        r = await redis_manager.connect()
        # xrevrange returns in reverse chronological order
        raw_events = await r.xrevrange(stream_key(), max="+", min="-", count=count)
        events = []
        for msg_id, fields in reversed(raw_events):
            events.append(StreamService._format_stream_event(msg_id, fields))
        return events

    @staticmethod
    async def sse_event_generator(
        last_event_id: Optional[str] = None,
        replay_history: bool = True
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generates Server-Sent Events from the Redis Stream.
        Supports:
        - Last-Event-ID replay from client reconnection.
        - Automatic cursor progression.
        - Periodic keep-alive comments.
        """
        r = await redis_manager.connect()
        s_key = stream_key()

        # Determine starting cursor
        current_cursor = "0-0"
        if last_event_id and last_event_id.strip():
            current_cursor = last_event_id.strip()
        elif not replay_history:
            # Only listen for future events
            current_cursor = "$"

        logger.info(f"SSE client connected with cursor: {current_cursor}")

        # Send initial connected banner
        yield {
            "event": "CONNECTED",
            "id": current_cursor,
            "data": json.dumps({
                "message": "Connected to Concord live contention stream",
                "cursor": current_cursor
            })
        }

        # If replaying from past cursor, stream existing items first
        if current_cursor != "$":
            try:
                history_resp = await r.xread({s_key: current_cursor}, count=100, block=500)
                if history_resp:
                    for stream_name, messages in history_resp:
                        for msg_id, fields in messages:
                            current_cursor = msg_id
                            yield {
                                "event": fields.get("event_type", "LIFECYCLE_EVENT"),
                                "id": msg_id,
                                "data": json.dumps(StreamService._format_stream_event(msg_id, fields))
                            }
            except Exception as e:
                logger.warning(f"Error during initial event replay: {e}")

        # Now continuously listen for new events using xread block
        idle_ticks = 0
        while True:
            try:
                read_cursor = current_cursor if current_cursor != "$" else "$"
                res = await r.xread({s_key: read_cursor}, count=50, block=2000)

                if res:
                    idle_ticks = 0
                    for stream_name, messages in res:
                        for msg_id, fields in messages:
                            current_cursor = msg_id
                            yield {
                                "event": fields.get("event_type", "LIFECYCLE_EVENT"),
                                "id": msg_id,
                                "data": json.dumps(StreamService._format_stream_event(msg_id, fields))
                            }
                else:
                    idle_ticks += 1
                    # Send keepalive ping every 10 seconds (5 * 2s block)
                    if idle_ticks >= 5:
                        idle_ticks = 0
                        yield {
                            "event": "PING",
                            "data": json.dumps({"status": "keepalive"})
                        }

            except asyncio.CancelledError:
                logger.info("SSE client disconnected.")
                break
            except Exception as e:
                logger.error(f"SSE loop error: {e}")
                await asyncio.sleep(1.0)
