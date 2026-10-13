Below is the **Phase 2 Master Technical Specification** engineered for **Gemini Flash**.

It builds directly on top of the Phase 1 codebase without breaking any Phase 1 tests. You can copy and paste this entire specification into Gemini Flash to implement **Zero-Copy `Request` Objects (`@cython.freelist(4096)`)**, **Dynamic Path Parameter Matching (`/users/{user_id}`)**, and **C-Level Typed Parameter Extraction & Bounds Validation (`Path`, `Query`, `Header`, `Cookie`)**.

---

# PHASE 2 SPECIFICATION: Zero-Copy `Request` Wrapper & C-Level Typed Parameter Extraction

## 1. System Prompt & Strict Guardrails for Gemini Flash

> **Instructions for the AI Implementer (Gemini Flash):**
> 1. **Backward Compatibility Gate:** All 5 tests in `tests/phase1/test_core_router.py` MUST continue to pass without modification.
> 2. **Strict TDD Order:** Write `tests/phase2/test_params.py` FIRST (Red Phase), then implement the C files (`bt_params.h`, `bt_params.c`, updated `bt_http.h`/`bt_http.c`), the Cython bridge (`_core.pxd`, `_core.pyx`), and the Python parameter descriptors (`buckthorn/params.py`).
> 3. **Zero-Allocation C Scanning:** Do NOT use Python `urllib.parse.parse_qs`, `http.cookies`, or Python `dict` comprehensions to extract route parameters. Query strings (`?a=1&b=2`), HTTP headers, and cookies MUST be scanned in C using `bt_slice_t` pointer slices (`const char *ptr`, `size_t len`) pointing directly into the request buffer.
> 4. **C-Level Coercion & Constraint Validation:** Integer conversion (`strtoll`), float conversion (`strtod`), numeric bounds (`ge`, `le`, `gt`, `lt`), and string length bounds (`min_length`, `max_length`) MUST be validated in C before calling the Python handler. If validation fails, immediately return HTTP `422 Unprocessable Entity` with a FastAPI-formatted JSON body without invoking the route function.
> 5. **Cython Freelist Mandate:** The `Request` extension type in `_core.pyx` MUST be decorated with `@cython.freelist(4096)` so creating a `Request` object reuses pre-allocated C struct slots in ~3 nanoseconds.
> 
> 

---

## 2. Phase 2 Target Directory Tree (New & Updated Files)

```text
buckthorn/
├── setup.py                          # [UPDATED] Adds buckthorn/c_src/bt_params.c
├── buckthorn/
│   ├── __init__.py                   # [UPDATED] Exports Request, Path, Query, Header, Cookie
│   ├── app.py                        # [UPDATED] Inspects function signatures & compiles ParamSpecs
│   ├── params.py                     # [NEW] FastAPI-style Path, Query, Header, Cookie descriptors
│   ├── testing.py                    # [UPDATED] Adds .get(), .post(), .delete() to RawTestClient
│   ├── _core.pxd                     # [UPDATED] Declares bt_params.h C structs & functions
│   ├── _core.pyx                     # [UPDATED] @cython.freelist(4096) Request + C Param Binder
│   └── c_src/
│       ├── bt_arena.h                # (Unchanged from Phase 1)
│       ├── bt_params.h               # [NEW] Zero-copy C slice scanners & numeric/string validators
│       ├── bt_params.c               # [NEW] C implementation of query/header/cookie/path extractors
│       ├── bt_http.h                 # [UPDATED] Adds parameterized route table to bt_router_t
│       └── bt_http.c                 # [UPDATED] Static rax fast-path + C segment matcher for {params}
└── tests/
    ├── phase1/
    │   └── test_core_router.py       # (Must remain 100% green)
    └── phase2/
        └── test_params.py            # [NEW] Phase 2 TDD test suite

```

---

## 3. Step 1: TDD Red Phase — Write the Phase 2 Test Suite First

Create `tests/phase2/test_params.py` before modifying any C or Cython code:

