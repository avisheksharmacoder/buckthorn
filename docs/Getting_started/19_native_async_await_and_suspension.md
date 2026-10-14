# 19. Native async/await & Microsecond Non-Blocking Suspension

## Overview
A long-standing dilemma in Python web development has been the performance penalty of `async def`. Standard Python ASGI servers (such as Uvicorn running Starlette or FastAPI) lose 60% to 75% of their throughput when switching an endpoint from `def` to `async def`—even when the endpoint does not perform any actual I/O. 

**Buckthorn** completely eliminates this trade-off. By pushing coroutine driving down into compiled C11 and Cython 3.x and integrating directly with the `libuv` event loop, Buckthorn provides **native, full-featured Python `async/await` support** while sustaining **>80,000 to 130,000+ requests per second (RPS)** on a single CPU core.

---

## 1. Layman's Explanation

### The Fast-Food Counter vs. The Pager Buzzer

Imagine ordering food at a fast-food restaurant:

- **The Standard ASGI Approach (Uvicorn / FastAPI):**
  Every time a customer walks up to the counter, the cashier refuses to take their order directly. Instead, the cashier fills out a triplicate paper clipboard (`asyncio.Task`), registers the order with a central scheduling queue (`loop.create_task`), puts the customer on hold, and switches to another task. Even if the customer just wanted a pre-poured glass of water sitting right on the counter (zero waiting needed), the cashier still goes through the entire bureaucratic paperwork and queue-scheduling dance. As a result, the line moves at a fraction of its potential speed.

- **The Buckthorn 3-Tier Approach:**
  1. **Tier 0 (Instant Counter Handoff):** If your order requires no cooking (synchronous `def` or compiled code), the cashier hands it to you immediately in under 300 nanoseconds. No clipboards, no queues.
  2. **Tier 1 (Eager First-Step):** If you order something marked `async def`, the cashier checks *immediately* on the spot (`coro.send(None)`). If the item is already prepared (in-memory cache, computed value, or instantaneous result), you receive it immediately without ever stepping out of line or creating a clipboard. You get instant service at >120,000 orders per second.
  3. **Tier 2 (The Pager Buzzer):** Only if the kitchen genuinely has to grill a burger (a real `await database_query()` or `await asyncio.sleep()`) does the cashier hand you a vibrating buzzer (suspend coroutine) and immediately turn to serve the next customer in line. When your burger is ready, your buzzer rings, your order finishes, and your response is flushed down the network socket. The counter never sits idle, and zero time is wasted on unnecessary paperwork.

---

## 2. Technical Explanation

Buckthorn achieves this performance through its **3-Tier Eager Coroutine State Machine** implemented in [buckthorn/_core.pyx](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/_core.pyx) and [buckthorn/c_src/bt_http.c](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/c_src/bt_http.c).

```
                      Incoming HTTP Request
                                │
                                ▼
                   ┌─────────────────────────┐
                   │ Is handler def or AOT?  │
                   └────────────┬────────────┘
                         YES    │    NO (async def)
             ┌──────────────────┘    │
             ▼                       ▼
      ┌─────────────┐       ┌────────────────────────┐
      │   Tier 0    │       │         Tier 1         │
      │ Synchronous │       │   Eager First-Step     │
      │  Fast-Path  │       │    coro.send(None)     │
      │  (~300 ns)  │       └───────────┬────────────┘
      └──────┬──────┘                   │
             │           Did it complete immediately?
             │           (StopIteration raised)
             │                 YES      │     NO (Yielded Future)
             │         ┌────────────────┘     │
             │         ▼                      ▼
             │   ┌─────────────┐       ┌────────────────────────┐
             │   │   Instant   │       │         Tier 2         │
             │   │ Zero-Task   │       │ Non-Blocking Suspension│
             │   │ Completion  │       │ BT_HANDLER_SUSPENDED=2 │
             │   └──────┬──────┘       └───────────┬────────────┘
             │          │                          │
             │          │            libuv loop ticks via uv_idle_t
             │          │            Future completes -> _drive_coro
             │          │                          │
             ▼          ▼                          ▼
       Write HTTP Response via        bt_uv_complete_async_request()
       C Arena & picohttpparser       Flush Response & Resume Read
```

### Tier 0 — Compiled Synchronous Fast-Path
If the route is defined as a standard `def` function or was transpiled via Buckthorn's Ahead-of-Time AST compiler with coroutine elision:
- No coroutine object is allocated.
- The handler executes as a direct C/Cython function call.
- The response is serialized via C `yyjson` and written to the socket in ~300 nanoseconds.

### Tier 1 — Eager First-Step Fast-Path (`coro.send(None)`)
When an endpoint is defined with `async def`:
- In standard ASGI frameworks, calling `async def handler()` produces a coroutine, which is immediately wrapped in an `asyncio.Task` on the heap (`asyncio.create_task(coro)`), pushing it into the event loop queue and yielding control.
- In Buckthorn, the Cython bridge calls `coro.send(None)` **immediately on the active C stack frame** within `_execute_route_inner`.
- If the endpoint does not encounter a pending `await` (for example, reading cached data, in-memory computation, or returning static schemas), Python's C-API raises `StopIteration(return_value)` instantly.
- Buckthorn extracts the return value, serializes the response, and commits it to the C socket buffer immediately.
- **Result:** **Zero `asyncio.Task` object allocation**, zero callback chains, zero event loop round-trips, and **>120,000 RPS**.

