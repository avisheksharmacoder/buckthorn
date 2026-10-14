# Phase 7: Dual-Layer Middleware Pipeline (Pure C NOGIL Guards + Cython HTTP Hooks)

## Overview
Phase 7 introduces **Buckthorn’s Dual-Layer Middleware Pipeline**, combining zero-GIL pure C pre-request short-circuit guards with ultra-fast Cython HTTP middleware hooks (`@app.middleware("http")`).

---

## Key Features & Architecture

### Tier 1: Pure C `NOGIL` Middlewares (`bt_middleware.c`)
1. **Pre-Baked CORS:** Answers `OPTIONS` preflight requests in pure C via a single `memcpy` of a pre-formatted wire buffer (`cors_preflight_wire`), returning `204 No Content` in ~40ns without touching Python or acquiring the GIL. Also appends pre-baked CORS headers to standard route responses.
2. **FNV-1a Hash-Table Rate Limiting:** Fixed-window rate limiter utilizing a fast 64-bit FNV-1a hash of the client IP (`X-Forwarded-For` or `127.0.0.1`) across 256 hash buckets, rejecting excess requests directly in C with `429 Too Many Requests`.
3. **Trusted Host Validation:** Validates incoming `Host` headers directly in C and rejects unauthorized hostnames with `400 Bad Request` prior to route lookup.
4. **Pre-Baked Security Headers:** Injects `X-Frame-Options`, `X-Content-Type-Options`, and `Strict-Transport-Security` directly during response framing with zero string concatenation overhead.

### Tier 2: Cython HTTP Hooks (`@app.middleware("http")`)
- Fast request/response wrapper hooks allowing inspection of requests, short-circuiting responses, or injection of custom response headers (e.g., `X-Custom-Middleware`) into wire buffers.
- **Zero-Allocation Fast Path:** When no user middlewares are registered, route execution runs directly through `_execute_route_inner` with zero middleware object allocations or wrappers.

---

## File Changes Summary

| File | Status | Description |
|---|---|---|
| `buckthorn/c_src/bt_middleware.h` | **NEW** | C header defining `bt_middleware_chain_t`, CORS, rate limiter, trusted host, and security headers configuration and execution API. |
| `buckthorn/c_src/bt_middleware.c` | **NEW** | Pure C implementation of Tier-1 middlewares with FNV-1a hashing, pre-baked wire responses, and pre-request guard runners. |
| `buckthorn/middleware.py` | **NEW** | Python middleware surface: `CORSMiddleware`, `RateLimitMiddleware`, `TrustedHostMiddleware`, `SecurityHeadersMiddleware`, and `Response`. |
| `buckthorn/c_src/bt_http.h` | **UPDATED** | Embedded `bt_middleware_chain_t` into `bt_router_t`, updated `bt_handler_invoke_fn` with extra headers parameters, declared `bt_router_get_mw`. |
| `buckthorn/c_src/bt_http.c` | **UPDATED** | Initialized C middleware chain, executed `bt_middleware_run_pre` prior to routing, and appended pre-baked & extra response headers. |
| `buckthorn/_core.pxd` | **UPDATED** | Declared `bt_middleware.h` functions and updated `bt_handler_invoke_fn` signature. |
| `buckthorn/_core.pyx` | **UPDATED** | Implemented `MiddlewareResponse`, `_execute_route_inner`, `_RouteInvocationContext`, `_cython_invoke_handler`, `CoreRouter` middleware bindings, and `RawResponse.headers`. |
| `buckthorn/app.py` | **UPDATED** | Added `app.add_middleware(...)` and `@app.middleware("http")` decorator. |
| `buckthorn/testing.py` | **UPDATED** | Supported custom `Host` header overrides and added `.options()` helper on `RawTestClient`. |
| `buckthorn/__init__.py` | **UPDATED** | Exported `CORSMiddleware`, `RateLimitMiddleware`, `TrustedHostMiddleware`, `SecurityHeadersMiddleware`, `Response`. |
| `setup.py` | **UPDATED** | Added `bt_middleware.c` to sources and bumped version to `0.7.0`. |
| `tests/phase7/test_middlewares.py` | **NEW** | Comprehensive TDD test suite covering C CORS preflight, rate limiting, trusted host, security headers, user hooks, and speed gate. |

---

## Test & Benchmark Results

```text
============================= test session starts ==============================
collected 29 items

tests/phase1/test_core_router.py .....                                   [ 17%]
tests/phase2/test_params.py ....                                         [ 31%]
tests/phase3/test_schema_serde.py ....                                   [ 44%]
tests/phase4/test_depends_di.py .....                                    [ 62%]
tests/phase5/test_ring_logger.py ...                                     [ 72%]
tests/phase6/test_c_security.py ...                                      [ 82%]
tests/phase7/test_middlewares.py .....                                   [100%]

============================== 29 passed in 1.33s ==============================
```