```python
# tests/phase2/test_params.py
import json
import time
import pytest
from buckthorn import Buckthorn, Request, Path, Query, Header, Cookie
from buckthorn.testing import RawTestClient


def test_typed_path_query_header_and_cookie_extraction():
    app = Buckthorn()

    @app.get("/users/{user_id}/items/{sku}")
    async def get_user_item(
        user_id: int = Path(ge=1),
        sku: str = Path(min_length=3, max_length=12),
        limit: int = Query(default=20, ge=1, le=100),
        discount: float = Query(default=0.0, ge=0.0, lt=1.0),
        x_api_key: str = Header(alias="X-Api-Key", min_length=5),
        session_id: str = Cookie(default="guest"),
    ):
        return f"{user_id}|{sku}|{limit}|{discount:.2f}|{x_api_key}|{session_id}".encode()

    client = RawTestClient(app)

    # 1. Full valid request with query params, custom header, and cookie
    res = client.get(
        "/users/42/items/BOOK-99?limit=50&discount=0.25",
        headers={
            "X-Api-Key": "secret-token",
            "Cookie": "theme=dark; session_id=xyz-987; lang=en",
        },
    )
    assert res.status_code == 200
    assert res.body == b"42|BOOK-99|50|0.25|secret-token|xyz-987"

    # 2. Default values applied in Cython when optional Query/Cookie params are omitted
    res_defaults = client.get(
        "/users/7/items/PEN-01",
        headers={"X-Api-Key": "valid-key"},
    )
    assert res_defaults.status_code == 200
    assert res_defaults.body == b"7|PEN-01|20|0.00|valid-key|guest"


def test_c_level_validation_rejects_invalid_types_and_bounds_with_422():
    app = Buckthorn()
    handler_called = False

    @app.get("/products/{product_id}")
    def get_product(
        product_id: int = Path(gt=0, le=10_000),
        q: str = Query(min_length=3, max_length=10),
        x_tenant: str = Header(alias="X-Tenant"),
    ):
        nonlocal handler_called
        handler_called = True
        return b"ok"

    client = RawTestClient(app)

    # Case 1: Non-integer path parameter -> 422 in C before handler runs
    res1 = client.get("/products/not-a-number?q=shoes", headers={"X-Tenant": "acme"})
    assert res1.status_code == 422
    assert not handler_called
    err1 = json.loads(res1.body)
    assert err1["detail"][0]["loc"] == ["path", "product_id"]

    # Case 2: Path integer violates `gt=0` -> 422 in C
    res2 = client.get("/products/0?q=shoes", headers={"X-Tenant": "acme"})
    assert res2.status_code == 422
    assert not handler_called

    # Case 3: Query string violates `min_length=3` -> 422 in C
    res3 = client.get("/products/100?q=ab", headers={"X-Tenant": "acme"})
    assert res3.status_code == 422
    assert not handler_called
    err3 = json.loads(res3.body)
    assert err3["detail"][0]["loc"] == ["query", "q"]

    # Case 4: Missing required Header -> 422 in C
    res4 = client.get("/products/100?q=shoes")
    assert res4.status_code == 422
    assert not handler_called
    err4 = json.loads(res4.body)
    assert err4["detail"][0]["loc"] == ["header", "X-Tenant"]


def test_zero_copy_request_object_lazy_access():
    app = Buckthorn()

    @app.post("/inspect/{region}")
    def inspect_request(req: Request, region: str):
        user_agent = req.get_header("user-agent") or "unknown"
        mode = req.get_query("mode") or "standard"
        body_len = len(req.body)
        return f"{req.method}:{req.path}:{region}:{user_agent}:{mode}:{body_len}".encode()

    client = RawTestClient(app)
    res = client.post(
        "/inspect/eu-west?mode=turbo",
        body=b'{"hello":"world"}',
        headers={"User-Agent": "BuckthornBench/1.0"},
    )
    assert res.status_code == 200
    assert res.body == b"POST:/inspect/eu-west:eu-west:BuckthornBench/1.0:turbo:17"


def test_phase2_parameter_extraction_speed_gate():
    app = Buckthorn()

    @app.get("/bench/{org_id}/users/{user_id}")
    def bench_route(
        org_id: int,
        user_id: int = Path(ge=1),
        page: int = Query(default=1, ge=1, le=1000),
        x_token: str = Header(alias="X-Token"),
    ):
        return b'{"status":"ok"}'

    client = RawTestClient(app)
    raw_req = (
        b"GET /bench/10/users/999?page=5 HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Token: super-secret\r\n\r\n"
    )

    for _ in range(500):
        client.send_raw(raw_req)

    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = client.send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert rps > 80_000, f"Expected >80,000 RPS with 4 typed C params, got {rps:,.0f} RPS"

```

---

## 4. Step 2: Green Phase — Pure C Slice Scanner & Validator (`bt_params.h` & `bt_params.c`)

### 4.1 `buckthorn/c_src/bt_params.h`

```c
/* buckthorn/c_src/bt_params.h */
#ifndef BT_PARAMS_H
#define BT_PARAMS_H

#include <stddef.h>
#include <stdint.h>
#include "../c_vendor/picohttpparser.h"

#define BT_MAX_PATH_SEGMENTS 16
#define BT_MAX_PATH_PARAMS   8

typedef struct {
    const char *ptr;
    size_t len;
    int found;
} bt_slice_t;

typedef enum {
    BT_ERR_NONE = 0,
    BT_ERR_MISSING = 1,
    BT_ERR_TYPE_INT = 2,
    BT_ERR_TYPE_FLOAT = 3,
    BT_ERR_NUMBER_GE = 4,
    BT_ERR_NUMBER_LE = 5,
    BT_ERR_NUMBER_GT = 6,
    BT_ERR_NUMBER_LT = 7,
    BT_ERR_STR_MIN_LEN = 8,
    BT_ERR_STR_MAX_LEN = 9
} bt_val_error_t;

/* Zero-copy scanners over raw HTTP buffers */
int bt_find_query_param(const char *query, size_t query_len,
                        const char *key, size_t key_len, bt_slice_t *out_val);

int bt_find_header(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val);

int bt_find_cookie(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val);

/* C-level type coercion and numeric/length constraint validators */
bt_val_error_t bt_parse_int64_checked(
    bt_slice_t slice,
    int has_ge, int64_t ge,
    int has_le, int64_t le,
    int has_gt, int64_t gt,
    int has_lt, int64_t lt,
    int64_t *out_val
);

bt_val_error_t bt_parse_double_checked(
    bt_slice_t slice,
    int has_ge, double ge,
    int has_le, double le,
    int has_gt, double gt,
    int has_lt, double lt,
    double *out_val
);

bt_val_error_t bt_validate_str_checked(
    bt_slice_t slice,
    int has_min_len, size_t min_len,
    int has_max_len, size_t max_len
);

#endif /* BT_PARAMS_H */

```

### 4.2 `buckthorn/c_src/bt_params.c`