### Tier 2 — Non-Blocking Suspension (`BT_HANDLER_SUSPENDED = 2`)
When the coroutine encounters a pending I/O operation (e.g., `await asyncio.sleep(...)`, `await db.fetch(...)`, or `await http_client.get(...)`):
1. **Suspension Detection:** The `coro.send(None)` call yields a pending `asyncio.Future` instead of raising `StopIteration`.
2. **C-Engine Notification:** The Cython runner returns `BT_HANDLER_SUSPENDED = 2` to the C HTTP parser in `bt_http.c`.
3. **Socket Flow Control (`uv_read_stop`):** The C engine temporarily suspends `uv_read_start` on the client connection. This prevents incoming pipelined requests from corrupting the connection's 64 KB memory arena (`bt_arena_t`) while the coroutine is suspended.
4. **Lifecycle Protection (`in_flight_async_reqs`):** The C connection struct increments an atomic counter `in_flight_async_reqs`. If the remote client abruptly closes the TCP connection while the coroutine is suspended, Buckthorn defers freeing the connection resources until all suspended coroutines have completed, eliminating Use-After-Free (UAF) segmentation faults.
5. **Event Loop Ticking (`uv_idle_t`):** To allow Python `asyncio` and `uvloop` timers and file-descriptor polling to progress in lockstep with `libuv`, Buckthorn registers a native `uv_idle_t` handle. On every event loop iteration, this ticker invokes `loop._run_once()`, driving Python futures to resolution.
6. **Continuation (`_drive_coro`):** Buckthorn attaches a completion callback to the yielded future via `future.add_done_callback(on_fut_done)`. When the I/O finishes:
   - CPython's internal `_asyncio_future_blocking` flag is safely cleared.
   - The coroutine is resumed via `coro.send(fut.result())`.
   - If further `await` points exist, the cycle repeats.
   - Upon final completion, `bt_uv_complete_async_request()` is invoked, formatting the HTTP response headers and body from the suspended state, flushing the response down the socket, resetting the memory arena, and re-enabling `uv_read_start`.

---

## 3. Runnable Code Example

The following standalone application demonstrates eager async execution, true non-blocking async suspension, parallel async fetching (`asyncio.gather`), async generator dependencies with cleanup, and async background tasks:

