# Concord Automated Test & Load Benchmark Evidence
**Review 1 & Review 2 Technical Verification Report**

---

## 1. Automated Test Suite (pytest)

### Command
```powershell
python -m pytest -v tests/
```

### Execution Output & Summary
```
============================= test session starts =============================
platform win32 -- Python 3.11.5, pytest-9.1.1, pluggy-1.6.0
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

============================= 13 passed in 4.67s ==============================
```

### Verified Scenarios
1. **MongoDB CRUD & Indexes**: Validated document creation, unique name constraint, guarded deletion blocking when holds exist, and automatic index instantiation.
2. **Atomic Redis Lua Execution**: Validated single-step atomic stock decrement, state creation, and hold release.
3. **Idempotency Semantics**:
   - Replay of identical key returns cached original outcome with zero additional inventory decrement.
   - Reuse of key with a mutated payload correctly triggers `409 Conflict`.
   - Concurrent requests with identical keys are deduped to the single logical booking.
4. **Automated Hold Expiration**: Tested hold timeout, background sweeper recovery, status update to `EXPIRED`, and 100% inventory restitution.
5. **Cross-Database Reconciliation**: Simulated uncommitted orphan Redis holds (server crash emulation) and verified sweeper restores inventory without leakage.
6. **Live Event Stream**: Verified event emission to Redis Streams (`HOLD_CREATED`, `BOOKING_CONFIRMED`) and delivery via SSE.

---

## 2. High-Contention Load Benchmark (100 Clients on 10 Units)

### Benchmark Execution Command
```powershell
python -m load_test.load_test --url http://localhost:8000 --clients 100 --stock 10 --concurrency 50
```

### Actual Benchmark Terminal Output
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

[OK] Provisioned benchmark product 'Benchmark-GPU-816f0e' (ID: ba41ae44-1473-4d78-979d-edef4419409e) with 10 units

Executing 100 requests with concurrency 50...

======================================================================
LOAD BENCHMARK SUMMARY REPORT
======================================================================
Total Requests Dispatched  : 100
Wall Clock Duration        : 1.004 s
System Throughput          : 99.58 req/sec
Successful Reservations    : 10 (Allocated: 10 units)
Rejected (Out of Stock)    : 90
Unexpected Errors          : 0
Confirmed Purchases        : 5
Remaining Active Holds     : 5
Final Redis Stock          : 0
----------------------------------------------------------------------
LATENCY PERCENTILES
  Average Latency          : 436.57 ms
  p50 (Median)             : 383.01 ms
  p95                      : 620.82 ms
  p99                      : 626.82 ms
----------------------------------------------------------------------
INVARIANT VERIFICATION
  Allocated <= Initial     : True (10 <= 10)
  Stock Non-Negative       : True (Stock = 0)
  OVERALL INVARIANT STATUS : PASS - ZERO OVERSELLING
======================================================================
```

### Mathematical Invariant Proof
$$\text{Allocated Units} = \text{Active Holds} (5) + \text{Confirmed Purchases} (5) = 10$$
$$\text{Available Stock} (0) + \text{Allocated Units} (10) = \text{Initial Inventory} (10)$$
$$\text{Available Stock} \ge 0 \quad (\text{Value: } 0)$$

Under 50 simultaneous concurrent client connections, exactly 10 units were allocated across 10 successful requests and 90 requests were rejected due to insufficient stock. **Zero overselling was mathematically verified.**