```c
/* buckthorn/c_src/bt_params.c */
#include "bt_params.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

int bt_find_query_param(const char *query, size_t query_len,
                        const char *key, size_t key_len, bt_slice_t *out_val) {
    out_val->ptr = NULL;
    out_val->len = 0;
    out_val->found = 0;
    if (!query || query_len == 0) return 0;

    const char *cur = query;
    const char *end = query + query_len;

    while (cur < end) {
        const char *amp = (const char *)memchr(cur, '&', (size_t)(end - cur));
        const char *pair_end = amp ? amp : end;
        const char *eq = (const char *)memchr(cur, '=', (size_t)(pair_end - cur));

        if (eq) {
            size_t klen = (size_t)(eq - cur);
            if (klen == key_len && memcmp(cur, key, key_len) == 0) {
                out_val->ptr = eq + 1;
                out_val->len = (size_t)(pair_end - (eq + 1));
                out_val->found = 1;
                return 1;
            }
        }
        cur = pair_end + 1;
    }
    return 0;
}

int bt_find_header(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val) {
    out_val->ptr = NULL;
    out_val->len = 0;
    out_val->found = 0;

    for (size_t i = 0; i < num_headers; i++) {
        if (headers[i].name && headers[i].name_len == key_len) {
            if (strncasecmp(headers[i].name, key, key_len) == 0) {
                out_val->ptr = headers[i].value;
                out_val->len = headers[i].value_len;
                out_val->found = 1;
                return 1;
            }
        }
    }
    return 0;
}

int bt_find_cookie(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val) {
    bt_slice_t cookie_hdr;
    if (!bt_find_header(headers, num_headers, "Cookie", 6, &cookie_hdr)) {
        out_val->ptr = NULL;
        out_val->len = 0;
        out_val->found = 0;
        return 0;
    }

    const char *cur = cookie_hdr.ptr;
    const char *end = cookie_hdr.ptr + cookie_hdr.len;

    while (cur < end) {
        while (cur < end && (*cur == ' ' || *cur == '\t')) cur++;
        const char *semi = (const char *)memchr(cur, ';', (size_t)(end - cur));
        const char *pair_end = semi ? semi : end;
        const char *eq = (const char *)memchr(cur, '=', (size_t)(pair_end - cur));

        if (eq) {
            size_t klen = (size_t)(eq - cur);
            if (klen == key_len && memcmp(cur, key, key_len) == 0) {
                out_val->ptr = eq + 1;
                out_val->len = (size_t)(pair_end - (eq + 1));
                out_val->found = 1;
                return 1;
            }
        }
        cur = pair_end + 1;
    }

    out_val->ptr = NULL;
    out_val->len = 0;
    out_val->found = 0;
    return 0;
}

bt_val_error_t bt_parse_int64_checked(
    bt_slice_t slice,
    int has_ge, int64_t ge,
    int has_le, int64_t le,
    int has_gt, int64_t gt,
    int has_lt, int64_t lt,
    int64_t *out_val
) {
    if (!slice.found || slice.len == 0 || slice.len >= 63) {
        return BT_ERR_TYPE_INT;
    }
    char buf[64];
    memcpy(buf, slice.ptr, slice.len);
    buf[slice.len] = '\0';

    char *endptr = NULL;
    errno = 0;
    long long val = strtoll(buf, &endptr, 10);
    if (errno != 0 || endptr != buf + slice.len) {
        return BT_ERR_TYPE_INT;
    }

    int64_t v = (int64_t)val;
    if (has_ge && v < ge) return BT_ERR_NUMBER_GE;
    if (has_le && v > le) return BT_ERR_NUMBER_LE;
    if (has_gt && v <= gt) return BT_ERR_NUMBER_GT;
    if (has_lt && v >= lt) return BT_ERR_NUMBER_LT;

    *out_val = v;
    return BT_ERR_NONE;
}

bt_val_error_t bt_parse_double_checked(
    bt_slice_t slice,
    int has_ge, double ge,
    int has_le, double le,
    int has_gt, double gt,
    int has_lt, double lt,
    double *out_val
) {
    if (!slice.found || slice.len == 0 || slice.len >= 63) {
        return BT_ERR_TYPE_FLOAT;
    }
    char buf[64];
    memcpy(buf, slice.ptr, slice.len);
    buf[slice.len] = '\0';

    char *endptr = NULL;
    errno = 0;
    double v = strtod(buf, &endptr);
    if (errno != 0 || endptr != buf + slice.len) {
        return BT_ERR_TYPE_FLOAT;
    }

    if (has_ge && v < ge) return BT_ERR_NUMBER_GE;
    if (has_le && v > le) return BT_ERR_NUMBER_LE;
    if (has_gt && v <= gt) return BT_ERR_NUMBER_GT;
    if (has_lt && v >= lt) return BT_ERR_NUMBER_LT;

    *out_val = v;
    return BT_ERR_NONE;
}

bt_val_error_t bt_validate_str_checked(
    bt_slice_t slice,
    int has_min_len, size_t min_len,
    int has_max_len, size_t max_len
) {
    if (!slice.found) return BT_ERR_MISSING;
    if (has_min_len && slice.len < min_len) return BT_ERR_STR_MIN_LEN;
    if (has_max_len && slice.len > max_len) return BT_ERR_STR_MAX_LEN;
    return BT_ERR_NONE;
}

```

---

## 5. Step 3: Update `bt_http.h` & `bt_http.c` for Dynamic `{param}` Route Segments

Update `buckthorn/c_src/bt_http.h` and `buckthorn/c_src/bt_http.c` so the C router keeps the **0-branch `rax` fast path for static routes** while supporting parameterized URLs (`/users/{user_id}/items/{sku}`) with zero-copy segment slicing:

### 5.1 `buckthorn/c_src/bt_http.h`

