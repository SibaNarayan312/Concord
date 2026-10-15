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

## 2. Deployment Options

### Option A: Self-Hosted Production with Docker Compose (Recommended)
Suitable for any VPS (AWS EC2, DigitalOcean Droplet, Hetzner Cloud, GCP Compute Engine).

#### Step 1: Clone Repository & Setup Environment
```bash
git clone https://github.com/SibaNarayan312/Concord.git
cd Concord
cp .env.production.example .env.production
nano .env.production  # Fill in production values
```

#### Step 2: Build & Launch Production Containers
```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

#### Step 3: Verify Health & Port Isolation
Check container status and health:
```bash
docker compose -f docker-compose.prod.yml ps
```

Verify that MongoDB and Redis ports are NOT accessible externally:
```bash
curl -I http://localhost:27017  # Expected: Connection refused
curl -I http://localhost:6379   # Expected: Connection refused
curl http://localhost:8000/health  # Expected: HTTP 200 {"status":"healthy",...}
```

#### Step 4: Configure Reverse Proxy (Caddy / Nginx)
Example Caddy configuration (`/etc/caddy/Caddyfile`):
```caddy
concord.yourdomain.com {
    reverse_proxy localhost:8000 {
        # SSE Streaming Buffer Disabling
        header_up X-Accel-Buffering "no"
    }
}
```

---

### Option B: Managed Cloud Services (Render / Railway / GCP Cloud Run)
For fully managed cloud deployments:
1. **Databases**:
   - Provision a MongoDB cluster on **MongoDB Atlas** (Free M0 or Shared).
   - Provision a Redis instance on **Upstash Redis** or **Redis Enterprise Cloud**.
2. **Compute (FastAPI)**:
   - Deploy Dockerfile to **Render**, **Railway**, or **Google Cloud Run**.
   - Configure Environment Variables matching `.env.production`.
   - Set container port to `8000`.

---

### Option C: Live Public Demonstrator via Ngrok Tunnel
For presentations, academic evaluations, or live WooCommerce webhook testing without cloud hosting costs:

```powershell
# 1. Start Concord Backend locally
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 2. Launch public TLS tunnel
ngrok http 8000
```

Concord includes an active, validated public demonstrator endpoint:
- **Public HTTPS Base URL**: `https://ripping-yelling-kinsman.ngrok-free.dev`
- **Dashboard UI**: `https://ripping-yelling-kinsman.ngrok-free.dev/`
- **Health Check**: `https://ripping-yelling-kinsman.ngrok-free.dev/health`
- **WooCommerce Webhook URL**: `https://ripping-yelling-kinsman.ngrok-free.dev/woocommerce/webhooks`

---

## 3. Operational Monitoring & Background Workers

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
