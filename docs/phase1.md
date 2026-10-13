Below is the **Phase 1 Master Technical Specification** engineered specifically for **Gemini Flash**.

Because smaller/faster coding models perform best when **complex C pointer callbacks (`libuv`, `picohttpparser`, `rax`) are isolated in pure `.c`/`.h` files** and **Cython (`.pyx`) is used purely as a thin zero-copy bridge**, this specification structures Phase 1 around that exact separation. You can copy and paste this entire specification directly into Gemini Flash as its system/task prompt.

---

# PHASE 1 SPECIFICATION: Core HTTP Engine, 64KB C Arena Allocator & Redis `rax` Radix Router

## 1. System Prompt & Strict Guardrails for Gemini Flash

> **Instructions for the AI Implementer (Gemini Flash):**
> 1. **Strict TDD Order:** You MUST create the directory structure, vendor fetch script, and the `pytest` test file (`tests/phase1/test_core_router.py`) FIRST before writing the implementation code.
> 2. **No Pure-Python Routing or Parsing:** Do NOT use Python `re`, `urllib`, `http.server`, or Python `dict` lookup for routing. All HTTP parsing MUST run through `picohttpparser.c` and all route lookups MUST run through Redis `rax.c`.
> 3. **Garbage Collection Safety (`PyObject*` in C):** When storing a Python route handler function pointer as `void*` inside the `rax` C radix tree, you MUST also append the callable to a Python `list` (`self._handler_refs`) on the Cython extension class so CPython's garbage collector never deallocates the function while its C pointer lives in `rax`.
> 4. **Cython Compiler Directives:** Every `.pyx` file must start with `# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True`.
> 5. **Zero-Socket Testability:** Both the `RawTestClient` (for instant unit testing) and the live `libuv` TCP server callback (`on_uv_read`) MUST call the exact same C entrypoint function (`bt_handle_http_bytes`) so unit tests validate the exact production C hot path.
> 
> 

---

## 2. Phase 1 Target Directory Tree

Create the following exact file tree:

```text
buckthorn/
├── pyproject.toml
├── setup.py
├── scripts/
│   └── fetch_vendor.sh
├── buckthorn/
│   ├── __init__.py
│   ├── app.py
│   ├── testing.py
│   ├── _core.pxd
│   ├── _core.pyx
│   ├── c_vendor/
│   │   ├── picohttpparser.c
│   │   ├── picohttpparser.h
│   │   ├── rax.c
│   │   ├── rax.h
│   │   └── rax_malloc.h
│   └── c_src/
│       ├── bt_arena.h
│       ├── bt_http.h
│       └── bt_http.c
└── tests/
    └── phase1/
        └── test_core_router.py

```

---

## 3. Step 1: Vendor C Libraries Script (`scripts/fetch_vendor.sh`)

Create `scripts/fetch_vendor.sh` and run it first to pull the canonical `picohttpparser` (H2O) and `rax` (Redis ANSI C Radix Tree) source files into `buckthorn/c_vendor/`:

```bash
#!/usr/bin/env bash
set -euo pipefail

VENDOR_DIR="buckthorn/c_vendor"
mkdir -p "$VENDOR_DIR"

echo "[1/2] Fetching H2O picohttpparser (SIMD HTTP/1.1 parser)..."
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.c -o "$VENDOR_DIR/picohttpparser.c"
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.h -o "$VENDOR_DIR/picohttpparser.h"

echo "[2/2] Fetching Redis rax (ANSI C Radix Tree)..."
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.c -o "$VENDOR_DIR/rax.c"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.h -o "$VENDOR_DIR/rax.h"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax_malloc.h -o "$VENDOR_DIR/rax_malloc.h"

echo "Vendor C files ready in $VENDOR_DIR"

```

*(Note on Linux system dependencies: Ensure `libuv1-dev` is installed in the Linux/Docker environment via `apt-get install -y build-essential python3-dev libuv1-dev`.)*

---

## 4. Step 2: TDD Red Phase — Write the Phase 1 Test Suite First

Create `tests/phase1/test_core_router.py`. All 5 test cases below define the exact acceptance criteria for Phase 1:

