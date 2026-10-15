"""
04_crud_backend_api.py - Complete CRUD Backend Service with HTTPException
==========================================================================

This example demonstrates how to build a complete, production-style CRUD
(Create, Read, Update, Delete) REST API using Buckthorn:
1. Defining domain schemas for inputs (`ProductCreate`, `ProductUpdate`) and outputs (`ProductOut`).
2. Managing an in-memory database store.
3. Raising custom `HTTPException(status_code=..., detail=...)` for missing or conflicting resources.
4. Pagination using query parameters (`limit`, `offset`).
5. Standard REST HTTP status codes (`201 Created`, `200 OK`, `404 Not Found`).

To run this live server in development mode:
    buckthorn dev docs/examples/04_crud_backend_api.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/04_crud_backend_api.py
"""

from typing import Optional
from buckthorn import Buckthorn, Schema, Field, Path, Query, HTTPException, status

app = Buckthorn(
    title="Product Catalog CRUD API",
    version="1.0.0",
)


# ==============================================================================
# Domain Schemas
# ==============================================================================

class ProductCreate(Schema):
    name: str = Field(min_length=2, max_length=50)
    category: str = Field(min_length=2, max_length=30)
    price: float = Field(gt=0.0)
    stock: int = Field(default=0, ge=0)


class ProductUpdate(Schema):
    name: str = Field(min_length=2, max_length=50)
    price: float = Field(gt=0.0)
    stock: int = Field(ge=0)


class ProductOut(Schema):
    id: int
    name: str
    category: str
    price: float
    stock: int


# ==============================================================================
# In-Memory Database Store
# ==============================================================================
products_db: dict[int, ProductOut] = {}
next_product_id: int = 1


# ==============================================================================
# CRUD Route Handlers
# ==============================================================================

# 1. CREATE
@app.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(product_in: ProductCreate):
    """Creates a new product in the catalog."""
    global next_product_id
    pid = next_product_id
    next_product_id += 1

    product = ProductOut(
        id=pid,
        name=product_in.name,
        category=product_in.category,
        price=product_in.price,
        stock=product_in.stock,
    )
    products_db[pid] = product
    return product


# 2. READ (List with Pagination)
@app.get("/products")
def list_products(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Lists products with limit/offset pagination."""
    all_items = list(products_db.values())
    paged = all_items[offset : offset + limit]
    return {
        "total": len(all_items),
        "limit": limit,
        "offset": offset,
        "items": [item.model_dump() for item in paged],
    }


# 3. READ (Single Item by ID)
@app.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int = Path(ge=1)):
    """Retrieves a single product by ID. Raises 404 if not found."""
    product = products_db.get(product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product #{product_id} does not exist",
        )
    return product


# 4. UPDATE
@app.put("/products/{product_id}", response_model=ProductOut)
def update_product(
    update_data: ProductUpdate,
    product_id: int = Path(ge=1),
):
    """Updates an existing product's name, price, and stock."""
    product = products_db.get(product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cannot update: Product #{product_id} not found",
        )

    product.name = update_data.name
    product.price = update_data.price
    product.stock = update_data.stock
    return product


# 5. DELETE
@app.delete("/products/{product_id}")
def delete_product(product_id: int = Path(ge=1)):
    """Deletes a product by ID."""
    if product_id not in products_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cannot delete: Product #{product_id} not found",
        )
    del products_db[product_id]
    return {"deleted": True, "product_id": product_id}


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 04_crud_backend_api.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Test CREATE
    create_payload = {
        "name": "Wireless Mouse",
        "category": "Electronics",
        "price": 29.99,
        "stock": 50,
    }
    res_create = client.post("/products", json=create_payload)
    assert res_create.status_code == 201
    created_item = res_create.json()
    item_id = created_item["id"]
    assert created_item["name"] == "Wireless Mouse"
    print(f"  ✓ CREATE -> 201 {created_item}")

    # 2. Test READ (Single)
    res_get = client.get(f"/products/{item_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == item_id
    print(f"  ✓ READ Single -> 200 {res_get.json()}")

    # 3. Test UPDATE
    update_payload = {
        "name": "Ergonomic Wireless Mouse",
        "price": 34.99,
        "stock": 45,
    }
    res_update = client.put(f"/products/{item_id}", json=update_payload)
    assert res_update.status_code == 200
    assert res_update.json()["name"] == "Ergonomic Wireless Mouse"
    assert res_update.json()["price"] == 34.99
    print(f"  ✓ UPDATE -> 200 {res_update.json()}")

    # 4. Test LIST
    res_list = client.get("/products?limit=10&offset=0")
    assert res_list.status_code == 200
    assert res_list.json()["total"] == 1
    print(f"  ✓ LIST -> 200 Total: {res_list.json()['total']}")

    # 5. Test DELETE
    res_del = client.delete(f"/products/{item_id}")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] is True
    print(f"  ✓ DELETE -> 200 {res_del.json()}")

    # 6. Test 404 NOT FOUND after deletion
    res_404 = client.get(f"/products/{item_id}")
    assert res_404.status_code == 404
    assert "not exist" in res_404.json()["detail"]
    print(f"  ✓ 404 NOT FOUND -> 404 {res_404.json()}")

    print("\n✅ All 04_crud_backend_api.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/04_crud_backend_api.py:app")
