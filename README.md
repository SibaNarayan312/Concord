# Concord
### A High-Contention Inventory Reservation Service with Exactly-Once Booking Semantics and a Live Contention Stream

[![Tests](https://img.shields.io/badge/tests-34%20passed%2C%200%20failed-brightgreen.svg)](tests/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7.0-47A248.svg)](https://www.mongodb.com/)
[![Redis](https://img.shields.io/badge/Redis-7.2-DC382D.svg)](https://redis.io/)

Concord is an enterprise-grade NoSQL database project designed to solve the **high-contention inventory allocation problem** (such as flash sales, sneaker drops, or concert ticket releases) where hundreds or thousands of clients compete simultaneously for a strictly limited stock.

Built with **FastAPI**, **MongoDB 7.0**, **Redis 7.2**, **Redis Streams**, and **Server-Sent Events (SSE)**.


---

## Key Highlights

- **Zero-Oversell Invariant**: Enforced via atomic Redis Lua scripts (`reserve.lua`, `release.lua`, `confirm.lua`). Available inventory never falls below zero.
- **Dual-Engine NoSQL Architecture**:
  - **Redis**: In-memory coordination, sub-millisecond stock checks, temporary hold hashes, and real-time lifecycle event publishing.
  - **MongoDB**: Durable business records, audit trails, compound query indexes, and multi-stage aggregation pipelines.
- **WooCommerce Test Store Integration**: Real-time integration supporting order webhooks (`order.created`, `order.updated`), checkout blocking via a drop-in WordPress plugin, HMAC-SHA256 signature verification, and automated bidirectional stock synchronization.
- **Strict Exactly-Once Semantics**: Client idempotency keys with SHA-256 request fingerprinting prevent duplicate bookings upon network retries and reject payload mutations with `409 Conflict`.
- **Automated Hold Recovery**: Background sweepers automatically transition expired holds and restore reserved inventory to active stock without leakage.
- **Live Event Contention Stream**: Lifecycle events (`HOLD_CREATED`, `BOOKING_CONFIRMED`, `HOLD_EXPIRED`, `HOLD_CANCELLED`, `RESERVATION_REJECTED`, `WOOCOMMERCE_ORDER_RESERVED`) broadcast to browser dashboards via Server-Sent Events (SSE) with `Last-Event-ID` resume and replay support.
- **Real-Time MongoDB Aggregations**: Real `$group` and `$lookup` aggregation pipelines delivering status metrics and product conversion analytics.

---

## Architecture Overview

```
[ WooCommerce Test Store ]      [ Browser UI / Load Generator ]
           │                                   │
           │ Webhooks / Checkout Hooks          │ REST & SSE
           ▼                                   ▼
┌─────────────────────────────────────────────────────────────┐
│               FastAPI Application (Port 8000)               │
│  (Reservation Engine, WooCommerce Bridge, Idempotency, SSE) │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
               ▼                               ▼
       MongoDB 7.0                         Redis 7.2
    (Durable Store)                (In-Memory Coordination)
 • products (catalog)               • reserve.lua (atomic decr)
 • bookings (audit)                 • release.lua (atomic incr)
 • idempotency_records              • confirm.lua (atomic state)
 • reservation_operations           • concord:events (streams)
 • woocommerce_mappings             • Active hold TTL hashes
 • woocommerce_orders               • Instant stock balances
 • woocommerce_webhooks                        │
               │                               │
               └───────────────┬───────────────┘
                               │
                               ▼
                      Background Workers
              (Hold Expiration & Reconciliation)
```

---

## Live Demonstrator & Evaluation Endpoints

- **Local Evaluation (Recommended)**: Open **`http://localhost:8000`** in any web browser for the primary interactive dashboard, real-time metrics, and live contention stream.
- **Local Health Check**: `http://localhost:8000/health`
- **Local WooCommerce Status**: `http://localhost:8000/woocommerce/status`
- **Local Webhook Ingress**: `http://localhost:8000/woocommerce/webhooks`

> [!NOTE]
> **Public Tunnel Disclosure**: The public demonstration URL (`https://ripping-yelling-kinsman.ngrok-free.dev`) is an active-session tunnel that routes external webhooks to the local container. It requires an active ngrok session (`ngrok http 8000`). For independent local evaluation, always prioritize `http://localhost:8000`.

---

## 3-Minute Demo Walkthrough (Recruiter & Evaluator Guide)

Follow these 6 steps to evaluate Concord's core capabilities directly from the web interface at **`http://localhost:8000`**:

1. **Verify Health & Components**:
   - Check the status chips in the top navigation bar. Confirm **MongoDB** (`Connected`), **Redis** (`Connected`), **WooCommerce** (`Mock/Test`), and **Contention Stream** (`Live Feed`) are all active (Green).
2. **Interactive Temporary Reservation**:
   - In the **Interactive Reservation & Hold Simulator** card, select `NVIDIA GeForce RTX 4090 24GB` (10 available units).
   - Set **Units to Reserve** to `1` and **Hold Duration** to `30` seconds. Click **Acquire Temporary Hold**.
   - Observe: Available stock immediately decrements from `10` to `9`, an **ACTIVE HOLD** countdown panel appears with a ticking timer, and `[HOLD_CREATED]` appears in the live feed.
3. **Confirm, Cancel, or Expire**:
   - Click **Confirm Booking** to finalize the purchase (Confirmed Sold increments to `1`, stock remains `9`).
   - Alternatively, acquire a hold and click **Cancel Hold** (or wait 30 seconds for automatic background expiry) to watch inventory automatically return to `10` units via `[HOLD_CANCELLED]` or `[HOLD_EXPIRED]`.
4. **The 100-Client High-Contention Race (Zero-Oversell Demonstration)**:
   - Under the reservation form, locate the **High-Contention Load Benchmark** box.
   - Click **🚀 Run 100-Client Race**.
   - Observe 100 concurrent requests compete for 10 units simultaneously in ~300ms.
   - The result box displays: **10** Successfully Reserved (201), **90** Correctly Rejected (400), Final Stock = **0**, and **Zero-Oversell Invariant: PASS (Zero Overselling)**.
5. **WooCommerce Mock Purchase (Numeric Order ID Verification)**:
   - In the **MongoDB Aggregation Pipelines** section, click the **WooCommerce Store** tab.
   - Click **Simulate WC Purchase**.
   - Observe toast confirmation and verify the **Live Contention Stream** displays:
     `🛒 WooCommerce Order #<ID>: Reserved 1 unit(s)` (renders the exact numeric order ID, **never `#undefined`**).
   - Duplicate deliveries for the same order are acknowledged without duplicate stream cards.
6. **Live Stream Replay & Deduplication**:
   - In the **Live Contention Stream** header, click **Replay From Start**.
   - Observe past events replay from Redis Streams via `Last-Event-ID`. DOM-level deduplication prevents already-rendered events from duplicating in the view.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- Windows PowerShell / Linux / macOS terminal

### 1. Start Infrastructure via Docker Compose
Run MongoDB and Redis in the background:
```bash
docker compose up -d mongodb redis
```
Or launch the production-hardened network with isolated databases:
```bash
docker compose -f docker-compose.prod.yml up -d
```

### 2. Set Up Python Environment
Create and activate the virtual environment:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Initialize & Seed Database
Initialize collections, schema validation, and compound indexes:
```powershell
python -m backend.scripts.init_db
```
Seed sample demo products (RTX 4090, VIP Tickets, PS5 Pro):
```powershell
python -m backend.scripts.seed_data
```

### 4. Start Concord Backend & Dashboard
```powershell
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **`http://localhost:8000`** in any web browser to view the interactive dashboard.

---

GitHub Repository: [https://github.com/SibaNarayan312/Concord.git](https://github.com/SibaNarayan312/Concord.git)

---

## Running the Automated Test Suite

Run all 34 unit, integration, concurrency, idempotency, recovery, WooCommerce, and dashboard regression tests:
```powershell
python -m pytest -v tests/
```

**Test Suite Verification Result**: **34 passed, 0 failed, 0 skipped in 11.82s** (100% pass rate).

Test coverage includes:
- **Dashboard SSE Stream Projection & Deduplication**: Verified top-level projection of `order_id`, `stock_quantity`, `mode`, and `error`, preventing `#undefined` order IDs; validated DOM-level event deduplication and duplicate webhook suppression on pending order updates.
- **MongoDB CRUD & Validation**: Catalog management, schema validation, unique product name indexing, and guarded product deletion blocking active holds.
- **Redis Lua Atomic Coordination**: Zero-oversell enforcement via `reserve.lua`, `release.lua`, and `confirm.lua`.
- **Mandatory 100-Client Contention Race**: 100 simultaneous requests competing for 10 units (asserting exactly 10 holds, 90 out-of-stock rejections, zero overselling).
- **Strict Idempotency & Conflict Detection**: SHA-256 fingerprint deduplication returning cached responses and rejecting mutated payloads with `409 Conflict`.
- **Automated Hold Expiration**: Background worker releasing expired holds and restoring stock without leakage.
- **Cross-Database Reconciliation**: Self-healing worker repairing simulated orphan holds and incomplete operations.
- **Redis Streams & SSE Replay**: Real-time lifecycle event publishing and cursor resumption via `Last-Event-ID`.
- **MongoDB Aggregation Pipelines**: Multi-stage aggregation pipelines for conversion metrics, product performance, and status analytics.
- **WooCommerce Product ID Mappings**: Compound unique constraints on `wc_product_id` and `wc_variation_id`.
- **WooCommerce HMAC-SHA256 Webhooks**: Cryptographic signature validation, duplicate delivery deduplication, reachability pings (`deliver_ping`), and order lifecycle FSM (`pending` -> hold, `processing` -> confirm, `cancelled` -> release).
- **Bidirectional Stock Sync**: Idempotent stock push using absolute quantities, respecting `sync_enabled` flags.
- **Mandatory 100-Concurrent WooCommerce Webhook Race**: 100 simultaneous order webhook requests competing for 10 units (asserting exactly 10 holds, 90 rejections, zero oversell).

---

## Demo Limitations & Scope Disclosures

- **Browser Automation in Sandboxes**: The browser automation subagent cannot auto-download the Playwright binary due to external Microsoft/Azure CDN access restrictions in sandboxed evaluation environments. The dashboard UI at `http://localhost:8000/` was tested and verified via live ASGI client requests and direct browser access.
- **Cluster Topology**: Current development runs single-node MongoDB 7.0 and Redis 7.2 containers in Docker Compose, suitable for local evaluation and demonstration. Multi-region cluster failover is reserved for enterprise scale-out.
- **Temporary WooCommerce Test Store**: The previously connected WooCommerce store (`https://poorglue.s3-tastewp.com`) was provisioned on TasteWP for empirical integration testing. As TasteWP instances expire after 48 hours, Concord includes an internal Mock Mode (`WC_MOCK_MODE=true`) and complete end-to-end webhook test suites that operate independently of external store availability.

---

## Running the High-Contention Load Benchmark

### 1. Direct Reservation Mode (100 Shoppers on 10 Units)
```powershell
python -m load_test.load_test --url http://localhost:8000 --clients 100 --stock 10 --concurrency 50 --mode direct
```

### 2. WooCommerce Webhook Mode (100 Shoppers on 10 Units)
```powershell
python -m load_test.load_test --url http://localhost:8000 --clients 100 --stock 10 --concurrency 50 --mode woocommerce
```

Sample Benchmark Output:
```
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

## API Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Health check for MongoDB, Redis, and workers |
| `POST`| `/products` | Create catalog product |
| `GET` | `/products` | List all products with live stock |
| `GET` | `/products/{id}` | Get product details and hold counts |
| `PUT` | `/products/{id}` | Update product information |
| `DELETE` | `/products/{id}` | Guarded deletion of product |
| `POST`| `/products/{id}/reset` | Reset inventory for testing |
| `POST`| `/reservations` | Acquire atomic temporary hold |
| `GET` | `/reservations/{id}` | Inspect reservation hold |
| `POST`| `/reservations/{id}/confirm` | Confirm hold into booking |
| `POST`| `/reservations/{id}/cancel` | Cancel hold and restore stock |
| `GET` | `/bookings` | List durable bookings |
| `GET` | `/bookings/{id}` | Get booking audit record |
| `GET` | `/analytics/overview` | Platform metrics & invariant status |
| `GET` | `/analytics/bookings-by-status` | Aggregation 1: Group by status |
| `GET` | `/analytics/product-performance`| Aggregation 2: Product join |
| `GET` | `/analytics/confirmed-vs-expired`| Aggregation 4: Conversion ratio |
| `GET` | `/analytics/indexes` | Inspect active MongoDB indexes |
| `GET` | `/events/stream` | Server-Sent Events (SSE) live feed |
| `GET` | `/events/recent` | Recent events from Redis Stream |
| `GET` | `/woocommerce/status` | WooCommerce integration status & config |
| `GET` | `/woocommerce/mappings` | List active product ID mappings |
| `POST`| `/woocommerce/mappings` | Create or update product ID mapping |
| `POST`| `/woocommerce/webhooks` | Ingest signed WooCommerce order webhooks |
| `POST`| `/woocommerce/sync/{id}` | Synchronize single product stock to WooCommerce |
| `POST`| `/woocommerce/sync-all` | Batch synchronize all mapped product stock |
| `GET` | `/woocommerce/orders` | List tracked WooCommerce order reservation audits |
| `POST`| `/woocommerce/simulate-checkout` | Simulate an end-to-end WooCommerce purchase |

---

## Documentation Index

- [WooCommerce Integration Guide](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/woocommerce_integration_guide.md)
- [Production Deployment & Operations Guide](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/deployment_guide.md)
- [Concurrency & Reliability Test Report](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/concurrency_test_report.md)
- [Technical Architecture](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/technical_architecture.md)
- [Technical Verification & Evidence Report](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/verification_and_evidence_report.md)
- [MongoDB Schema & Collection Specifications](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/mongodb_schema.json)
- [MongoDB Aggregation Pipelines & Real Outputs](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/aggregation_examples.md)
- [REST API Reference & Examples](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/api_examples.md)
- [Test & Benchmark Evidence Report](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/test_evidence.md)
- [Implementation Status Matrix](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/implementation_status.md)