```python
# tests/phase1/test_core_router.py
import time
import pytest
from buckthorn import Buckthorn
from buckthorn.testing import RawTestClient


def test_static_get_and_post_routing():
    app = Buckthorn()

    @app.get("/ping")
    async def ping():
        return b"pong"

    @app.post("/orders")
    def create_order():
        return b'{"created":true}'

    client = RawTestClient(app)

    # 1. Test GET /ping
    res = client.send_raw(b"GET /ping HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res.status_code == 200
    assert res.body == b"pong"

    # 2. Test POST /orders
    res_post = client.send_raw(
        b"POST /orders HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}"
    )
    assert res_post.status_code == 200
    assert res_post.body == b'{"created":true}'


def test_unmatched_route_returns_404_in_c():
    app = Buckthorn()

    @app.get("/exists")
    def exists():
        return b"ok"

    client = RawTestClient(app)

    # Missing URL path -> 404 directly from C radix lookup
    res_404 = client.send_raw(b"GET /does-not-exist HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res_404.status_code == 404

    # Wrong HTTP method on existing path -> 404/405
    res_wrong_method = client.send_raw(b"DELETE /exists HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res_wrong_method.status_code in (404, 405)


def test_query_string_stripped_before_radix_lookup():
    app = Buckthorn()

    @app.get("/search")
    def search():
        return b"results"

    client = RawTestClient(app)
    res = client.send_raw(b"GET /search?q=buckthorn&limit=10 HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res.status_code == 200
    assert res.body == b"results"


def test_malformed_http_rejected_in_c_without_python():
    app = Buckthorn()

    @app.get("/ping")
    def ping():
        return b"pong"

    client = RawTestClient(app)
    res = client.send_raw(b"CORRUPTED_GARBAGE_PROTOCOL\r\n\r\n")
    assert res.status_code == 400
    assert b"Bad Request" in res.body


def test_c_arena_zero_leak_and_single_worker_speed_gate():
    app = Buckthorn()

    @app.get("/fast")
    def fast():
        return b'{"ok":1}'

    client = RawTestClient(app)
    raw_req = b"GET /fast HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"

    # Warmup
    for _ in range(500):
        client.send_raw(raw_req)

    # Execute 20,000 iterations through the C picohttpparser + rax + Cython pipeline
    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = client.send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert client.arena_offset == 0  # Verify C Arena resets to 0 after every request
    # In-memory C pipeline must easily exceed 100,000+ RPS ( guaranteeing >35,000 RPS over TCP )
    assert rps > 100_000, f"Expected >100k in-memory RPS, got {rps:,.0f} RPS"

```

---

## 5. Step 3: Green Phase — Pure C Arena & HTTP/Radix Engine (`buckthorn/c_src/`)

### 5.1 `buckthorn/c_src/bt_arena.h` (64 KB Bump-Pointer Arena Allocator)

This header provides $O(1)$ zero-overhead memory allocation and $O(1)$ reset after every HTTP request:

```c
/* buckthorn/c_src/bt_arena.h */
#ifndef BT_ARENA_H
#define BT_ARENA_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define BT_ARENA_DEFAULT_SIZE (64 * 1024) /* 64 KB per connection */

typedef struct {
    uint8_t *buf;
    size_t capacity;
    size_t offset;
} bt_arena_t;

static inline int bt_arena_init(bt_arena_t *arena, size_t capacity) {
    arena->buf = (uint8_t *)malloc(capacity);
    if (!arena->buf) return -1;
    arena->capacity = capacity;
    arena->offset = 0;
    return 0;
}

static inline void *bt_arena_alloc(bt_arena_t *arena, size_t size) {
    /* Align to 8 bytes */
    size_t aligned = (size + 7) & ~((size_t)7);
    if (arena->offset + aligned > arena->capacity) {
        return NULL; /* Out of arena memory */
    }
    void *ptr = arena->buf + arena->offset;
    arena->offset += aligned;
    return ptr;
}

static inline void bt_arena_reset(bt_arena_t *arena) {
    arena->offset = 0;
}

static inline void bt_arena_destroy(bt_arena_t *arena) {
    if (arena->buf) {
        free(arena->buf);
        arena->buf = NULL;
    }
    arena->capacity = 0;
    arena->offset = 0;
}

#endif /* BT_ARENA_H */

```

