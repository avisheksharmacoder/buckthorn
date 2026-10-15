"""
16_fastapi_migration.py - Migrating from FastAPI to Buckthorn
==============================================================

Buckthorn was deliberately designed with maximum developer empathy for FastAPI
engineers. The Python developer API is nearly 1-to-1 compatible with FastAPI,
allowing you to port existing microservices with minimal modifications while
gaining a 5x to 10x throughput improvement (>80,000 to >120,000 RPS).

---

### 🔄 Equivalence Cheat Sheet:

| FastAPI Construct | Buckthorn Equivalent | Notes |
|:---|:---|:---|
| `from fastapi import FastAPI` | `from buckthorn import Buckthorn` | Direct drop-in replacement |
| `from pydantic import BaseModel` | `from buckthorn import Schema` | Compiles to C struct in Prod |
| `from pydantic import Field` | `from buckthorn import Field` | Same `ge`, `le`, `min_length` constraints |
| `from fastapi import Depends` | `from buckthorn import Depends` | Flattened compile-time DAG |
| `from fastapi import Path, Query` | `from buckthorn import Path, Query` | Evaluated in C via `bt_params.c` |
| `from fastapi import BackgroundTasks` | `from buckthorn import BackgroundTasks` | Freelist-backed post-response queue |
| `from fastapi import HTTPException` | `from buckthorn import HTTPException` | Standard status_code & detail |
| `from fastapi import APIRouter` | `from buckthorn import APIRouter` | Radix tree flattening at startup |
| `from fastapi import UploadFile, File` | `from buckthorn import UploadFile, File` | Zero-copy C slice pointers |
| `from fastapi.security import OAuth2PasswordBearer` | `from buckthorn.security import OAuth2PasswordBearer` | OpenSSL C verification |

---

This example demonstrates a complete service written in Buckthorn using patterns
identical to standard FastAPI code.

To run this live server in development mode:
    buckthorn dev docs/examples/16_fastapi_migration.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/16_fastapi_migration.py
"""

import asyncio
from typing import Generator
from buckthorn import (
    Buckthorn,
    Schema,
    Field,
    Path,
    Query,
    Depends,
    BackgroundTasks,
    HTTPException,
    status,
)

# 1. Initialize Application (Identical to `app = FastAPI(...)`)
app = Buckthorn(
    title="FastAPI-to-Buckthorn Migration Example",
    version="1.0.0",
)


# 2. Schemas (Replace `BaseModel` with `Schema`)
class ItemCreate(Schema):
    title: str = Field(min_length=2, max_length=50)
    price: float = Field(gt=0.0)
    tax: float = Field(default=0.0, ge=0.0)
    tags: list[str] = []


class ItemResponse(Schema):
    id: int
    title: str
    price: float
    total: float


# 3. Dependencies (Identical to FastAPI `Depends`)
def get_db_session() -> Generator[str, None, None]:
    """Database session generator dependency."""
    session = "Postgres_Session_Pool"
    try:
        yield session
    finally:
        pass  # session closed


# 4. Background Task Function
async def notify_warehouse(item_id: int):
    await asyncio.sleep(0.001)


# 5. Route Handlers (Notice: identical signature and syntax to FastAPI!)
@app.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    item: ItemCreate,
    bg: BackgroundTasks,
    db: str = Depends(get_db_session),
):
    """
    Looks and feels 100% like a FastAPI endpoint, but executes with:
    1. Zero pure-Python JSON deserialization (C yyjson).
    2. Zero heap malloc during request handling (64KB C arena).
    3. Zero asyncio.Task overhead for eager paths.
    """
    item_id = 42
    total_price = round(item.price + item.tax, 2)

    # Queue post-response task
    bg.add_task(notify_warehouse, item_id)

    return ItemResponse(
        id=item_id,
        title=item.title,
        price=item.price,
        total=total_price,
    )


@app.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(
    item_id: int = Path(ge=1),
    include_tax: bool = Query(default=True),
    db: str = Depends(get_db_session),
):
    """Fetches an existing item by ID."""
    if item_id != 42:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found",
        )

    base_price = 19.99
    tax = 1.60 if include_tax else 0.0
    return ItemResponse(
        id=item_id,
        title="Ergonomic Mousepad",
        price=base_price,
        total=round(base_price + tax, 2),
    )


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 16_fastapi_migration.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Test POST /items
    payload = {
        "title": "USB-C Hub",
        "price": 49.99,
        "tax": 4.50,
        "tags": ["hardware", "usb"],
    }
    res = client.post("/items", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"] == 42
    assert data["title"] == "USB-C Hub"
    assert data["total"] == 54.49
    print(f"  ✓ POST /items (FastAPI syntax on Buckthorn) -> 201 {data}")

    # 2. Test GET /items/42
    res_get = client.get("/items/42?include_tax=true")
    assert res_get.status_code == 200
    assert res_get.json()["total"] == 21.59
    print(f"  ✓ GET /items/42 -> 200 {res_get.json()}")

    # 3. Test 404
    res_404 = client.get("/items/999")
    assert res_404.status_code == 404
    print(f"  ✓ GET /items/999 (404 Error) -> 404 {res_404.json()}")

    print("\n✅ All 16_fastapi_migration.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/16_fastapi_migration.py:app")
