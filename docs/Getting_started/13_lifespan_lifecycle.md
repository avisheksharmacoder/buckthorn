# 13. Lifespan Events & Resource Lifecycle Management

## Overview
Production services require robust hooks to manage long-lived resources: connecting to database connection pools, warming up in-memory caches, initializing Redis clients, and loading multi-gigabyte machine learning models into GPU VRAM before traffic arrives, and safely tearing them down on shutdown. Buckthorn natively supports the modern **Async Lifespan Context Manager Protocol** (`lifespan=...`) without relying on legacy deprecated startup/shutdown event lists.

---

## 1. Layman's Explanation
Think of opening a restaurant:
- Before unlocking the front doors for customers, the staff must turn on the ovens, prep ingredients, test the credit card machines, and calibrate the coffee grinders (Startup).
- At the end of the night, after all customers have left, the staff must turn off the gas, empty the grease traps, lock the cash registers, and set the alarm (Shutdown).

In Buckthorn, the `lifespan` context manager is like that restaurant checklist. All the preparation work happens cleanly before the server accepts its very first network packet. When the server is shut down (via `Ctrl+C` or a Kubernetes `SIGTERM`), the cleanup code runs automatically to ensure no database transactions or files are corrupted.

---

## 2. Technical Explanation
1. **The Modern Lifespan Protocol:**
   - Defined using Python's standard `contextlib.asynccontextmanager`.
   - Passed directly into the application constructor: `app = Buckthorn(lifespan=lifespan)`.
2. **Startup Phase (`_startup`):**
   - Executed before the `libuv` socket begins listening (`uv_listen`).
   - The application instantiates the lifespan context: `self._lifespan_ctx = self.lifespan(self)`.
   - Enters the context by driving `self._lifespan_ctx.__aenter__()` to completion.
   - Any state attached to the application (e.g., `app.state.db` or module singletons) is guaranteed to be fully initialized and warmed before any incoming TCP connection is accepted.
3. **Shutdown Phase (`_shutdown`):**
   - Triggered when the process receives a termination signal (`SIGINT`, `SIGTERM`) or the event loop ceases.
   - Buckthorn executes `self._lifespan_ctx.__aexit__(None, None, None)` inside a strict `try...finally` block in [buckthorn/app.py](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/app.py#L349-L355).
   - Resources are closed gracefully, socket handles are terminated, and connection pools drain without hanging threads.

---

## 3. Code Example

```python
# lifespan_demo.py
from contextlib import asynccontextmanager
from buckthorn import Buckthorn

# Simulated Database Connection Pool & ML Model
class GlobalResources:
    db_pool = None
    ml_model = None

resources = GlobalResources()

@asynccontextmanager
async def lifespan(app: Buckthorn):
    # =================================================================
    # STARTUP: Runs BEFORE server starts accepting requests
    # =================================================================
    print("[Startup] Initializing PostgreSQL connection pool...")
    resources.db_pool = {"pool_size": 20, "status": "connected"}

    print("[Startup] Loading Machine Learning weights into GPU VRAM...")
    resources.ml_model = {"weights": "v2.4_optimized", "ready": True}

    print("[Startup] All resources initialized. Accepting network traffic.")

    yield  # The application serves incoming HTTP/WebSocket traffic here

    # =================================================================
    # SHUTDOWN: Runs when server receives SIGINT/SIGTERM
    # =================================================================
    print("[Shutdown] Draining active database queries...")
    resources.db_pool = None

    print("[Shutdown] Offloading ML weights from GPU memory...")
    resources.ml_model = None

    print("[Shutdown] Clean shutdown complete.")

# Pass lifespan into the Buckthorn constructor
app = Buckthorn(title="Lifespan Lifecycle Demo", lifespan=lifespan)

@app.get("/status")
def get_service_status():
    return {
        "database_connected": resources.db_pool is not None,
        "model_loaded": resources.ml_model is not None,
        "pool_size": resources.db_pool.get("pool_size") if resources.db_pool else 0,
    }

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `@asynccontextmanager async def lifespan(app: Buckthorn):`:
   - Defines the lifespan generator using Python's standard `contextlib` library.
   - The `app` instance is passed as the sole argument.
2. Code before `yield`:
   - Runs during `app._startup()`.
   - Ideal for initializing async database clients (such as asyncpg, Motor, redis-py) or downloading/loading AI models.
   - If an exception occurs here, the server halts immediately with an informative error rather than failing mysteriously on the first client request.
3. `yield`:
   - Suspends the lifespan context while the `libuv` event loop runs and processes HTTP and WebSocket connections.
4. Code after `yield`:
   - Runs during `app._shutdown()`.
   - Guarantees that connection pools close cleanly and in-flight operations finish gracefully.
5. `app = Buckthorn(..., lifespan=lifespan)`:
   - Registers the lifecycle manager on the application instance.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | Legacy Frameworks (Flask / Django) | Starlette / FastAPI | Buckthorn |
|---|---|---|---|
| **Protocol** | `before_first_request` (deprecated) or signal handlers | `@app.on_event("startup")` (deprecated) / lifespan context | **Modern `@asynccontextmanager` Lifespan** |
| **Startup Safety** | Requests can arrive before initialization completes | Handled by ASGI runner wrapper | **Pre-Listen Guarantee** (`libuv` does not bind until startup finishes) |
| **Shutdown Certainty** | Worker kills (`SIGKILL`) frequently leave connections dangling | Asyncio loop cancellation can interrupt cleanups | **Strict `try...finally` teardown** in application runtime |
| **Integration** | Requires manual hooks into WSGI workers | ASGI lifespan specification wrapper | **Directly orchestrated by native application core** |
