# Concord Technical Verification & Evidence Report
**Review 1 & Review 2 Empirical Audit**

**Date**: 2026-10-14  
**Environment**: Windows 11 (64-bit), Python 3.11.5, Docker 29.8.0, MongoDB 7.0, Redis 7.2 Alpine  
**Service Endpoint**: `http://localhost:8000` (FastAPI + Vanilla JS Dashboard)

---

## 1. Executive Summary

All core functional and non-functional requirements for Concord have been empirically verified on live infrastructure without resetting demo data or rewriting existing features:
1. **Automated Test Suite**: 13 of 13 pytest tests passed in 5.42 seconds.
2. **High-Contention Concurrency**: Under 100 simultaneous requests competing for 10 units, exactly 10 units were allocated across 10 successful requests and 90 requests were rejected with `400 Bad Request`.
3. **Inventory Invariant**: Verified across all 25 catalog products in both MongoDB and Redis state: `Available (Redis) + Active Holds (Mongo) + Confirmed (Mongo) == Initial Stock (Mongo)`. Zero overselling was detected.
4. **Exactly-Once Idempotency**: Submitting identical reservation requests with the same idempotency key returned the cached response (`cached: True`), did not decrement stock a second time, and created exactly one durable document in MongoDB. Attempting to reuse the key with a mutated quantity triggered `409 Conflict`.
5. **Redis Stream & SSE Replay**: The stream currently holds 725 retained events. Reconnecting via `Last-Event-ID: 1791961418236-0` correctly replayed subsequent events in order with cursor progression.
6. **Data Parity Audit**: 100% consistency verified between `/analytics/overview` dashboard metrics and raw MongoDB collection counts.

---

## 2. Automated Test Suite (pytest)

### Command
```powershell
.\.venv\Scripts\python -m pytest -v tests/
```

### Verbatim Terminal Output
```
============================= test session starts =============================
platform win32 -- Python 3.11.5, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\HP\OneDrive\Desktop\Concord\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\HP\OneDrive\Desktop\Concord
configfile: pytest.ini
plugins: anyio-4.15.1, asyncio-1.4.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=session, asyncio_default_test_loop_scope=function
collecting ... collected 13 items

tests/test_analytics_aggregations.py::test_analytics_endpoints_and_aggregations PASSED [  7%]
tests/test_events_sse.py::test_redis_streams_lifecycle_events PASSED     [ 15%]
tests/test_events_sse.py::test_recent_events_endpoint PASSED             [ 23%]
tests/test_health.py::test_health_check_healthy PASSED                   [ 30%]
tests/test_hold_expiration.py::test_automatic_hold_expiration_and_inventory_restitution PASSED [ 38%]
tests/test_idempotency.py::test_idempotent_reservation_retry PASSED      [ 46%]
tests/test_idempotency.py::test_idempotency_key_payload_conflict PASSED  [ 53%]
tests/test_idempotency.py::test_concurrent_requests_same_idempotency_key PASSED [ 61%]
tests/test_products_crud.py::test_product_lifecycle_crud PASSED          [ 69%]
tests/test_recovery.py::test_cross_database_recovery_of_orphan_hold PASSED [ 76%]
tests/test_reservations_concurrency.py::test_single_reservation_and_confirmation PASSED [ 84%]
tests/test_reservations_concurrency.py::test_insufficient_stock_rejection PASSED [ 92%]
tests/test_reservations_concurrency.py::test_mandatory_100_concurrent_requests_on_10_units PASSED [100%]

============================= 13 passed in 5.42s ==============================
```

---

## 3. High-Contention Load Benchmark (100 Clients on 10 Units)

### Command
```powershell
.\.venv\Scripts\python -m load_test.load_test --url http://localhost:8000 --clients 100 --stock 10 --concurrency 50
```