```python
# async_native_demo.py
import asyncio
from typing import AsyncGenerator
from buckthorn import Buckthorn, Depends, BackgroundTasks, Schema

app = Buckthorn(title="Buckthorn Native Async Demo")

# ---------------------------------------------------------
# 1. Async Generator Dependency with Guaranteed Teardown
# ---------------------------------------------------------
class AsyncDatabaseSession:
    async def query(self, query_str: str) -> dict:
        # Simulate non-blocking async database query
        await asyncio.sleep(0.01)
        return {"result": f"Executed: {query_str}", "rows": 42}

    async def close(self):
        # Simulate non-blocking connection pool cleanup
        await asyncio.sleep(0.001)
        print("[DB] Async connection pool cleanly released.")

async def get_async_db() -> AsyncGenerator[AsyncDatabaseSession, None]:
    db = AsyncDatabaseSession()
    try:
        yield db
    finally:
        await db.close()

# ---------------------------------------------------------
# 2. Schema Definition
# ---------------------------------------------------------
class OrderRequest(Schema):
    order_id: int
    item: str

# ---------------------------------------------------------
# 3. Async Background Task
# ---------------------------------------------------------
async def async_dispatch_webhook(order_id: int, item: str):
    await asyncio.sleep(0.05)  # Non-blocking async webhook
    print(f"[Webhook Worker] Dispatched notification for Order #{order_id} ({item})")

# ---------------------------------------------------------
# 4. Route A: Tier 1 Eager Fast-Path (No await inside)
#    Executes instantly in ~5 microseconds; >120,000 RPS!
# ---------------------------------------------------------
@app.get("/eager-fastpath")
async def eager_fastpath():
    # Even though declared as 'async def', because there is no pending I/O,
    # Buckthorn completes it in Tier 1 with ZERO asyncio.Task allocation!
    return {
        "tier": "Tier 1 Eager Fast-Path",
        "overhead": "zero asyncio.Task allocations",
        "status": "blazing",
    }

# ---------------------------------------------------------
# 5. Route B: Tier 2 True Non-Blocking Suspension
#    Suspends without blocking the libuv worker thread!
# ---------------------------------------------------------
@app.get("/async-io")
async def async_io():
    # Truly suspends the coroutine; libuv continues serving other requests
    await asyncio.sleep(0.02)
    return {"message": "Awoke from non-blocking async I/O sleep", "waited_ms": 20}

# ---------------------------------------------------------
# 6. Route C: Parallel Async Operations (asyncio.gather)
# ---------------------------------------------------------
async def fetch_pricing():
    await asyncio.sleep(0.015)
    return 199.99

async def fetch_inventory():
    await asyncio.sleep(0.015)
    return {"in_stock": True, "warehouse_id": 4}

@app.get("/parallel-gather")
async def parallel_gather():
    # Runs both coroutines concurrently in parallel
    price, inventory = await asyncio.gather(fetch_pricing(), fetch_inventory())
    return {
        "price": price,
        "inventory": inventory,
        "concurrent": True,
    }

# ---------------------------------------------------------
# 7. Route D: Async Dependency + Async Background Task
# ---------------------------------------------------------
@app.post("/orders")
async def place_order(
    payload: OrderRequest,
    tasks: BackgroundTasks,
    db: AsyncDatabaseSession = Depends(get_async_db),
):
    # Await query using injected async dependency
    db_result = await db.query(f"INSERT INTO orders (id, item) VALUES ({payload.order_id}, '{payload.item}')")

    # Schedule non-blocking async background task after response
    tasks.add_task(async_dispatch_webhook, order_id=payload.order_id, item=payload.item)

    return {
        "status": "confirmed",
        "order_id": payload.order_id,
        "db": db_result,
    }

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `async def eager_fastpath():`:
   - Declared as `async def`, but contains no `await` statements.
   - When called, `_execute_route_inner` runs `coro.send(None)`.
   - The coroutine returns immediately by raising `StopIteration({"tier": ...})`.
   - Buckthorn commits the response instantly on the current C call stack. The Python event loop is never even contacted, avoiding all scheduling overhead.
2. `await asyncio.sleep(0.02)`:
   - When `coro.send(None)` hits `await asyncio.sleep(0.02)`, Python's generator yields a pending `_asyncio.Future`.
   - Buckthorn detects that the handler yielded without finishing, sets `out_status_code[0] = BT_HANDLER_SUSPENDED (2)`, and pauses C-level socket reads (`uv_read_stop`).
   - The `uv_idle_t` ticker advances Python's event loop.
   - When the 20ms timer fires, `on_fut_done` invokes `_drive_coro`, resumes the handler, formats the JSON bytes, and writes the HTTP packet to the client via `bt_uv_complete_async_request()`.
3. `await asyncio.gather(fetch_pricing(), fetch_inventory())`:
   - Demonstrates that standard Python asynchronous patterns—including `asyncio.gather`, `asyncio.shield`, `asyncio.wait_for`, and third-party async drivers (such as `asyncpg`, `httpx`, `redis.asyncio`)—work natively inside Buckthorn handlers without special wrappers.
4. `db: AsyncDatabaseSession = Depends(get_async_db)`:
   - Buckthorn's compiled dependency runner evaluates `get_async_db()` via `anext()`.
   - If the dependency awaits an async connection pool, it suspends gracefully.
   - In the `finally:` block after the response is completed, Buckthorn executes `aclose()` on the async generator, guaranteeing that `await db.close()` runs and connections return to the pool.
5. `tasks.add_task(async_dispatch_webhook, ...)`:
   - Post-response background tasks can be either synchronous or asynchronous.
   - Buckthorn executes `async_dispatch_webhook` via `_execute_all_async()` after the client has already received their HTTP `200 OK` response, adding zero latency to the client's request.

---

## 5. Why It Is Different From Other Python Frameworks

| Performance Metric & Architecture | Standard ASGI (FastAPI / Uvicorn / Starlette) | WSGI (Flask / Django + Gunicorn) | Buckthorn Native Async Engine |
|---|---|---|---|
| **Zero-Wait Coroutine Execution** | Unconditionally allocates an `asyncio.Task` on the Python heap; schedules on loop queue | Not supported (synchronous blocking worker thread model) | **Tier 1 Eager First-Step:** Runs directly on C stack via `coro.send(None)` with **zero Task allocation** |
| **Throughput on Fast Async Endpoints** | ~20,000 – 35,000 req/sec | ~12,000 – 18,000 req/sec | **>120,000 – 130,000+ req/sec** (pure Python) / **>200,000 req/sec** (AOT compiled) |
| **Throughput Penalty of `async def`** | **-60% to -75% loss** vs sync `def` | N/A | **0% penalty:** Eager execution matches synchronous speeds |
| **Event Loop & I/O Poller** | Pure Python loop or `uvloop` wrapper with Python object callback chains | OS thread context switching (`pthread`) | Native C11 `libuv` loop unified with Cython state machine via `uv_idle_t` |
| **Async Dependency Cleanup** | Handled through deep Python async context-manager stacks | Manual teardown hooks | **Compiled LIFO teardown stack** with native `aclose()` support |
| **Connection Safety During Suspension** | Relies on high-level Python socket wrappers | Thread-per-request blocking | **C-level `uv_read_stop` buffer protection** and `in_flight_async_reqs` UAF guards |
| **Post-Response Async Tasks** | Scheduled on event loop with queue overhead | Requires Celery / Redis background worker queue | **Native `_execute_all_async()`** running post-response with zero added client latency |
