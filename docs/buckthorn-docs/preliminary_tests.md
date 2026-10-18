# 🌲 Buckthorn: Single-Core Performance & Preliminary Test Records

This document preserves the official benchmark and performance records for the Buckthorn web framework running on a **single CPU worker core** (`-t1` configuration). It records both live network socket throughput (`wrk`) and pure in-memory engine micro-benchmarks across all 10 architectural phases.

---

## 🖥️ System & Benchmark Test Environment

All benchmarks were captured on an identical, standardized reference platform:

| Parameter | Specification |
| :--- | :--- |
| **CPU** | Intel® Core™ i5-10210U CPU @ 1.60GHz (4 Cores / 8 Threads, 6 MiB L3 Cache, 15W TDP) |
| **Operating System** | Linux x86_64 (Kernel 6.8.0) |
| **Python Runtimes** | CPython 3.14.7 / CPython 3.12.3 |
| **Compiler & Flags** | GCC 13.x with `-O3 -march=native -flto -fno-semantic-interposition` |
| **HTTP Parser** | `picohttpparser` (SSE4.2 / AVX SIMD vectorization) |
| **JSON SerDe** | `yyjson` (SIMD JSON parser with thread-local 64KB pools) |
| **Routing Engine** | `rax` (Redis ANSI C Radix Tree, O(K) lookup) |
| **Network Client** | `wrk` (Single thread `-t1`, 100 concurrent connections `-c100`, 10-second duration `-d10s`) |

---

## 1. Live Network Benchmarks: Single-Worker (`wrk -t1 -c100 -d10s`)

The following benchmarks measure the **end-to-end network pipeline over real TCP loopback sockets** via `libuv`. These tests execute the full lifecycle: kernel socket read, SIMD HTTP parsing, C radix routing, parameter extraction, schema validation, Cython handler dispatch, wire formatting, and socket write.

### Workload Suite Breakdown (`benchmarks/app.py` & `benchmarks/async_app.py`)

| Endpoint / Workload | Throughput (RPS) | Avg Latency | p50 Latency | p99 Latency | Total Requests / 10s | Data Transferred |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`GET /health`** (Raw JSON / Plaintext) | **50,893 req/s** | 1.96 ms | 1.91 ms | 3.12 ms | 511,480 reqs | 67.26 MB |
| **`GET /stores/101/analytics`** (Path Param + Complex JSON) | **44,998 req/s** | 2.22 ms | 2.13 ms | 3.82 ms | 452,246 reqs | 114.75 MB |
| **`POST /items`** (SIMD `yyjson` Schema SerDe Validation) | **42,406 req/s** | 2.35 ms | 2.29 ms | 4.10 ms | 426,192 reqs | 64.62 MB |
| **`GET /stores/101/inventory/SKU`** (Path + Query Params) | **40,524 req/s** | 2.46 ms | 2.39 ms | 4.35 ms | 407,284 reqs | 61.36 MB |
| **Mixed Enterprise Traffic** (`mixed_traffic.lua`) | **35,960 req/s** | 2.78 ms | 2.73 ms | 4.79 ms | 361,407 reqs | 61.43 MB |
| **`POST /stores/101/checkout`** (JWT + Nested Schema) | **24,919 req/s** | 4.01 ms | 3.93 ms | 6.84 ms | 250,447 reqs | 44.18 MB |
| **Native Async `GET /health`** (`async_app.py` via `PyIter_Send`) | **49,371 req/s** | 2.02 ms | 1.96 ms | 3.32 ms | 496,203 reqs | 65.25 MB |

> [!NOTE]
> In single-worker TCP loopback testing (`wrk -t1`), the primary performance ceiling is the **Linux kernel TCP/IP network stack** (`epoll_wait`, `read`, `write`, context switches), not the Python or C runtime. Under this constraint, Buckthorn achieves **>50,000 req/s on a single core**, while traditional Python frameworks (FastAPI/Uvicorn) achieve ~10,000–15,000 req/s under identical conditions.

---

## 2. In-Memory Engine Micro-Benchmarks (`benchmarks/verify_overview.py`)

These micro-benchmarks measure the pure computational overhead of Buckthorn's C runtime engine without OS socket overhead, directly evaluating `bt_handle_http_bytes`:

| Workflow Phase | In-Memory Throughput | p50 Latency | Latency Margin / Overhead |
| :--- | :---: | :---: | :--- |
| **Plaintext / Static Route** (`GET /health`) | **1,262,278 RPS** | **0.57 µs** | Baseline C radix lookup + bump arena reset |
| **Fast 401/403 Security Rejection** | **1,184,896 RPS** | **0.61 µs** | Pure C middleware short-circuit |
| **Typed Parameter Extraction** (Path/Query/Header) | **577,278 RPS** | **1.45 µs** | Zero-copy `bt_slice_t` pointer slicing |
| **Validated Schema SerDe** (`POST /items`) | **430,562 RPS** | **1.97 µs** | Single-pass `yyjson` C struct validation |
| **Dependency Injection** (3-Level `Depends()` DAG) | **407,381 RPS** | **2.10 µs** | Flattened compiled array step execution |
| **OpenSSL HMAC-SHA256 JWT Verification** | **186,401 RPS** | **4.62 µs** | Hardware-accelerated cryptographic HMAC |
| **Lock-Free Atomic Ring Logger** (2 logs/req) | **998,416 RPS** | **0.78 µs** | Lock-free C11 atomic SPSC buffer |

---

## 3. AOT Compiled Native Binary Mode (`.so`)

