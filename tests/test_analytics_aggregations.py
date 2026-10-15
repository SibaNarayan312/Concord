import pytest
import httpx

@pytest.mark.asyncio
async def test_analytics_endpoints_and_aggregations(client: httpx.AsyncClient):
    # Overview
    ov_resp = await client.get("/analytics/overview")
    assert ov_resp.status_code == 200
    ov_data = ov_resp.json()
    assert "total_products" in ov_data
    assert "total_initial_inventory" in ov_data
    assert "inventory_invariant_satisfied" in ov_data

    # Pipeline 1: Bookings by Status
    status_resp = await client.get("/analytics/bookings-by-status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert isinstance(status_data, list)

    # Pipeline 2: Product Performance
    perf_resp = await client.get("/analytics/product-performance")
    assert perf_resp.status_code == 200
    perf_data = perf_resp.json()
    assert isinstance(perf_data, list)
    if perf_data:
        assert "product_name" in perf_data[0]
        assert "initial_stock" in perf_data[0]
        assert "available_stock" in perf_data[0]

    # Pipeline 4: Confirmed vs Expired
    ratio_resp = await client.get("/analytics/confirmed-vs-expired")
    assert ratio_resp.status_code == 200

    # Pipeline 5: Operations stats
    ops_resp = await client.get("/analytics/operations")
    assert ops_resp.status_code == 200

    # Invariants verification
    inv_resp = await client.get("/analytics/invariants")
    assert inv_resp.status_code == 200
    inv_data = inv_resp.json()
    assert isinstance(inv_data, list)

    # Indexes inspection
    idx_resp = await client.get("/analytics/indexes")
    assert idx_resp.status_code == 200
    idx_data = idx_resp.json()
    assert "products" in idx_data
    assert "bookings" in idx_data
    assert "idempotency_records" in idx_data
    assert "reservation_operations" in idx_data
