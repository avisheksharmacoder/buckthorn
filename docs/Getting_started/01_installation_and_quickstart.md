# 01. Installation & Quickstart

## Overview
**Buckthorn** is an enterprise-grade, compiled C/Cython web framework designed for extreme throughput, microsecond latencies, and modern Python developer ergonomics. It combines the developer-friendly syntax of FastAPI with the raw execution speed of native C11 networking (`libuv`), C SIMD HTTP parsing (`picohttpparser`), and single-pass JSON validation (`yyjson`).

---

## 1. Layman's Explanation
Imagine most Python web frameworks as a translator who has to read a letter, translate every word into a foreign language in their head, write it down in a notebook, and only then reply. This creates a lot of delays and extra paperwork (memory allocations).

Buckthorn is like a high-speed assembly line. The heavy lifting (reading network packets, parsing HTTP headers, checking numbers and text) is done by native machine code written in C. Python only steps in to run your specific business logic. Because of this, Buckthorn feels just as simple to write as standard Python, but handles tens of thousands of requests per second without breaking a sweat.

---

## 2. Technical Explanation
Buckthorn uses a hybrid C11 and Cython 3.x architecture:
1. **Network Layer:** Socket creation, connection listening, and event handling are powered by `libuv`, using non-blocking asynchronous event loops on Linux `epoll`.
2. **HTTP Framing:** Raw byte streams are parsed using `picohttpparser`, which uses Intel SSE4.2 SIMD vector instructions to find request boundaries in tens of nanoseconds.
3. **Memory Management:** Each incoming request is processed inside a pre-allocated 64 KB C memory arena (`bt_arena_t`). Memory is allocated in sequential chunks and wiped clean in a single pointer reset upon request completion, bypassing the operating system's heap allocator (`malloc`/`free`).
4. **Execution Bridge:** The compiled Cython layer (`_core.pyx`) communicates directly with C structs and function pointers, acquiring the Python Global Interpreter Lock (GIL) only when invoking user code.
5. **3-Tier Eager Coroutine Engine:** Unlike traditional ASGI servers that unconditionally allocate an `asyncio.Task` on the Python heap for every request, Buckthorn executes `async def` endpoints eagerly via `coro.send(None)` directly on the C call stack. Endpoints that don't block run without task allocation overhead, while endpoints awaiting I/O suspend gracefully and yield control back to `libuv` (see [Chapter 19: Native async/await & Suspension](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/19_native_async_await_and_suspension.md)).

---

## 3. Code Example

Create a file named `main.py`:

```python
# main.py
from buckthorn import Buckthorn

app = Buckthorn(
    title="My First Buckthorn API",
    version="1.0.0",
    docs_url="/docs",
    scalar_url="/scalar",
)

@app.get("/")
def home():
    return {
        "framework": "Buckthorn",
        "speed": "blazing",
        "status": "ready",
    }

@app.get("/hello/{name}")
def greet(name: str):
    return {"message": f"Hello, {name}!"}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `from buckthorn import Buckthorn`:
   Imports the core application class that wraps the underlying C router, memory arena, and event loop.
2. `app = Buckthorn(...)`:
   Initializes the framework instance, registers built-in documentation routes (`/docs` using Swagger UI and `/scalar` using Scalar API Reference), and initializes the internal C router.
3. `@app.get("/")`:
   A decorator registering a route on the C `rax` radix tree for HTTP `GET` requests targeting the root path `/`.
4. `def home(): return {...}`:
   The route handler. Buckthorn accepts standard synchronous `def` functions as well as native `async def` functions. Thanks to Buckthorn's 3-Tier Eager Coroutine Engine, writing `async def` carries zero throughput penalty for fast paths (see [Chapter 19](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/19_native_async_await_and_suspension.md)). The returned Python dictionary is automatically serialized into compact JSON bytes using C `yyjson`.
5. `@app.get("/hello/{name}")`:
   Registers a dynamic parameterized path. The dynamic segment `{name}` is extracted in C and passed as a string parameter to `greet(name: str)`.
6. `app.run(host="127.0.0.1", port=8000)`:
   Starts the `libuv` event loop and binds the TCP socket with `SO_REUSEPORT` enabled.

---

## 5. Running the Application

You can run your Buckthorn application in three distinct modes:

### A. Development Mode (with Hot Reload)
```bash
buckthorn dev main:app --port 8000 --reload
```
Opens the server on `http://127.0.0.1:8000`. Any code modifications will trigger a hot reload.

### B. Production Multi-Worker Mode (SO_REUSEPORT)
```bash
buckthorn run main:app --host 0.0.0.0 --port 8000 --workers 4
```
Forks 4 worker processes. Each process binds to the exact same port using the Linux kernel's `SO_REUSEPORT` feature, distributing incoming TCP connections across CPU cores with zero master-process bottleneck.

### C. Ahead-Of-Time (AOT) Compiled Production Binary
```bash
buckthorn build main.py --prod --out-dir dist
```
Transpiles `main.py` into native Cython code, applies coroutine elision, and compiles it into an optimized `.so` shared library (`-O3 -march=native -msse4.2 -flto`).

---

## 6. Why It Is Different From Other Python Frameworks

| Feature | Standard Python (FastAPI / Starlette) | Buckthorn |
|---|---|---|
| **Networking** | ASGI over asyncio / uvloop Python wrappers | Native C11 `libuv` event loop |
| **Async Architecture** | Unconditional `asyncio.Task` allocation & event loop queue overhead | **3-Tier Eager-First State Machine** (zero Task allocation on eager completion) |
| **HTTP Parser** | Python string decoding (`httptools` or pure Python) | Pure C SSE4.2 SIMD (`picohttpparser`) |
| **Memory Allocation** | Dynamic Python object heap allocation per request | Pre-allocated 64 KB per-request C memory arena |
| **Throughput (Single Core)** | ~15,000 – 30,000 req/sec | **>120,000 req/sec** (pure Python) / **>200,000 req/sec** (compiled) |
| **Deployment** | Requires external runner (`uvicorn` / `gunicorn`) | Self-contained engine + built-in `buckthorn` CLI |