```c
/* buckthorn/c_src/bt_http.h */
#ifndef BT_HTTP_H
#define BT_HTTP_H

#include <stddef.h>
#include <stdint.h>
#include "bt_arena.h"
#include "bt_params.h"
#include "../c_vendor/picohttpparser.h"
#include "../c_vendor/rax.h"

#define BT_MAX_HEADERS 64
#define BT_RESP_BUF_SIZE (64 * 1024)
#define BT_MAX_PARAM_ROUTES 256

typedef int (*bt_handler_invoke_fn)(
    void *handler_obj,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    struct phr_header *headers, size_t num_headers,
    bt_slice_t *path_params, size_t num_path_params,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    int *out_status_code
);

typedef struct {
    char seg_str[64];
    size_t seg_len;
    int is_param; /* 1 if {param}, 0 if literal segment */
} bt_route_seg_t;

typedef struct {
    char method[16];
    size_t method_len;
    size_t num_segments;
    bt_route_seg_t segments[BT_MAX_PATH_SEGMENTS];
    void *handler_obj;
} bt_param_route_t;

typedef struct {
    rax *tree;
    bt_handler_invoke_fn invoke_cb;
    size_t num_param_routes;
    bt_param_route_t param_routes[BT_MAX_PARAM_ROUTES];
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

int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                     const char *path, size_t path_len, void *handler_obj);

int bt_handle_http_bytes(
    bt_router_t *router,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
);

int bt_uv_server_run(bt_router_t *router, const char *host, int port);

#endif /* BT_HTTP_H */

```

### 5.2 `buckthorn/c_src/bt_http.c`

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

static size_t split_url_segments(const char *path, size_t path_len, bt_slice_t *out_segs) {
    size_t count = 0;
    const char *cur = path;
    const char *end = path + path_len;
    while (cur < end && *cur == '/') cur++;

    while (cur < end && count < BT_MAX_PATH_SEGMENTS) {
        const char *slash = (const char *)memchr(cur, '/', (size_t)(end - cur));
        const char *seg_end = slash ? slash : end;
        if (seg_end > cur) {
            out_segs[count].ptr = cur;
            out_segs[count].len = (size_t)(seg_end - cur);
            out_segs[count].found = 1;
            count++;
        }
        if (!slash) break;
        cur = slash + 1;
    }
    return count;
}

bt_router_t *bt_router_create(bt_handler_invoke_fn invoke_cb) {
    bt_router_t *r = (bt_router_t *)calloc(1, sizeof(bt_router_t));
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
    /* If route contains '{', register in parameterized C segment matcher */
    if (memchr(path, '{', path_len) != NULL) {
        if (router->num_param_routes >= BT_MAX_PARAM_ROUTES) return -1;
        bt_param_route_t *pr = &router->param_routes[router->num_param_routes++];
        memcpy(pr->method, method, method_len);
        pr->method[method_len] = '\0';
        pr->method_len = method_len;
        pr->handler_obj = handler_obj;

        bt_slice_t segs[BT_MAX_PATH_SEGMENTS];
        pr->num_segments = split_url_segments(path, path_len, segs);
        for (size_t i = 0; i < pr->num_segments; i++) {
            pr->segments[i].seg_len = segs[i].len;
            memcpy(pr->segments[i].seg_str, segs[i].ptr, segs[i].len);
            pr->segments[i].seg_str[segs[i].len] = '\0';
            pr->segments[i].is_param = (segs[i].len > 2 && segs[i].ptr[0] == '{' &&
                                        segs[i].ptr[segs[i].len - 1] == '}') ? 1 : 0;
        }
        return 0;
    }

    /* Static route fast path -> insert directly into Redis rax radix tree */
    unsigned char key[512];
    if (method_len + 1 + path_len >= sizeof(key)) return -1;
    memcpy(key, method, method_len);
    key[method_len] = ':';
    memcpy(key + method_len + 1, path, path_len);
    size_t key_len = method_len + 1 + path_len;

    raxInsert(router->tree, key, key_len, handler_obj, NULL);
    return 0;
}

