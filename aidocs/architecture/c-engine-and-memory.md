# Domain 1: Pure C11 Engine & Zero-Copy Memory Architecture

## 1. Scope & Files
- `buckthorn/c_src/bt_arena.h` — Per-connection 64KB bump-pointer arena allocator.
- `buckthorn/c_src/bt_http.h` & `bt_http.c` — `libuv` TCP server, `picohttpparser` SIMD scanner, `rax` static router, and parameterized segment matcher.
- `buckthorn/c_src/bt_params.h` & `bt_params.c` — Zero-copy `bt_slice_t` scanners for query strings, headers, cookies, and `strtoll`/`strtod` bounds checking.
- `buckthorn/c_src/bt_json.h` & `bt_json.c` — Thread-local 64KB pool allocators for `yyjson` single-pass validation and response serialization.
- `buckthorn/c_src/bt_logger.h` & `bt_logger.c` — Lock-free C11 `<stdatomic.h>` SPSC ring buffer and background `pthread` `writev()` flusher.
- `buckthorn/c_src/bt_security.h` & `bt_security.c` — OpenSSL `HMAC(EVP_sha256())` JWT verification, `CRYPTO_memcmp` constant-time checks, and OAuth2 scope validation.
- `buckthorn/c_src/bt_middleware.h` & `bt_middleware.c` — Pre-baked C wire buffers for CORS preflight, FNV-1a hash-table rate limiting, and security headers.
- `buckthorn/c_src/bt_realtime.h` & `bt_realtime.c` — RFC 6455 WebSocket frame codec, 64-bit word XOR unmasking, and zero-copy `multipart/form-data` scanner.

---

## 2. Request Lifecycle in Pure C (`bt_handle_http_bytes`)
Both live `libuv` TCP connections (`on_uv_read`) and `RawTestClient.send_raw()` enter through `bt_handle_http_bytes()` in `bt_http.c`. This guarantees unit tests exercise 100% of the production C pipeline:

1. **Arena Reset:** `bt_arena_reset(arena)` sets `arena->offset = 0`.
2. **SIMD HTTP Parse:** `phr_parse_request()` scans the raw TCP buffer in-place and populates `struct phr_header headers[64]` with `(const char * ptr, size_t len)` slices.
3. **Tier-1 C Middleware Pre-Check (`bt_middleware_run_pre`):**
   - Validates `Host` header if `trusted_host_enabled` (returns `400` in C on mismatch).
   - Short-circuits `OPTIONS` preflight requests via `memcpy(resp_wire_buf, mw->cors_preflight_wire, ...)` in ~40ns (`204 No Content`).
   - Hashes `X-Forwarded-For` with 64-bit FNV-1a into `mw->rl_buckets[256]`; returns `429 Too Many Requests` in C if rate limit is exceeded.
4. **Route Resolution:**
   - **Fast Path:** Lookup `"METHOD:/clean_path"` in Redis `rax` (`raxFind`).
   - **Parametric Fallback:** If `raxFind` returns `raxNotFound`, scan `router->param_routes` via `bt_match_param_route()` to populate `bt_slice_t path_params[8]`.
5. **Cython Handler Callback (`router->invoke_cb`):**
   - Allocates `handler_out_buf` (32KB) and `extra_hdrs_buf` (2KB) inside `bt_arena_t`.
   - Invokes `_cython_invoke_handler` in `_core.pyx`.
6. **Wire Assembly & Access Log:**
   - Writes HTTP status line + `Content-Length`, appends `cors_append_hdrs`, `sec_append_hdrs`, and `extra_hdrs_buf`, copies `handler_out_buf`, and records an atomic ring-buffer access log (`bt_logger_log_http_access`) if `router->access_logger != NULL`.

---

## 3. Rules When Modifying the C Layer
- **Never `free()` Slices:** `bt_slice_t` pointers point directly into the caller's `raw_req` buffer or `bt_arena_t`. Never call `free()` on `slice.ptr`.
- **Thread-Local `yyjson` Pools:** `bt_json.c` uses `_Thread_local char tls_read_pool[65536]` and `tls_write_pool[65536]`. If you support payloads larger than 64KB, increase the pool constant or fall back to dynamic `yyjson_alc_dyn_new()` only when `len > 32768`.
- **Constant-Time Security:** Any comparison of secrets, API keys, passwords, or HMAC digests in `bt_security.c` must use `bt_const_time_eq()` (which wraps OpenSSL `CRYPTO_memcmp`).
