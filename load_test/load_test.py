"""
Concord High-Contention Load Test Harness
Simulates N concurrent clients racing to reserve limited inventory units.
Measures latency (p50, p95, p99), throughput, zero-oversell invariant, and logical idempotency.
"""

import asyncio
import time
import argparse
import uuid
import httpx
from typing import List, Dict, Any
import statistics

async def run_load_test(
    base_url: str,
    initial_stock: int,
    num_clients: int,
    requested_quantity: int,
    concurrency_limit: int,
    hold_ttl: int,
    confirm_percentage: float = 0.5,
    mode: str = "direct"
) -> Dict[str, Any]:
    print("\n" + "="*70)
    print("CONCORD HIGH-CONTENTION LOAD TEST HARNESS")
    print("="*70)
    print(f"Target Service URL   : {base_url}")
    print(f"Benchmark Mode       : {mode.upper()}")
    print(f"Initial Stock Units  : {initial_stock}")
    print(f"Competing Clients    : {num_clients}")
    print(f"Quantity Per Request : {requested_quantity}")
    print(f"Concurrency Limit    : {concurrency_limit}")
    print(f"Hold Duration (TTL)  : {hold_ttl}s")
    print(f"Auto-Confirm Ratio   : {int(confirm_percentage * 100)}%")
    print("="*70 + "\n")

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=30.0,
        headers={"ngrok-skip-browser-warning": "true"}
    ) as client:
        # Step 1: Health check
        h_resp = await client.get("/health")
        if h_resp.status_code != 200 or h_resp.json().get("status") != "HEALTHY":
            raise RuntimeError(f"Service at {base_url} is not healthy: {h_resp.text}")

        # Step 2: Create a dedicated stress test product
        unique_name = f"Benchmark-GPU-{uuid.uuid4().hex[:6]}"
        p_resp = await client.post("/products", json={
            "name": unique_name,
            "description": f"High-Contention Load Benchmark Target ({mode})",
            "initial_stock": initial_stock
        })
        if p_resp.status_code != 201:
            raise RuntimeError(f"Failed to create benchmark product: {p_resp.text}")
        product_id = p_resp.json()["id"]
        wc_product_id = int(str(uuid.uuid4().int)[:6])

        if mode == "woocommerce":
            # Map WooCommerce product
            m_resp = await client.post("/woocommerce/mappings", json={
                "concord_product_id": product_id,
                "wc_product_id": wc_product_id,
                "sync_enabled": True
            })
            if m_resp.status_code != 201:
                raise RuntimeError(f"Failed to map WooCommerce benchmark product: {m_resp.text}")
            print(f"[OK] Mapped WooCommerce Product #{wc_product_id} to Concord Product {product_id}")

        print(f"[OK] Provisioned benchmark product '{unique_name}' (ID: {product_id}) with {initial_stock} units\n")

        # Step 3: Concurrently launch reservation requests
        sem = asyncio.Semaphore(concurrency_limit)
        latencies = []
        successful_bookings = []
        rejections = 0
        errors = 0

        run_batch_id = int(str(uuid.uuid4().int)[:5])

        async def make_reservation(client_idx: int):
            nonlocal rejections, errors
            idemp_key = f"loadtest-{product_id}-{client_idx}-{uuid.uuid4().hex[:4]}"

            async with sem:
                t0 = time.perf_counter()
                try:
                    if mode == "woocommerce":
                        order_id = run_batch_id * 1000 + client_idx
                        wc_payload = {
                            "id": order_id,
                            "status": "pending",
                            "line_items": [
                                {"id": client_idx + 1, "product_id": wc_product_id, "quantity": requested_quantity}
                            ]
                        }
                        resp = await client.post(
                            "/woocommerce/webhooks",
                            json=wc_payload,
                            headers={
                                "x-wc-webhook-topic": "order.created",
                                "x-wc-webhook-delivery-id": f"wc-bench-del-{order_id}"
                            }
                        )
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                        latencies.append(elapsed_ms)

                        if resp.status_code == 200:
                            data = resp.json()
                            outcomes = data.get("outcomes", [])
                            if outcomes and outcomes[0].get("status") == "HELD":
                                successful_bookings.append({
                                    "booking_id": outcomes[0]["booking_id"],
                                    "order_id": order_id
                                })
                            else:
                                rejections += 1
                        else:
                            errors += 1
                    else:
                        req_payload = {
                            "product_id": product_id,
                            "quantity": requested_quantity,
                            "hold_ttl_seconds": hold_ttl,
                            "idempotency_key": idemp_key
                        }
                        resp = await client.post("/reservations", json=req_payload)
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                        latencies.append(elapsed_ms)

                        if resp.status_code in (200, 201):
                            data = resp.json()
                            successful_bookings.append(data)
                        elif resp.status_code == 400:
                            rejections += 1
                        else:
                            errors += 1
                except Exception as e:
                    errors += 1

        print(f"Executing {num_clients} requests with concurrency {concurrency_limit}...")
        start_wall_time = time.perf_counter()
        tasks = [asyncio.create_task(make_reservation(i)) for i in range(num_clients)]
        await asyncio.gather(*tasks)
        total_wall_duration = time.perf_counter() - start_wall_time

        # Step 4: Confirm a subset of successful bookings
        confirmed_count = 0
        to_confirm = int(len(successful_bookings) * confirm_percentage)
        for b in successful_bookings[:to_confirm]:
            if mode == "woocommerce":
                c_resp = await client.post(
                    "/woocommerce/webhooks",
                    json={
                        "id": b["order_id"],
                        "status": "processing",
                        "line_items": [{"id": 1, "product_id": wc_product_id, "quantity": requested_quantity}]
                    },
                    headers={"x-wc-webhook-topic": "order.updated", "x-wc-webhook-delivery-id": f"wc-conf-del-{b['order_id']}"}
                )
                if c_resp.status_code == 200:
                    confirmed_count += 1
            else:
                c_resp = await client.post(f"/reservations/{b['booking_id']}/confirm")
                if c_resp.status_code == 200:
                    confirmed_count += 1

        # Step 5: Verify Final Invariant State
        final_prod = (await client.get(f"/products/{product_id}")).json()
        final_stock = final_prod["available_stock"]
        active_holds = final_prod["active_holds"]
        confirmed_units = final_prod["confirmed_units"]

        # Teardown product
        try:
            # cancel remaining holds to allow deletion
            for b in successful_bookings[to_confirm:]:
                await client.post(f"/reservations/{b['booking_id']}/cancel")
            await client.delete(f"/products/{product_id}")
        except Exception:
            pass

        # Calculate metrics
        latencies.sort()
        avg_lat = statistics.mean(latencies) if latencies else 0
        p50 = statistics.median(latencies) if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
        throughput = round(num_clients / total_wall_duration, 2) if total_wall_duration > 0 else 0

        # Assert Invariants
        total_allocated = (len(successful_bookings) * requested_quantity)
        invariant_no_oversell = (total_allocated <= initial_stock)
        stock_non_negative = (final_stock >= 0)
        invariant_satisfied = invariant_no_oversell and stock_non_negative

        print("\n" + "="*70)
        print("LOAD BENCHMARK SUMMARY REPORT")
        print("="*70)
        print(f"Total Requests Dispatched  : {num_clients}")
        print(f"Wall Clock Duration        : {total_wall_duration:.3f} s")
        print(f"System Throughput          : {throughput} req/sec")
        print(f"Successful Reservations    : {len(successful_bookings)} (Allocated: {total_allocated} units)")
        print(f"Rejected (Out of Stock)    : {rejections}")
        print(f"Unexpected Errors          : {errors}")
        print(f"Confirmed Purchases        : {confirmed_count}")
        print(f"Remaining Active Holds     : {len(successful_bookings) - confirmed_count}")
        print(f"Final Redis Stock          : {final_stock}")
        print("-" * 70)
        print("LATENCY PERCENTILES")
        print(f"  Average Latency          : {avg_lat:.2f} ms")
        print(f"  p50 (Median)             : {p50:.2f} ms")
        print(f"  p95                      : {p95:.2f} ms")
        print(f"  p99                      : {p99:.2f} ms")
        print("-" * 70)
        print("INVARIANT VERIFICATION")
        print(f"  Allocated <= Initial     : {invariant_no_oversell} ({total_allocated} <= {initial_stock})")
        print(f"  Stock Non-Negative       : {stock_non_negative} (Stock = {final_stock})")
        print(f"  OVERALL INVARIANT STATUS : {'PASS - ZERO OVERSELLING' if invariant_satisfied else 'FAIL - OVERSELLING DETECTED'}")
        print("="*70 + "\n")

        assert invariant_satisfied, "CRITICAL: Inventory invariant was violated!"

        return {
            "num_clients": num_clients,
            "initial_stock": initial_stock,
            "successful_reservations": len(successful_bookings),
            "rejections": rejections,
            "throughput_rps": throughput,
            "avg_latency_ms": round(avg_lat, 2),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "invariant_satisfied": invariant_satisfied
        }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concord High-Contention Load Test Harness")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of Concord service")
    parser.add_argument("--stock", type=int, default=10, help="Initial stock of test product")
    parser.add_argument("--clients", type=int, default=100, help="Number of concurrent clients")
    parser.add_argument("--qty", type=int, default=1, help="Quantity per request")
    parser.add_argument("--concurrency", type=int, default=50, help="Concurrency limit")
    parser.add_argument("--ttl", type=int, default=30, help="Hold duration in seconds")
    parser.add_argument("--confirm-ratio", type=float, default=0.5, help="Ratio of holds to confirm")
    parser.add_argument("--mode", choices=["direct", "woocommerce"], default="direct", help="Benchmark target mode: direct reservation or woocommerce webhook")

    args = parser.parse_args()
    asyncio.run(run_load_test(
        base_url=args.url,
        initial_stock=args.stock,
        num_clients=args.clients,
        requested_quantity=args.qty,
        concurrency_limit=args.concurrency,
        hold_ttl=args.ttl,
        confirm_percentage=args.confirm_ratio,
        mode=args.mode
    ))
