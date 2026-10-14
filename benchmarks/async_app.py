# benchmarks/async_app.py
"""
Buckthorn 100% Asynchronous Enterprise Cloud Commerce Benchmark
================================================================================

This benchmark implements the exact same enterprise cloud commerce architecture
as `benchmarks/app.py`, but with 100% asynchronous routes and async dependencies:
  * 100% `async def` route handlers across all 4 endpoints.
  * Async dependency injection graph (`async def` parameter extraction).
  * Async generator database session lifecycle (`async def` with `yield` and LIFO teardown).
  * Asynchronous Tier-2 HTTP telemetry middleware.
  * Native C OpenSSL JWT authentication & yyjson SIMD AST schema validation.
  * Response model filtering in pure C.

================================================================================
HOW TO RUN THE 100% ASYNC SERVER:
================================================================================
Single Worker:
    python benchmarks/async_app.py

Multi-Worker (SO_REUSEPORT across all CPU cores):
    for i in $(seq 1 $(nproc)); do python benchmarks/async_app.py & done

To stop all background workers:
    pkill -f "benchmarks/async_app.py"

================================================================================
WRK BENCHMARK COMMANDS:
================================================================================
1. Mixed Traffic (75% GET Inventory, 20% POST Order, 5% GET Analytics):
    wrk -t1 -c100 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/

2. Multi-Core Mixed Traffic (4 threads, 100 connections):
    wrk -t4 -c100 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/

3. Authenticated POST Checkout with JWT:
    wrk -t1 -c100 -d10s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/stores/101/checkout
"""

import time
from typing import Optional
from buckthorn import (
    Buckthorn,
    Schema,
    Field,
    Path,
    Query,
    Header,
    Depends,
    Security,
    Request,
    HTTPException,
    status,
)
from buckthorn.middleware import (
    CORSMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from buckthorn.security import (
    HTTPBearer,
    APIKeyHeader,
    create_jwt_token,
)

# ---------------------------------------------------------------------------
# Application Initialization & Middlewares
# ---------------------------------------------------------------------------
app = Buckthorn(
    title="Buckthorn 100% Async Commerce Benchmark",
    version="1.0.0",
)

# Tier-1 Pre-Baked C Middlewares (Executed in C without GIL acquisition)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dashboard.buckthorn.io"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID", "X-API-Key"],
)
app.add_middleware(
    SecurityHeadersMiddleware,
    x_frame_options="DENY",
    x_content_type_options="nosniff",
    strict_transport_security="max-age=63072000; includeSubDomains",
)
app.add_middleware(RateLimitMiddleware, max_requests=10_000_000, window_seconds=60)

# Tier-2 Cython HTTP Middleware Hook (Request timing & server metadata)
@app.middleware("http")
async def add_timing_and_telemetry(request: Request, call_next):
    start_ns = time.perf_counter_ns()
    response = await call_next(request)
    elapsed_us = (time.perf_counter_ns() - start_ns) / 1000.0
    response.headers["X-Process-Time-Us"] = f"{elapsed_us:.1f}"
    response.headers["X-Server-Node"] = "buckthorn-c-async-worker"
    return response


# ---------------------------------------------------------------------------
# Cryptographic Security Schemes
# ---------------------------------------------------------------------------
JWT_SECRET = "buckthorn-enterprise-bench-secret-2026"
jwt_bearer = HTTPBearer(jwt_secret=JWT_SECRET)
api_key_guard = APIKeyHeader(name="X-API-Key", expected_key="live_bench_api_key_999")

# Pre-generate deterministic benchmark JWT token matching wrk scripts
BENCH_TOKEN = create_jwt_token(
    {
        "sub": "merchant_7701",
        "org_id": "org_enterprise_alpha",
        "role": "admin",
        "scopes": ["orders:write", "inventory:read", "analytics:read"],
        "exp": 2000000000,  # Sat May 18 2033
    },
    secret=JWT_SECRET,
)


# ---------------------------------------------------------------------------
# Data Schemas & Models
# ---------------------------------------------------------------------------
class OrderCreate(Schema):
    sku: str = Field(min_length=3, max_length=24)
    customer_email: str = Field(min_length=5, max_length=64)
    unit_price: float = Field(gt=0.0, le=50_000.0)
    quantity: int = Field(default=1, ge=1, le=500)
    shipping_method: str = Field(default="express")
    customer_note: str = Field(default="")
    tags: list[str] = ["retail", "priority"]


class OrderPublic(Schema):
    """Public customer response model - internal margins & tx_id stripped in C."""
    sku: str
    customer_email: str
    unit_price: float
    quantity: int
    total_price: float
    shipping_method: str
    order_status: str
    tracking_id: str


