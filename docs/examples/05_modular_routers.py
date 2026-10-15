"""
05_modular_routers.py - Structuring Large Apps with APIRouter
==============================================================

In real-world applications, you don't put all route handlers in a single file.
Buckthorn provides `APIRouter`, which allows you to group related endpoints
into modular sub-routers (e.g. `users`, `orders`, `billing`).

Key Architecture Detail:
Unlike Python ASGI frameworks that traverse nested router objects at runtime
for every incoming request, Buckthorn **flattens** all prefixes, dependencies,
and tags into the compiled C `rax` radix tree at application startup.
This means modularized apps run with the exact same nanosecond lookup speed
as single-file apps!

This example demonstrates:
1. Creating independent sub-routers using `APIRouter(prefix=..., tags=...)`.
2. Mounting sub-routers into the main application with `app.include_router()`.
3. Nested routing patterns.

To run this live server in development mode:
    buckthorn dev docs/examples/05_modular_routers.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/05_modular_routers.py
"""

from buckthorn import Buckthorn, APIRouter, Path, Schema, status

# ==============================================================================
# 1. Users Router (`/api/v1/users`)
# ==============================================================================
users_router = APIRouter(prefix="/users", tags=["Users"])


class UserProfile(Schema):
    id: int
    username: str
    role: str


@users_router.get("/")
def list_users():
    """Lists all active user names in the system."""
    return {
        "count": 2,
        "usernames": ["admin", "dev_alex"],
    }


@users_router.get("/{user_id}")
def get_user_profile(user_id: int = Path(ge=1)):
    """Fetches details for a specific user ID."""
    return {"id": user_id, "username": f"user_{user_id}", "status": "active"}


# ==============================================================================
# 2. Orders Router (`/api/v1/orders`)
# ==============================================================================
orders_router = APIRouter(prefix="/orders", tags=["Orders"])


class OrderCreate(Schema):
    item: str
    quantity: int


@orders_router.post("/", status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate):
    """Submits a new customer order."""
    return {
        "order_id": 9901,
        "item": order.item,
        "quantity": order.quantity,
        "status": "placed",
    }


@orders_router.get("/{order_id}")
def get_order_status(order_id: int = Path(ge=1)):
    """Checks the delivery status of an order."""
    return {"order_id": order_id, "status": "shipped"}


# ==============================================================================
# 3. Main Application Assembly
# ==============================================================================
app = Buckthorn(
    title="Modular Enterprise API",
    version="1.0.0",
)

# Root-level health endpoint
@app.get("/health")
def health():
    return {"status": "ok"}


# Include both sub-routers under a shared `/api/v1` prefix!
# At startup, Buckthorn pre-computes `/api/v1/users`, `/api/v1/orders`, etc.,
# and inserts them directly into the C11 radix tree.
app.include_router(users_router, prefix="/api/v1")
app.include_router(orders_router, prefix="/api/v1")


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 05_modular_routers.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Test root health endpoint
    res_h = client.get("/health")
    assert res_h.status_code == 200
    print(f"  ✓ Root /health -> 200 {res_h.json()}")

    # 2. Test Users Router under /api/v1 prefix
    res_users = client.get("/api/v1/users")
    assert res_users.status_code == 200
    assert res_users.json()["count"] == 2
    assert "admin" in res_users.json()["usernames"]
    print(f"  ✓ GET /api/v1/users -> 200 {res_users.json()}")

    res_user = client.get("/api/v1/users/42")
    assert res_user.status_code == 200
    assert res_user.json()["id"] == 42
    print(f"  ✓ GET /api/v1/users/42 -> 200 {res_user.json()}")

    # 3. Test Orders Router under /api/v1 prefix
    order_payload = {"item": "Server Rack Unit", "quantity": 2}
    res_order = client.post("/api/v1/orders", json=order_payload)
    assert res_order.status_code == 201
    assert res_order.json()["order_id"] == 9901
    print(f"  ✓ POST /api/v1/orders -> 201 {res_order.json()}")

    res_order_get = client.get("/api/v1/orders/9901")
    assert res_order_get.status_code == 200
    assert res_order_get.json()["status"] == "shipped"
    print(f"  ✓ GET /api/v1/orders/9901 -> 200 {res_order_get.json()}")

    print("\n✅ All 05_modular_routers.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/05_modular_routers.py:app")