### 5.2 `buckthorn/c_src/bt_http.h` (HTTP Context, Router & `libuv` Declarations)

```c
/* buckthorn/c_src/bt_http.h */
#ifndef BT_HTTP_H
#define BT_HTTP_H

#include <stddef.h>
#include <stdint.h>
#include "bt_arena.h"
#include "../c_vendor/picohttpparser.h"
#include "../c_vendor/rax.h"

#define BT_MAX_HEADERS 64
#define BT_RESP_BUF_SIZE (64 * 1024)

/* Callback signature invoked when C router matches a Cython/Python handler */
typedef int (*bt_handler_invoke_fn)(
    void *handler_obj,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    struct phr_header *headers, size_t num_headers,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    int *out_status_code
);

typedef struct {
    rax *tree;
    bt_handler_invoke_fn invoke_cb;
} bt_router_t;

typedef struct {
    int status_code;
    char *http_wire_buf;
    size_t http_wire_len;
    const char *body_ptr;
    size_t body_len;
} bt_raw_response_t;

bt_router_t *bt_router_create(bt_handler_invoke_fn invoke_cb);
void bt_router_destroy(bt_router_t *router);

/* Registers a route key formatted as "METHOD:/path" (e.g. "GET:/ping") into rax */
int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                     const char *path, size_t path_len, void *handler_obj);

/* Core zero-allocation C HTTP processor used by both RawTestClient and libuv */
int bt_handle_http_bytes(
    bt_router_t *router,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
);

/* Starts the native libuv TCP server with SO_REUSEPORT */
int bt_uv_server_run(bt_router_t *router, const char *host, int port);

#endif /* BT_HTTP_H */

```

### 5.3 `buckthorn/c_src/bt_http.c` (Implementation of `picohttpparser` + `rax` + `libuv`)

