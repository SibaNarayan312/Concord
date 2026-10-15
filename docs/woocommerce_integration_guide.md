# WooCommerce Integration Guide

## Overview

Concord integrates with WooCommerce test stores to provide **high-contention inventory protection** and **atomic holds** for flash-sales, limited-edition merchandise drops, and ticket sales. Traditional WooCommerce stores process orders through relational database transactions (MySQL), which can suffer from overselling when dozens of concurrent customers reach checkout for the final units simultaneously.

Concord solves this by sitting between the WooCommerce checkout lifecycle and an in-memory Redis atomic allocation engine, backed by MongoDB for durable auditing.

---

## 1. System Architecture & Lifecycle Mapping

```
 [WooCommerce Test Store]
   │
   ├─► 1. Checkout Initiated / Order Created (status: "pending")
   │    │
   │    ▼ Webhook: order.created (HMAC-SHA256 signed)
   │   [Concord API: /woocommerce/webhooks]
   │    │
   │    ▼ Product Mapping Lookup (WC ID -> Concord ID)
   │    ▼ Atomic Hold via Redis Lua (reserve.lua)
   │    │── If Out of Stock: Hold Rejected (Oversell Alert Streamed)
   │    └── If Stock Available: Hold Created (TTL: 900s)
   │
   ├─► 2. Payment Succeeded (status: "processing" / "completed")
   │    │
   │    ▼ Webhook: order.updated
   │   [Concord API: /woocommerce/webhooks]
   │    │
   │    ▼ Atomic Confirm via Redis Lua (confirm.lua)
   │    ▼ Durable Booking Record in MongoDB
   │    ▼ Live SSE Contention Stream Broadcast
   │    ▼ Async Stock Sync to WooCommerce Store (PUT /wp-json/wc/v3/products/{id})
   │
   └─► 3. Payment Failed / Order Cancelled (status: "cancelled" / "failed")
        │
        ▼ Webhook: order.updated
       [Concord API: /woocommerce/webhooks]
        │
        ▼ Atomic Release via Redis Lua (release.lua)
        ▼ Inventory Restored to Pool
        ▼ Booking Marked as CANCELLED
        ▼ Async Stock Sync to WooCommerce Store
```

---

## 2. Order & Hold State Machine

| WooCommerce Event | Order Status | Concord Action | Redis State | MongoDB Audit Record |
| :--- | :--- | :--- | :--- | :--- |
| `order.created` | `pending` | `create_reservation()` | `stock:avail` decr, `hold:{id}` set with TTL | `reservation_operations` (PENDING) |
| `order.updated` | `processing` or `completed` | `confirm_booking()` | `hold:{id}` deleted, `stock:total` decr | `bookings` (CONFIRMED) |
| `order.updated` | `cancelled` or `failed` | `cancel_booking()` | `hold:{id}` deleted, `stock:avail` incr | `bookings` (CANCELLED) |
| Timeout (>900s) | Any | Background Sweeper | `hold:{id}` expired, `stock:avail` incr | `reservation_operations` (EXPIRED) |

### Compensation Path for Late Payments
If a customer's payment succeeds after their 15-minute hold expires:
1. Concord detects that the hold was already expired by the background sweeper.
2. Concord executes a re-reservation attempt.
3. If stock is still available, the order is confirmed immediately.
4. If stock was already claimed by another buyer, Concord logs an `OVERSELL_ALERT` event to Redis Streams and flags the order in `woocommerce_orders` for store administrator review/refund.

---

## 3. Step-by-Step WooCommerce Store Setup

### Step 3.1: Generate WooCommerce REST API Credentials
1. Log in to your WordPress Admin dashboard.
2. Navigate to **WooCommerce** -> **Settings** -> **Advanced** -> **REST API**.
3. Click **Add key**.
4. Description: `Concord Reservation Integration`.
5. User: Select your administrator account.
6. Permissions: `Read/Write`.
7. Click **Generate API key**.
8. Save the **Consumer Key** (`ck_...`) and **Consumer Secret** (`cs_...`).

### Step 3.2: Configure Concord Environment Variables
Add the credentials to your Concord `.env` file:
```env
WC_STORE_URL="https://your-store.example.com"
WC_CONSUMER_KEY="ck_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
WC_CONSUMER_SECRET="cs_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
WC_WEBHOOK_SECRET="whsec_my_secure_webhook_passphrase_123"
WC_HOLD_TTL_SECONDS=900
WC_STOCK_SYNC_ENABLED="true"
WC_VERIFY_WEBHOOK_SIGNATURE="true"
WC_MOCK_MODE="false"
```

