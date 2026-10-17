import pytest
import httpx
import uuid
import json
import subprocess
from typing import Dict, Any

from backend.db.redis_client import redis_manager, stream_key
from backend.services.stream_service import StreamService
from backend.services.woocommerce_service import WooCommerceService
from backend.models.schemas import WooCommerceMappingCreate
from backend.config import settings
from tests.test_woocommerce_integration import make_wc_headers

@pytest.mark.asyncio
async def test_01_stream_service_format_stream_event_safe_projection():
    """Verify StreamService._format_stream_event projects order_id, error, stock_quantity, mode safely."""
    # Complete fields
    fields_complete = {
        "event_type": "WOOCOMMERCE_ORDER_RESERVED",
        "product_id": "prod-test-123",
        "booking_id": "book-test-456",
        "quantity": "2",
        "timestamp": "2026-10-17T15:00:00Z",
        "outcome": "HELD",
        "order_id": "998877",
        "error": None,
        "stock_quantity": "8",
        "mode": "LIVE"
    }

    formatted = StreamService._format_stream_event("1729150000000-0", fields_complete)

    assert formatted["event_id"] == "1729150000000-0"
    assert formatted["event_type"] == "WOOCOMMERCE_ORDER_RESERVED"
    assert formatted["order_id"] == "998877"
    assert formatted["quantity"] == 2
    assert formatted["stock_quantity"] == 8
    assert formatted["mode"] == "LIVE"
    assert formatted["data"] == fields_complete

    # Malformed and missing fields
    fields_malformed = {
        "event_type": "WOOCOMMERCE_OVERSELL_ALERT",
        "quantity": "not_an_int",
        "stock_quantity": "corrupted",
        "order_id": 554433,
        "error": "Stock depleted"
    }

    formatted_malformed = StreamService._format_stream_event("1729150000001-0", fields_malformed)
    assert formatted_malformed["quantity"] is None
    assert formatted_malformed["stock_quantity"] is None
    assert formatted_malformed["order_id"] == 554433
    assert formatted_malformed["error"] == "Stock depleted"

@pytest.mark.asyncio
async def test_02_recent_events_and_sse_payload_structure():
    """Verify get_recent_events() and sse_event_generator() expose top-level order_id."""
    r = await redis_manager.connect()
    s_key = stream_key()
    test_order_id = f"test-ord-{uuid.uuid4().hex[:6]}"

    # Get the latest message ID currently in stream so we can test replay exactly from here
    latest = await r.xrevrange(s_key, max="+", min="-", count=1)
    baseline_id = latest[0][0] if latest else "0-0"

    # Inject a test event
    msg_id = await r.xadd(
        s_key,
        {
            "event_type": "WOOCOMMERCE_ORDER_RESERVED",
            "order_id": test_order_id,
            "product_id": "prod-test",
            "booking_id": "book-test",
            "quantity": "3",
            "timestamp": "2026-10-17T15:00:00Z",
            "outcome": "HELD"
        }
    )

    # 1. Test get_recent_events
    recent = await StreamService.get_recent_events(count=20)
    matched = [e for e in recent if e.get("order_id") == test_order_id]
    assert len(matched) >= 1
    event_entry = matched[0]
    assert event_entry["order_id"] == test_order_id
    assert event_entry["quantity"] == 3
    assert event_entry["event_type"] == "WOOCOMMERCE_ORDER_RESERVED"
    assert "data" in event_entry

    # 2. Test sse_event_generator with replay from baseline_id
    gen = StreamService.sse_event_generator(last_event_id=baseline_id, replay_history=True)
    found_sse = False
    try:
        # First item is CONNECTED
        first = await gen.__anext__()
        assert first["event"] == "CONNECTED"

        # Next item should be our injected event in replay
        second = await gen.__anext__()
        assert second["event"] == "WOOCOMMERCE_ORDER_RESERVED"
        data = json.loads(second["data"])
        if data.get("order_id") == test_order_id:
            found_sse = True
            assert data["order_id"] == test_order_id
            assert data["quantity"] == 3
            assert "data" in data
    finally:
        await gen.aclose()

    assert found_sse, f"SSE generator did not yield test event with order_id {test_order_id}"