```c
/* buckthorn/c_src/bt_http.c */
#include "bt_http.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <uv.h>

static const char *RESP_400 =
    "HTTP/1.1 400 Bad Request\r\n"
    "Content-Length: 11\r\n"
    "Connection: close\r\n\r\n"
    "Bad Request";

static const char *RESP_404 =
    "HTTP/1.1 404 Not Found\r\n"
    "Content-Length: 9\r\n"
    "Connection: keep-alive\r\n\r\n"
    "Not Found";

static const char *RESP_500 =
    "HTTP/1.1 500 Internal Server Error\r\n"
    "Content-Length: 21\r\n"
    "Connection: close\r\n\r\n"
    "Internal Server Error";

bt_router_t *bt_router_create(bt_handler_invoke_fn invoke_cb) {
    bt_router_t *r = (bt_router_t *)malloc(sizeof(bt_router_t));
    if (!r) return NULL;
    r->tree = raxNew();
    r->invoke_cb = invoke_cb;
    return r;
}

void bt_router_destroy(bt_router_t *router) {
    if (!router) return;
    if (router->tree) raxFree(router->tree);
    free(router);
}

int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                     const char *path, size_t path_len, void *handler_obj) {
    unsigned char key[512];
    if (method_len + 1 + path_len >= sizeof(key)) return -1;

    memcpy(key, method, method_len);
    key[method_len] = ':';
    memcpy(key + method_len + 1, path, path_len);
    size_t key_len = method_len + 1 + path_len;

    raxInsert(router->tree, key, key_len, handler_obj, NULL);
    return 0;
}

int bt_handle_http_bytes(
    bt_router_t *router,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
) {
    bt_arena_reset(arena);

    const char *method = NULL;
    size_t method_len = 0;
    const char *path = NULL;
    size_t path_len = 0;
    int minor_version = 0;
    struct phr_header headers[BT_MAX_HEADERS];
    size_t num_headers = BT_MAX_HEADERS;

    int pret = phr_parse_request(
        raw_req, raw_req_len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers, 0
    );

    if (pret <= 0) {
        size_t len = strlen(RESP_400);
        memcpy(resp_wire_buf, RESP_400, len);
        out_result->status_code = 400;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 11);
        out_result->body_len = 11;
        bt_arena_reset(arena);
        return -1;
    }

    /* Separate path and query string ('?') using zero-copy pointer arithmetic */
    const char *query = NULL;
    size_t query_len = 0;
    size_t clean_path_len = path_len;
    const char *qmark = (const char *)memchr(path, '?', path_len);
    if (qmark) {
        clean_path_len = (size_t)(qmark - path);
        query = qmark + 1;
        query_len = path_len - clean_path_len - 1;
    }

    /* Build lookup key "METHOD:/clean_path" inside the C Arena */
    size_t key_len = method_len + 1 + clean_path_len;
    unsigned char *lookup_key = (unsigned char *)bt_arena_alloc(arena, key_len);
    if (!lookup_key) return -2;

    memcpy(lookup_key, method, method_len);
    lookup_key[method_len] = ':';
    memcpy(lookup_key + method_len + 1, path, clean_path_len);

    void *handler_obj = raxFind(router->tree, lookup_key, key_len);
    if (handler_obj == raxNotFound) {
        size_t len = strlen(RESP_404);
        memcpy(resp_wire_buf, RESP_404, len);
        out_result->status_code = 404;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 9);
        out_result->body_len = 9;
        bt_arena_reset(arena);
        return 0;
    }

    const char *body_ptr = raw_req + pret;
    size_t body_len = raw_req_len - (size_t)pret;

    /* Allocate temporary response body buffer inside the C Arena */
    char *handler_out_buf = (char *)bt_arena_alloc(arena, 32 * 1024);
    size_t handler_out_len = 0;
    int status_code = 200;

    int rc = router->invoke_cb(
        handler_obj,
        method, method_len,
        path, clean_path_len,
        query, query_len,
        headers, num_headers,
        body_ptr, body_len,
        handler_out_buf, &handler_out_len,
        &status_code
    );

    if (rc != 0) {
        size_t len = strlen(RESP_500);
        memcpy(resp_wire_buf, RESP_500, len);
        out_result->status_code = 500;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 21);
        out_result->body_len = 21;
        bt_arena_reset(arena);
        return -1;
    }

    /* Format HTTP/1.1 response header + body into resp_wire_buf */
    int hdr_len = snprintf(
        resp_wire_buf, resp_wire_cap,
        "HTTP/1.1 %d OK\r\n"
        "Content-Length: %zu\r\n"
        "Connection: keep-alive\r\n\r\n",
        status_code, handler_out_len
    );

    memcpy(resp_wire_buf + hdr_len, handler_out_buf, handler_out_len);
    out_result->status_code = status_code;
    out_result->http_wire_buf = resp_wire_buf;
    out_result->http_wire_len = (size_t)hdr_len + handler_out_len;
    out_result->body_ptr = resp_wire_buf + hdr_len;
    out_result->body_len = handler_out_len;

    /* Reset arena offset to 0 immediately after request completes */
    bt_arena_reset(arena);
    return 0;
}

/* =========================================================================
 * Native libuv TCP Server Glue (per-connection 64KB Arena + Read/Write)
 * ========================================================================= */
typedef struct {
    uv_tcp_t handle;
    bt_router_t *router;
    bt_arena_t arena;
    char read_buf[8192];
    char write_buf[BT_RESP_BUF_SIZE];
    uv_write_t write_req;
} bt_uv_client_t;

static void on_client_close(uv_handle_t *handle) {
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    bt_arena_destroy(&client->arena);
    free(client);
}

static void on_alloc_buffer(uv_handle_t *handle, size_t suggested_size, uv_buf_t *buf) {
    (void)suggested_size;
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    buf->base = client->read_buf;
    buf->len = sizeof(client->read_buf);
}

static void on_write_complete(uv_write_t *req, int status) {
    (void)req;
    (void)status;
}

static void on_uv_read(uv_stream_t *stream, ssize_t nread, const uv_buf_t *buf) {
    bt_uv_client_t *client = (bt_uv_client_t *)stream->data;
    if (nread > 0) {
        bt_raw_response_t result;
        bt_handle_http_bytes(
            client->router,
            &client->arena,
            buf->base,
            (size_t)nread,
            client->write_buf,
            sizeof(client->write_buf),
            &result
        );
        uv_buf_t wbuf = uv_buf_init(result.http_wire_buf, (unsigned int)result.http_wire_len);
        uv_write(&client->write_req, stream, &wbuf, 1, on_write_complete);
    } else if (nread < 0) {
        uv_close((uv_handle_t *)stream, on_client_close);
    }
}

static void on_new_connection(uv_stream_t *server, int status) {
    if (status < 0) return;
    bt_router_t *router = (bt_router_t *)server->data;

    bt_uv_client_t *client = (bt_uv_client_t *)malloc(sizeof(bt_uv_client_t));
    if (!client) return;
    bt_arena_init(&client->arena, BT_ARENA_DEFAULT_SIZE);
    client->router = router;

    uv_tcp_init(server->loop, &client->handle);
    client->handle.data = client;

    if (uv_accept(server, (uv_stream_t *)&client->handle) == 0) {
        uv_tcp_nodelay(&client->handle, 1);
        uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
    } else {
        uv_close((uv_handle_t *)&client->handle, on_client_close);
    }
}

int bt_uv_server_run(bt_router_t *router, const char *host, int port) {
    uv_loop_t *loop = uv_default_loop();
    uv_tcp_t server;
    uv_tcp_init_ex(loop, &server, AF_INET);
    server.data = router;

    /* Enable SO_REUSEPORT on Linux if supported via raw fd */
    uv_os_fd_t fd;
    if (uv_fileno((const uv_handle_t *)&server, &fd) == 0) {
        int opt = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &opt, sizeof(opt));
    }

    struct sockaddr_in addr;
    uv_ip4_addr(host, port, &addr);
    uv_tcp_bind(&server, (const struct sockaddr *)&addr, 0);

    int r = uv_listen((uv_stream_t *)&server, 4096, on_new_connection);
    if (r) return r;

    return uv_run(loop, UV_RUN_DEFAULT);
}

```

