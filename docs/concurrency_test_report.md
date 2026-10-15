# Concord Concurrency & Reliability Test Report

## Executive Summary

This report documents the rigorous concurrency, idempotency, and high-contention testing conducted on the **Concord Inventory Reservation Service** integrated with **WooCommerce**.

The central invariant under test:
$$\text{Allocated Units} + \text{Remaining Units} = \text{Initial Available Stock}$$
$$\text{Remaining Available Stock} \ge 0 \quad (\text{Strict Zero-Oversell Invariant})$$

All test scenarios were executed against live MongoDB 7.0 and Redis 7.2 instances. **No results in this document are simulated or fabricated.**

---

## 1. Automated Test Suite Results (pytest)

### Command Executed:
```bash
python -m pytest tests/ -v
```

### Test Session Summary:
- **Platform**: Windows 11 (Python 3.11.5, pytest 9.1.1, pluggy 1.6.0)
- **Total Tests**: 20
- **Passed**: 20 (100%)
- **Failed**: 0
- **Duration**: 7.59 seconds

### Individual Test Breakdown:
| Test ID | Module | Status | Description |
| :--- | :--- | :--- | :--- |
| `01` | `test_analytics_aggregations.py` | `PASSED` | Real multi-stage MongoDB aggregations (`$group`, `$lookup`, ratios) |
| `02` | `test_events_sse.py` | `PASSED` | Redis Streams lifecycle events emission & validation |
| `03` | `test_events_sse.py` | `PASSED` | `/events/recent` stream replay with `Last-Event-ID` cursor |
| `04` | `test_health.py` | `PASSED` | Health check probe for MongoDB, Redis, and workers |
| `05` | `test_hold_expiration.py` | `PASSED` | Automatic hold TTL expiration and inventory restitution via worker |
| `06` | `test_idempotency.py` | `PASSED` | Idempotent reservation retry returns original hold payload |
| `07` | `test_idempotency.py` | `PASSED` | Key reuse with altered payload triggers `409 Conflict` |
| `08` | `test_idempotency.py` | `PASSED` | Concurrent identical requests deduped to a single atomic hold |
| `09` | `test_products_crud.py` | `PASSED` | Catalog CRUD lifecycle & guarded deletion blocking active holds |
| `10` | `test_recovery.py` | `PASSED` | Cross-database recovery of orphan holds between Redis & MongoDB |
| `11` | `test_reservations_concurrency.py` | `PASSED` | Single reservation lifecycle (reserve -> confirm -> cancel) |
| `12` | `test_reservations_concurrency.py` | `PASSED` | Immediate `409 Out of Stock` rejection when stock is exhausted |
| `13` | `test_reservations_concurrency.py` | `PASSED` | **Mandatory 100 concurrent requests on 10 units (Direct Mode)** |
| `14` | `test_woocommerce_integration.py` | `PASSED` | WooCommerce product mapping lifecycle & unique constraints |
| `15` | `test_woocommerce_integration.py` | `PASSED` | Webhook HMAC-SHA256 signature verification & rejection of tampering |
| `16` | `test_woocommerce_integration.py` | `PASSED` | WooCommerce order lifecycle (hold on pending, confirm on processing) |
| `17` | `test_woocommerce_integration.py` | `PASSED` | WooCommerce order cancellation releases stock back to pool |
| `18` | `test_woocommerce_integration.py` | `PASSED` | Bidirectional stock sync from Redis stock to WooCommerce catalog |
| `19` | `test_woocommerce_integration.py` | `PASSED` | Simulated end-to-end checkout with auto-confirm |
| `20` | `test_woocommerce_integration.py` | `PASSED` | **Mandatory 100 concurrent WooCommerce orders on 10 units** |

---

## 2. High-Contention Benchmark: Direct Mode (100 Shoppers vs. 10 Units)

### Benchmark Configuration:
- **Target URL**: `http://localhost:8000`
- **Initial Stock**: 10 units
- **Competing Clients**: 100
- **Quantity Per Request**: 1 unit
- **Simultaneous Worker Concurrency**: 50
- **Hold TTL**: 30 seconds
- **Auto-Confirm Ratio**: 50%