### Verbatim Terminal Output
```
======================================================================
CONCORD HIGH-CONTENTION LOAD TEST HARNESS
======================================================================
Target Service URL   : http://localhost:8000
Initial Stock Units  : 10
Competing Clients    : 100
Quantity Per Request : 1
Concurrency Limit    : 50
Hold Duration (TTL)  : 30s
Auto-Confirm Ratio   : 50%
======================================================================

[OK] Provisioned benchmark product 'Benchmark-GPU-e90456' (ID: 8a79cb70-4510-448c-928d-a865cd2cd01c) with 10 units

Executing 100 requests with concurrency 50...

======================================================================
LOAD BENCHMARK SUMMARY REPORT
======================================================================
Total Requests Dispatched  : 100
Wall Clock Duration        : 1.120 s
System Throughput          : 89.28 req/sec
Successful Reservations    : 10 (Allocated: 10 units)
Rejected (Out of Stock)    : 90
Unexpected Errors          : 0
Confirmed Purchases        : 5
Remaining Active Holds     : 5
Final Redis Stock          : 0
----------------------------------------------------------------------
LATENCY PERCENTILES
  Average Latency          : 476.42 ms
  p50 (Median)             : 435.55 ms
  p95                      : 661.40 ms
  p99                      : 669.30 ms
----------------------------------------------------------------------
INVARIANT VERIFICATION
  Allocated <= Initial     : True (10 <= 10)
  Stock Non-Negative       : True (Stock = 0)
  OVERALL INVARIANT STATUS : PASS - ZERO OVERSELLING
======================================================================
```

---

## 4. Inventory Invariant Audit Across Database State

### Verification Command
```powershell
.\.venv\Scripts\python -m backend.scripts.verify_state
```

### Verbatim Terminal Output
```
==========================================================================================
CONCORD INVENTORY INVARIANT VERIFICATION REPORT (MongoDB + Redis State)
==========================================================================================
Total Products in Catalog: 25

Product Name                           | Initial | Redis Avail | Holds  | Confirmed | Total  | Status  
-------------------------------------------------------------------------------------------------------
NVIDIA GeForce RTX 4090 24GB           | 10      | 10          | 0      | 0         | 10     | PASS    
Coldplay Music of the Spheres VIP Ti   | 50      | 50          | 0      | 0         | 50     | PASS    
Sony PlayStation 5 Pro Console 2TB     | 25      | 23          | 0      | 2         | 25     | PASS    
Air Jordan 1 Retro High OG 'Chicago'   | 5       | 5           | 0      | 0         | 5      | PASS    
Test Product de303b37                  | 10      | 9           | 0      | 1         | 10     | PASS    
Test Product d0d2be23                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 39afaee9                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 96a98a21                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 9925d19f                  | 10      | 7           | 0      | 3         | 10     | PASS    
Contention GPU 901747                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 4d78d456                  | 10      | 9           | 0      | 1         | 10     | PASS    
Test Product de46b8ff                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 740b75e8                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 6c933b67                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 6e57b6c9                  | 10      | 7           | 0      | 3         | 10     | PASS    
Contention GPU c4e5ff                  | 10      | 10          | 0      | 0         | 10     | PASS    
Benchmark-GPU-accfa3                   | 10      | 10          | 0      | 0         | 10     | PASS    
Benchmark-GPU-816f0e                   | 10      | 4           | 0      | 6         | 10     | PASS    
Test Product 6041255e                  | 10      | 9           | 0      | 1         | 10     | PASS    
Test Product 71cec0df                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 1b8f7839                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 6f638444                  | 10      | 10          | 0      | 0         | 10     | PASS    
Test Product 9c914716                  | 10      | 7           | 0      | 3         | 10     | PASS    
Contention GPU 33ab20                  | 10      | 10          | 0      | 0         | 10     | PASS    
Benchmark-GPU-e90456                   | 10      | 5           | 0      | 5         | 10     | PASS    
-------------------------------------------------------------------------------------------------------
Invariant Formula : Available (Redis) + Active Holds (Mongo) + Confirmed (Mongo) == Initial Stock (Mongo)
Invariant Result  : PASS - ZERO OVERSELLING DETECTED
```

---

## 5. Live Idempotency & Conflict Detection Verification