static void *bt_match_param_route(
    bt_router_t *router,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    bt_slice_t *out_params, size_t *out_num_params
) {
    *out_num_params = 0;
    if (router->num_param_routes == 0) return raxNotFound;

    bt_slice_t req_segs[BT_MAX_PATH_SEGMENTS];
    size_t num_req_segs = split_url_segments(path, path_len, req_segs);

    for (size_t r = 0; r < router->num_param_routes; r++) {
        bt_param_route_t *pr = &router->param_routes[r];
        if (pr->num_segments != num_req_segs || pr->method_len != method_len) continue;
        if (memcmp(pr->method, method, method_len) != 0) continue;

        size_t p_idx = 0;
        int matched = 1;
        for (size_t s = 0; s < num_req_segs; s++) {
            if (pr->segments[s].is_param) {
                if (p_idx < BT_MAX_PATH_PARAMS) {
                    out_params[p_idx++] = req_segs[s];
                }
            } else {
                if (pr->segments[s].seg_len != req_segs[s].len ||
                    memcmp(pr->segments[s].seg_str, req_segs[s].ptr, req_segs[s].len) != 0) {
                    matched = 0;
                    break;
                }
            }
        }
        if (matched) {
            *out_num_params = p_idx;
            return pr->handler_obj;
        }
    }
    return raxNotFound;
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

    const char *query = NULL;
    size_t query_len = 0;
    size_t clean_path_len = path_len;
    const char *qmark = (const char *)memchr(path, '?', path_len);
    if (qmark) {
        clean_path_len = (size_t)(qmark - path);
        query = qmark + 1;
        query_len = path_len - clean_path_len - 1;
    }

    size_t key_len = method_len + 1 + clean_path_len;
    unsigned char *lookup_key = (unsigned char *)bt_arena_alloc(arena, key_len);
    if (!lookup_key) return -2;

    memcpy(lookup_key, method, method_len);
    lookup_key[method_len] = ':';
    memcpy(lookup_key + method_len + 1, path, clean_path_len);

    bt_slice_t path_params[BT_MAX_PATH_PARAMS];
    size_t num_path_params = 0;

    /* 1. Try O(K) static route lookup in Redis rax */
    void *handler_obj = raxFind(router->tree, lookup_key, key_len);

    /* 2. Fallback to zero-copy C segment matcher for parameterized routes */
    if (handler_obj == raxNotFound) {
        handler_obj = bt_match_param_route(
            router, method, method_len, path, clean_path_len, path_params, &num_path_params
        );
    }

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

    char *handler_out_buf = (char *)bt_arena_alloc(arena, 32 * 1024);
    size_t handler_out_len = 0;
    int status_code = 200;

    int rc = router->invoke_cb(
        handler_obj,
        method, method_len,
        path, clean_path_len,
        query, query_len,
        headers, num_headers,
        path_params, num_path_params,
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

    const char *status_text = (status_code == 200) ? "OK" :
                              (status_code == 422) ? "Unprocessable Entity" : "OK";

    int hdr_len = snprintf(
        resp_wire_buf, resp_wire_cap,
        "HTTP/1.1 %d %s\r\n"
        "Content-Length: %zu\r\n"
        "Connection: keep-alive\r\n\r\n",
        status_code, status_text, handler_out_len
    );

    memcpy(resp_wire_buf + hdr_len, handler_out_buf, handler_out_len);
    out_result->status_code = status_code;
    out_result->http_wire_buf = resp_wire_buf;
    out_result->http_wire_len = (size_t)hdr_len + handler_out_len;
    out_result->body_ptr = resp_wire_buf + hdr_len;
    out_result->body_len = handler_out_len;

    bt_arena_reset(arena);
    return 0;
}

/* =========================================================================
 * Native libuv TCP Server Glue (Unchanged from Phase 1)
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
from libc.stdint cimport uint8_t, int64_t

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

cdef extern from "c_src/bt_params.h" nogil:
    ctypedef struct bt_slice_t:
        const char *ptr
        size_t len
        int found

    ctypedef enum bt_val_error_t:
        BT_ERR_NONE
        BT_ERR_MISSING
        BT_ERR_TYPE_INT
        BT_ERR_TYPE_FLOAT
        BT_ERR_NUMBER_GE
        BT_ERR_NUMBER_LE
        BT_ERR_NUMBER_GT
        BT_ERR_NUMBER_LT
        BT_ERR_STR_MIN_LEN
        BT_ERR_STR_MAX_LEN

    int bt_find_query_param(const char *query, size_t query_len,
                            const char *key, size_t key_len, bt_slice_t *out_val)
    int bt_find_header(phr_header *headers, size_t num_headers,
                       const char *key, size_t key_len, bt_slice_t *out_val)
    int bt_find_cookie(phr_header *headers, size_t num_headers,
                       const char *key, size_t key_len, bt_slice_t *out_val)

    bt_val_error_t bt_parse_int64_checked(
        bt_slice_t slice,
        int has_ge, int64_t ge,
        int has_le, int64_t le,
        int has_gt, int64_t gt,
        int has_lt, int64_t lt,
        int64_t *out_val
    )
    bt_val_error_t bt_parse_double_checked(
        bt_slice_t slice,
        int has_ge, double ge,
        int has_le, double le,
        int has_gt, double gt,
        int has_lt, double lt,
        double *out_val
    )
    bt_val_error_t bt_validate_str_checked(
        bt_slice_t slice,
        int has_min_len, size_t min_len,
        int has_max_len, size_t max_len
    )

cdef extern from "c_src/bt_http.h" nogil:
    ctypedef int (*bt_handler_invoke_fn)(
        void *handler_obj,
        const char *method, size_t method_len,
        const char *path, size_t path_len,
        const char *query, size_t query_len,
        phr_header *headers, size_t num_headers,
        bt_slice_t *path_params, size_t num_path_params,
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
cimport cython
from libc.string cimport memcpy
from libc.stdio cimport snprintf
from libc.stdint cimport int64_t
from cpython.bytes cimport PyBytes_AsStringAndSize, PyBytes_FromStringAndSize
from cpython.unicode cimport PyUnicode_DecodeUTF8
import inspect

from buckthorn._core cimport (
    phr_header, bt_arena_t, bt_arena_init, bt_arena_destroy,
    bt_slice_t, bt_val_error_t, BT_ERR_NONE,
    bt_find_query_param, bt_find_header, bt_find_cookie,
    bt_parse_int64_checked, bt_parse_double_checked, bt_validate_str_checked,
    bt_router_t, bt_raw_response_t,
    bt_router_create, bt_router_destroy, bt_router_insert,
    bt_handle_http_bytes, bt_uv_server_run
)

# Source Enum Constants
DEF SRC_PATH    = 1
DEF SRC_QUERY   = 2
DEF SRC_HEADER  = 3
DEF SRC_COOKIE  = 4
DEF SRC_REQUEST = 5

# Type Enum Constants
DEF TYPE_INT     = 1
DEF TYPE_FLOAT   = 2
DEF TYPE_STR     = 3
DEF TYPE_REQUEST = 4


@cython.freelist(4096)
cdef class Request:
    """Zero-copy Cython wrapper around raw HTTP buffer pointers."""
    cdef const char *_method
    cdef size_t _method_len
    cdef const char *_path
    cdef size_t _path_len
    cdef const char *_query
    cdef size_t _query_len
    cdef phr_header *_headers
    cdef size_t _num_headers
    cdef const char *_body
    cdef size_t _body_len

    cdef inline void bind_c(
        self,
        const char *method, size_t method_len,
        const char *path, size_t path_len,
        const char *query, size_t query_len,
        phr_header *headers, size_t num_headers,
        const char *body, size_t body_len
    ) noexcept nogil:
        self._method = method
        self._method_len = method_len
        self._path = path
        self._path_len = path_len
        self._query = query
        self._query_len = query_len
        self._headers = headers
        self._num_headers = num_headers
        self._body = body
        self._body_len = body_len

    @property
    def method(self) -> str:
        return PyUnicode_DecodeUTF8(self._method, <Py_ssize_t>self._method_len, "strict")

    @property
    def path(self) -> str:
        return PyUnicode_DecodeUTF8(self._path, <Py_ssize_t>self._path_len, "strict")

    @property
    def body(self) -> bytes:
        if self._body == NULL or self._body_len == 0:
            return b""
        return PyBytes_FromStringAndSize(self._body, <Py_ssize_t>self._body_len)

    cpdef object get_header(self, str name):
        cdef bytes b_name = name.encode("ascii")
        cdef char *k_ptr = NULL
        cdef Py_ssize_t k_len = 0
        cdef bt_slice_t val
        PyBytes_AsStringAndSize(b_name, &k_ptr, &k_len)
        if bt_find_header(self._headers, self._num_headers, k_ptr, <size_t>k_len, &val):
            return PyUnicode_DecodeUTF8(val.ptr, <Py_ssize_t>val.len, "replace")
        return None

    cpdef object get_query(self, str name):
        cdef bytes b_name = name.encode("utf-8")
        cdef char *k_ptr = NULL
        cdef Py_ssize_t k_len = 0
        cdef bt_slice_t val
        PyBytes_AsStringAndSize(b_name, &k_ptr, &k_len)
        if bt_find_query_param(self._query, self._query_len, k_ptr, <size_t>k_len, &val):
            return PyUnicode_DecodeUTF8(val.ptr, <Py_ssize_t>val.len, "replace")
        return None


cdef class CompiledParamSpec:
    """Pre-compiled C parameter extraction and validation descriptor."""
    cdef public str arg_name
    cdef public bytes lookup_key
    cdef const char *_key_ptr
    cdef size_t _key_len
    cdef public int source       # SRC_PATH, SRC_QUERY, SRC_HEADER, SRC_COOKIE, SRC_REQUEST
    cdef public int param_type   # TYPE_INT, TYPE_FLOAT, TYPE_STR, TYPE_REQUEST
    cdef public int path_index
    cdef public bint required
    cdef public object default_val

    cdef public bint has_ge
    cdef public int64_t int_ge
    cdef public double float_ge

    cdef public bint has_le
    cdef public int64_t int_le
    cdef public double float_le

    cdef public bint has_gt
    cdef public int64_t int_gt
    cdef public double float_gt

    cdef public bint has_lt
    cdef public int64_t int_lt
    cdef public double float_lt

    cdef public bint has_min_len
    cdef public size_t min_len
    cdef public bint has_max_len
    cdef public size_t max_len

    def __cinit__(
        self,
        str arg_name,
        str lookup_name,
        int source,
        int param_type,
        int path_index=-1,
        bint required=True,
        object default_val=None,
        object ge=None,
        object le=None,
        object gt=None,
        object lt=None,
        object min_length=None,
        object max_length=None,
    ):
        self.arg_name = arg_name
        self.lookup_key = lookup_name.encode("utf-8")
        cdef char *ptr = NULL
        cdef Py_ssize_t length = 0
        PyBytes_AsStringAndSize(self.lookup_key, &ptr, &length)
        self._key_ptr = <const char *>ptr
        self._key_len = <size_t>length

        self.source = source
        self.param_type = param_type
        self.path_index = path_index
        self.required = required
        self.default_val = default_val

        self.has_ge = ge is not None
        if self.has_ge:
            self.int_ge = <int64_t>int(ge)
            self.float_ge = <double>float(ge)

        self.has_le = le is not None
        if self.has_le:
            self.int_le = <int64_t>int(le)
            self.float_le = <double>float(le)

        self.has_gt = gt is not None
        if self.has_gt:
            self.int_gt = <int64_t>int(gt)
            self.float_gt = <double>float(gt)

        self.has_lt = lt is not None
        if self.has_lt:
            self.int_lt = <int64_t>int(lt)
            self.float_lt = <double>float(lt)

        self.has_min_len = min_length is not None
        if self.has_min_len:
            self.min_len = <size_t>int(min_length)

        self.has_max_len = max_length is not None
        if self.has_max_len:
            self.max_len = <size_t>int(max_length)


cdef class RouteEntry:
    cdef public object fn
    cdef public bint is_coroutine
    cdef public tuple param_specs
    cdef public int num_params

    def __cinit__(self, object fn, list param_specs):
        self.fn = fn
        self.is_coroutine = inspect.iscoroutinefunction(fn)
        self.param_specs = tuple(param_specs)
        self.num_params = len(param_specs)


cdef inline void _write_422_error(
    int source,
    const char *key_ptr,
    char *out_body_buf,
    size_t *out_body_len,
    int *out_status_code
) noexcept nogil:
    cdef const char *loc = "query"
    if source == SRC_PATH:
        loc = "path"
    elif source == SRC_HEADER:
        loc = "header"
    elif source == SRC_COOKIE:
        loc = "cookie"

    cdef int written = snprintf(
        out_body_buf, 1024,
        '{"detail":[{"loc":["%s","%s"],"msg":"validation_error","type":"value_error"}]}',
        loc, key_ptr
    )
    out_body_len[0] = <size_t>written
    out_status_code[0] = 422


cdef int _cython_invoke_handler(
    void *handler_obj,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    phr_header *headers, size_t num_headers,
    bt_slice_t *path_params, size_t num_path_params,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    int *out_status_code
) noexcept with gil:
    cdef RouteEntry entry = <RouteEntry>handler_obj
    cdef object result
    cdef char *raw_bytes_ptr = NULL
    cdef Py_ssize_t raw_bytes_len = 0
    cdef int i
    cdef CompiledParamSpec spec
    cdef bt_slice_t raw_slice
    cdef bt_val_error_t val_err
    cdef int64_t parsed_int = 0
    cdef double parsed_float = 0.0
    cdef Request req_obj
    cdef dict kwargs

    try:
        if entry.num_params == 0:
            if entry.is_coroutine:
                coro = entry.fn()
                try:
                    coro.send(None)
                except StopIteration as stop:
                    result = stop.value
                finally:
                    coro.close()
            else:
                result = entry.fn()
        else:
            kwargs = {}
            for i in range(entry.num_params):
                spec = <CompiledParamSpec>(entry.param_specs[i])

                if spec.source == SRC_REQUEST:
                    req_obj = Request.__new__(Request)
                    req_obj.bind_c(
                        method, method_len, path, path_len,
                        query, query_len, headers, num_headers, body, body_len
                    )
                    kwargs[spec.arg_name] = req_obj
                    continue

                raw_slice.found = 0
                raw_slice.ptr = NULL
                raw_slice.len = 0

                if spec.source == SRC_PATH:
                    if spec.path_index >= 0 and <size_t>spec.path_index < num_path_params:
                        raw_slice = path_params[spec.path_index]
                elif spec.source == SRC_QUERY:
                    bt_find_query_param(query, query_len, spec._key_ptr, spec._key_len, &raw_slice)
                elif spec.source == SRC_HEADER:
                    bt_find_header(headers, num_headers, spec._key_ptr, spec._key_len, &raw_slice)
                elif spec.source == SRC_COOKIE:
                    bt_find_cookie(headers, num_headers, spec._key_ptr, spec._key_len, &raw_slice)

                if raw_slice.found == 0:
                    if spec.required:
                        _write_422_error(spec.source, spec._key_ptr, out_body_buf, out_body_len, out_status_code)
                        return 0
                    else:
                        kwargs[spec.arg_name] = spec.default_val
                        continue

                # C-level Type Coercion & Constraint Check
                if spec.param_type == TYPE_INT:
                    val_err = bt_parse_int64_checked(
                        raw_slice,
                        spec.has_ge, spec.int_ge,
                        spec.has_le, spec.int_le,
                        spec.has_gt, spec.int_gt,
                        spec.has_lt, spec.int_lt,
                        &parsed_int
                    )
                    if val_err != BT_ERR_NONE:
                        _write_422_error(spec.source, spec._key_ptr, out_body_buf, out_body_len, out_status_code)
                        return 0
                    kwargs[spec.arg_name] = parsed_int

                elif spec.param_type == TYPE_FLOAT:
                    val_err = bt_parse_double_checked(
                        raw_slice,
                        spec.has_ge, spec.float_ge,
                        spec.has_le, spec.float_le,
                        spec.has_gt, spec.float_gt,
                        spec.has_lt, spec.float_lt,
                        &parsed_float
                    )
                    if val_err != BT_ERR_NONE:
                        _write_422_error(spec.source, spec._key_ptr, out_body_buf, out_body_len, out_status_code)
                        return 0
                    kwargs[spec.arg_name] = parsed_float

                else:  # TYPE_STR
                    val_err = bt_validate_str_checked(
                        raw_slice,
                        spec.has_min_len, spec.min_len,
                        spec.has_max_len, spec.max_len
                    )
                    if val_err != BT_ERR_NONE:
                        _write_422_error(spec.source, spec._key_ptr, out_body_buf, out_body_len, out_status_code)
                        return 0
                    kwargs[spec.arg_name] = PyUnicode_DecodeUTF8(
                        raw_slice.ptr, <Py_ssize_t>raw_slice.len, "strict"
                    )

            if entry.is_coroutine:
                coro = entry.fn(**kwargs)
                try:
                    coro.send(None)
                except StopIteration as stop:
                    result = stop.value
                finally:
                    coro.close()
            else:
                result = entry.fn(**kwargs)

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
    cdef list _handler_refs

    def __cinit__(self):
        self._handler_refs = []
        self._router = bt_router_create(_cython_invoke_handler)
        if self._router == NULL:
            raise MemoryError("Failed to allocate C router")

    def __dealloc__(self):
        if self._router != NULL:
            bt_router_destroy(self._router)
            self._router = NULL

    cpdef void add_route(self, str method, str path, object fn, list param_specs=None):
        cdef bytes b_method = method.upper().encode("ascii")
        cdef bytes b_path = path.encode("utf-8")
        cdef RouteEntry entry = RouteEntry(fn, param_specs or [])
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

## 7. Step 5: Python Descriptors & Signature Compiler (`params.py`, `app.py`, `testing.py`, `__init__.py`)

### 7.1 `buckthorn/params.py`

```python
# buckthorn/params.py
from typing import Any, Optional

_UNDEFINED = Ellipsis


class ParamInfo:
    source_id: int = 2  # Default SRC_QUERY

    def __init__(
        self,
        default: Any = _UNDEFINED,
        *,
        alias: Optional[str] = None,
        ge: Optional[float] = None,
        le: Optional[float] = None,
        gt: Optional[float] = None,
        lt: Optional[float] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
    ):
        self.default = default
        self.required = default is _UNDEFINED
        self.alias = alias
        self.ge = ge
        self.le = le
        self.gt = gt
        self.lt = lt
        self.min_length = min_length
        self.max_length = max_length


class PathInfo(ParamInfo):
    source_id = 1  # SRC_PATH

    def __init__(self, default: Any = _UNDEFINED, **kwargs: Any):
        super().__init__(default=default, **kwargs)
        self.required = True


class QueryInfo(ParamInfo):
    source_id = 2  # SRC_QUERY


class HeaderInfo(ParamInfo):
    source_id = 3  # SRC_HEADER


class CookieInfo(ParamInfo):
    source_id = 4  # SRC_COOKIE


def Path(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return PathInfo(default=default, **kwargs)


def Query(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return QueryInfo(default=default, **kwargs)


def Header(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return HeaderInfo(default=default, **kwargs)


def Cookie(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return CookieInfo(default=default, **kwargs)

```

### 7.2 `buckthorn/app.py` (Compiles Python Type Hints into C `CompiledParamSpec` at Startup)

```python
# buckthorn/app.py
import inspect
from typing import Callable, get_type_hints
from buckthorn._core import CoreRouter, CompiledParamSpec, Request
from buckthorn.params import ParamInfo, PathInfo, QueryInfo, _UNDEFINED

_TYPE_MAP = {
    int: 1,      # TYPE_INT
    float: 2,    # TYPE_FLOAT
    str: 3,      # TYPE_STR
    Request: 4,  # TYPE_REQUEST
}


def _extract_path_param_names(path: str) -> list[str]:
    names: list[str] = []
    for seg in path.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            raw_name = seg[1:-1].split(":")[0]
            names.append(raw_name)
    return names


def _compile_route_params(path: str, endpoint: Callable) -> list[CompiledParamSpec]:
    sig = inspect.signature(endpoint)
    if not sig.parameters:
        return []

    hints = get_type_hints(endpoint)
    path_param_names = _extract_path_param_names(path)
    specs: list[CompiledParamSpec] = []

    for arg_name, param in sig.parameters.items():
        annotation = hints.get(arg_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = str

        if annotation is Request:
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=5,      # SRC_REQUEST
                    param_type=4,  # TYPE_REQUEST
                    required=True,
                )
            )
            continue

        param_type = _TYPE_MAP.get(annotation, 3)
        default_obj = param.default

        if isinstance(default_obj, ParamInfo):
            info = default_obj
        elif arg_name in path_param_names:
            info = PathInfo()
        else:
            info = QueryInfo(
                default=_UNDEFINED if default_obj is inspect.Parameter.empty else default_obj
            )

        path_idx = path_param_names.index(arg_name) if arg_name in path_param_names else -1
        source = 1 if path_idx >= 0 else info.source_id
        lookup_name = info.alias or arg_name

        specs.append(
            CompiledParamSpec(
                arg_name=arg_name,
                lookup_name=lookup_name,
                source=source,
                param_type=param_type,
                path_index=path_idx,
                required=True if source == 1 else info.required,
                default_val=None if info.default is _UNDEFINED else info.default,
                ge=info.ge,
                le=info.le,
                gt=info.gt,
                lt=info.lt,
                min_length=info.min_length,
                max_length=info.max_length,
            )
        )

    return specs


class Buckthorn:
    def __init__(self, title: str = "Buckthorn", version: str = "0.2.0"):
        self.title = title
        self.version = version
        self._core = CoreRouter()

    def add_api_route(self, path: str, endpoint: Callable, methods: list[str]) -> None:
        param_specs = _compile_route_params(path, endpoint)
        for method in methods:
            self._core.add_route(method.upper(), path, endpoint, param_specs)

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

### 7.3 `buckthorn/testing.py`

```python
# buckthorn/testing.py
from typing import Optional
from buckthorn.app import Buckthorn
from buckthorn._core import CythonRawTestClient, RawResponse


class RawTestClient:
    def __init__(self, app: Buckthorn):
        self._client = CythonRawTestClient(app._core)

    def send_raw(self, raw_http: bytes) -> RawResponse:
        return self._client.send_raw(raw_http)

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[dict[str, str]] = None,
        body: bytes = b"",
    ) -> RawResponse:
        hdr_lines = [f"{method.upper()} {path} HTTP/1.1", "Host: localhost"]
        if headers:
            for k, v in headers.items():
                hdr_lines.append(f"{k}: {v}")
        if body:
            hdr_lines.append(f"Content-Length: {len(body)}")
        raw_head = ("\r\n".join(hdr_lines) + "\r\n\r\n").encode("utf-8")
        return self._client.send_raw(raw_head + body)

    def get(self, path: str, headers: Optional[dict[str, str]] = None) -> RawResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self, path: str, body: bytes = b"", headers: Optional[dict[str, str]] = None
    ) -> RawResponse:
        return self.request("POST", path, headers=headers, body=body)

    def delete(self, path: str, headers: Optional[dict[str, str]] = None) -> RawResponse:
        return self.request("DELETE", path, headers=headers)

    @property
    def arena_offset(self) -> int:
        return self._client.arena_offset

```

### 7.4 `buckthorn/__init__.py`

```python
# buckthorn/__init__.py
from buckthorn.app import Buckthorn
from buckthorn._core import Request
from buckthorn.params import Path, Query, Header, Cookie

__all__ = ["Buckthorn", "Request", "Path", "Query", "Header", "Cookie"]

```

---

## 8. Step 6: Update `setup.py` to Compile `bt_params.c`

Add `"buckthorn/c_src/bt_params.c"` to the `sources` list in `setup.py`:

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
            "buckthorn/c_src/bt_params.c",
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
    version="0.2.0",
    packages=find_packages(),
    ext_modules=cythonize(
        ext_modules,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
        annotate=True,
    ),
)

```

---

## 9. Step 7: Execution & Verification Checklist for Gemini Flash

Run these two commands to recompile the Cython extension and run both Phase 1 and Phase 2 TDD test suites:

```bash
# 1. Recompile C + Cython extension with bt_params.c included
pip install -e ".[dev]" --no-build-isolation

# 2. Run both Phase 1 and Phase 2 TDD suites
pytest tests/phase1/test_core_router.py tests/phase2/test_params.py -v -s

```