Buckthorn includes an AST-to-Cython transpiler (`buckthorn.compiler`) that compiles standard Python route handlers and schemas into native C `.so` shared libraries.

### 3A. In-Memory Micro-Benchmarks (`benchmarks/run_suite.py --mode compiled`)
Measures pure execution speed of the compiled binary in RAM via `RawTestClient` (zero OS socket overhead):

| Endpoint | In-Memory Throughput | Avg Latency | p50 Latency | p99 Latency |
| :--- | :---: | :---: | :---: | :---: |
| **`GET /health`** (Telemetry Probe) | **135,576 req/s** | 7.0 µs | 5.9 µs | 25.6 µs |
| **`GET /stores/101/analytics`** (OpenSSL API-Key) | **110,062 req/s** | 8.6 µs | 7.5 µs | 27.8 µs |
| **`POST /items`** (SIMD `yyjson` Schema Valid) | **101,986 req/s** | 9.3 µs | 8.1 µs | 30.1 µs |
| **`GET /inventory`** (Path+Query+DI) | **82,220 req/s** | 11.7 µs | 9.9 µs | 35.4 µs |
| **`POST /checkout`** (JWT+`yyjson`+Filter) | **37,478 req/s** | 26.0 µs | 23.0 µs | 63.3 µs |

### 3B. Live Network Socket Benchmarks (`wrk -t1 -c100 -d10s`)
Measures the compiled binary served live over TCP loopback sockets on port 8000:

| Endpoint / Workload | Live Network Throughput | Avg Latency | p50 Latency | p99 Latency |
| :--- | :---: | :---: | :---: | :---: |
| **`GET /health`** | **46,491 req/s** | 2.15 ms | 2.06 ms | 3.45 ms |
| **`GET /stores/101/analytics`** | **40,911 req/s** | 2.45 ms | 2.33 ms | 3.84 ms |
| **`POST /items`** (`post_item.lua`) | **39,935 req/s** | 2.51 ms | 2.38 ms | 3.64 ms |
| **`GET /stores/101/inventory/SKU`** | **37,171 req/s** | 2.69 ms | 2.57 ms | 3.77 ms |
| **Mixed Enterprise Traffic** (`mixed_traffic.lua`) | **32,028 req/s** | 3.12 ms | 2.98 ms | 4.74 ms |
| **`POST /stores/101/checkout`** (`post_order.lua`) | **22,542 req/s** | 4.43 ms | 4.31 ms | 5.94 ms |

> [!NOTE]
> **Why does In-Memory reach ~136k RPS while `wrk -t1` reaches ~46k RPS?**  
> In in-memory testing (`run_suite.py`), the compiled Cython binary takes only **7.0 µs** per request in user-space RAM ($\sim 136,000\text{ RPS}$).  
> When running `wrk` over TCP loopback, every request must also traverse the **Linux kernel network subsystem** (`epoll_wait`, TCP socket buffers, context switches, packet ACKs), which adds ~14–15 µs of fixed kernel latency per request. On a single thread and single core, the Linux loopback TCP stack itself caps out at ~45k–50k RPS. To break past 50k on the network layer, multi-worker scaling (`--workers N`) across multiple CPU cores must be used.

---

## 4. Single-Worker Speed Gates Verification (All Cleared)

Every pull request and optimization phase is verified against strict single-worker architectural speed gates:

| Phase | Architectural Layer | Measured Speed | Speed Gate Floor | Clearance Margin | Status |
| :---: | :--- | :---: | :---: | :---: | :---: |
| **Phase 1** | Core Radix Router & Bump Arena | **1,837,280 RPS** | `> 120,000 RPS` | **+1,431%** | ✅ CLEARED |
| **Phase 2** | Typed Parameter Extraction | **691,391 RPS** | `> 95,000 RPS` | **+627%** | ✅ CLEARED |
| **Phase 3** | Single-Pass Schema SerDe (`yyjson`) | **481,198 RPS** | `> 98,000 RPS` | **+391%** | ✅ CLEARED |
| **Phase 4** | Flattened Dependency Injection (DI) | **> 450,000 RPS** | `> 86,000 RPS` | **+423%** | ✅ CLEARED |
| **Phase 5** | Atomic Ring Buffer Logger (2 logs/req) | **998,416 RPS** | `> 100,000 RPS` | **+898%** | ✅ CLEARED |
| **Phase 6** | OpenSSL JWT HS256 Security | **255,204 RPS** | `> 98,000 RPS` | **+160%** | ✅ CLEARED |
| **Phases 7–10** | Middlewares, WebSockets & Transpiler | Fully Verified | Baseline Verified | **100% Passed** | ✅ CLEARED |

---

## 5. How to Reproduce These Results

### Step 1: Rebuild Native Extensions
```bash
# Build Cython & C extensions with Link-Time Optimization (-flto)
pip install -e ".[dev]" --no-build-isolation
```

### Step 2: Reproduce In-Memory Speed Gates
```bash
python benchmarks/verify_overview.py
```

### Step 3: Reproduce Single-Core Network Benchmarks (`wrk -t1`)
```bash
# Terminal 1: Launch the single-worker server
python -m buckthorn.cli run benchmarks/app.py:app --port 8000

# Terminal 2: Run wrk with single thread (-t1)
wrk -t1 -c100 -d10s --latency http://127.0.0.1:8000/health
wrk -t1 -c100 -d10s --latency http://127.0.0.1:8000/stores/101/analytics
wrk -t1 -c100 -d10s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/items
wrk -t1 -c100 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/
```

### Step 4: Reproduce AOT Compiled Mode
```bash
python benchmarks/run_suite.py --mode compiled
```