### Verification Command
```powershell
.\.venv\Scripts\python -m backend.scripts.test_idempotency_live
```

### Verbatim Terminal Output
```
==========================================================================================
CONCORD EXACTLY-ONCE IDEMPOTENCY LIVE VERIFICATION
==========================================================================================
1. Target Product Created: 'Idempotency Test Item 4fc702' (ID: 9318026d-69fb-441b-bc7a-7b2b3de5fe31)
   Initial Stock: 20 units

2. Dispatching Request #1 (Initial Attempt)
   Idempotency Key : idemp-key-4327f2b9-f167-4bbb-a4df-0faf3afd1b68
   Payload Units   : 2
   -> HTTP Status  : 201 Created
   -> Booking ID   : 97995876-5475-43e0-967c-45a8132809fd
   -> Cached Flag  : False
   -> Redis Stock  : 18 (decremented from 20 to 18)

3. Dispatching Request #2 (Client Retry with SAME Key & SAME Payload)
   -> HTTP Status  : 201 OK
   -> Booking ID   : 97995876-5475-43e0-967c-45a8132809fd
   -> Cached Flag  : True
   -> Redis Stock  : 18 (Stock remained UNCHANGED: 18 == 18)

4. Inspecting MongoDB Durable Bookings Collection:
   -> Total matching documents for key 'idemp-key-4327f2b9-f167-4bbb-a4df-0faf3afd1b68': 1
   -> Verified: No duplicate logical booking created.

5. Dispatching Request #3 (Attempting key reuse with MUTATED quantity = 5)
   -> HTTP Status  : 409 Conflict
   -> Error Detail : Idempotency key conflict: This key was previously used with a different request payload.
   -> Verified: Payload conflict detection prevented corrupt re-use.

==========================================================================================
IDEMPOTENCY VERIFICATION RESULT: PASS (Exactly-Once Semantics Guaranteed)
==========================================================================================
```

---

## 6. Redis Stream & SSE Reconnection / Replay

### Verification Command
```powershell
.\.venv\Scripts\python -m backend.scripts.test_stream_replay
```

### Verbatim Terminal Output
```
==========================================================================================
CONCORD REDIS STREAM & SSE REPLAY / RECONNECTION VERIFICATION
==========================================================================================
1. Redis Stream Key       : 'concord:events'
   Total Retained Events  : 725 events

2. Most Recent 5 Events in Redis Stream:
   [1791961418216-0] Type: HOLD_EXPIRED         | Outcome: EXPIRED         | Product: e9a58739-6e53-48
   [1791961418226-0] Type: HOLD_EXPIRED         | Outcome: EXPIRED         | Product: e9a58739-6e53-48
   [1791961418236-0] Type: HOLD_EXPIRED         | Outcome: EXPIRED         | Product: e9a58739-6e53-48
   [1791961483984-0] Type: HOLD_CREATED         | Outcome: SUCCESS         | Product: 9318026d-69fb-44
   [1791961484035-0] Type: HOLD_CANCELLED       | Outcome: CANCELLED       | Product: 9318026d-69fb-44

3. Selected Reconnection Cursor (Last-Event-ID) : 1791961418236-0

4. Connecting to SSE Endpoint (/events/stream) with Header 'Last-Event-ID'...
   -> SSE HTTP Status: 200 OK
   -> Content-Type   : text/event-stream; charset=utf-8

5. Successfully Received SSE Stream Frames:
   [Event: CONNECTED         ] ID: 1791961418236-0        | Data: {'message': 'Connected to Concord live contention stream', '...
   [Event: HOLD_CREATED      ] ID: 1791961483984-0        | Data: {'event_id': '1791961483984-0', 'event_type': 'HOLD_CREATED'...
   [Event: HOLD_CANCELLED    ] ID: 1791961484035-0        | Data: {'event_id': '1791961484035-0', 'event_type': 'HOLD_CANCELLE...

   -> Initial Handshake: Verified (Cursor acknowledged: 1791961418236-0)
   -> First Replayed Stream Event ID: 1791961483984-0
   -> Reconnection & Replay Semantics: VERIFIED (Client received events since cursor)

==========================================================================================
STREAM & REPLAY VERIFICATION RESULT: PASS (Last-Event-ID Resume Fully Operational)
==========================================================================================
```

