"""
11_middlewares.py - Dual-Layer Middleware Architecture
======================================================

In typical Python frameworks, every middleware is a chained Python coroutine.
When you stack CORS, Rate Limiting, Security Headers, and Host validation,
every incoming request traverses 4-6 Python layers, destroying throughput.

Buckthorn introduces a **Dual-Layer Middleware Architecture**:
1. **Tier-1 Pure C NOGIL Guards (`app.add_middleware`):**
   - **CORS (`CORSMiddleware`):** Preflight `OPTIONS` requests are answered
     in ~40 nanoseconds directly in C with pre-baked wire responses, without
     ever calling Python.
   - **Rate Limiting (`RateLimitMiddleware`):** In-memory FNV-1a hash table with
     token-bucket enforcement in C. Excess requests receive immediate 429 errors.
   - **Trusted Host (`TrustedHostMiddleware`):** Rejects spoofed Host headers in C.
   - **Security Headers (`SecurityHeadersMiddleware`):** Injects HSTS, X-Frame-Options,
     and X-Content-Type-Options into the C socket buffer.
2. **Tier-2 Custom Python Middleware (`@app.middleware("http")`):**
   - Flexible Python async hooks for request timing, custom headers, or tracing.

This example demonstrates:
- Adding Tier-1 C middlewares.
- Adding a Tier-2 custom Python logging/timing middleware.
- Verifying CORS preflight and custom response header injection.

To run this live server in development mode:
    buckthorn dev docs/examples/11_middlewares.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/11_middlewares.py
"""

import time
from buckthorn import Buckthorn, Request
from buckthorn.middleware import (
    CORSMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)

app = Buckthorn(
    title="Dual-Layer Middleware API",
    version="1.0.0",
)

# ==============================================================================
# 1. Tier-1: Pure C Fast Middlewares
# ==============================================================================

# Fast CORS: OPTIONS preflights are answered in ~40ns in pure C
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://frontend.buckthorn.dev"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Custom-Trace"],
)

# C-level Security Headers (HSTS, nosniff, DENY)
app.add_middleware(SecurityHeadersMiddleware)

# C-level Token-Bucket Rate Limiter (e.g. 100 requests per 60 seconds)
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)


# ==============================================================================
# 2. Tier-2: Custom Python HTTP Middleware
# ==============================================================================
@app.middleware("http")
async def custom_timing_middleware(request: Request, call_next):
    """
    Python middleware hook. Computes execution duration and appends
    a custom `X-Process-Time-Micros` header to the response.
    """
    t0 = time.perf_counter()

    # Pass request down to the next handler
    response = await call_next(request)

    elapsed_micros = (time.perf_counter() - t0) * 1_000_000
    response.headers["X-Process-Time-Micros"] = f"{elapsed_micros:.1f}"
    response.headers["X-Engine-Tier"] = "Buckthorn-DualLayer"
    return response


# ==============================================================================
# 3. Route Handlers
# ==============================================================================
@app.get("/data")
def get_data():
    """Protected by C rate limiter, C CORS, and Python timing middleware."""
    return {"message": "Protected resource", "tier": "C11 + Cython"}


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 11_middlewares.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Test CORS preflight (OPTIONS) answered in pure C without reaching handlers
    preflight = client.options(
        "/data",
        headers={
            "Origin": "https://frontend.buckthorn.dev",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["Access-Control-Allow-Origin"] == "https://frontend.buckthorn.dev"
    assert "GET" in preflight.headers["Access-Control-Allow-Methods"]
    print(f"  ✓ C11 CORS Preflight -> 204 (Headers injected in C)")

    # 2. Test GET request with CORS Origin and verify Security + Custom Headers
    res = client.get("/data", headers={"Origin": "https://frontend.buckthorn.dev"})
    assert res.status_code == 200
    assert res.json()["message"] == "Protected resource"

    # Verify C-level Security Headers
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["X-Content-Type-Options"] == "nosniff"

    # Verify Custom Python Middleware Headers
    assert res.headers["X-Engine-Tier"] == "Buckthorn-DualLayer"
    assert "X-Process-Time-Micros" in res.headers
    print(f"  ✓ Response Headers Verified -> {dict(res.headers)}")

    print("\n✅ All 11_middlewares.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/11_middlewares.py:app")
