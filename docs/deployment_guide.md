# Concord Production Deployment & Operations Guide

## Overview

This guide details the production deployment, security hardening, cloud configuration, and disaster recovery procedures for **Concord: High-Contention Inventory Reservation Service with WooCommerce Integration**.

---

## 1. Production Architecture & Security Boundaries

```
[ Public Internet ]
        │
        │ HTTPS (TLS 1.3 / Port 443)
        ▼
[ Reverse Proxy / Cloud Load Balancer / Ngrok Tunnel ]
        │
        │ HTTP (Port 8000)
        ▼
[ Concord FastAPI Backend Container ]
        │
        │ Private Docker Bridge Network (`concord_internal`)
        │ (NO public port exposure, strictly isolated)
        ├───────────────────────────────┐
        ▼                               ▼
[ Redis 7.2 Container ]       [ MongoDB 7.0 Container ]
  - Port 6379 (Internal only)   - Port 27017 (Internal only)
  - AOF & RDB Persistence        - Persistent WiredTiger Storage
  - Maxmemory 512MB noeviction   - Compound Unique Indexes
```

### Security Hardening Standards
1. **Network Isolation**: In `docker-compose.prod.yml`, Redis and MongoDB omit `ports:` mappings. They are reachable **only** by the `concord_prod_backend` container over `concord_internal`.
2. **Secret Hygiene**: Real passwords, API keys, and webhook secrets are injected via `.env.production` and never committed to source control.
3. **Webhook Authenticity**: All incoming WooCommerce requests require HMAC-SHA256 signature verification matching `WC_WEBHOOK_SECRET`.
4. **Idempotency & Replay Protection**: Each webhook delivery ID is persisted in MongoDB (`woocommerce_webhooks`) with a unique index, neutralizing duplicate payloads.

---

## 2. Cloud Hosting Feasibility Comparison (Student Portfolio Analysis)

| Hosting Provider | Architecture Pattern | Monthly Cost | Free Tier Limitations | Persistent MongoDB | Persistent Redis (AOF/Lua) | SSE Streaming Support | WooCommerce Webhook Reliability | Deployment Effort |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DigitalOcean / Hetzner VPS** *(Recommended)* | Single-node Docker Compose (`docker-compose.prod.yml`) | $4 - $6 / mo | None (standard compute) | **Native** (Docker named volume) | **Native** (Docker volume, AOF everysec) | **Full** (Nginx / Caddy `X-Accel-Buffering: no`) | **High** (Static IP, zero sleep, immediate response) | Low (Docker Compose) |
| **Railway** | Multi-service project (FastAPI + Mongo + Redis plugins) | ~$5 / mo | $5 trial credit | **Plugin / Volume** | **Plugin / Volume** | **Supported** | **High** (Always on, no sleep) | Very Low (Git push deploy) |
| **Render** | Web Service + External Databases | $0 - $7 / mo | Free tier spins down after 15 min inactivity | Requires MongoDB Atlas | Requires Upstash / Redis Cloud | Supported (HTTP/2) | **POOR on Free Tier** (Sleep causes 504 timeouts on webhooks!) | Low |
| **Fly.io** | Fly Machines + Fly Volumes | $0 - $5 / mo | Free allowance limited | Fly Volumes (single node) | Fly Volumes (standalone) | **Supported** | **Medium** (Ensure `auto_stop_machines = false`) | Medium |
| **GCP Cloud Run + Atlas + Upstash** | Serverless Container + Managed DBs | Pay-per-use (~$0 - $3) | Free tier on Atlas (512MB) & Upstash | Atlas M0 Free Tier | Upstash Redis REST / Socket | Supported with min-instances = 1 | **High** if min-instances >= 1 | Medium-High |
| **Local Demonstrator (Ngrok)** | Local Docker Compose + Ngrok TLS | $0 | Session reconnect on restart | Local volume | Local volume | Full support | **High** during active demo sessions | Instant |

### Critical Technical Warning: Sleeping / Scale-to-Zero Hazards
> [!CAUTION]
> **Never host WooCommerce webhook consumers on a scale-to-zero / sleeping free tier!**
> Free-tier PaaS services (such as Render Free or Glitch) spin down containers after 15 minutes of inactivity. When a customer places an order on WooCommerce:
> 1. WooCommerce fires the `order.created` webhook with a strict HTTP timeout (usually 5–10 seconds).
> 2. A sleeping backend requires 30–60 seconds for a cold boot.
> 3. WooCommerce encounters an HTTP 504 Gateway Timeout or Connection Dropped.
> 4. The order is left in `pending` on WordPress without an atomic reservation hold acquired in Concord, risking an oversell if another shopper checks out before WooCommerce retries!
> **Production Rule**: The Concord backend MUST run with `min-instances >= 1` or on an always-on VPS container.

---

## 3. State Persistence & Architectural Decoupling

Concord cleanly separates stateful storage from stateless execution:

1. **Stateful Components (Persistent Volumes Required)**:
   - **MongoDB (`mongo_prod_data:/data/db`)**: Holds catalog definitions, historical audit bookings, idempotency deduplication fingerprints, and WooCommerce mapping definitions. Losing this volume deletes audit lineage.
   - **Redis (`redis_prod_data:/data`)**: Holds active stock counters, active hold hashes, and the `concord:events` stream. Configured with `--appendonly yes` and `--appendfsync everysec` so restarts restore live balances without overselling.
2. **Stateless Components (Horizontally Scalable)**:
   - **FastAPI Backend (`concord_prod_backend`)**: Contains the reservation engine, WooCommerce webhook bridge, and dashboard UI. Multiple backend replicas can run simultaneously behind a load balancer, coordinating via the shared Redis instance and MongoDB cluster.
   - **Static Dashboard**: Can be served directly by FastAPI or deployed to a static CDN (Vercel, Cloudflare Pages, Netlify) pointing to the API.

