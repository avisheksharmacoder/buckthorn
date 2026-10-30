# Buckthorn Benchmark Progress Report — V1 (2026-10-30)

**Date:** October 30, 2026  
**Environment:** Linux 6.8 (x86_64) | Python 3.14.7 | GCC 13.3.0  
**Configuration:** Single Worker (`1 worker` process) | `wrk` Tool (`1 thread`, `50-100 connections`)  
**Engine:** Buckthorn Native C99 / Cython 3.x / libuv Runtime  

---

## 1. Executive Summary

This report documents baseline production throughput and latency metrics for Buckthorn across realistic cloud commerce endpoints under a strict **1 worker vs. 1 wrk thread** test configuration.

All tests were performed against a live HTTP/1.1 TCP server on `http://127.0.0.1:8000` with **full security middlewares active** (C CORS preflight, SecurityHeaders, FNV-1a Rate Limiter, C Audit Guard, and Cython telemetry headers).

### Key Takeaway
* **Live TCP Throughput:** Buckthorn achieves **61,653 RPS** on lightweight telemetry endpoints and **38,452 RPS** under realistic mixed multi-tenant enterprise traffic on a single CPU core.
* **Complex Cryptographic Payloads:** Even with full OpenSSL HMAC-SHA256 JWT signature verification, claims decoding, OAuth2 scopes checking, SIMD `yyjson` schema validation, and database generator dependency injection, Buckthorn sustains **25,761 RPS** with a median latency of **1.85 ms**.
* **Zero Packet Errors:** Across >2,500,000 total benchmark requests, Buckthorn sustained **0 socket errors**, **0 timeouts**, and **0 connection drops**.

---

## 2. Live HTTP/1.1 TCP Benchmark Results (`wrk -t1`)

| Endpoint & Scenario | Method & Payload | Concurrency | Requests/sec (RPS) | p50 Latency | p90 Latency | p99 Latency | Transfer/sec |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. GET /health** | Static JSON Probe | 50 conns | **61,653.56** | 775 µs | 880 µs | 1.54 ms | 23.11 MB/s |
| **1b. GET /health** | Static JSON Probe | 100 conns | **60,782.86** | 1.59 ms | 1.84 ms | 2.33 ms | 22.78 MB/s |
| **2. GET /inventory** | Path + Query + DB DI Teardown | 50 conns | **43,825.29** | 1.08 ms | 1.28 ms | 2.19 ms | 19.02 MB/s |
| **3. POST /items** | SIMD `yyjson` Schema Body | 50 conns | **48,170.93** | 980 µs | 1.15 ms | 1.99 ms | 17.46 MB/s |
| **4. GET /analytics** | OpenSSL `CRYPTO_memcmp` API Key | 50 conns | **42,572.05** | 1.11 ms | 1.33 ms | 2.29 ms | 18.27 MB/s |
| **5. POST /checkout** | JWT + Scopes + `yyjson` + DI + Filter | 50 conns | **25,761.00** | 1.85 ms | 2.16 ms | 3.70 ms | 12.46 MB/s |
| **6. Mixed Traffic** | 75% GET, 20% POST, 5% Analytics | 50 conns | **38,452.20** | 1.24 ms | 1.38 ms | 2.58 ms | 16.89 MB/s |

---

## 3. In-Memory Microsecond Benchmark Suite (`run_suite.py`)

To evaluate raw C engine execution overhead without kernel TCP socket framing, `benchmarks/run_suite.py` was executed across 10,000 iterations comparing synchronous handlers with native `async def` coroutines:

| Endpoint & Scenario | Sync Throughput | Async Throughput | Retention | Overhead |
| :--- | :---: | :---: | :---: | :---: |
| **1. GET /health (Telemetry Probe)** | 174,594 req/s | 153,592 req/s | 88.0% | +0.8 µs |
| **2. GET /inventory (Path + Query + DI)** | 102,575 req/s | 78,857 req/s | 76.9% | +2.9 µs |
| **3. POST /items (`yyjson` Schema)** | 122,864 req/s | 119,926 req/s | 97.6% | +0.2 µs |
| **4. GET /analytics (OpenSSL API-Key)** | 96,945 req/s | 94,242 req/s | 97.2% | +0.3 µs |
| **5. POST /checkout (JWT + `yyjson` + Filter)** | 40,668 req/s | 34,763 req/s | 85.5% | +4.1 µs |

> Traditional ASGI frameworks (FastAPI + Uvicorn / Starlette) experience a 60%–75% performance penalty on `async def` endpoints due to `asyncio.Task` allocation overhead. Buckthorn's 3-Tier Eager Coroutine runner preserves **77%–98%** of raw sync throughput.

---

## 4. Hardware & Benchmark Reproducibility

### System Specifications
* **CPU:** x86_64 CPU (SSE4.2 enabled, native hardware acceleration)
* **RAM:** 64-bit Linux Architecture
* **Python Version:** Python 3.14.7

### Reproduction Commands
```bash
# 1. Start Buckthorn with 1 worker:
python benchmarks/app.py

# 2. Run wrk with 1 worker thread (10s duration, 50 connections):
wrk -t1 -c50 -d10s --latency http://127.0.0.1:8000/health
wrk -t1 -c50 -d10s --latency -H "X-Tenant-ID: tenant-corp-01" "http://127.0.0.1:8000/stores/101/inventory/SKU-9942?currency=USD"
wrk -t1 -c50 -d10s --latency -s benchmarks/post_item.lua http://127.0.0.1:8000/items
wrk -t1 -c50 -d10s --latency -H "X-API-Key: live_bench_api_key_999" "http://127.0.0.1:8000/stores/101/analytics?period=today"
wrk -t1 -c50 -d10s --latency -s benchmarks/post_order.lua http://127.0.0.1:8000/stores/101/checkout
wrk -t1 -c50 -d10s --latency -s benchmarks/mixed_traffic.lua http://127.0.0.1:8000/
```