### Step 3.3: Configure Webhooks in WooCommerce
1. In WordPress Admin, navigate to **WooCommerce** -> **Settings** -> **Advanced** -> **Webhooks**.
2. Click **Add webhook**.
3. Create Webhook 1 — Order Created:
   - **Name**: `Concord Order Created`
   - **Status**: `Active`
   - **Topic**: `Order created`
   - **Delivery URL**: `https://<your-concord-domain>/woocommerce/webhooks`
   - **Secret**: Value of `WC_WEBHOOK_SECRET`
   - **API Version**: `WP REST API v3`
4. Create Webhook 2 — Order Updated:
   - **Name**: `Concord Order Updated`
   - **Status**: `Active`
   - **Topic**: `Order updated`
   - **Delivery URL**: `https://<your-concord-domain>/woocommerce/webhooks`
   - **Secret**: Value of `WC_WEBHOOK_SECRET`
   - **API Version**: `WP REST API v3`

---

## 4. Drop-in WordPress / WooCommerce Plugin

To block checkouts synchronously before payment is authorized, Concord provides a drop-in WordPress plugin located in `integrations/woocommerce/concord-woocommerce.php`.

### Installation:
1. Copy `integrations/woocommerce/concord-woocommerce.php` into your WordPress installation:
   `wp-content/plugins/concord-woocommerce/concord-woocommerce.php`
2. In WordPress Admin, navigate to **Plugins** -> **Installed Plugins**.
3. Activate **Concord WooCommerce Inventory Bridge**.
4. In **Settings** -> **Concord Inventory**, configure:
   - **Concord API Base URL**: `https://<your-concord-domain>`
   - **Hold TTL (seconds)**: `900`
   - **Webhook Secret**: Matching secret

The plugin hooks into `woocommerce_checkout_order_processed` to ensure reservations are acquired before order completion, preventing race conditions at checkout.

---

## 5. Product Mapping Management

Concord maintains an authoritative mapping between WooCommerce product IDs (and variations) and Concord MongoDB inventory IDs.

### Create a Mapping via REST API:
```http
POST /woocommerce/mappings HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "wc_product_id": 101,
  "wc_variation_id": 0,
  "wc_product_name": "GeForce RTX 4090 OC",
  "concord_product_id": "673f1234abcd0001",
  "sync_stock_enabled": true
}
```

### List Active Mappings:
```http
GET /woocommerce/mappings HTTP/1.1
Host: localhost:8000
```

### Auto-Map Demo Items:
In the Concord Dashboard, switch to the **WooCommerce Store** tab and click **Auto-Map Demo Items**. Concord will automatically map sample demo items (RTX 4090, VIP Ticket, PS5 Pro) to test WooCommerce IDs `101`, `102`, and `103`.

---

## 6. Stock Synchronization

Concord can push authoritative in-memory Redis inventory levels back to WooCommerce so that customer-facing catalog displays stay accurate.

### Endpoints:
- `POST /woocommerce/sync/{concord_product_id}`: Syncs a single mapped product.
- `POST /woocommerce/sync-all`: Syncs all active mappings in a batch.

### Behavior:
- Calls WooCommerce REST API `PUT /wp-json/wc/v3/products/{id}`.
- Sets `manage_stock: true`, `stock_quantity: <redis_available_stock>`, and `stock_status: "instock" | "outofstock"`.
- In Mock Mode (`WC_MOCK_MODE=true`), updates are simulated in-memory and logged without making external network calls.

---

## 7. Testing & Checkout Simulation

Concord provides an integrated simulation endpoint for end-to-end verification without needing a live browser checkout session:

```http
POST /woocommerce/simulate-checkout HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "wc_product_id": 101,
  "quantity": 1,
  "customer_email": "shopper@example.com",
  "auto_confirm": true
}
```

Response:
```json
{
  "success": true,
  "order_id": 482190,
  "wc_product_id": 101,
  "concord_product_id": "673f1234abcd0001",
  "hold_id": "hold_e9f1a23c4d5b",
  "booking_id": "673f9876ef010002",
  "status": "completed",
  "allocated_stock": 1,
  "message": "Order 482190 simulated successfully and confirmed."
}
```

All simulated orders stream to the Concord Dashboard in real-time under event type `WOOCOMMERCE_ORDER_RESERVED` and `WOOCOMMERCE_ORDER_CONFIRMED`.