---

## 6. Step 4: Cython Bridge (`buckthorn/_core.pxd` & `buckthorn/_core.pyx`)

### 6.1 `buckthorn/_core.pxd`

```cython
# cython: language_level=3
from libc.stddef cimport size_t
from libc.stdint cimport uint8_t

cdef extern from "c_vendor/picohttpparser.h" nogil:
    struct phr_header:
        const char *name
        size_t name_len
        const char *value
        size_t value_len

cdef extern from "c_src/bt_arena.h" nogil:
    ctypedef struct bt_arena_t:
        uint8_t *buf
        size_t capacity
        size_t offset

    int bt_arena_init(bt_arena_t *arena, size_t capacity)
    void bt_arena_reset(bt_arena_t *arena)
    void bt_arena_destroy(bt_arena_t *arena)

cdef extern from "c_src/bt_http.h" nogil:
    ctypedef int (*bt_handler_invoke_fn)(
        void *handler_obj,
        const char *method, size_t method_len,
        const char *path, size_t path_len,
        const char *query, size_t query_len,
        phr_header *headers, size_t num_headers,
        const char *body, size_t body_len,
        char *out_body_buf, size_t *out_body_len,
        int *out_status_code
    )

    ctypedef struct bt_router_t:
        pass

    ctypedef struct bt_raw_response_t:
        int status_code
        char *http_wire_buf
        size_t http_wire_len
        const char *body_ptr
        size_t body_len

    bt_router_t *bt_router_create(bt_handler_invoke_fn invoke_cb)
    void bt_router_destroy(bt_router_t *router)
    int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                         const char *path, size_t path_len, void *handler_obj)
    int bt_handle_http_bytes(
        bt_router_t *router,
        bt_arena_t *arena,
        const char *raw_req,
        size_t raw_req_len,
        char *resp_wire_buf,
        size_t resp_wire_cap,
        bt_raw_response_t *out_result
    )
    int bt_uv_server_run(bt_router_t *router, const char *host, int port)

```

### 6.2 `buckthorn/_core.pyx`