# ---------------------------------------------------------------------------
# 100% Async Dependency Injection Graph with Async Teardown
# ---------------------------------------------------------------------------
async def get_async_tenant_context(
    x_tenant_id: str = Header(default="tenant-corp-01", alias="X-Tenant-ID"),
) -> str:
    """Tier-1 Async DI: Resolves tenant context asynchronously."""
    return x_tenant_id


async def get_async_db_session(tenant_id: str = Depends(get_async_tenant_context)):
    """Tier-2 Async DI: Async generator with guaranteed LIFO transaction teardown."""
    session = {
        "tenant_id": tenant_id,
        "tx_id": 99401,
        "connected": True,
    }
    try:
        yield session
    finally:
        session["connected"] = False  # Guaranteed async cleanup upon request finish


# ---------------------------------------------------------------------------
# 100% Async Benchmark Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def async_health():
    """Endpoint 1: Asynchronous health & service telemetry probe."""
    return {
        "status": "healthy",
        "engine": "Buckthorn C99 / Cython runtime (100% Async)",
        "active_version": "1.0.0",
    }


@app.get("/stores/{store_id}/inventory/{sku}")
async def async_get_inventory(
    store_id: int = Path(ge=1, le=9999),
    sku: str = Path(min_length=3, max_length=24),
    currency: str = Query(default="USD", min_length=3, max_length=3),
    include_discounts: str = Query(default="false"),
    db: dict = Depends(get_async_db_session),
):
    """
    Endpoint 2: Asynchronous inventory lookup.
    Exercises:
      - Async handler execution
      - 2 C-level Path parameters (`store_id`, `sku`)
      - 2 C-level Query parameters (`currency`, `include_discounts`)
      - 1 C-level Header parameter (`X-Tenant-ID`)
      - Async generator DB session injection with LIFO teardown.
    """
    return {
        "store_id": store_id,
        "sku": sku,
        "available_units": 142,
        "reserved_units": 8,
        "currency": currency,
        "tenant": db["tenant_id"],
        "warehouse": "us-east-1a",
    }


@app.post(
    "/stores/{store_id}/checkout",
    response_model=OrderPublic,
    status_code=status.HTTP_201_CREATED,
)
async def async_create_order(
    store_id: int = Path(ge=1, le=9999),
    payload: OrderCreate = ...,
    claims: dict = Security(jwt_bearer, scopes=["orders:write"]),
    db: dict = Depends(get_async_db_session),
):
    """
    Endpoint 3: Asynchronous authenticated e-commerce checkout.
    Exercises:
      - Pure C OpenSSL HMAC-SHA256 JWT signature & scope verification.
      - yyjson SIMD request body validation directly on AST pointers.
      - Async parameter extraction & async DB generator teardown.
      - Direct-to-buffer response model field filtering in C.
    """
    total = round(payload.unit_price * payload.quantity, 2)
    return {
        # Public schema fields:
        "sku": payload.sku,
        "customer_email": payload.customer_email,
        "unit_price": payload.unit_price,
        "quantity": payload.quantity,
        "total_price": total,
        "shipping_method": payload.shipping_method,
        "order_status": "confirmed",
        "tracking_id": f"TRK-{store_id}-{payload.quantity}X-9942",
        # Internal fields automatically stripped by yyjson in C:
        "cost_of_goods": round(payload.unit_price * 0.45, 2),
        "gross_margin": round(total * 0.55, 2),
        "internal_risk_score": 7,
        "db_tx_id": db["tx_id"],
        "authenticated_merchant": claims["sub"],
    }


@app.get("/stores/{store_id}/analytics")
async def async_get_store_analytics(
    store_id: int = Path(ge=1, le=9999),
    period: str = Query(default="today"),
    api_key: str = Security(api_key_guard),
):
    """
    Endpoint 4: Asynchronous merchant analytics endpoint.
    Exercises:
      - Constant-time API Key verification via OpenSSL CRYPTO_memcmp in C.
      - Fast path & query validation.
    """
    return {
        "store_id": store_id,
        "period": period,
        "total_gross_volume": 48291.50,
        "successful_transactions": 642,
        "refund_rate": 0.008,
        "authorized_by": "APIKeyHeader",
    }


# ---------------------------------------------------------------------------
# Server Launcher
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 78)
    print("🌲 Buckthorn Enterprise Cloud Commerce API (100% Async Suite)")
    print("⚡ Engine: Native C99 libuv / Cython 3.x / SO_REUSEPORT")
    print("🔒 JWT Secret: " + JWT_SECRET)
    print("🔑 Benchmark JWT Token:")
    print(f"   Bearer {BENCH_TOKEN}")
    print("=" * 78)
    print("🚀 Server listening on http://127.0.0.1:8000")
    print("👉 Press Ctrl+C to stop.")
    try:
        app.run(host="127.0.0.1", port=8000)
    except KeyboardInterrupt:
        pass
    print("\n🛑 Server closed cleanly.")
