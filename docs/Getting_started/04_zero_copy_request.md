# 04. Zero-Copy Freelist Request Object

## Overview
In high-throughput web applications, instantiating a new Python `Request` object for every incoming connection creates massive heap churn and triggers frequent garbage collection pauses. Buckthorn completely eliminates this overhead through **Zero-Copy Memory Slicing** and a **4,096-slot Cython Object Freelist** (`@cython.freelist(4096)`).

---

## 1. Layman's Explanation
Imagine a busy restaurant where the waiter throws away their notepad and buys a brand-new one every single time a customer asks for a glass of water. At thousands of customers per minute, the restaurant floor is buried in discarded paper, and the waiters spend all their time cleaning up trash (garbage collection) instead of serving food.

Buckthorn uses a reusable chalkboard slate. When a customer orders, the waiter simply points to the customer's table on the chalkboard. When the order is complete, the slate is wiped clean in a split second and immediately handed to the next customer. No paper is wasted, and no cleanup crew is needed.

Furthermore, if you only want to know the customer's table number, the waiter doesn't copy out their entire order history, name, and address into new documents. Buckthorn only looks up the exact detail you ask for, exactly when you ask for it.

---

## 2. Technical Explanation
1. **The Cython Freelist:**
   - The `Request` class is declared with `@cython.freelist(4096)` in [buckthorn/_core.pyx](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/_core.pyx#L119-L150).
   - Instead of invoking Python's standard `tp_alloc` heap allocator on every request, Python recycles pre-allocated `Request` C structs from an internal 4,096-element LIFO freelist array.
2. **Raw Pointer Binding (`bind_c`):**
   - When a route handler requests the `Request` object, the engine binds raw C pointers directly from the underlying 64 KB per-request memory arena (`bt_arena_t`):
     - `const char *_method`, `size_t _method_len`
     - `const char *_path`, `size_t _path_len`
     - `const char *_query`, `size_t _query_len`
     - `phr_header *_headers`, `size_t _num_headers`
     - `const char *_body`, `size_t _body_len`
   - **Zero copying** of memory buffers occurs between the network socket buffer, `picohttpparser`, and the `Request` instance.
3. **Lazy Property Access:**
   - Standard frameworks eagerly allocate Python dictionaries for `request.headers`, `request.cookies`, and `request.query_params` before your handler even runs.
   - In Buckthorn, no dictionaries are constructed upfront. Calling `request.get_header("Authorization")` triggers `bt_find_header()` in C, scanning raw SIMD header structs and decoding only the requested slice into Python Unicode on demand.
4. **Direct Zero-Copy Body Access:**
   - `request.body` accesses the raw memory slice via `PyBytes_FromStringAndSize` without parsing or intermediate buffering.

---

## 3. Code Example

```python
# zero_copy_request_demo.py
from buckthorn import Buckthorn, Request

app = Buckthorn(title="Zero-Copy Request Demo")

@app.post("/inspect")
def inspect_request(req: Request):
    # 1. Access HTTP method and path without heap decoding
    method = req.method
    path = req.path

    # 2. Lazy header lookup via native C SIMD scan
    user_agent = req.get_header("User-Agent")
    auth_header = req.get_header("Authorization")

    # 3. Lazy query parameter extraction
    debug_mode = req.get_query("debug")

    # 4. Access raw unbuffered body bytes directly from the C arena
    raw_payload = req.body

    return {
        "method": method,
        "path": path,
        "user_agent": user_agent or "Unknown",
        "has_auth": auth_header is not None,
        "debug": debug_mode == "1",
        "body_size_bytes": len(raw_payload),
    }

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `def inspect_request(req: Request):`:
   - By declaring `req: Request` in your function signature, Buckthorn's parameter compiler automatically detects `SRC_REQUEST`.
   - Instead of allocating a new Python object from the OS heap, it pops an existing `Request` instance from the 4,096-slot freelist and executes `req.bind_c(...)` with zero data copying.
2. `method = req.method` & `path = req.path`:
   - Inspects the request path and method directly from the C memory pointer.
3. `user_agent = req.get_header("User-Agent")`:
   - Calls `cpdef get_header(self, str name)`. Under the hood, this calls `bt_find_header(self._headers, self._num_headers, ...)` in C. If the header exists, only that single slice is converted into a Python string. Unread headers are never converted into Python objects.
4. `debug_mode = req.get_query("debug")`:
   - Runs `bt_find_query_param` in C directly over the raw query string buffer `_query`.
5. `raw_payload = req.body`:
   - Returns the raw bytes of the request body directly from the C memory arena slice. If the body is empty, it returns Python's singleton `b""` with zero memory allocation.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | FastAPI / Starlette / Flask | Buckthorn |
|---|---|---|
| **Memory Allocation** | Allocates new Python object per request on standard heap | 4,096-entry pre-allocated C freelist (`@cython.freelist(4096)`) |
| **Headers Dictionary** | Eagerly creates a multidict of all headers for every request | **Lazy C lookup** (`bt_find_header`); zero dictionaries allocated |
| **Query Parameters** | Eagerly parses and populates `dict` / `QueryParams` object | **Lazy C lookup** (`bt_find_query_param`); zero dictionaries allocated |
| **Body Buffer** | Reads into intermediate Python stream/buffer chunks | Direct zero-copy slice of the 64 KB C arena buffer |
| **Garbage Collector Load** | High GC object generation and frequent collections | **Near-zero GC churn** due to struct recycling and pointer slicing |
