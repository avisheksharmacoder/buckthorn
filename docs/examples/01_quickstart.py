"""
01_quickstart.py - Minimal Buckthorn Backend Server
=====================================================

This example introduces the absolute basics of building a web application with Buckthorn:
1. Creating a `Buckthorn` application instance.
2. Defining HTTP route handlers with decorators (`@app.get`, `@app.post`, `@app.put`, `@app.delete`).
3. Returning native Python dicts (serialized automatically in C using SIMD yyjson).
4. Returning custom HTTP status codes.

To run this live server in development mode:
    buckthorn dev docs/examples/01_quickstart.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/01_quickstart.py
"""

from buckthorn import Buckthorn, status

# 1. Initialize the main Buckthorn application instance
# In Buckthorn, initializing the app prepares the C11 radix-tree router and memory arena.
app = Buckthorn(
    title="Quickstart API",
    version="1.0.0",
)


# 2. Simple GET endpoint returning JSON
# In Buckthorn, Python dicts returned from route handlers are serialized into JSON
# using `yyjson` in pure C, completely bypassing Python's slow `json.dumps`.
@app.get("/")
def read_root():
    """Returns a simple greeting message."""
    return {"message": "Hello, Buckthorn!", "engine": "C11 + Cython"}


# 3. Health check endpoint (common pattern in production backends)
@app.get("/health")
def health_check():
    """A lightweight endpoint for Kubernetes/Docker health probes."""
    return {"status": "ok", "healthy": True}


# 4. POST endpoint accepting and returning data with a 201 Created status
# Notice how we pass `status_code=status.HTTP_201_CREATED` directly in the decorator.
@app.post("/items", status_code=status.HTTP_201_CREATED)
def create_item():
    """Simulates creating a new item."""
    return {"id": 101, "name": "Mechanical Keyboard", "created": True}


# 5. PUT endpoint for updates
@app.put("/items/101")
def update_item():
    """Simulates updating an existing resource."""
    return {"id": 101, "name": "Custom Mechanical Keyboard", "updated": True}


# 6. DELETE endpoint
@app.delete("/items/101", status_code=status.HTTP_200_OK)
def delete_item():
    """Simulates deleting a resource."""
    return {"id": 101, "deleted": True}


# ==============================================================================
# Self-Verification Test Runner
# Running `python 01_quickstart.py` tests all routes locally in memory without
# needing to open a live TCP socket!
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 01_quickstart.py in-memory verification...")

    client = RawTestClient(app)

    # Test GET /
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert res_root.json()["message"] == "Hello, Buckthorn!"
    print(f"  ✓ GET / -> {res_root.status_code} {res_root.json()}")

    # Test GET /health
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"
    print(f"  ✓ GET /health -> {res_health.status_code} {res_health.json()}")

    # Test POST /items
    res_post = client.post("/items")
    assert res_post.status_code == 201
    assert res_post.json()["created"] is True
    print(f"  ✓ POST /items -> {res_post.status_code} {res_post.json()}")

    # Test PUT /items/101
    res_put = client.put("/items/101")
    assert res_put.status_code == 200
    assert res_put.json()["updated"] is True
    print(f"  ✓ PUT /items/101 -> {res_put.status_code} {res_put.json()}")

    # Test DELETE /items/101
    res_del = client.delete("/items/101")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] is True
    print(f"  ✓ DELETE /items/101 -> {res_del.status_code} {res_del.json()}")

    print("\n✅ All 01_quickstart.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/01_quickstart.py:app")