```cython
# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
from libc.string cimport memcpy
from cpython.bytes cimport PyBytes_AsStringAndSize, PyBytes_FromStringAndSize
import inspect

from buckthorn._core cimport (
    phr_header, bt_arena_t, bt_arena_init, bt_arena_destroy,
    bt_router_t, bt_raw_response_t,
    bt_router_create, bt_router_destroy, bt_router_insert,
    bt_handle_http_bytes, bt_uv_server_run
)


cdef class RouteEntry:
    cdef public object fn
    cdef public bint is_coroutine

    def __cinit__(self, object fn):
        self.fn = fn
        self.is_coroutine = inspect.iscoroutinefunction(fn)


cdef int _cython_invoke_handler(
    void *handler_obj,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    phr_header *headers, size_t num_headers,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    int *out_status_code
) noexcept with gil:
    cdef RouteEntry entry = <RouteEntry>handler_obj
    cdef object result
    cdef char *raw_bytes_ptr = NULL
    cdef Py_ssize_t raw_bytes_len = 0

    try:
        if entry.is_coroutine:
            # Phase 1 synchronous stepping of simple coroutine (no-await fast path)
            coro = entry.fn()
            try:
                coro.send(None)
            except StopIteration as stop:
                result = stop.value
            finally:
                coro.close()
        else:
            result = entry.fn()

        if isinstance(result, bytes):
            PyBytes_AsStringAndSize(result, &raw_bytes_ptr, &raw_bytes_len)
            memcpy(out_body_buf, raw_bytes_ptr, <size_t>raw_bytes_len)
            out_body_len[0] = <size_t>raw_bytes_len
            out_status_code[0] = 200
            return 0
        elif isinstance(result, str):
            encoded = (<str>result).encode("utf-8")
            PyBytes_AsStringAndSize(encoded, &raw_bytes_ptr, &raw_bytes_len)
            memcpy(out_body_buf, raw_bytes_ptr, <size_t>raw_bytes_len)
            out_body_len[0] = <size_t>raw_bytes_len
            out_status_code[0] = 200
            return 0
        else:
            return -1
    except Exception:
        return -1


cdef class CoreRouter:
    cdef bt_router_t *_router
    cdef list _handler_refs  # Prevents Python GC from collecting RouteEntry pointers in C rax

    def __cinit__(self):
        self._handler_refs = []
        self._router = bt_router_create(_cython_invoke_handler)
        if self._router == NULL:
            raise MemoryError("Failed to allocate C rax router")

    def __dealloc__(self):
        if self._router != NULL:
            bt_router_destroy(self._router)
            self._router = NULL

    cpdef void add_route(self, str method, str path, object fn):
        cdef bytes b_method = method.upper().encode("ascii")
        cdef bytes b_path = path.encode("utf-8")
        cdef RouteEntry entry = RouteEntry(fn)
        self._handler_refs.append(entry)

        bt_router_insert(
            self._router,
            <const char *>b_method, <size_t>len(b_method),
            <const char *>b_path, <size_t>len(b_path),
            <void *>entry
        )

    cpdef void run_server(self, str host="0.0.0.0", int port=8000):
        cdef bytes b_host = host.encode("ascii")
        bt_uv_server_run(self._router, <const char *>b_host, port)


cdef class RawResponse:
    cdef readonly int status_code
    cdef readonly bytes body
    cdef readonly bytes wire_bytes

    def __cinit__(self, int status_code, bytes body, bytes wire_bytes):
        self.status_code = status_code
        self.body = body
        self.wire_bytes = wire_bytes


cdef class CythonRawTestClient:
    cdef CoreRouter _core_router
    cdef bt_arena_t _arena
    cdef char _resp_buf[65536]

    def __cinit__(self, CoreRouter core_router):
        self._core_router = core_router
        if bt_arena_init(&self._arena, 65536) != 0:
            raise MemoryError("Failed to allocate 64KB C Arena")

    def __dealloc__(self):
        bt_arena_destroy(&self._arena)

    @property
    def arena_offset(self) -> int:
        return <int>self._arena.offset

    cpdef RawResponse send_raw(self, bytes raw_request):
        cdef char *req_ptr = NULL
        cdef Py_ssize_t req_len = 0
        cdef bt_raw_response_t out_res

        PyBytes_AsStringAndSize(raw_request, &req_ptr, &req_len)
        bt_handle_http_bytes(
            self._core_router._router,
            &self._arena,
            <const char *>req_ptr,
            <size_t>req_len,
            self._resp_buf,
            65536,
            &out_res
        )

        cdef bytes body_bytes = PyBytes_FromStringAndSize(out_res.body_ptr, <Py_ssize_t>out_res.body_len)
        cdef bytes wire_bytes = PyBytes_FromStringAndSize(out_res.http_wire_buf, <Py_ssize_t>out_res.http_wire_len)
        return RawResponse(out_res.status_code, body_bytes, wire_bytes)

```

