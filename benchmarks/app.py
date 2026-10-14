# benchmarks/app.py
"""
Buckthorn Enterprise Cloud Commerce & Inventory API (Realistic Benchmark Suite)
================================================================================

This benchmark implements a realistic multi-tenant cloud commerce architecture:
  * Tier-1 Pure C Middlewares: Pre-baked CORS (204 preflight), Security Headers, FNV-1a Rate Limiter.
  * Tier-2 Cython HTTP Hook: Injects X-Process-Time and X-Server-Node headers.
  * Parameter Extraction & Validation: Dynamic path segments, query filters, and header aliases.
  * Graph Dependency Injection: Multi-tier DAG with database generator lifecycle teardown.
  * OpenSSL Cryptographic Security: Native HMAC-SHA256 JWT signature & OAuth2 scopes verification.
  * Constant-Time API Key: Constant-time CRYPTO_memcmp verification for analytics endpoints.
  * Single-Pass JSON Deserialization: yyjson C pool validation directly on AST pointers.
  * Response Model Field Filtering: Automatically strips internal/sensitive attributes in C.
  * High-Performance Logging: Non-blocking structured logging via C11 atomic SPSC RingLogger.

================================================================================
HOW TO RUN THE SERVER:
================================================================================
Single Worker (Measures pure single-core efficiency):
    python benchmarks/app.py

Multi-Worker (Full CPU core saturation with Linux kernel SO_REUSEPORT):
    for i in $(seq 1 $(nproc)); do python benchmarks/app.py & done

To stop all background workers:
    pkill -f "benchmarks/app.py"

================================================================================
WRK BENCHMARK COMMANDS:
================================================================================

1. Realistic Mixed Production Traffic (75% GET Inventory, 20% POST Order, 5% GET Analytics):
    wrk -t4 -c100 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/

2. Secure Order Checkout (POST with OpenSSL JWT Auth + yyjson Validation + Response Filtering):
    wrk -t4 -c100 -d10s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/stores/101/checkout

3. Dynamic Path & Query Inventory Lookup with DB Dependency Teardown:
    wrk -t4 -c100 -d10s --latency -H "X-Tenant-ID: tenant-enterprise-42" "http://127.0.0.1:8000/stores/101/inventory/SKU-9942?currency=USD"

4. API Key Secured Analytics (Constant-time CRYPTO_memcmp verification):
    wrk -t4 -c100 -d10s --latency -H "X-API-Key: live_bench_api_key_999" "http://127.0.0.1:8000/stores/101/analytics?period=today"

5. High-Concurrency Stress Test (8 threads, 400 connections across mixed traffic):
    wrk -t8 -c400 -d20s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/
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
    title="Buckthorn Enterprise Commerce Benchmark",
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
    response.headers["X-Server-Node"] = "buckthorn-c-worker-01"
    return response


# ---------------------------------------------------------------------------
# Cryptographic Security Schemes
# ---------------------------------------------------------------------------
JWT_SECRET = "buckthorn-enterprise-bench-secret-2026"
jwt_bearer = HTTPBearer(jwt_secret=JWT_SECRET)
api_key_guard = APIKeyHeader(name="X-API-Key", expected_key="live_bench_api_key_999")

# Pre-generate valid benchmark JWT token
BENCH_TOKEN = create_jwt_token(
    {
        "sub": "merchant_7701",
        "org_id": "org_enterprise_alpha",
        "role": "admin",
        "scopes": ["orders:write", "inventory:read", "analytics:read"],
        "exp": 2000000000,  # Deterministic expiry (May 2033)
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
# Multi-Tier Dependency Injection Graph with Teardown
# ---------------------------------------------------------------------------
def get_tenant_context(
    x_tenant_id: str = Header(default="tenant-corp-01", alias="X-Tenant-ID"),
) -> str:
    """Tier-1 DI: Tenant context resolution."""
    return x_tenant_id


def get_db_session(tenant_id: str = Depends(get_tenant_context)):
    """Tier-2 DI: Database session with guaranteed LIFO transaction teardown."""
    session = {
        "tenant_id": tenant_id,
        "tx_id": 99401,
        "connected": True,
    }
    try:
        yield session
    finally:
        session["connected"] = False  # Cleanup guaranteed on request finish


# ---------------------------------------------------------------------------
# Realistic Benchmark Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Endpoint 1: Fast liveness & service telemetry probe."""
    return {
        "status": "healthy",
        "engine": "Buckthorn C99 / Cython runtime",
        "active_version": "1.0.0",
    }


@app.get("/stores/{store_id}/inventory/{sku}")
def get_inventory(
    store_id: int = Path(ge=1, le=9999),
    sku: str = Path(min_length=3, max_length=24),
    currency: str = Query(default="USD", min_length=3, max_length=3),
    include_discounts: str = Query(default="false"),
    db: dict = Depends(get_db_session),
):
    """
    Endpoint 2: E-commerce inventory lookup.
    Exercises:
      - 2 C-level Path parameters (`store_id`, `sku`)
      - 2 C-level Query parameters (`currency`, `include_discounts`)
      - 1 C-level Header parameter (`X-Tenant-ID`)
      - Multi-tier generator DB dependency injection with cleanup.
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
def create_order(
    store_id: int = Path(ge=1, le=9999),
    payload: OrderCreate = ...,
    claims: dict = Security(jwt_bearer, scopes=["orders:write"]),
    db: dict = Depends(get_db_session),
):
    """
    Endpoint 3: Full authenticated e-commerce checkout.
    Exercises:
      - OpenSSL HMAC-SHA256 JWT signature verification & scope check in pure C.
      - yyjson SIMD request body validation directly on AST pointers.
      - Parameter extraction & DB generator teardown.
      - Automatic response model field filtering (internal fields omitted in C).
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
        # Internal / sensitive fields automatically stripped by yyjson in C:
        "cost_of_goods": round(payload.unit_price * 0.45, 2),
        "gross_margin": round(total * 0.55, 2),
        "internal_risk_score": 7,
        "db_tx_id": db["tx_id"],
        "authenticated_merchant": claims["sub"],
    }


@app.get("/stores/{store_id}/analytics")
def get_store_analytics(
    store_id: int = Path(ge=1, le=9999),
    period: str = Query(default="today"),
    api_key: str = Security(api_key_guard),
):
    """
    Endpoint 4: Merchant analytics endpoint.
    Exercises:
      - Constant-time API Key header verification via OpenSSL CRYPTO_memcmp in C.
      - Path & Query validation.
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
    print("🌲 Buckthorn Enterprise Cloud Commerce API")
    print("⚡ Engine: Native C99 / Cython 3.x / libuv (SO_REUSEPORT)")
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