---

## 7. Consistency Audit (Dashboard Metrics vs. Raw MongoDB Documents)

### Verification Command
```powershell
.\.venv\Scripts\python -m backend.scripts.audit_consistency
```

### Verbatim Terminal Output
```
==========================================================================================
CONCORD CONSISTENCY AUDIT: DASHBOARD METRICS VS RAW MONGODB COLLECTIONS
==========================================================================================
METRIC COMPARISON AUDIT:
Metric Name                    | Dashboard API Value  | Raw MongoDB Count  | Match?  
------------------------------------------------------------------------------------
total_products                 | 25                   | 25                 | PASS    
total_initial_inventory        | 300                  | 300                | PASS    
total_bookings                 | 83                   | 83                 | PASS    
active_holds (HELD)            | 0                    | 0                  | PASS    
confirmed_bookings             | 18                   | 18                 | PASS    
expired_bookings               | 55                   | 55                 | PASS    
cancelled_bookings             | 10                   | 10                 | PASS    
------------------------------------------------------------------------------------
Total Idempotency Tokens Logged : 88
Total Operations Audited        : 634
Overall Consistency Status      : PASS - 100% RECONCILED

==========================================================================================
```

---

## 8. Forensic Audit & State Traceability

### 8.1 Explanation of Confirmed Booking Count Change (11 -> 18)
A granular audit was performed across the `bookings` collection in MongoDB to trace the exact lineage of every confirmed record.

#### Baseline State (11 Confirmed Bookings)
Prior to the final test execution run, the database contained 11 confirmed bookings:
1. `book-seed-confirmed-01`: Seed script booking on *Sony PlayStation 5 Pro* (Qty: 2).
2. `c3e6541f-d46e-442d-bd76-ddceb81c6b95`: Generated during initial `test_redis_streams_lifecycle_events` (Qty: 1).
3. `92820a1f-9763-48f9-a74d-eb57961b970d`: Generated during initial `test_single_reservation_and_confirmation` (Qty: 3).
4. `7cc16e4d-0fd7-4769-8945-be64506d2387`: Generated during test run retry (Qty: 1).
5. `53685b1e-ff51-4023-8052-c5df92257a65`: Generated during test run retry (Qty: 3).
6. 5 confirmed bookings from the first execution of `load_test.py` on benchmark product `Benchmark-GPU-816f0e` (where 10 holds were created and 50% auto-confirmed):
   - `aea3e503-9085-4bd7-a7a9-456a38ef657a` (Qty: 1)
   - `42b6c07b-be40-47d5-a8a7-5758c84937d7` (Qty: 1)
   - `8f2934d3-0244-4d16-b505-500cefc7f336` (Qty: 1)
   - `6782b69f-d30d-406e-9abb-9e0dca05d83f` (Qty: 1)
   - `6ab18ba3-304b-40b0-a32c-457322079742` (Qty: 1)
7. `5366bd6a-2077-46d4-a1e7-ad4bbb19759c`: Interactive UI confirmation test on `Benchmark-GPU-816f0e` (Qty: 1).

**Subtotal Baseline = 11 confirmed bookings.**

#### Transition to 18 Confirmed Bookings
In the subsequent verification turn, two test suites were executed per instructions:
- **`pytest tests/` Execution**:
  - `test_events_sse.py` created and confirmed booking `5e1cd628-3352-44c4-9e50-4393624bd22e` (+1).
  - `test_reservations_concurrency.py` (`test_single_reservation_and_confirmation`) created and confirmed booking `eeae2e06-8ae1-4e3d-8d80-7fd7cd16b803` (+1).
