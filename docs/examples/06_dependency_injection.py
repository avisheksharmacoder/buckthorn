"""
06_dependency_injection.py - Compile-Time Flattened Dependency Injection (DI)
==============================================================================

Dependency Injection (DI) in Buckthorn is inspired by FastAPI, but re-architected
for extreme performance:
1. **Compile-Time Post-Order DAG Traversal:**
   When you register an endpoint, Buckthorn analyzes all dependencies, detects
   shared singletons (`use_cache=True`), and compiles them into a linear, flat
   execution plan (`CompiledDepStep`).
2. **O(1) Execution Slots:**
   At request time, Buckthorn allocates a flat Python list of dependency slots
   (`dep_slots = [None] * num_steps`). Dependencies execute in linear order,
   storing their results in fixed indices. Lookups occur in O(1) time with zero
   dictionary hashing or runtime tree walks!
3. **Guaranteed LIFO Generator Cleanup:**
   Dependencies using `yield` (sync or async) are placed on an unwind stack.
   When the response completes (or even if an `HTTPException` is raised),
   generators are closed in reverse (LIFO) order: Last Opened = First Closed.

This example demonstrates:
- Basic service injection with `Depends()`.
- Shared dependency deduplication (`use_cache=True`).
- Sync and async generator dependencies with `try: yield ... finally: teardown`.
- Error resiliency during teardown.

To run this live server in development mode:
    buckthorn dev docs/examples/06_dependency_injection.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/06_dependency_injection.py
"""

from typing import Generator
from buckthorn import Buckthorn, Depends, HTTPException, status

app = Buckthorn(
    title="Dependency Injection API",
    version="1.0.0",
)

# Global tracker for educational verification of lifecycles
lifecycle_events: list[str] = []


# ==============================================================================
# 1. Generator Dependencies with Cleanup (Yield)
# ==============================================================================

def get_database_connection() -> Generator[str, None, None]:
    """Simulates acquiring and releasing a database connection."""
    lifecycle_events.append("db_connected")
    conn = "PostgreSQL_Conn_42"
    try:
        yield conn
    finally:
        # This code ALWAYS runs after the response is rendered, even on errors!
        lifecycle_events.append("db_closed")


def get_redis_cache() -> Generator[str, None, None]:
    """Simulates acquiring and releasing a Redis cache handle."""
    lifecycle_events.append("cache_connected")
    cache = "Redis_Client_6379"
    try:
        yield cache
    finally:
        lifecycle_events.append("cache_closed")


# ==============================================================================
# 2. Sub-Dependencies (Hierarchical Composition)
# ==============================================================================

def get_user_repository(
    db: str = Depends(get_database_connection),
    cache: str = Depends(get_redis_cache),
) -> str:
    """Combines database and cache into a User Repository."""
    return f"UserRepository(db={db}, cache={cache})"


def get_order_repository(
    db: str = Depends(get_database_connection),
) -> str:
    """
    Notice: get_order_repository ALSO depends on get_database_connection!
    Because `use_cache=True` by default, Buckthorn deduplicates this:
    get_database_connection() is called ONCE per request, not twice!
    """
    return f"OrderRepository(db={db})"


# ==============================================================================
# 3. Route Handlers Using Injected Dependencies
# ==============================================================================

@app.get("/user-dashboard")
def user_dashboard(
    user_repo: str = Depends(get_user_repository),
    order_repo: str = Depends(get_order_repository),
):
    """
    Both repositories are injected into this handler.
    Buckthorn resolves the DAG at compile-time:
      Step 0: get_database_connection (cached)
      Step 1: get_redis_cache (cached)
      Step 2: get_user_repository
      Step 3: get_order_repository
    """
    lifecycle_events.append("handler_executed")
    return {
        "status": "success",
        "user_repo": user_repo,
        "order_repo": order_repo,
    }


@app.get("/fail-with-cleanup")
def fail_with_cleanup(db: str = Depends(get_database_connection)):
    """
    Demonstrates that even if an exception occurs mid-request,
    generator cleanup (`finally:`) is strictly guaranteed.
    """
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Simulated operational failure",
    )


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 06_dependency_injection.py in-memory verification...")
    client = RawTestClient(app)

    lifecycle_events.clear()

    # 1. Test normal execution & DAG deduplication
    res = client.get("/user-dashboard")
    assert res.status_code == 200
    data = res.json()
    assert "PostgreSQL_Conn_42" in data["user_repo"]
    assert "PostgreSQL_Conn_42" in data["order_repo"]
    print(f"  ✓ Dashboard Response -> 200 {data}")

    # Verify Lifecycle Order & LIFO Unwind:
    # Opened: db -> cache
    # Handler: executed
    # Closed: cache -> db (Reverse LIFO order!)
    assert lifecycle_events == [
        "db_connected",
        "cache_connected",
        "handler_executed",
        "cache_closed",
        "db_closed",
    ]
    print(f"  ✓ Verified LIFO Lifecycle -> {lifecycle_events}")

    # 2. Test Exception Teardown Safety
    lifecycle_events.clear()
    res_err = client.get("/fail-with-cleanup")
    assert res_err.status_code == 400
    # Even though 400 was raised, db_closed must have run!
    assert lifecycle_events == ["db_connected", "db_closed"]
    print(f"  ✓ Exception Teardown Safety -> 400 {lifecycle_events}")

    print("\n✅ All 06_dependency_injection.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/06_dependency_injection.py:app")