---

## 4. Production Environment Variables Checklist

Use this checklist when provisioning `.env.production`. **Never enter real secrets into version-controlled templates!**

```ini
# Application Configuration
PROJECT_NAME="Concord Inventory Reservation Service"
ENVIRONMENT="production"
HOST="0.0.0.0"
PORT=8000

# Private Internal Database URIs (In Docker: mongodb and redis hostnames)
MONGO_URI="mongodb://mongodb:27017"
MONGO_DB_NAME="concord"
REDIS_URI="redis://redis:6379/0"

# Inventory Hold & Worker Policies
DEFAULT_HOLD_TTL_SECONDS=30
EXPIRATION_WORKER_INTERVAL_SECONDS=2
RECONCILIATION_WORKER_INTERVAL_SECONDS=5
MAX_RESERVATION_QUANTITY=100
STREAM_MAX_LEN=10000

# WooCommerce Production Store Credentials (REST API)
WC_STORE_URL="https://your-production-woocommerce-store.com"
WC_CONSUMER_KEY="ck_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
WC_CONSUMER_SECRET="cs_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Webhook Shared Secret (Configured in WooCommerce Admin -> Advanced -> Webhooks)
WC_WEBHOOK_SECRET="whsec_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# WooCommerce Hold & Synchronization Settings
WC_HOLD_TTL_SECONDS=900
WC_STOCK_SYNC_ENABLED="true"
WC_VERIFY_WEBHOOK_SIGNATURE="true"
WC_MOCK_MODE="false"
```

---

## 5. Deployment Step-by-Step Procedure

### Step 1: Provision Host & Install Docker
Ensure Docker Engine 24+ and Docker Compose v2+ are installed on the VPS or host.

### Step 2: Configure Environment
```bash
cp .env.production.example .env.production
chmod 600 .env.production
# Edit .env.production with production credentials
```

### Step 3: Launch Isolated Infrastructure
```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

### Step 4: Verify Health & Network Isolation
```bash
# Verify health
curl -s http://localhost:8000/health | jq .

# Verify private database ports are NOT open publicly
nc -zv 127.0.0.1 27017  # Must fail / refuse connection
nc -zv 127.0.0.1 6379   # Must fail / refuse connection
```

### Step 5: Configure Reverse Proxy (Caddy / Nginx) with TLS
Configure Caddy to manage automatic Let's Encrypt certificates:
```caddy
concord.yourdomain.com {
    reverse_proxy localhost:8000 {
        # Required for Server-Sent Events (SSE) live stream
        header_up X-Accel-Buffering "no"
    }
}
```

### Step 6: Register Webhook in WooCommerce Admin
1. Navigate to **WooCommerce Admin -> Settings -> Advanced -> Webhooks**.
2. Click **Add Webhook**.
3. Name: `Concord Order Created`
4. Status: `Active`
5. Topic: `Order created`
6. Delivery URL: `https://concord.yourdomain.com/woocommerce/webhooks`
7. Secret: Must match `WC_WEBHOOK_SECRET` in `.env.production`.
8. API Version: `WP REST API Integration v3`.
9. Click **Save Webhook**. WooCommerce will dispatch a reachability ping (`webhook_id=...`), which Concord will acknowledge with HTTP 200.
10. Repeat for topic `Order updated`.

---

## 6. Operational Monitoring & Background Workers

### Automatic Health Verification
Concord exposes a comprehensive `/health` endpoint:
```json
{
  "status": "healthy",
  "environment": "production",
  "components": {
    "mongodb": {
      "status": "up",
      "latency_ms": 1.2
    },
    "redis": {
      "status": "up",
      "latency_ms": 0.5
    }
  },
  "background_workers": {
    "expiration_worker": "active",
    "reconciliation_worker": "active"
  }
}
```

### Background Workers Lifecycle
- **Hold Expiration Worker**: Runs every `EXPIRATION_WORKER_INTERVAL_SECONDS` (default: 2s). Scans Redis `hold:*` keys, identifies TTL expiries, and executes `release.lua` to return unclaimed stock.
- **Cross-Database Reconciliation Worker**: Runs every `RECONCILIATION_WORKER_INTERVAL_SECONDS` (default: 5s). Cross-references MongoDB `bookings` and Redis `stock:avail`, repairing any orphan holds from network partitions or ungraceful worker terminations.

---

## 4. Backup & Disaster Recovery

### MongoDB Automated Backup
Create a snapshot of all persistent collections (`products`, `bookings`, `idempotency_records`, `reservation_operations`, `woocommerce_mappings`, `woocommerce_orders`, `woocommerce_webhooks`):
```bash
docker exec -t concord_prod_mongodb mongodump --db concord --out /data/db/backup_$(date +%F)
```

### Redis Persistence & Snapshot
Redis is configured with dual persistence:
1. **AOF (Append Only File)**: Syncs every second (`appendfsync everysec`).
2. **RDB Snapshots**: Triggers on 10 writes per 300 seconds.
Manual backup snapshot:
```bash
docker exec -t concord_prod_redis redis-cli BGSAVE
```

### Rollback Procedure
If a bad build or regression is deployed:
1. Stop the failing container:
   ```bash
   docker compose -f docker-compose.prod.yml stop backend
   ```
2. Revert Git checkout to the last known stable commit tag:
   ```bash
   git checkout <last_stable_commit>
   ```
3. Rebuild and launch the previous stable version:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build backend
   ```
4. Run integration tests to verify database integrity:
   ```bash
   python -m pytest tests/
   ```