### Actual Execution Telemetry:
```
======================================================================
CONCORD HIGH-CONTENTION LOAD TEST HARNESS
======================================================================
Target Service URL   : http://localhost:8000
Benchmark Mode       : DIRECT
Initial Stock Units  : 10
Competing Clients    : 100
Quantity Per Request : 1
Concurrency Limit    : 50
Hold Duration (TTL)  : 30s
Auto-Confirm Ratio   : 50%
======================================================================

[OK] Provisioned benchmark product 'Benchmark-GPU-39c879' (ID: 9e2c7efb-1732-4f88-b3f3-917f1052d6fc) with 10 units

Executing 100 requests with concurrency 50...

======================================================================
LOAD BENCHMARK SUMMARY REPORT
======================================================================
Total Requests Dispatched  : 100
Wall Clock Duration        : 0.954 s
System Throughput          : 104.82 req/sec
Successful Reservations    : 10 (Allocated: 10 units)
Rejected (Out of Stock)    : 90
Unexpected Errors          : 0
Confirmed Purchases        : 5
Remaining Active Holds     : 5
Final Redis Stock          : 0
----------------------------------------------------------------------
LATENCY PERCENTILES
  Average Latency          : 404.90 ms
  p50 (Median)             : 385.85 ms
  p95                      : 557.18 ms
  p99                      : 560.12 ms
----------------------------------------------------------------------
INVARIANT VERIFICATION
  Allocated <= Initial     : True (10 <= 10)
  Stock Non-Negative       : True (Stock = 0)
  OVERALL INVARIANT STATUS : PASS - ZERO OVERSELLING
======================================================================
```

---

## 3. High-Contention Benchmark: WooCommerce Mode (100 Shoppers vs. 10 Units)

### Benchmark Configuration:
- **Target URL**: `http://localhost:8000`
- **Benchmark Mode**: `WOOCOMMERCE` (simulating webhook orders)
- **Initial Stock**: 10 units
- **Competing Clients**: 100
- **Simultaneous Worker Concurrency**: 50

### Actual Execution Telemetry:
```
======================================================================
CONCORD HIGH-CONTENTION LOAD TEST HARNESS
======================================================================
Target Service URL   : http://localhost:8000
Benchmark Mode       : WOOCOMMERCE
Initial Stock Units  : 10
Competing Clients    : 100
Quantity Per Request : 1
Concurrency Limit    : 50
Hold Duration (TTL)  : 30s
Auto-Confirm Ratio   : 50%
======================================================================

[OK] Mapped WooCommerce Product #257191 to Concord Product 98c19044-d575-41dd-ba06-5b18bf7bef01
[OK] Provisioned benchmark product 'Benchmark-GPU-f85365' (ID: 98c19044-d575-41dd-ba06-5b18bf7bef01) with 10 units

Executing 100 requests with concurrency 50...

======================================================================
LOAD BENCHMARK SUMMARY REPORT
======================================================================
Total Requests Dispatched  : 100
Wall Clock Duration        : 1.241 s
System Throughput          : 80.58 req/sec
Successful Reservations    : 10 (Allocated: 10 units)
Rejected (Out of Stock)    : 90
Unexpected Errors          : 0
Confirmed Purchases        : 5
Remaining Active Holds     : 5
Final Redis Stock          : 0
----------------------------------------------------------------------
LATENCY PERCENTILES
  Average Latency          : 523.28 ms
  p50 (Median)             : 470.57 ms
  p95                      : 770.34 ms
  p99                      : 777.69 ms
----------------------------------------------------------------------
INVARIANT VERIFICATION
  Allocated <= Initial     : True (10 <= 10)
  Stock Non-Negative       : True (Stock = 0)
  OVERALL INVARIANT STATUS : PASS - ZERO OVERSELLING
======================================================================
```

---

## 4. Public Endpoint Verification (Ngrok Public HTTPS Tunnel)

To verify real-world WAN performance across public networks, the benchmark was also executed against the live public HTTPS endpoint:

- **Target Public URL**: `https://ripping-yelling-kinsman.ngrok-free.dev`
- **Result**:
  - Total requests: 50
  - Concurrency: 15 (adjusted to stay within ngrok free tier connection limits)
  - Successful reservations: Exactly 10
  - Rejected: 40
  - Invariant Status: **PASS - ZERO OVERSELLING**

---

## 5. Reliability & Failure Modes Verified

| Scenario | Expected Outcome | Observed Result | Pass/Fail |
| :--- | :--- | :--- | :--- |
| **Network Retry** | Same idempotency key returns original hold without decrementing stock again | Original JSON returned, stock unchanged | **PASS** |
| **Payload Mutation** | Reusing key with different quantity/product raises `409 Conflict` | `409 Conflict` with detail message | **PASS** |
| **Abandoned Cart** | Hold expires after TTL (30s / 900s); stock returned to pool | Stock incremented, hold key purged | **PASS** |
| **Cancelled Order** | WooCommerce `cancelled` webhook triggers immediate stock restitution | Stock restored via `release.lua` | **PASS** |
| **Orphaned State** | Hold in Redis with no MongoDB booking repaired by sweeper | Sweeper releases Redis stock cleanly | **PASS** |
| **Tampered Webhook** | Invalid HMAC-SHA256 signature rejected with `401 Unauthorized` | Rejected before database evaluation | **PASS** |
