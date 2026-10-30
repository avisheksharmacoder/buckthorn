# 🌲 Buckthorn

**A Compiled C11 & Cython Web Framework for High-Frequency Python APIs**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Engine: C11 + Cython 3](https://img.shields.io/badge/engine-C11%20%2B%20Cython%203-00ADD8.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Buckthorn is a systems-first Python web framework that replaces the traditional Python ASGI server, pure-Python HTTP parser, and dynamic runtime validation loop with a **unified C11 & Cython execution engine**.

You write ergonomic, type-annotated Python (`@app.get`, `Schema`, `Depends`, `Security`, `WebSocket`), and Buckthorn executes the request lifecycle—TCP socket I/O, HTTP/1.1 parsing, RFC 9112 security auditing, radix routing, parameter coercion, JSON schema validation, HS256 JWT verification, rate limiting, and lock-free structured logging—directly in C11 before touching a single Python object.

---

## ⚡ Why Buckthorn?

Standard Python web frameworks pay a steep per-request tax: allocating an ASGI `scope` dictionary, decoding headers into Python strings, boxing every field during validation, allocating coroutine frames for synchronous functions, and formatting responses in pure Python.

Buckthorn eliminates those bottlenecks at the architecture level:

1. **Zero-ASGI Native `libuv` Engine (`bt_http.c`):** Multi-packet TCP stream reassembly, mixed sync/async HTTP/1.1 pipelining, `uv_try_write` synchronous fast paths, and `SO_REUSEPORT` multi-process worker scaling with automatic crash recovery.
2. **Per-Connection 64KB Bump Arena (`bt_arena_t`):** Every TCP connection owns a pre-allocated 64KB C bump arena with an on-demand overflow slab. Request headers, path slices, and response buffers are allocated in $O(1)$ pointer bumps (`arena->offset += aligned`) and reset in 1 CPU instruction upon request completion—with automatic lifetime extension across suspended `async def` coroutines.
3. **SIMD HTTP & JSON Pipeline (`picohttpparser` + `yyjson`):** SSE4.2-accelerated HTTP header parsing paired with `yyjson` using thread-local 64KB pools (`tls_read_pool` / `tls_write_pool`), $O(1)$ ordered iterator field lookups (`yyjson_obj_iter_getn`), 2-node `NOESC` key writers, and zero-copy direct-to-arena `yyjson_mut_write_buf` serialization.
4. **Redis `rax` Radix Router & Compile-Time `Depends()` DAG:** Static routes resolve in $O(K)$ via Redis's compressed radix tree (`rax`), while dependency injection trees (`Depends()`) are topologically flattened at route registration time into a linear Cython execution array (`CompiledDepStep`).
5. **Ahead-of-Time AST-to-Cython Compiler (`buckthorn build --prod`):** Transpiles standard Python route modules and `Schema` classes into Cython `cdef class` extension types (with `@cython.freelist(4096)`), elides non-suspending `async def` coroutines into synchronous C entry points, and compiles your application into a native `.so` shared library.

---

## 🏗️ Request Lifecycle Architecture

```text
  [ TCP Socket (libuv + SO_REUSEPORT + 15s Slowloris uv_timer_t) ]
                                 │
                                 ▼
  [ picohttpparser (SSE4.2) + bt_audit.c (RFC 9112 Smuggling Guard) ]
                                 │
                                 ▼
  [ Tier-1 C Middlewares: TrustedHost -> CORS Preflight -> FNV-1a RateLimit ]
                                 │
                                 ▼
  [ Redis `rax` Radix Tree (Static) / Zero-Copy C Segment Matcher (Params) ]
                                 │
                                 ▼
  [ C Security (OpenSSL SHA256/HMAC JWT) + C Parameter & yyjson Validators ]
                                 │
                                 ▼
  [ Cython Freelist Bridge (`PyIter_Send` Eager Coroutine Driver + DI Array) ]
                                 │
                                 ▼
  [ Direct-to-Arena `yyjson_mut_write_buf` + Divisionless Wire Formatter ]
                                 │
                                 ▼
  [ Lock-Free C11 SPSC RingLogger (`_Alignas(64)` + Batch `writev` Thread) ]
```

---

## 🚀 Quickstart

### Installation

```bash
# Install from pre-built wheel (bundles libuv and OpenSSL)
pip install buckthorn

# Or install from source with Cython build toolchain & test extras
pip install -e ".[dev]" --no-build-isolation
```

### Example Application (`main.py`)

```python
import time
from buckthorn import (
    BackgroundTasks,
    Buckthorn,
    Depends,
    Field,
    Header,
    HTTPException,
    Query,
    Schema,
    Security,
    WebSocket,
)
from buckthorn.middleware import (
    CORSMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)
from buckthorn.security import HTTPBearer, create_jwt_token

app = Buckthorn(
    title="Order Execution Engine",
    version="1.0.0",
    access_log=True,
    max_body_size=1024 * 1024,
)

# Tier-1 Middlewares execute entirely in C before acquiring the Python GIL
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "api.example.com"])
app.add_middleware(CORSMiddleware, allow_origins=["https://app.example.com"])
app.add_middleware(RateLimitMiddleware, max_requests=5000, window_seconds=60)
app.add_middleware(SecurityHeadersMiddleware)

JWT_SECRET = "prod-hs256-signing-secret-32-bytes-minimum!"
bearer = HTTPBearer(jwt_secret=JWT_SECRET)


class ShippingAddress(Schema):
    city: str = Field(..., min_length=2, max_length=64)
    pincode: int = Field(..., ge=100000, le=999999)


class OrderItem(Schema):
    sku: str = Field(..., min_length=3, max_length=32)
    qty: int = Field(..., ge=1, le=100)
    price: float = Field(..., gt=0.0, le=1_000_000.0)


class CreateOrderRequest(Schema):
    account_id: int = Field(..., ge=1)
    address: ShippingAddress
    items: list[OrderItem]


def get_audit_tag(x_request_id: str = Header("req-default", max_length=64)) -> str:
    return f"audit:{x_request_id}"


@app.post("/orders", response_model=CreateOrderRequest, status_code=201)
async def create_order(
    order: CreateOrderRequest,
    bg: BackgroundTasks,
    audit_tag: str = Depends(get_audit_tag),
    claims: dict = Security(bearer, scopes=["orders:write"]),
):
    bg.add_task(
        app.logger.info,
        "order_accepted",
        account_id=order.account_id,
        actor=claims.get("sub", "unknown"),
        audit=audit_tag,
    )
    return order


@app.websocket("/ws/quotes/{symbol}")
async def quote_stream(ws: WebSocket, symbol: str):
    await ws.accept()
    ws.subscribe(f"ticker:{symbol}")
    msg = await ws.receive_text()
    await ws.send_text(f"[{symbol}] ack:{msg}")
    await ws.close()
```

---

## 📊 Single-Core Benchmark Floor & Latency Gates

Benchmarked via `scripts/verify_release.py` (C11 `-O3 -flto` + Cython 3, zero-malloc 64KB arena per connection):

| Phase & Subsystem | What Executes Under the Hood | Single-Core Gate Floor | Typical Throughput | Arena Leak |
| :--- | :--- | ---: | ---: | :---: |
| **1. Core C Router** | `picohttpparser` + Redis `rax` static/param match + wire format | **100,000 req/s** | **145,000+ req/s** | `0 B` |
| **2. C Param Validator** | Path, URL-decoded Query, Header & Cookie range/length checks in C | **80,000 req/s** | **115,000+ req/s** | `0 B` |
| **3. `yyjson` Schema Serde** | Nested `Schema` + `list[Schema]` C validation & zero-copy `write_buf` | **80,000 req/s** | **110,000+ req/s** | `0 B` |
| **4. Flattened `Depends()`** | Compile-time topological DAG array + LIFO generator teardown | **75,000 req/s** | **98,000+ req/s** | `0 B` |
| **5. C11 `RingLogger`** | Lock-free `_Alignas(64)` SPSC ring slot + background `writev()` | **80,000 req/s** | **120,000+ req/s** | `0 B` |
| **6. C Security & JWT** | OpenSSL `HMAC-SHA256`, `CRYPTO_memcmp`, `exp`/`nbf` & scope checks | **75,000 req/s** | **105,000+ req/s** | `0 B` |
| **7. C Middleware Chain** | C `TrustedHost` + `CORS` + `FNV-1a` Rate Limiter + SecHeaders | **80,000 req/s** | **125,000+ req/s** | `0 B` |
| **8. RFC 6455 WebSocket** | 64-bit word-aligned XOR frame unmasking & server frame encoding | **60,000 cyc/s** | **95,000+ cyc/s** | `0 B` |
| **9. `/openapi.json`** | Pre-cached OpenAPI 3.1.0 byte buffer served from C route table | **150,000 req/s** | **210,000+ req/s** | `0 B` |
| **10. Compiled `.so` App** | Ahead-of-time AST-to-Cython `cdef class` + coroutine elision | **80,000 req/s** | **118,000+ req/s** | `0 B` |

---

## 🛡️ Security & Memory-Safety Guarantees (OWASP ASVS / RFC 9112 / RFC 8259)

Buckthorn enforces strict protocol and memory invariants in C before user code is ever reached:

* **HTTP/1.1 Request Smuggling Prevention (`bt_audit.c`):** Rejects conflicting or standalone `Transfer-Encoding` headers, duplicate `Host` or `Content-Length` headers, `obs-fold` multiline continuations, whitespace before colons in header names, and any mismatch between `Content-Length` and received body bytes.
* **Slowloris & Stalled Socket Reclamation (`bt_http.c`):** Every connection is guarded by a 15-second `uv_timer_t` idle timer that automatically closes stalled partial-header or partial-body connections and reclaims their 64KB arena.
* **Path Traversal & Double-Encoding Defense (`bt_audit.c` & `responses.py`):** Blocks literal `..`, backslashes, control characters, malformed `%` sequences, null bytes (`%00`), encoded dots (`%2e`), encoded slashes (`%2f`), encoded backslashes (`%5c`), and double-encoding (`%25`). `FileResponse` enforces `os.path.realpath` `root_dir` confinement, `O_NOFOLLOW`, and `stat.S_ISREG` verification.
* **JSON Depth-Bomb, Null-Byte & Wrap-Around Protection (`bt_json.c` & `bt_params.c`):** Enforces a compile-time 64-level nesting cap (`YYJSON_READER_DEPTH_LIMIT=64`), rejects embedded `\u0000` bytes inside JSON strings to prevent C/Python string truncation mismatches, blocks `uint64_t` values greater than `INT64_MAX` from wrapping around to negative `int64_t`, and blocks IEEE-754 `NaN` / `Infinity` from bypassing numeric `ge`/`le`/`gt`/`lt` constraints.
* **Length-Hiding Constant-Time Auth (`bt_security.c`):** Hashes both inputs with OpenSSL `SHA256` before calling `CRYPTO_memcmp` and folds length equality in constant time so secret/API-key lengths are never leaked via timing side-channels.
* **CRLF Response Splitting Neutralization (`bt_audit.c` & `bt_middleware.c`):** Strips `\r`, `\n`, and `\0` from all user middleware headers, CORS configurations, and `FileResponse` filenames in C.

---

## 🛠️ CLI Reference

```bash
# 1. Run in development mode with zero-dependency file watcher hot-reload
buckthorn dev main:app --host 127.0.0.1 --port 8000 --reload

# 2. Transpile Python routes & Schemas to Cython and compile to a native .so binary
buckthorn build main.py --prod --out-dir dist_buckthorn

# 3. Run in production mode with multi-process SO_REUSEPORT workers & auto-respawn
buckthorn run main:app --host 0.0.0.0 --port 8000 --workers 4

# 4. Benchmark all discovered endpoints and enforce a minimum RPS CI speed gate
buckthorn bench main:app --iterations 20000 --gate 80000
```

---

## 🧪 Running the Release & Security Verification Suite

```bash
# Run full build + pytest + OWASP torture tests + 10-Phase RPS gates + live TCP tests
python3 scripts/verify_release.py

# Run under AddressSanitizer (ASan) + UndefinedBehaviorSanitizer (UBSan)
python3 scripts/verify_release.py --asan --scale 0.35
```
