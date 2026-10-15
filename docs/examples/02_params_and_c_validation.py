"""
02_params_and_c_validation.py - Path, Query, Header & Cookie Parameters
========================================================================

In Buckthorn, all request parameter parsing and boundary validation happens
directly in compiled C (`bt_params.c` and `picohttpparser`), before Python objects
are ever allocated. If a constraint is violated, Buckthorn immediately returns
an RFC-compliant `422 Unprocessable Entity` JSON response with zero Python overhead.

This example demonstrates:
1. Path parameters with numeric constraints (`Path(ge=1, le=10000)`).
2. Query parameters with default values and ranges (`Query(default=20, ge=1, le=100)`).
3. Custom request Headers (`Header(alias="X-Request-ID")`).
4. Cookies (`Cookie(alias="session_id")`).
5. How invalid inputs automatically trigger C-level 422 error responses.

To run this live server in development mode:
    buckthorn dev docs/examples/02_params_and_c_validation.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/02_params_and_c_validation.py
"""

from typing import Optional
from buckthorn import Buckthorn, Path, Query, Header, Cookie

app = Buckthorn(
    title="Parameter Validation API",
    version="1.0.0",
)


# 1. Path Parameter with Numeric Boundary Validation (ge: greater-than-or-equal)
# In Buckthorn, `{item_id}` is extracted directly from the C URI slice pointers.
# `Path(ge=1, le=50000)` is validated via SIMD/C arithmetic without `int()` parsing in Python.
@app.get("/items/{item_id}")
def get_item(
    item_id: int = Path(ge=1, le=50000),
):
    """Fetches an item by its ID. item_id must be between 1 and 50,000."""
    return {"item_id": item_id, "status": "active"}


# 2. Query Parameters with Defaults and Constraints
# If `limit` or `offset` are missing from the URL query string, default values are used.
# If provided, they are validated against `ge` and `le` in C.
@app.get("/search")
def search_items(
    q: str = Query(min_length=2, max_length=50),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Searches items with pagination. q must be 2-50 characters."""
    return {
        "query": q,
        "limit": limit,
        "offset": offset,
        "results": [f"Result #{i}" for i in range(offset, offset + limit)],
    }


# 3. Header and Cookie Parameters with Aliases
# Headers and cookies are extracted from zero-copy C slice tables.
@app.get("/client-info")
def get_client_info(
    user_agent: str = Header(alias="User-Agent", default="unknown"),
    client_id: str = Header(alias="X-Client-ID", default="guest"),
    session_id: Optional[str] = Cookie(alias="session_id", default=None),
):
    """Extracts custom HTTP headers and browser cookies."""
    return {
        "user_agent": user_agent,
        "client_id": client_id,
        "session_id": session_id or "no-session",
    }


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 02_params_and_c_validation.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Valid Path Parameter
    res = client.get("/items/42")
    assert res.status_code == 200
    assert res.json() == {"item_id": 42, "status": "active"}
    print(f"  ✓ Valid Path -> 200 {res.json()}")

    # 2. Invalid Path Parameter (item_id < 1 violates ge=1 constraint)
    # The C validator detects this instantly and returns 422
    res_err = client.get("/items/0")
    assert res_err.status_code == 422
    print(f"  ✓ Boundary Check (ge=1) -> 422 {res_err.json()}")

    # 3. Valid Query Parameters
    res_search = client.get("/search?q=laptop&limit=5&offset=10")
    assert res_search.status_code == 200
    data = res_search.json()
    assert data["query"] == "laptop"
    assert len(data["results"]) == 5
    print(f"  ✓ Valid Query -> 200 (Fetched {len(data['results'])} items)")

    # 4. Invalid Query Parameter (limit exceeds max le=100)
    res_query_err = client.get("/search?q=laptop&limit=500")
    assert res_query_err.status_code == 422
    print(f"  ✓ Query Constraint Check (le=100) -> 422 {res_query_err.json()}")

    # 5. Header and Cookie Parameters
    raw_req = (
        b"GET /client-info HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"User-Agent: BuckthornClient/1.0\r\n"
        b"X-Client-ID: developer_99\r\n"
        b"Cookie: session_id=sess_abc123\r\n\r\n"
    )
    res_headers = client.send_raw(raw_req)
    assert res_headers.status_code == 200
    h_data = res_headers.json()
    assert h_data["client_id"] == "developer_99"
    assert h_data["session_id"] == "sess_abc123"
    print(f"  ✓ Headers & Cookies -> 200 {h_data}")

    print("\n✅ All 02_params_and_c_validation.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/02_params_and_c_validation.py:app")