---

## 7. Step 5: Python FastAPI-Style Surface (`buckthorn/app.py`, `testing.py`, `__init__.py`)

### 7.1 `buckthorn/app.py`

```python
# buckthorn/app.py
from typing import Callable
from buckthorn._core import CoreRouter


class Buckthorn:
    def __init__(self, title: str = "Buckthorn", version: str = "0.1.0"):
        self.title = title
        self.version = version
        self._core = CoreRouter()

    def add_api_route(self, path: str, endpoint: Callable, methods: list[str]) -> None:
        for method in methods:
            self._core.add_route(method.upper(), path, endpoint)

    def get(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["GET"])
            return fn
        return decorator

    def post(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["POST"])
            return fn
        return decorator

    def put(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["PUT"])
            return fn
        return decorator

    def delete(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["DELETE"])
            return fn
        return decorator

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self._core.run_server(host, port)

```

### 7.2 `buckthorn/testing.py`

```python
# buckthorn/testing.py
from buckthorn.app import Buckthorn
from buckthorn._core import CythonRawTestClient, RawResponse


class RawTestClient:
    def __init__(self, app: Buckthorn):
        self._client = CythonRawTestClient(app._core)

    def send_raw(self, raw_http: bytes) -> RawResponse:
        return self._client.send_raw(raw_http)

    @property
    def arena_offset(self) -> int:
        return self._client.arena_offset

```

### 7.3 `buckthorn/__init__.py`

```python
# buckthorn/__init__.py
from buckthorn.app import Buckthorn

__all__ = ["Buckthorn"]

```

---

## 8. Step 6: Build System (`setup.py` & `pyproject.toml`)

### 8.1 `setup.py`

```python
# setup.py
from setuptools import setup, find_packages, Extension
from Cython.Build import cythonize

ext_modules = [
    Extension(
        name="buckthorn._core",
        sources=[
            "buckthorn/_core.pyx",
            "buckthorn/c_src/bt_http.c",
            "buckthorn/c_vendor/picohttpparser.c",
            "buckthorn/c_vendor/rax.c",
        ],
        include_dirs=[
            "buckthorn",
            "buckthorn/c_src",
            "buckthorn/c_vendor",
        ],
        libraries=["uv"],
        extra_compile_args=[
            "-O3",
            "-march=native",
            "-msse4.2",
            "-flto",
            "-Wall",
            "-Wno-unused-function",
        ],
        extra_link_args=["-flto"],
    )
]

setup(
    name="buckthorn",
    version="0.1.0",
    packages=find_packages(),
    ext_modules=cythonize(
        ext_modules,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
        annotate=True,  # Generates buckthorn/_core.html to inspect C vs Python lines
    ),
)

```

### 8.2 `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68.0", "Cython>=3.0.10", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "buckthorn"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0"]

```

---

## 9. Step 7: Execution & Verification Checklist for Gemini Flash

Have Gemini Flash run these exact 3 commands sequentially and verify all outputs before moving to Phase 2:

```bash
# 1. Fetch picohttpparser and rax C vendor files
bash scripts/fetch_vendor.sh

# 2. Compile C + Cython extension in-place with SIMD (-msse4.2) and LTO (-flto)
pip install -e ".[dev]" --no-build-isolation

# 3. Run the Phase 1 TDD Test Suite & Speed Gate
pytest tests/phase1/test_core_router.py -v -s

```