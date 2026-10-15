# Concord
### A High-Contention Inventory Reservation Service with Exactly-Once Booking Semantics and a Live Contention Stream

Concord is an enterprise-grade NoSQL database project designed to solve the **high-contention inventory allocation problem** (such as flash sales, sneaker drops, or concert ticket releases) where hundreds or thousands of clients compete simultaneously for a strictly limited stock.

Built with **FastAPI**, **MongoDB 7.0**, **Redis 7.2**, **Redis Streams**, and **Server-Sent Events (SSE)**.

---

## Key Highlights

- **Zero-Oversell Invariant**: Enforced via atomic Redis Lua scripts (`reserve.lua`, `release.lua`, `confirm.lua`). Available inventory never falls below zero.
- **Dual-Engine NoSQL Architecture**:
  - **Redis**: In-memory coordination, sub-millisecond stock checks, temporary hold hashes, and real-time lifecycle event publishing.
  - **MongoDB**: Durable business records, audit trails, compound query indexes, and multi-stage aggregation pipelines.
- **Strict Exactly-Once Semantics**: Client idempotency keys with SHA-256 request fingerprinting prevent duplicate bookings upon network retries and reject payload mutations with `409 Conflict`.
- **Automated Hold Recovery**: Background sweepers automatically transition expired holds and restore reserved inventory to active stock without leakage.
- **Live Event Contention Stream**: Lifecycle events (`HOLD_CREATED`, `BOOKING_CONFIRMED`, `HOLD_EXPIRED`, `HOLD_CANCELLED`, `RESERVATION_REJECTED`) broadcast to browser dashboards via Server-Sent Events (SSE) with `Last-Event-ID` resume and replay support.
- **Real-Time MongoDB Aggregations**: Real `$group` and `$lookup` aggregation pipelines delivering status metrics and product conversion analytics.

---

## Architecture Overview

```
Concurrent Clients / Browser UI / Load Generator
                       │
                       ▼
             FastAPI Application (Port 8000)
             (REST Endpoints, Idempotency, SSE)
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
    MongoDB 7.0                  Redis 7.2
 (Durable NoSQL Store)     (Coordination & Streams)
  • products (catalog)      • reserve.lua (atomic decr)
  • bookings (audit)        • release.lua (atomic incr)
  • idempotency_records     • confirm.lua (atomic state)
  • reservation_operations  • concord:events (streams)
         │                           │
         └─────────────┬─────────────┘
                       │
                       ▼
              Background Workers
         (Hold Expiration & Reconciliation)
```

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
Verify containers are healthy:
```bash
docker compose ps
```

### 2. Set Up Python Environment
Create and activate the virtual environment:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Initialize & Seed Database
Initialize collections, schema validation, and indexes:
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

## Running the Automated Test Suite

Run all 13 unit, integration, concurrency, and failure recovery tests:
```powershell
python -m pytest -v tests/
```

Test coverage includes:
- MongoDB CRUD operations and unique name validation.
- Guarded product deletion blocking active holds.
- Redis Lua atomic reservation, confirmation, and cancellation.
- Mandatory 100-client race test on 10 units (asserting zero oversell).
- Idempotency key deduplication and payload conflict detection (`409 Conflict`).
- Automatic hold expiration and inventory restitution.
- Cross-database reconciliation of simulated orphan holds.
- Redis Streams lifecycle event emission and SSE retrieval.
- MongoDB aggregation pipeline outputs.

---

## Running the High-Contention Load Benchmark

Simulate 100 concurrent clients competing for 10 units with 50 simultaneous connections:
```powershell
python -m load_test.load_test --url http://localhost:8000 --clients 100 --stock 10 --concurrency 50
```

Sample Benchmark Output:
```
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

---

## API Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Health check for MongoDB and Redis |
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

---

## Documentation & Evidence Index

- [Technical Architecture](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/technical_architecture.md)
- [MongoDB Schema & Collection Specifications](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/mongodb_schema.json)
- [MongoDB Aggregation Pipelines & Real Outputs](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/aggregation_examples.md)
- [REST API Reference & Examples](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/api_examples.md)
- [Test & Benchmark Evidence Report](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/test_evidence.md)
- [Implementation Status Matrix](file:///c:/Users/HP/OneDrive/Desktop/Concord/docs/implementation_status.md)
