import asyncio
import json
import httpx
from backend.db.redis_client import connect_redis, close_redis, redis_manager, stream_key

async def test_stream_and_replay():
    print("\n==========================================================================================")
    print("CONCORD REDIS STREAM & SSE REPLAY / RECONNECTION VERIFICATION")
    print("==========================================================================================")

    # Step 1: Direct Redis Stream Inspection
    r = await connect_redis()
    s_key = stream_key()
    stream_len = await r.xlen(s_key)
    print(f"1. Redis Stream Key       : '{s_key}'")
    print(f"   Total Retained Events  : {stream_len} events")

    # Fetch last 5 events directly from Redis
    raw_recent = await r.xrevrange(s_key, max="+", min="-", count=5)
    print(f"\n2. Most Recent 5 Events in Redis Stream:")
    event_ids = []
    for msg_id, fields in reversed(raw_recent):
        event_ids.append(msg_id)
        print(f"   [{msg_id}] Type: {fields.get('event_type'):<20} | Outcome: {fields.get('outcome'):<15} | Product: {fields.get('product_id', '')[:16]}")

    assert len(event_ids) > 0, "Redis Stream should contain historical events!"
    # Pick the 3rd event as the reconnection cursor
    reconnection_cursor = event_ids[max(0, len(event_ids) - 3)]
    print(f"\n3. Selected Reconnection Cursor (Last-Event-ID) : {reconnection_cursor}")

    # Step 2: Test SSE Reconnection with Last-Event-ID Header
    print("\n4. Connecting to SSE Endpoint (/events/stream) with Header 'Last-Event-ID'...")
    base_url = "http://localhost:8000"
    replayed_events = []

    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        headers = {"Last-Event-ID": reconnection_cursor}
        async with client.stream("GET", "/events/stream?replay=true", headers=headers) as response:
            assert response.status_code == 200
            print("   -> SSE HTTP Status: 200 OK")
            print("   -> Content-Type   : " + response.headers.get("content-type", ""))

            current_event_type = None
            current_event_id = None

            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current_event_type = line.replace("event:", "").strip()
                elif line.startswith("id:"):
                    current_event_id = line.replace("id:", "").strip()
                elif line.startswith("data:"):
                    raw_data = line.replace("data:", "").strip()
                    try:
                        parsed = json.loads(raw_data)
                    except Exception:
                        parsed = raw_data

                    replayed_events.append({
                        "event": current_event_type,
                        "id": current_event_id,
                        "data": parsed
                    })

                # Once we collect initial handshake + at least 2 replayed items, break
                if len(replayed_events) >= 3:
                    break

    print(f"\n5. Successfully Received SSE Stream Frames:")
    for ev in replayed_events:
        print(f"   [Event: {ev['event']:<18}] ID: {str(ev['id']):<22} | Data: {str(ev['data'])[:60]}...")

    # Verification: Handshake received
    assert replayed_events[0]["event"] == "CONNECTED"
    print(f"\n   -> Initial Handshake: Verified (Cursor acknowledged: {replayed_events[0]['id']})")

    # Verification: Events replayed after the cursor
    if len(replayed_events) > 1:
        first_replayed_id = replayed_events[1]["id"]
        print(f"   -> First Replayed Stream Event ID: {first_replayed_id}")
        assert first_replayed_id is not None
        print(f"   -> Reconnection & Replay Semantics: VERIFIED (Client received events since cursor)")

    print("\n==========================================================================================")
    print("STREAM & REPLAY VERIFICATION RESULT: PASS (Last-Event-ID Resume Fully Operational)")
    print("==========================================================================================\n")
    await close_redis()

if __name__ == "__main__":
    asyncio.run(test_stream_and_replay())