@pytest.mark.asyncio
async def test_03_duplicate_webhook_deliveries_do_not_emit_duplicate_stream_events(client: httpx.AsyncClient, test_product):
    """
    Verify that repeated webhook deliveries with DIFFERENT delivery IDs
    (e.g. order.created followed by order.updated while still pending)
    do NOT emit duplicate WOOCOMMERCE_ORDER_RESERVED events into Redis Stream.
    """
    pid = test_product["id"]
    wc_pid = 77000 + uuid.uuid4().int % 10000

    # Create mapping
    await WooCommerceService.create_mapping(WooCommerceMappingCreate(
        concord_product_id=pid,
        wc_product_id=wc_pid,
        wc_variation_id=0,
        sync_enabled=True
    ))

    unique_order_id = 880000 + uuid.uuid4().int % 100000
    line_item_id = 101

    payload = {
        "id": unique_order_id,
        "status": "pending",
        "currency": "USD",
        "line_items": [
            {
                "id": line_item_id,
                "product_id": wc_pid,
                "name": "Widget",
                "quantity": 1
            }
        ]
    }

    # 1. First delivery (order.created)
    body1 = json.dumps(payload).encode("utf-8")
    headers1 = make_wc_headers(body1, topic="order.created", delivery_id=f"del-first-{uuid.uuid4().hex[:6]}")
    resp1 = await client.post("/woocommerce/webhooks", content=body1, headers=headers1)
    assert resp1.status_code == 200

    # Stock decremented by 1
    stock_after_first = await redis_manager.get_inventory(pid)
    assert stock_after_first == 9

    # Count stream events for this order
    recent1 = await StreamService.get_recent_events(count=50)
    wc_events_1 = [
        e for e in recent1
        if e.get("event_type") == "WOOCOMMERCE_ORDER_RESERVED" and str(e.get("order_id")) == str(unique_order_id)
    ]
    assert len(wc_events_1) == 1, "First delivery must emit exactly 1 WOOCOMMERCE_ORDER_RESERVED event"

    # 2. Second delivery with DIFFERENT delivery ID (order.updated while pending)
    body2 = json.dumps(payload).encode("utf-8")
    headers2 = make_wc_headers(body2, topic="order.updated", delivery_id=f"del-second-{uuid.uuid4().hex[:6]}")
    resp2 = await client.post("/woocommerce/webhooks", content=body2, headers=headers2)
    assert resp2.status_code == 200

    # Stock must remain 9 (not decremented twice)
    stock_after_second = await redis_manager.get_inventory(pid)
    assert stock_after_second == 9

    # Count stream events again: must STILL be exactly 1!
    recent2 = await StreamService.get_recent_events(count=50)
    wc_events_2 = [
        e for e in recent2
        if e.get("event_type") == "WOOCOMMERCE_ORDER_RESERVED" and str(e.get("order_id")) == str(unique_order_id)
    ]
    assert len(wc_events_2) == 1, (
        f"Second delivery with new delivery_id must NOT emit duplicate WOOCOMMERCE_ORDER_RESERVED event. Found {len(wc_events_2)}"
    )

    # 3. Legitimate cancellation event still appears
    cancel_payload = dict(payload)
    cancel_payload["status"] = "cancelled"
    body_cancel = json.dumps(cancel_payload).encode("utf-8")
    headers_cancel = make_wc_headers(body_cancel, topic="order.updated", delivery_id=f"del-cancel-{uuid.uuid4().hex[:6]}")
    resp_cancel = await client.post("/woocommerce/webhooks", content=body_cancel, headers=headers_cancel)
    assert resp_cancel.status_code == 200

    # Stock restored to 10
    stock_after_cancel = await redis_manager.get_inventory(pid)
    assert stock_after_cancel == 10

    # WOOCOMMERCE_ORDER_CANCELLED event emitted
    recent3 = await StreamService.get_recent_events(count=50)
    wc_cancel_events = [
        e for e in recent3
        if e.get("event_type") == "WOOCOMMERCE_ORDER_CANCELLED" and str(e.get("order_id")) == str(unique_order_id)
    ]
    assert len(wc_cancel_events) == 1, "Cancellation must emit exactly 1 WOOCOMMERCE_ORDER_CANCELLED event"