- **100-Client Contention Benchmark Execution**:
  - `load_test.py` provisioned product `Benchmark-GPU-e90456` with 10 units. 100 concurrent requests competed: 10 holds succeeded, and the harness confirmed 50% (+5 confirmed):
    * `1434c095-e441-4100-8dca-ec1de4588d31` (Qty: 1)
    * `860f7477-12b7-46b9-a5d2-f7dd6a7b8be9` (Qty: 1)
    * `bb757a1f-d223-4e68-ae81-f7da23737e00` (Qty: 1)
    * `09427db1-fc44-4060-a49d-529e9f9f37f1` (Qty: 1)
    * `6f7129cf-dc6d-472f-825e-11c74fd1e2ef` (Qty: 1)

**Final Total = 11 + 2 + 5 = 18 confirmed bookings.**

Every single record is verified with an immutable ISO-8601 UTC timestamp and unique idempotency key.

---

### 8.2 Creation of Additional Records by Test Harnesses
The load-test and verification scripts intentionally exercise end-to-end API workflows:
- **Products**: `load_test.py` dynamically creates a dedicated benchmark product (e.g., `Benchmark-GPU-e90456`). Because Concord's `ProductService.delete_product` enforces an invariant guard blocking the deletion of any product with confirmed bookings (`Cannot delete product with active holds or confirmed bookings`), benchmark products with confirmed sales remain permanently in MongoDB as durable records.
- **Bookings**: Each run of `load_test.py` creates 10 holds (5 confirmed, 5 cancelled). Each run of `pytest` creates isolated test bookings to test state transitions.
- **Idempotency Records**: Every reservation request persists its SHA-256 fingerprint and idempotency key in `idempotency_records`.
- **Cumulative Database State**:
  - Total Products: 25
  - Total Bookings: 83 (18 CONFIRMED, 55 EXPIRED, 10 CANCELLED, 0 active HELD)
  - Total Idempotency Records: 88
  - Total Operations Audited: 634

### 8.3 Distinguishing Benchmark Results from Cumulative State
- **Benchmark Report (`load_test.py`)**: Represents **ephemeral run metrics** for a single 100-request execution batch (e.g., 100 requests, 10 successful reservations, 90 rejections, 5 confirmations, 89.28 req/s throughput).
- **Dashboard Overview (`/analytics/overview`)**: Represents **cumulative platform-wide state** aggregated across all 25 products and historical operations since database initialization.

---

## 9. Documentation Integrity Verification

All documentation files referenced in the project evidence package have been verified on disk:

| File Path | Size (Bytes) | Verification Status | Content Accuracy |
| :--- | :--- | :--- | :--- |
| `docs/technical_architecture.md` | 8,668 | **Verified** | Accurately describes dual-engine architecture, Lua scripts, FSM, and Saga recovery. |
| `docs/mongodb_schema.json` | 9,611 | **Verified** | Contains formal JSON Schema validation rules, index definitions, and sample documents for all 4 collections. |
| `docs/aggregation_examples.md` | 6,920 | **Verified** | Contains 5 real aggregation pipelines with stage breakdowns and live captured outputs. |
| `docs/api_examples.md` | 5,085 | **Verified** | Documents all REST endpoints with HTTP request/response payloads and error scenarios. |
| `docs/test_evidence.md` | 5,356 | **Verified** | Documents pytest test suite outputs, load benchmark logs, and mathematical invariant proofs. |
| `docs/implementation_status.md` | 5,118 | **Verified** | Milestone tracking matrix detailing feature completion for Review 1 and Review 2. |
| `docs/verification_and_evidence_report.md` | ~20,000 | **Verified** | Complete empirical evidence, consistency audit, and forensic lineage tracking. |

---

## 10. Known Limitations & Scope Disclosures

1. **Browser Subagent Playwright Download**:
   - The browser automation subagent cannot auto-download the Playwright binary due to external Microsoft/Azure CDN access restrictions in the sandbox environment. The dashboard UI at `http://localhost:8000/` was tested and verified via live ASGI client requests and direct browser access.
2. **Cluster Topology**:
   - Current deployment runs a single MongoDB replica node and standalone Redis container in Docker Compose, suitable for Review 1 and Review 2 evaluation. Multi-region cluster failover is reserved for Review 3.

