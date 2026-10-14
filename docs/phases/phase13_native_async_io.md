# Phase 13: Native async/await Suspension & libuv Tick Bridge

## 1. Overview
Standard Python ASGI frameworks (Uvicorn + Starlette) lose 60%–75% of their throughput when executing `async def` endpoints because every request unconditionally allocates an `asyncio.Task` on the heap, wraps the coroutine in callback chains, and performs context switches even when the endpoint never awaits slow I/O.

Buckthorn implements a **3-Tier Eager-First Coroutine State Machine** integrated directly into the C11 core and Cython bridge:

- **Tier 0 — Compiled Synchronous Fast Path:** Standard `def` functions or AST-elided functions execute as direct C/Cython function calls in ~300 ns with zero coroutine allocation.
- **Tier 1 — Eager First-Step Fast Path:** `async def` handlers call `coro.send(None)` eagerly on the current C stack frame before scheduling an event loop task. If the coroutine finishes synchronously (cache hits, JWT validation in C, schema checks), `StopIteration(value)` is raised immediately. Zero `asyncio.Task` objects are allocated, achieving **>80,000–130,000+ RPS**.
- **Tier 2 — True Non-Blocking Suspension (`BT_HANDLER_SUSPENDED`):** When a coroutine hits an actual `await` (`asyncio.sleep`, `asyncio.gather`, `asyncpg`, `httpx`):
  - `_execute_route_inner` returns `BT_HANDLER_SUSPENDED (2)` to `bt_http.c`.
  - The client's `uv_tcp_t` connection is left open while `uv_read_stop` pauses reads on that connection to prevent keep-alive buffer races.
  - The libuv worker thread continues accepting and serving incoming HTTP requests.
  - A callback-driven driver (`_drive_coro`) advances the coroutine across await points using `future.add_done_callback`.
  - `uv_idle_t` steps the Python event loop (`_SHARED_ASYNC_LOOP`) on each libuv tick via `bt_async_tick_fn`.
  - Upon completion, `bt_uv_complete_async_request()` formats the HTTP wire response and calls `uv_write()` on the paused socket.

---

## 2. CPython & libuv Invariants Solved

1. **Callback-Driven Resume (`future.add_done_callback`):**
   When `coro.send(None)` suspends at `await fut`, CPython sets `fut->_asyncio_future_blocking = 1`. Re-awaiting `fut` inside a secondary coroutine fails with `RuntimeError: await wasn't used with future`. Driving `coro` via `add_done_callback(on_fut_done)` matches CPython's internal `Task.__step` behavior.

2. **`_asyncio_future_blocking` Reset:**
   Before registering done callbacks on yielded futures, `_drive_coro` explicitly clears `setattr(fut, "_asyncio_future_blocking", False)` to prevent `RuntimeError: yield was used instead of yield from` in compound awaitables like `asyncio.gather`.

3. **Active Loop Pointer Preservation (`prev_loop`):**
   `events.get_running_loop()` is preserved across eager steps:
   ```python
   prev_loop = events._get_running_loop()
   if prev_loop is None:
       events._set_running_loop(loop)
   try:
       first_yielded = coro.send(None)
   finally:
       if prev_loop is None:
           events._set_running_loop(None)
   ```
   This prevents wiping CPython's thread-local running loop during active event loop ticks.

4. **Reentrancy Protection (`_step_coroutine_async` vs `_step_coroutine_sync`):**
   When `loop.is_running() == True` (inside `_drive_and_flush`), `run_until_complete()` is forbidden. Generator teardowns (`gen.aclose()`) and background tasks are awaited directly via `_step_coroutine_async()`.

5. **Client Lifecycle & UAF Guard:**
   `bt_uv_client_t` tracks `in_flight_async_reqs`. If a client disconnects (`nread < 0`) while suspended, closing is deferred until `in_flight_async_reqs == 0`, eliminating Use-After-Free crashes.

---

## 3. Verification
All 55 tests across Phases 1 through 13 pass cleanly:
- `test_true_async_await_suspension_gather_and_taskgroup` (PASS)
- `test_async_generator_dependency_with_real_await_in_setup_and_teardown` (PASS)
- `test_eager_fast_path_preserves_80k_rps_alongside_suspending_routes` (PASS, >80,000 RPS gate exceeded)
- `test_async_route_http_exception` (PASS)