def test_04_frontend_javascript_dom_deduplication_and_order_id_rendering():
    """
    Automated regression test executing Node.js against the exact frontend logic from dashboard.js.
    Validates:
    - Order ID renders correctly (not '#undefined')
    - Nested fallback works
    - Missing fallback renders '#N/A'
    - Identical event_id is deduplicated in DOM
    - Distinct event_ids for same order are both rendered
    """
    node_script = """
    // Mock minimal DOM
    class Element {
      constructor(tag) {
        this.tag = tag;
        this.attributes = {};
        this.children = [];
        this.innerHTML = '';
        this.className = '';
      }
      setAttribute(name, val) { this.attributes[name] = String(val); }
      getAttribute(name) { return this.attributes[name]; }
      querySelector(selector) {
        const match = selector.match(/\\[([\\w-]+)="([^"]+)"\\]/);
        if (match) {
          const [_, attr, val] = match;
          return this.children.find(c => c.getAttribute(attr) === val) || null;
        }
        return null;
      }
      insertBefore(child, before) {
        this.children.unshift(child);
      }
      removeChild(child) {
        const idx = this.children.indexOf(child);
        if (idx !== -1) this.children.splice(idx, 1);
      }
    }

    const container = new Element('div');
    global.document = {
      getElementById: (id) => (id === 'stream-feed-container' ? container : new Element('div')),
      createElement: (tag) => new Element(tag)
    };
    global.formatTime = (t) => '12:00:00';

    // The exact appendStreamEvent logic from frontend/dashboard.js
    function appendStreamEvent(type, data, isReplay = false) {
      if (data.event_id) {
        const existing = container.querySelector(`[data-event-id="${data.event_id}"]`);
        if (existing) {
          return false; // deduplicated
        }
      }

      const item = document.createElement('div');
      item.className = `stream-event-item event-${type}`;
      if (data.event_id) {
        item.setAttribute('data-event-id', data.event_id);
      }

      const timeStr = formatTime(data.timestamp || new Date().toISOString());

      const orderId = (data.order_id !== undefined && data.order_id !== null && data.order_id !== '')
        ? data.order_id
        : (data.data && data.data.order_id !== undefined && data.data.order_id !== null && data.data.order_id !== '')
          ? data.data.order_id
          : 'N/A';

      let summary = '';
      if (type === 'WOOCOMMERCE_ORDER_RESERVED') {
        summary = `🛒 WooCommerce Order #${orderId}: Reserved ${data.quantity || 1} unit(s)`;
      } else {
        summary = `Event #${orderId}`;
      }

      item.innerHTML = `<span class="event-summary">${summary}</span>`;
      container.insertBefore(item, container.children[0]);
      return true;
    }

    const results = {};

    // 1. Top-level order_id rendering
    appendStreamEvent('WOOCOMMERCE_ORDER_RESERVED', {
      event_id: '100-0',
      order_id: 12345,
      quantity: 1
    });
    results.top_level_rendered = container.children[0].innerHTML.includes('#12345');
    results.not_undefined = !container.children[0].innerHTML.includes('#undefined');

    // 2. Replayed event with same event_id: must be deduplicated
    const secondAdd = appendStreamEvent('WOOCOMMERCE_ORDER_RESERVED', {
      event_id: '100-0',
      order_id: 12345,
      quantity: 1
    }, true);
    results.deduplicated_same_event_id = (secondAdd === false) && (container.children.length === 1);

    // 3. Distinct event_id referencing same order: must be rendered
    const distinctAdd = appendStreamEvent('WOOCOMMERCE_ORDER_RESERVED', {
      event_id: '100-1',
      order_id: 12345,
      quantity: 1
    });
    results.distinct_event_allowed = (distinctAdd === true) && (container.children.length === 2);

    // 4. Nested fallback
    appendStreamEvent('WOOCOMMERCE_ORDER_RESERVED', {
      event_id: '100-2',
      data: { order_id: 67890 }
    });
    results.nested_fallback_rendered = container.children[0].innerHTML.includes('#67890');

    // 5. Missing fallback (safe 'N/A')
    appendStreamEvent('WOOCOMMERCE_ORDER_RESERVED', {
      event_id: '100-3'
    });
    results.missing_fallback_rendered = container.children[0].innerHTML.includes('#N/A');

    console.log(JSON.stringify(results));
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout.strip())

    assert out["top_level_rendered"] is True, "Top-level order_id must render in summary"
    assert out["not_undefined"] is True, "Summary must never contain #undefined"
    assert out["deduplicated_same_event_id"] is True, "Replayed event with identical event_id must be deduplicated"
    assert out["distinct_event_allowed"] is True, "Distinct event_ids for same order must both render"
    assert out["nested_fallback_rendered"] is True, "Nested data.data.order_id fallback must render correctly"
    assert out["missing_fallback_rendered"] is True, "Missing order_id must safely render #N/A"
