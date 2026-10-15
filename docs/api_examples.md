# Concord REST API Documentation & Examples
**Endpoints, Request Payloads, Response Formats, and Error Scenarios**

The service runs on `http://localhost:8000`. Interactive OpenAPI documentation is accessible at `http://localhost:8000/docs`.

---

## 1. Health & Telemetry

### Check Service Health
```http
GET /health
```
**Response (200 OK):**
```json
{
  "status": "HEALTHY",
  "service": "Concord Inventory Reservation Service",
  "environment": "development",
  "latency_ms": 1.25,
  "components": {
    "mongodb": "UP",
    "redis": "UP"
  },
  "details": {
    "mongo_database": "concord",
    "redis_uri": "redis://localhost:6379/0"
  }
}
```

---

## 2. Product Catalog Management

### Create Product
```http
POST /products
Content-Type: application/json

{
  "name": "NVIDIA GeForce RTX 5090 32GB",
  "description": "Next-gen Blackwell gaming flagship GPU",
  "initial_stock": 10
}
```
**Response (201 Created):**
```json
{
  "id": "e8d9c1b2-3a4f-4d5e-9f6a-7b8c9d0e1f2a",
  "name": "NVIDIA GeForce RTX 5090 32GB",
  "description": "Next-gen Blackwell gaming flagship GPU",
  "initial_stock": 10,
  "available_stock": 10,
  "active_holds": 0,
  "confirmed_units": 0,
  "created_at": "2026-10-14T05:20:00.000Z",
  "updated_at": "2026-10-14T05:20:00.000Z"
}
```

### List All Products
```http
GET /products
```

### Get Product by ID
```http
GET /products/{product_id}
```

### Update Product
```http
PUT /products/{product_id}
Content-Type: application/json

{
  "description": "Updated product description"
}
```

### Delete Product (Safely Guarded)
```http
DELETE /products/{product_id}
```
**Error Response if Active Holds or Confirmed Bookings Exist (400 Bad Request):**
```json
{
  "detail": "Cannot delete product with 2 active holds and 1 confirmed bookings."
}
```

### Reset Product Inventory (Test Helper)
```http
POST /products/{product_id}/reset?stock=10
```

---

## 3. Inventory Reservation Lifecycle

### Create Reservation Hold
```http
POST /reservations
Content-Type: application/json

{
  "product_id": "prod-rtx-4090",
  "quantity": 2,
  "hold_ttl_seconds": 30,
  "idempotency_key": "idemp-client-txn-99482"
}
```
**Response (201 Created):**
```json
{
  "booking_id": "8f3b2a1c-9d0e-4f5a-8b7c-6d5e4f3a2b1c",
  "product_id": "prod-rtx-4090",
  "quantity": 2,
  "status": "HELD",
  "expires_at": "2026-10-14T05:20:30.000Z",
  "idempotency_key": "idemp-client-txn-99482",
  "message": "Reserved 2 units successfully. Hold expires at 2026-10-14T05:20:30.000Z.",
  "cached": false,
  "remaining_stock": 8
}
```

### Duplicate Request with Same Idempotency Key (Idempotent Retry)
```http
POST /reservations
Content-Type: application/json

{
  "product_id": "prod-rtx-4090",
  "quantity": 2,
  "idempotency_key": "idemp-client-txn-99482"
}
```
**Response (200 OK / 201 Created):**
```json
{
  "booking_id": "8f3b2a1c-9d0e-4f5a-8b7c-6d5e4f3a2b1c",
  "product_id": "prod-rtx-4090",
  "quantity": 2,
  "status": "HELD",
  "expires_at": "2026-10-14T05:20:30.000Z",
  "idempotency_key": "idemp-client-txn-99482",
  "message": "Reserved 2 units successfully. Hold expires at 2026-10-14T05:20:30.000Z.",
  "cached": true,
  "remaining_stock": 8
}
```
*Note: Inventory is not decremented twice; original outcome is returned from cache.*

### Idempotency Key Reused with Different Payload (Conflict Detection)
```http
POST /reservations
Content-Type: application/json

{
  "product_id": "prod-rtx-4090",
  "quantity": 5,
  "idempotency_key": "idemp-client-txn-99482"
}
```
**Error Response (409 Conflict):**
```json
{
  "detail": "Idempotency key conflict: This key was previously used with a different request payload."
}
```

### Insufficient Stock Error
```http
POST /reservations
Content-Type: application/json

{
  "product_id": "prod-rtx-4090",
  "quantity": 50,
  "idempotency_key": "idemp-client-excess-1"
}
```
**Error Response (400 Bad Request):**
```json
{
  "detail": "Insufficient inventory available for product prod-rtx-4090. Available: 8"
}
```

### Confirm Reservation
```http
POST /reservations/{booking_id}/confirm
```
**Response (200 OK):**
```json
{
  "booking_id": "8f3b2a1c-9d0e-4f5a-8b7c-6d5e4f3a2b1c",
  "product_id": "prod-rtx-4090",
  "quantity": 2,
  "status": "CONFIRMED",
  "message": "Booking confirmed successfully."
}
```

### Cancel Reservation
```http
POST /reservations/{booking_id}/cancel
```
**Response (200 OK):**
```json
{
  "booking_id": "8f3b2a1c-9d0e-4f5a-8b7c-6d5e4f3a2b1c",
  "product_id": "prod-rtx-4090",
  "quantity": 2,
  "status": "CANCELLED",
  "message": "Booking cancelled and inventory returned to available stock."
}
```

---

## 4. Live Contention Stream & SSE

### Subscribe to Event Stream
```http
GET /events/stream?replay=true
Accept: text/event-stream
Last-Event-ID: 1728883200000-0
```

### Sample SSE Data Frame
```
event: HOLD_CREATED
id: 1728883215000-0
data: {"event_id": "1728883215000-0", "event_type": "HOLD_CREATED", "product_id": "prod-rtx-4090", "booking_id": "8f3b2a1c-9d0e-4f5a-8b7c-6d5e4f3a2b1c", "quantity": 2, "timestamp": "2026-10-14T05:20:15Z", "outcome": "SUCCESS", "remaining_stock": "8"}
```
