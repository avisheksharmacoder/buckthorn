# Phase 9: BackgroundTasks, Lifespan Management, Modular APIRouter & Pre-Baked OpenAPI 3.1

## Overview
Phase 9 expands Buckthorn’s enterprise ergonomics without compromising its microsecond-level performance:
* **Post-Response `BackgroundTasks`:** Executes asynchronous or synchronous cleanup/audit tasks strictly *after* response wire bytes have been flushed to the network socket.
* **`lifespan` Context Managers:** Asynchronous startup and shutdown lifecycle hooks (`@asynccontextmanager`) executed cleanly before accepting traffic and upon server termination.
* **Modular `APIRouter`:** Compile-time prefix, tag, and dependency flattening into the core C `rax` radix tree with zero runtime routing overhead.
* **Pre-Baked OpenAPI 3.1 & Interactive Docs:** Pre-generates OpenAPI 3.1 JSON and serves interactive Swagger UI (`/docs`) and Scalar UI (`/scalar`) from cached byte buffers in memory.

---

## Key Features & Architecture

### 1. Post-Response `BackgroundTasks` (`buckthorn/background.py`)
- Injected into handlers via type annotation `tasks: BackgroundTasks`.
- Enqueued tasks are held in a lightweight task list attached to the request invocation context.
- Once the wire response is formatted and written to the socket buffer, `_dispatch_request` executes background tasks sequentially (awaiting coroutines and calling sync functions) with robust exception isolation.
- **Zero Client Latency:** Response delivery time is completely decoupled from background work.

### 2. `lifespan` State & Lifecycle Hooks
- Replaces legacy startup/shutdown events with Python’s standard `@asynccontextmanager async def lifespan(app)` protocol.
- Executed in `Buckthorn.run()`:
  - Startup phase runs before `bt_uv_server_run()` initializes the `libuv` loop.
  - Shutdown phase executes inside `finally:` when SIGINT/SIGTERM is caught.

### 3. Modular `APIRouter` (`buckthorn/routing.py`)
- Allows organizing endpoints into isolated modular modules (`users_router = APIRouter(prefix="/users", tags=["Users"])`).
- When mounted via `app.include_router(router, prefix="/api/v1", dependencies=[...])`:
  - Prefixes are normalized and concatenated at registration time (`/api/v1/users/...`).
  - Outer router dependencies are merged and flattened directly into the route’s Dependency DAG slot array.
  - Endpoints are inserted directly into the Redis `rax` C tree. At runtime, there is **zero router nesting penalty**.

### 4. Zero-Runtime-Cost Static OpenAPI 3.1 & Swagger/Scalar Docs (`buckthorn/openapi.py`)
- Automatically inspects route signatures, path parameters, query parameters, schemas, and security schemes.
- Generates OpenAPI 3.1.0 specifications with typed JSON schemas for all request/response models.
- **Caching Mechanism:** Pre-bakes the OpenAPI schema into UTF-8 JSON bytes (`_openapi_bytes_cache`) upon the first request or registration, serving subsequent `/openapi.json`, `/docs`, and `/scalar` requests in microseconds with zero serialization cost.

---

## File Changes Summary

| File | Status | Description |
|---|---|---|
| `buckthorn/background.py` | **NEW** | Implemented `BackgroundTasks` class and task runner. |
| `buckthorn/routing.py` | **NEW** | Implemented `APIRouter` supporting route decorators, prefix nesting, and dependency cascading. |
| `buckthorn/openapi.py` | **NEW** | OpenAPI 3.1 generator, Swagger UI HTML, and Scalar UI HTML formatters. |
| `buckthorn/_core.pyx` | **UPDATED** | Added `SRC_BACKGROUND_TASKS` parameter resolution and post-response execution in `_dispatch_request`. |
| `buckthorn/app.py` | **UPDATED** | Added `lifespan` support, `include_router`, `/docs`, `/scalar`, and OpenAPI caching. |
| `buckthorn/__init__.py` | **UPDATED** | Exported `BackgroundTasks`, `APIRouter`. |
| `tests/phase9/test_background_and_openapi.py` | **NEW** | TDD test suite covering background tasks, lifespan manager, APIRouter prefix/deps, and OpenAPI docs. |

---

## Test & Speed Gate Results

```text
tests/phase9/test_background_and_openapi.py::test_lifespan_startup_shutdown_and_background_tasks PASSED
tests/phase9/test_background_and_openapi.py::test_modular_api_router_prefix_tags_and_dependencies PASSED
tests/phase9/test_background_and_openapi.py::test_openapi_3_1_schema_generation_and_docs_uis PASSED
tests/phase9/test_background_and_openapi.py::test_phase9_static_openapi_and_background_tasks_speed_gate PASSED (>100k req/s)

============================== 37 passed in 2.12s ==============================
```
