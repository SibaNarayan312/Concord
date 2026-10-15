# MongoDB Aggregation Pipelines in Concord
**Technical Evidence & Pipeline Documentation for Review 2**

Review 2 requires verified, non-trivial MongoDB aggregation pipelines rather than hardcoded responses. Concord implements real aggregation pipelines executed directly against MongoDB collections (`bookings`, `products`, `reservation_operations`).

---

## Pipeline 1: Booking Counts and Quantities Grouped by Lifecycle Status

### Objective
Summarize the volume and total inventory units occupied across all finite state machine states (`HELD`, `CONFIRMED`, `EXPIRED`, `CANCELLED`).

### Endpoint
`GET /analytics/bookings-by-status`

### MongoDB Query / Pipeline Syntax
```python
pipeline = [
    {
        "$group": {
            "_id": "$status",
            "count": {"$sum": 1},
            "total_quantity": {"$sum": "$quantity"}
        }
    },
    {
        "$sort": {"count": -1}
    }
]
```

### Stage Analysis
1. **`$group`**: Groups documents by the `$status` string field. Computes two accumulators:
   - `count`: uses `{"$sum": 1}` to count total booking events in that state.
   - `total_quantity`: uses `{"$sum": "$quantity"}` to sum the physical product units allocated.
2. **`$sort`**: Orders the resulting status buckets in descending order of frequency.

### Actual Output Captured from Concord Instance
```json
[
  {
    "status": "EXPIRED",
    "count": 27,
    "total_quantity": 34
  },
  {
    "status": "CONFIRMED",
    "count": 10,
    "total_quantity": 15
  },
  {
    "status": "CANCELLED",
    "count": 5,
    "total_quantity": 5
  }
]
```

---

## Pipeline 2: Product Performance Multi-Collection Join (`$lookup` & `$project`)

### Objective
Cross-correlate catalog products with booking history to compute conversion statistics (active holds, confirmed purchases, expired holds, and total units sold) for each product.

### Endpoint
`GET /analytics/product-performance`

### MongoDB Query / Pipeline Syntax
```python
pipeline = [
    {
        "$lookup": {
            "from": "bookings",
            "localField": "_id",
            "foreignField": "product_id",
            "as": "bookings_data"
        }
    },
    {
        "$project": {
            "product_id": "$_id",
            "product_name": "$name",
            "initial_stock": "$initial_stock",
            "total_bookings": {"$size": "$bookings_data"},
            "active_holds": {
                "$sum": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$bookings_data",
                                "as": "b",
                                "cond": {"$eq": ["$$b.status", "HELD"]}
                            }
                        },
                        "as": "hb",
                        "in": "$$hb.quantity"
                    }
                }
            },
            "confirmed_count": {
                "$size": {
                    "$filter": {
                        "input": "$bookings_data",
                        "as": "b",
                        "cond": {"$eq": ["$$b.status", "CONFIRMED"]}
                    }
                }
            },
            "expired_count": {
                "$size": {
                    "$filter": {
                        "input": "$bookings_data",
                        "as": "b",
                        "cond": {"$eq": ["$$b.status", "EXPIRED"]}
                    }
                }
            },
            "cancelled_count": {
                "$size": {
                    "$filter": {
                        "input": "$bookings_data",
                        "as": "b",
                        "cond": {"$eq": ["$$b.status", "CANCELLED"]}
                    }
                }
            },
            "total_units_sold": {
                "$sum": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$bookings_data",
                                "as": "b",
                                "cond": {"$eq": ["$$b.status", "CONFIRMED"]}
                            }
                        },
                        "as": "cb",
                        "in": "$$cb.quantity"
                    }
                }
            }
        }
    },
    {
        "$sort": {"total_bookings": -1}
    }
]
```

### Stage Analysis
1. **`$lookup`**: Performs a left outer join from the `products` collection to the `bookings` collection matching `products._id` to `bookings.product_id`.
2. **`$project` with array expressions (`$filter`, `$map`, `$sum`, `$size`)**:
   - Isolates specific states in the embedded booking documents without requiring memory-intensive `$unwind` stages.
   - Computes units sold and holds in a single efficient query execution.
3. **`$sort`**: Sorts products by popularity (total booking requests received).

### Actual Output Captured from Concord Instance
```json
[
  {
    "product_id": "prod-ps5-pro",
    "product_name": "Sony PlayStation 5 Pro Console 2TB",
    "initial_stock": 25,
    "available_stock": 23,
    "active_holds": 0,
    "confirmed_count": 1,
    "expired_count": 0,
    "cancelled_count": 0,
    "total_units_sold": 2
  },
  {
    "product_id": "prod-concert-vip",
    "product_name": "Coldplay Music of the Spheres VIP Ticket",
    "initial_stock": 50,
    "available_stock": 50,
    "active_holds": 0,
    "confirmed_count": 0,
    "expired_count": 1,
    "cancelled_count": 0,
    "total_units_sold": 0
  }
]
```

---

## Pipeline 3: Total Inventory and Catalog Metrics

### Objective
Calculate aggregate catalog inventory and total distinct product records across the platform.

### Pipeline Syntax
```python
pipeline = [
    {
        "$group": {
            "_id": None,
            "total_initial_stock": {"$sum": "$initial_stock"},
            "total_products": {"$sum": 1}
        }
    }
]
```

---

## Pipeline 4: Confirmed vs. Expired Conversion Analysis

### Objective
Assess customer checkout conversion rates by contrasting successfully confirmed bookings against expired reservations.

### Endpoint
`GET /analytics/confirmed-vs-expired`

### Pipeline Syntax
```python
pipeline = [
    {
        "$match": {
            "status": {"$in": ["CONFIRMED", "EXPIRED"]}
        }
    },
    {
        "$group": {
            "_id": "$status",
            "count": {"$sum": 1},
            "total_units": {"$sum": "$quantity"}
        }
    }
]
```

---

## Pipeline 5: Cross-Database Operation Audit Statistics

### Objective
Audits the outcomes of all cross-database reservation attempts tracked in `reservation_operations`.

### Endpoint
`GET /analytics/operations`

### Pipeline Syntax
```python
pipeline = [
    {
        "$group": {
            "_id": "$status",
            "count": {"$sum": 1}
        }
    },
    {
        "$sort": {"count": -1}
    }
]
```
