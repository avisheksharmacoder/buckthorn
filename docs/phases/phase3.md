Below is the **Phase 3 Master Technical Specification** engineered for **Gemini Flash**.

It builds directly on top of Phases 1 and 2 while keeping all previous tests 100% green. Copy and paste this entire specification into Gemini Flash to implement **`buckthorn.Schema` Data Models**, **`Field(...)` Validation Constraints**, **Single-Pass `yyjson` C Deserialization**, and **Direct-to-Buffer `yyjson` Response Serialization (`response_model` filtering)**.

---

# PHASE 3 SPECIFICATION: `buckthorn.Schema` Data Models & Single-Pass `yyjson` C Engine

## 1. System Prompt & Strict Guardrails for Gemini Flash

> **Instructions for the AI Implementer (Gemini Flash):**
> 1. **Backward Compatibility Gate:** All tests in `tests/phase1/test_core_router.py` and `tests/phase2/test_params.py` MUST continue to pass without modification.
> 2. **Strict TDD Order:** Update `scripts/fetch_vendor.sh` to fetch `yyjson`, write `tests/phase3/test_schema_serde.py` FIRST (Red Phase), and then implement the C, Cython, and Python files (Green Phase).
> 3. **No Python `json.loads()` or `json.dumps()` on the Server Hot Path:** Incoming JSON request bodies MUST be parsed in C via `yyjson_read_opts()` backed by a pre-allocated C memory pool (`yyjson_alc_pool_init`). Outgoing `Schema`, `dict`, and `list` responses MUST be serialized directly into the C response buffer `out_body_buf` via `yyjson_mut_write_opts()` without allocating intermediate Python JSON strings.
> 4. **Single-Pass C Validation:** Field presence (`required`), type checking (`int`, `float`, `bool`, `str`, `list`), numeric bounds (`ge`, `le`, `gt`, `lt`), and string length bounds (`min_length`, `max_length`) MUST be validated directly on the `yyjson_val*` C pointers before populating the `Schema` instance.
> 5. **C-Level `response_model` Filtering:** When `@app.post(..., response_model=PublicSchema)` is specified, the Cython/C serializer must iterate ONLY over the `CompiledFieldSpec` descriptors of `PublicSchema`, stripping sensitive/unlisted fields during serialization without allocating an intermediate filtered Python `dict`.
> 
> 

---

## 2. Phase 3 Target Directory Tree (New & Updated Files)

```text
buckthorn/
├── setup.py                          # [UPDATED] Adds buckthorn/c_vendor/yyjson.c & buckthorn/c_src/bt_json.c
├── scripts/
│   └── fetch_vendor.sh               # [UPDATED] Downloads yyjson.c and yyjson.h
├── buckthorn/
│   ├── __init__.py                   # [UPDATED] Exports Schema, Field, status
│   ├── app.py                        # [UPDATED] Adds response_model, status_code, and Schema body detection
│   ├── params.py                     # (Unchanged from Phase 2)
│   ├── schema.py                     # [NEW] FastAPI/Pydantic-style Schema base class & Field() descriptor
│   ├── status.py                     # [NEW] FastAPI-style HTTP status constants
│   ├── testing.py                    # [UPDATED] Adds json=... argument to RawTestClient.post/put/request
│   ├── _core.pxd                     # [UPDATED] Declares bt_json.h C structs & yyjson helpers
│   ├── _core.pyx                     # [UPDATED] Adds CompiledSchemaSpec, single-pass yyjson reader & writer
│   ├── c_vendor/
│   │   ├── picohttpparser.c          # (From Phase 1)
│   │   ├── picohttpparser.h          # (From Phase 1)
│   │   ├── rax.c                     # (From Phase 1)
│   │   ├── rax.h                     # (From Phase 1)
│   │   ├── rax_malloc.h              # (From Phase 1)
│   │   ├── yyjson.c                  # [NEW] Fetched by scripts/fetch_vendor.sh
│   │   └── yyjson.h                  # [NEW] Fetched by scripts/fetch_vendor.sh
│   └── c_src/
│       ├── bt_arena.h                # (From Phase 1)
│       ├── bt_params.h               # (From Phase 2)
│       ├── bt_params.c               # (From Phase 2)
│       ├── bt_json.h                 # [NEW] Zero-malloc yyjson C pool parser, field validator & writer
│       ├── bt_json.c                 # [NEW] C implementation of single-pass yyjson validation & serialization
│       ├── bt_http.h                 # (From Phase 2)
│       └── bt_http.c                 # [UPDATED] Adds 201 Created status text
└── tests/
    ├── phase1/
    │   └── test_core_router.py       # (Must remain 100% green)
    ├── phase2/
    │   └── test_params.py            # (Must remain 100% green)
    └── phase3/
        └── test_schema_serde.py      # [NEW] Phase 3 TDD test suite

```

---

## 3. Step 1: Update `scripts/fetch_vendor.sh` to Fetch `yyjson`

Update `scripts/fetch_vendor.sh` and run it so `yyjson.c` and `yyjson.h` are downloaded into `buckthorn/c_vendor/`:

```bash
#!/usr/bin/env bash
set -euo pipefail

VENDOR_DIR="buckthorn/c_vendor"
mkdir -p "$VENDOR_DIR"

echo "[1/3] Fetching H2O picohttpparser (SIMD HTTP/1.1 parser)..."
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.c -o "$VENDOR_DIR/picohttpparser.c"
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.h -o "$VENDOR_DIR/picohttpparser.h"

echo "[2/3] Fetching Redis rax (ANSI C Radix Tree)..."
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.c -o "$VENDOR_DIR/rax.c"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.h -o "$VENDOR_DIR/rax.h"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax_malloc.h -o "$VENDOR_DIR/rax_malloc.h"

echo "[3/3] Fetching ibireme/yyjson (SIMD C99 JSON Engine)..."
curl -sSL https://raw.githubusercontent.com/ibireme/yyjson/master/src/yyjson.c -o "$VENDOR_DIR/yyjson.c"
curl -sSL https://raw.githubusercontent.com/ibireme/yyjson/master/src/yyjson.h -o "$VENDOR_DIR/yyjson.h"

echo "Vendor C files ready in $VENDOR_DIR"

```

---

## 4. Step 2: TDD Red Phase — Write the Phase 3 Test Suite First

Create `tests/phase3/test_schema_serde.py` before writing the Phase 3 implementation code:

```python
# tests/phase3/test_schema_serde.py
import time
import pytest
from buckthorn import Buckthorn, Schema, Field, Path, Query, status
from buckthorn.testing import RawTestClient


class CreateProduct(Schema):
    sku: str = Field(min_length=3, max_length=16)
    price: float = Field(gt=0.0, le=100_000.0)
    qty: int = Field(default=1, ge=1, le=1000)
    in_stock: bool = True
    tags: list[str] = []


class ProductPublic(Schema):
    sku: str
    price: float
    qty: int
    tags: list[str]


def test_single_pass_json_validation_and_response_model_filtering():
    app = Buckthorn()

    @app.post("/products", response_model=ProductPublic, status_code=status.HTTP_201_CREATED)
    async def create_product(prod: CreateProduct):
        # Handler returns CreateProduct (which includes `in_stock`);
        # C response_model serializer must strip `in_stock` automatically
        return prod

    client = RawTestClient(app)

    # 1. Full valid payload
    res = client.post(
        "/products",
        json={
            "sku": "BK-900",
            "price": 49.95,
            "qty": 5,
            "in_stock": False,
            "tags": ["compiled", "cython"],
        },
    )
    assert res.status_code == 201
    assert res.json() == {
        "sku": "BK-900",
        "price": 49.95,
        "qty": 5,
        "tags": ["compiled", "cython"],
    }

    # 2. Omitted optional fields use Schema defaults without mutating shared list default
    res_defaults = client.post("/products", json={"sku": "BK-101", "price": 12.50})
    assert res_defaults.status_code == 201
    assert res_defaults.json() == {
        "sku": "BK-101",
        "price": 12.5,
        "qty": 1,
        "tags": [],
    }


def test_c_yyjson_schema_validation_errors_return_422():
    app = Buckthorn()
    handler_invoked = False

    @app.post("/products")
    def create_product(prod: CreateProduct):
        nonlocal handler_invoked
        handler_invoked = True
        return prod

    client = RawTestClient(app)

    # 1. Malformed JSON syntax -> 422 in C
    res_syntax = client.post("/products", body=b'{"sku": "BK-100", broken_json')
    assert res_syntax.status_code == 422
    assert not handler_invoked

    # 2. Missing required field (`price`) -> 422 in C
    res_missing = client.post("/products", json={"sku": "BK-100"})
    assert res_missing.status_code == 422
    assert not handler_invoked
    assert res_missing.json()["detail"][0]["loc"] == ["body", "price"]

    # 3. String constraint violation (`sku` < 3 chars) -> 422 in C
    res_short = client.post("/products", json={"sku": "AB", "price": 10.0})
    assert res_short.status_code == 422
    assert not handler_invoked
    assert res_short.json()["detail"][0]["loc"] == ["body", "sku"]

    # 4. Numeric constraint violation (`price` <= 0.0) -> 422 in C
    res_neg = client.post("/products", json={"sku": "BK-100", "price": -5.0})
    assert res_neg.status_code == 422
    assert not handler_invoked
    assert res_neg.json()["detail"][0]["loc"] == ["body", "price"]

    # 5. Wrong primitive type (`qty` is string instead of int) -> 422 in C
    res_type = client.post("/products", json={"sku": "BK-100", "price": 10.0, "qty": "five"})
    assert res_type.status_code == 422
    assert not handler_invoked
    assert res_type.json()["detail"][0]["loc"] == ["body", "qty"]


def test_mixed_path_query_and_schema_body_with_dict_response():
    app = Buckthorn()

    class OrderPatch(Schema):
        quantity: int = Field(ge=1, le=50)
        expedited: bool = False

    @app.post("/stores/{store_id}/orders")
    def create_store_order(
        store_id: int = Path(ge=1),
        currency: str = Query(default="USD", min_length=3, max_length=3),
        payload: OrderPatch = None,
    ):
        return {
            "store_id": store_id,
            "currency": currency,
            "quantity": payload.quantity,
            "expedited": payload.expedited,
        }

    client = RawTestClient(app)
    res = client.post(
        "/stores/12/orders?currency=EUR",
        json={"quantity": 8, "expedited": True},
    )
    assert res.status_code == 200
    assert res.json() == {
        "store_id": 12,
        "currency": "EUR",
        "quantity": 8,
        "expedited": True,
    }


def test_phase3_yyjson_schema_speed_gate():
    app = Buckthorn()

    @app.post("/bench/orders", response_model=ProductPublic)
    def bench_order(prod: CreateProduct):
        return prod

    client = RawTestClient(app)
    raw_req = (
        b"POST /bench/orders HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 71\r\n\r\n"
        b'{"sku":"BK-777","price":199.99,"qty":3,"in_stock":true,"tags":["a","b"]}'
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
    assert rps > 80_000, f"Expected >80,000 single-worker JSON Schema RPS, got {rps:,.0f} RPS"

```

---

## 5. Step 3: Green Phase — Pure C `yyjson` Pool Parser, Validator & Writer (`bt_json.h` & `bt_json.c`)

### 5.1 `buckthorn/c_src/bt_json.h`

```c
/* buckthorn/c_src/bt_json.h */
#ifndef BT_JSON_H
#define BT_JSON_H

#include <stddef.h>
#include <stdint.h>
#include "bt_params.h"
#include "../c_vendor/yyjson.h"

/* Opaque handle wrapping yyjson_doc and root yyjson_val */
typedef struct {
    yyjson_doc *doc;
    yyjson_val *root;
} bt_json_doc_t;

/* Mutable JSON writer backed by zero-malloc C pool buffer */
typedef struct {
    yyjson_mut_doc *doc;
    yyjson_mut_val *root;
} bt_json_writer_t;

/* Parse raw HTTP body bytes into a yyjson document using a zero-malloc C pool */
int bt_json_parse_obj(const char *buf, size_t len, bt_json_doc_t *out_doc);

/* Single-pass C field lookup + type check + constraint validation */
bt_val_error_t bt_json_get_int64_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_ge, int64_t ge,
    int has_le, int64_t le,
    int has_gt, int64_t gt,
    int has_lt, int64_t lt,
    int *out_found,
    int64_t *out_val
);

bt_val_error_t bt_json_get_double_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_ge, double ge,
    int has_le, double le,
    int has_gt, double gt,
    int has_lt, double lt,
    int *out_found,
    double *out_val
);

bt_val_error_t bt_json_get_bool_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int *out_found,
    int *out_val
);

bt_val_error_t bt_json_get_str_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_min_len, size_t min_len,
    int has_max_len, size_t max_len,
    int *out_found,
    const char **out_str,
    size_t *out_len
);

yyjson_val *bt_json_get_raw_field(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int *out_found
);

/* Direct-to-buffer yyjson response writer */
int bt_json_writer_init(bt_json_writer_t *w);
void bt_json_writer_add_int(bt_json_writer_t *w, const char *key, size_t key_len, int64_t val);
void bt_json_writer_add_double(bt_json_writer_t *w, const char *key, size_t key_len, double val);
void bt_json_writer_add_bool(bt_json_writer_t *w, const char *key, size_t key_len, int val);
void bt_json_writer_add_str(bt_json_writer_t *w, const char *key, size_t key_len,
                            const char *val, size_t val_len);
void bt_json_writer_add_null(bt_json_writer_t *w, const char *key, size_t key_len);
int bt_json_writer_finish(bt_json_writer_t *w, char *out_buf, size_t out_cap, size_t *out_len);

#endif /* BT_JSON_H */

```

### 5.2 `buckthorn/c_src/bt_json.c`

```c
/* buckthorn/c_src/bt_json.c */
#include "bt_json.h"
#include <string.h>

/* Pre-allocated 64KB C pools for zero-malloc yyjson read and write per worker thread */
static _Thread_local char tls_read_pool[65536];
static _Thread_local yyjson_alc tls_read_alc;

static _Thread_local char tls_write_pool[65536];
static _Thread_local yyjson_alc tls_write_alc;

int bt_json_parse_obj(const char *buf, size_t len, bt_json_doc_t *out_doc) {
    out_doc->doc = NULL;
    out_doc->root = NULL;
    if (!buf || len == 0) return -1;

    yyjson_alc_pool_init(&tls_read_alc, tls_read_pool, sizeof(tls_read_pool));

    /* yyjson_read_opts with pool allocator performs zero system mallocs */
    yyjson_doc *doc = yyjson_read_opts((char *)buf, len, 0, &tls_read_alc, NULL);
    if (!doc) return -1;

    yyjson_val *root = yyjson_doc_get_root(doc);
    if (!root || !yyjson_is_obj(root)) {
        return -1;
    }

    out_doc->doc = doc;
    out_doc->root = root;
    return 0;
}

bt_val_error_t bt_json_get_int64_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_ge, int64_t ge,
    int has_le, int64_t le,
    int has_gt, int64_t gt,
    int has_lt, int64_t lt,
    int *out_found,
    int64_t *out_val
) {
    yyjson_val *val = yyjson_obj_getn(jdoc->root, key, key_len);
    if (!val || yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    /* Reject booleans disguised as ints */
    if (!yyjson_is_int(val) || yyjson_is_bool(val)) {
        return BT_ERR_TYPE_INT;
    }

    int64_t v = yyjson_get_sint(val);
    if (has_ge && v < ge) return BT_ERR_NUMBER_GE;
    if (has_le && v > le) return BT_ERR_NUMBER_LE;
    if (has_gt && v <= gt) return BT_ERR_NUMBER_GT;
    if (has_lt && v >= lt) return BT_ERR_NUMBER_LT;

    *out_val = v;
    return BT_ERR_NONE;
}

bt_val_error_t bt_json_get_double_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_ge, double ge,
    int has_le, double le,
    int has_gt, double gt,
    int has_lt, double lt,
    int *out_found,
    double *out_val
) {
    yyjson_val *val = yyjson_obj_getn(jdoc->root, key, key_len);
    if (!val || yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    if (!yyjson_is_num(val) || yyjson_is_bool(val)) {
        return BT_ERR_TYPE_FLOAT;
    }

    double v = yyjson_get_num(val);
    if (has_ge && v < ge) return BT_ERR_NUMBER_GE;
    if (has_le && v > le) return BT_ERR_NUMBER_LE;
    if (has_gt && v <= gt) return BT_ERR_NUMBER_GT;
    if (has_lt && v >= lt) return BT_ERR_NUMBER_LT;

    *out_val = v;
    return BT_ERR_NONE;
}

bt_val_error_t bt_json_get_bool_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int *out_found,
    int *out_val
) {
    yyjson_val *val = yyjson_obj_getn(jdoc->root, key, key_len);
    if (!val || yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    if (!yyjson_is_bool(val)) {
        return BT_ERR_TYPE_INT;
    }

    *out_val = yyjson_get_bool(val) ? 1 : 0;
    return BT_ERR_NONE;
}

bt_val_error_t bt_json_get_str_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_min_len, size_t min_len,
    int has_max_len, size_t max_len,
    int *out_found,
    const char **out_str,
    size_t *out_len
) {
    yyjson_val *val = yyjson_obj_getn(jdoc->root, key, key_len);
    if (!val || yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    if (!yyjson_is_str(val)) {
        return BT_ERR_STR_MIN_LEN;
    }

    size_t slen = yyjson_get_len(val);
    if (has_min_len && slen < min_len) return BT_ERR_STR_MIN_LEN;
    if (has_max_len && slen > max_len) return BT_ERR_STR_MAX_LEN;

    *out_str = yyjson_get_str(val);
    *out_len = slen;
    return BT_ERR_NONE;
}

yyjson_val *bt_json_get_raw_field(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int *out_found
) {
    yyjson_val *val = yyjson_obj_getn(jdoc->root, key, key_len);
    if (!val || yyjson_is_null(val)) {
        *out_found = 0;
        return NULL;
    }
    *out_found = 1;
    return val;
}

int bt_json_writer_init(bt_json_writer_t *w) {
    yyjson_alc_pool_init(&tls_write_alc, tls_write_pool, sizeof(tls_write_pool));
    w->doc = yyjson_mut_doc_new(&tls_write_alc);
    if (!w->doc) return -1;
    w->root = yyjson_mut_obj(w->doc);
    if (!w->root) return -1;
    yyjson_mut_doc_set_root(w->doc, w->root);
    return 0;
}

void bt_json_writer_add_int(bt_json_writer_t *w, const char *key, size_t key_len, int64_t val) {
    yyjson_mut_val *k = yyjson_mut_strn(w->doc, key, key_len);
    yyjson_mut_val *v = yyjson_mut_sint(w->doc, val);
    yyjson_mut_obj_add(w->root, k, v);
}

void bt_json_writer_add_double(bt_json_writer_t *w, const char *key, size_t key_len, double val) {
    yyjson_mut_val *k = yyjson_mut_strn(w->doc, key, key_len);
    yyjson_mut_val *v = yyjson_mut_real(w->doc, val);
    yyjson_mut_obj_add(w->root, k, v);
}

void bt_json_writer_add_bool(bt_json_writer_t *w, const char *key, size_t key_len, int val) {
    yyjson_mut_val *k = yyjson_mut_strn(w->doc, key, key_len);
    yyjson_mut_val *v = yyjson_mut_bool(w->doc, val != 0);
    yyjson_mut_obj_add(w->root, k, v);
}

void bt_json_writer_add_str(bt_json_writer_t *w, const char *key, size_t key_len,
                            const char *val, size_t val_len) {
    yyjson_mut_val *k = yyjson_mut_strn(w->doc, key, key_len);
    yyjson_mut_val *v = yyjson_mut_strn(w->doc, val, val_len);
    yyjson_mut_obj_add(w->root, k, v);
}

void bt_json_writer_add_null(bt_json_writer_t *w, const char *key, size_t key_len) {
    yyjson_mut_val *k = yyjson_mut_strn(w->doc, key, key_len);
    yyjson_mut_val *v = yyjson_mut_null(w->doc);
    yyjson_mut_obj_add(w->root, k, v);
}

int bt_json_writer_finish(bt_json_writer_t *w, char *out_buf, size_t out_cap, size_t *out_len) {
    yyjson_write_err err;
    size_t written = yyjson_mut_write_opts(w->doc, 0, &tls_write_alc, &written, &err);
    /* Wait: yyjson_mut_write_opts allocates a buffer inside tls_write_alc and returns char*! */
    char *json_ptr = yyjson_mut_write_opts(w->doc, 0, &tls_write_alc, out_len, &err);
    if (!json_ptr || *out_len > out_cap) {
        return -1;
    }
    memcpy(out_buf, json_ptr, *out_len);
    return 0;
}

```

*(Note: In `bt_http.c`, update the `status_text` ternary in `bt_handle_http_bytes` so `201` formats as `"Created"`:)*

```c
    const char *status_text = (status_code == 200) ? "OK" :
                              (status_code == 201) ? "Created" :
                              (status_code == 422) ? "Unprocessable Entity" : "OK";

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

cdef extern from "c_vendor/yyjson.h" nogil:
    ctypedef struct yyjson_doc:
        pass
    ctypedef struct yyjson_val:
        pass
    ctypedef struct yyjson_mut_doc:
        pass
    ctypedef struct yyjson_mut_val:
        pass

    bint yyjson_is_null(yyjson_val *val)
    bint yyjson_is_bool(yyjson_val *val)
    bint yyjson_is_int(yyjson_val *val)
    bint yyjson_is_num(yyjson_val *val)
    bint yyjson_is_str(yyjson_val *val)
    bint yyjson_is_arr(yyjson_val *val)
    bint yyjson_is_obj(yyjson_val *val)

    bint yyjson_get_bool(yyjson_val *val)
    int64_t yyjson_get_sint(yyjson_val *val)
    double yyjson_get_num(yyjson_val *val)
    const char *yyjson_get_str(yyjson_val *val)
    size_t yyjson_get_len(yyjson_val *val)
    yyjson_val *yyjson_arr_get_first(yyjson_val *arr)

    yyjson_mut_val *yyjson_mut_arr(yyjson_mut_doc *doc)
    yyjson_mut_val *yyjson_mut_obj(yyjson_mut_doc *doc)
    yyjson_mut_val *yyjson_mut_sint(yyjson_mut_doc *doc, int64_t num)
    yyjson_mut_val *yyjson_mut_real(yyjson_mut_doc *doc, double num)
    yyjson_mut_val *yyjson_mut_bool(yyjson_mut_doc *doc, bint val)
    yyjson_mut_val *yyjson_mut_null(yyjson_mut_doc *doc)
    yyjson_mut_val *yyjson_mut_strn(yyjson_mut_doc *doc, const char *str, size_t len)
    bint yyjson_mut_arr_append(yyjson_mut_val *arr, yyjson_mut_val *val)
    bint yyjson_mut_obj_add(yyjson_mut_val *obj, yyjson_mut_val *key, yyjson_mut_val *val)

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

cdef extern from "c_src/bt_json.h" nogil:
    ctypedef struct bt_json_doc_t:
        yyjson_doc *doc
        yyjson_val *root

    ctypedef struct bt_json_writer_t:
        yyjson_mut_doc *doc
        yyjson_mut_val *root

    int bt_json_parse_obj(const char *buf, size_t len, bt_json_doc_t *out_doc)

    bt_val_error_t bt_json_get_int64_checked(
        bt_json_doc_t *jdoc,
        const char *key, size_t key_len,
        int has_ge, int64_t ge,
        int has_le, int64_t le,
        int has_gt, int64_t gt,
        int has_lt, int64_t lt,
        int *out_found,
        int64_t *out_val
    )
    bt_val_error_t bt_json_get_double_checked(
        bt_json_doc_t *jdoc,
        const char *key, size_t key_len,
        int has_ge, double ge,
        int has_le, double le,
        int has_gt, double gt,
        int has_lt, double lt,
        int *out_found,
        double *out_val
    )
    bt_val_error_t bt_json_get_bool_checked(
        bt_json_doc_t *jdoc,
        const char *key, size_t key_len,
        int *out_found,
        int *out_val
    )
    bt_val_error_t bt_json_get_str_checked(
        bt_json_doc_t *jdoc,
        const char *key, size_t key_len,
        int has_min_len, size_t min_len,
        int has_max_len, size_t max_len,
        int *out_found,
        const char **out_str,
        size_t *out_len
    )
    yyjson_val *bt_json_get_raw_field(
        bt_json_doc_t *jdoc,
        const char *key, size_t key_len,
        int *out_found
    )

    int bt_json_writer_init(bt_json_writer_t *w)
    void bt_json_writer_add_int(bt_json_writer_t *w, const char *key, size_t key_len, int64_t val)
    void bt_json_writer_add_double(bt_json_writer_t *w, const char *key, size_t key_len, double val)
    void bt_json_writer_add_bool(bt_json_writer_t *w, const char *key, size_t key_len, int val)
    void bt_json_writer_add_str(bt_json_writer_t *w, const char *key, size_t key_len,
                                const char *val, size_t val_len)
    void bt_json_writer_add_null(bt_json_writer_t *w, const char *key, size_t key_len)
    int bt_json_writer_finish(bt_json_writer_t *w, char *out_buf, size_t out_cap, size_t *out_len)

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
from cpython.unicode cimport PyUnicode_DecodeUTF8, PyUnicode_AsUTF8AndSize
from cpython.object cimport PyObject_GenericSetAttr, PyObject_GenericGetAttr
import inspect
import json as _py_json

from buckthorn._core cimport (
    phr_header, bt_arena_t, bt_arena_init, bt_arena_destroy,
    bt_slice_t, bt_val_error_t, BT_ERR_NONE,
    bt_find_query_param, bt_find_header, bt_find_cookie,
    bt_parse_int64_checked, bt_parse_double_checked, bt_validate_str_checked,
    yyjson_val, yyjson_mut_doc, yyjson_mut_val,
    yyjson_is_null, yyjson_is_bool, yyjson_is_int, yyjson_is_num, yyjson_is_str, yyjson_is_arr,
    yyjson_get_bool, yyjson_get_sint, yyjson_get_num, yyjson_get_str, yyjson_get_len,
    yyjson_arr_get_first,
    yyjson_mut_arr, yyjson_mut_obj, yyjson_mut_sint, yyjson_mut_real,
    yyjson_mut_bool, yyjson_mut_null, yyjson_mut_strn,
    yyjson_mut_arr_append, yyjson_mut_obj_add,
    bt_json_doc_t, bt_json_writer_t, bt_json_parse_obj,
    bt_json_get_int64_checked, bt_json_get_double_checked,
    bt_json_get_bool_checked, bt_json_get_str_checked, bt_json_get_raw_field,
    bt_json_writer_init, bt_json_writer_add_int, bt_json_writer_add_double,
    bt_json_writer_add_bool, bt_json_writer_add_str, bt_json_writer_add_null,
    bt_json_writer_finish,
    bt_router_t, bt_raw_response_t,
    bt_router_create, bt_router_destroy, bt_router_insert,
    bt_handle_http_bytes, bt_uv_server_run
)

# Parameter Source Constants
DEF SRC_PATH        = 1
DEF SRC_QUERY       = 2
DEF SRC_HEADER      = 3
DEF SRC_COOKIE      = 4
DEF SRC_REQUEST     = 5
DEF SRC_BODY_SCHEMA = 6

# Parameter / Field Type Constants
DEF TYPE_INT     = 1
DEF TYPE_FLOAT   = 2
DEF TYPE_STR     = 3
DEF TYPE_REQUEST = 4
DEF TYPE_BOOL    = 5
DEF TYPE_LIST    = 6
DEF TYPE_SCHEMA  = 7


@cython.freelist(4096)
cdef class Request:
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


cdef class CompiledFieldSpec:
    """Pre-compiled C field descriptor for a buckthorn.Schema model."""
    cdef public str name
    cdef public bytes b_name
    cdef const char *_key_ptr
    cdef size_t _key_len
    cdef public int field_type  # TYPE_INT, TYPE_FLOAT, TYPE_STR, TYPE_BOOL, TYPE_LIST
    cdef public bint required
    cdef public object default_val
    cdef public bint is_list_default

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
        str name,
        int field_type,
        bint required=True,
        object default_val=None,
        object ge=None,
        object le=None,
        object gt=None,
        object lt=None,
        object min_length=None,
        object max_length=None,
    ):
        self.name = name
        self.b_name = name.encode("utf-8")
        cdef char *ptr = NULL
        cdef Py_ssize_t length = 0
        PyBytes_AsStringAndSize(self.b_name, &ptr, &length)
        self._key_ptr = <const char *>ptr
        self._key_len = <size_t>length

        self.field_type = field_type
        self.required = required
        self.default_val = default_val
        self.is_list_default = isinstance(default_val, list)

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


cdef class CompiledSchemaSpec:
    cdef public object schema_cls
    cdef public tuple fields
    cdef public int num_fields

    def __cinit__(self, object schema_cls, list fields):
        self.schema_cls = schema_cls
        self.fields = tuple(fields)
        self.num_fields = len(fields)


cdef class CompiledParamSpec:
    cdef public str arg_name
    cdef public bytes lookup_key
    cdef const char *_key_ptr
    cdef size_t _key_len
    cdef public int source       # SRC_PATH..SRC_BODY_SCHEMA
    cdef public int param_type   # TYPE_INT..TYPE_SCHEMA
    cdef public int path_index
    cdef public bint required
    cdef public object default_val
    cdef public CompiledSchemaSpec schema_spec

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
        CompiledSchemaSpec schema_spec=None,
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
        self.schema_spec = schema_spec

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
    cdef public CompiledSchemaSpec response_schema_spec
    cdef public int default_status_code

    def __cinit__(
        self,
        object fn,
        list param_specs,
        CompiledSchemaSpec response_schema_spec=None,
        int default_status_code=200,
    ):
        self.fn = fn
        self.is_coroutine = inspect.iscoroutinefunction(fn)
        self.param_specs = tuple(param_specs)
        self.num_params = len(param_specs)
        self.response_schema_spec = response_schema_spec
        self.default_status_code = default_status_code


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
    elif source == SRC_BODY_SCHEMA:
        loc = "body"

    cdef int written = snprintf(
        out_body_buf, 1024,
        '{"detail":[{"loc":["%s","%s"],"msg":"validation_error","type":"value_error"}]}',
        loc, key_ptr
    )
    out_body_len[0] = <size_t>written
    out_status_code[0] = 422


cdef list _parse_yyjson_array(yyjson_val *arr_val):
    cdef size_t n = yyjson_get_len(arr_val)
    cdef list res = []
    cdef yyjson_val *elem
    cdef size_t i
    if n == 0:
        return res
    # In yyjson, flat primitive array elements are contiguous or can be stepped:
    # For safety with any array element, we convert via yyjson primitive inspection
    elem = yyjson_arr_get_first(arr_val)
    for i in range(n):
        if yyjson_is_str(elem):
            res.append(PyUnicode_DecodeUTF8(yyjson_get_str(elem), <Py_ssize_t>yyjson_get_len(elem), "strict"))
        elif yyjson_is_int(elem):
            res.append(yyjson_get_sint(elem))
        elif yyjson_is_num(elem):
            res.append(yyjson_get_num(elem))
        elif yyjson_is_bool(elem):
            res.append(bool(yyjson_get_bool(elem)))
        elif yyjson_is_null(elem):
            res.append(None)
        elem += 1
    return res


cdef object _deserialize_schema_from_c_json(
    CompiledSchemaSpec schema_spec,
    const char *body_ptr,
    size_t body_len,
    char *out_body_buf,
    size_t *out_body_len,
    int *out_status_code
):
    cdef bt_json_doc_t jdoc
    if bt_json_parse_obj(body_ptr, body_len, &jdoc) != 0:
        _write_422_error(SRC_BODY_SCHEMA, "__root__", out_body_buf, out_body_len, out_status_code)
        return None

    cdef object instance = (<type>(schema_spec.schema_cls)).__new__(<type>(schema_spec.schema_cls))
    cdef int i
    cdef CompiledFieldSpec fspec
    cdef int found = 0
    cdef bt_val_error_t err
    cdef int64_t v_int = 0
    cdef double v_flt = 0.0
    cdef int v_bool = 0
    cdef const char *v_str = NULL
    cdef size_t v_str_len = 0
    cdef yyjson_val *raw_val = NULL
    cdef object py_val

    for i in range(schema_spec.num_fields):
        fspec = <CompiledFieldSpec>(schema_spec.fields[i])
        found = 0

        if fspec.field_type == TYPE_INT:
            err = bt_json_get_int64_checked(
                &jdoc, fspec._key_ptr, fspec._key_len,
                fspec.has_ge, fspec.int_ge,
                fspec.has_le, fspec.int_le,
                fspec.has_gt, fspec.int_gt,
                fspec.has_lt, fspec.int_lt,
                &found, &v_int
            )
            if err != BT_ERR_NONE:
                _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return None
            if found:
                PyObject_GenericSetAttr(instance, fspec.name, v_int)
                continue

        elif fspec.field_type == TYPE_FLOAT:
            err = bt_json_get_double_checked(
                &jdoc, fspec._key_ptr, fspec._key_len,
                fspec.has_ge, fspec.float_ge,
                fspec.has_le, fspec.float_le,
                fspec.has_gt, fspec.float_gt,
                fspec.has_lt, fspec.float_lt,
                &found, &v_flt
            )
            if err != BT_ERR_NONE:
                _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return None
            if found:
                PyObject_GenericSetAttr(instance, fspec.name, v_flt)
                continue

        elif fspec.field_type == TYPE_BOOL:
            err = bt_json_get_bool_checked(&jdoc, fspec._key_ptr, fspec._key_len, &found, &v_bool)
            if err != BT_ERR_NONE:
                _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return None
            if found:
                PyObject_GenericSetAttr(instance, fspec.name, True if v_bool else False)
                continue

        elif fspec.field_type == TYPE_STR:
            err = bt_json_get_str_checked(
                &jdoc, fspec._key_ptr, fspec._key_len,
                fspec.has_min_len, fspec.min_len,
                fspec.has_max_len, fspec.max_len,
                &found, &v_str, &v_str_len
            )
            if err != BT_ERR_NONE:
                _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return None
            if found:
                py_val = PyUnicode_DecodeUTF8(v_str, <Py_ssize_t>v_str_len, "strict")
                PyObject_GenericSetAttr(instance, fspec.name, py_val)
                continue

        elif fspec.field_type == TYPE_LIST:
            raw_val = bt_json_get_raw_field(&jdoc, fspec._key_ptr, fspec._key_len, &found)
            if found:
                if not yyjson_is_arr(raw_val):
                    _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                    return None
                PyObject_GenericSetAttr(instance, fspec.name, _parse_yyjson_array(raw_val))
                continue

        # Field was omitted or null in JSON
        if fspec.required:
            _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
            return None
        if fspec.is_list_default:
            PyObject_GenericSetAttr(instance, fspec.name, list(fspec.default_val))
        else:
            PyObject_GenericSetAttr(instance, fspec.name, fspec.default_val)

    return instance


cdef inline void _write_py_value_to_yyjson(
    bt_json_writer_t *w,
    const char *key_ptr,
    size_t key_len,
    object val
):
    cdef const char *s_ptr = NULL
    cdef Py_ssize_t s_len = 0
    cdef yyjson_mut_val *k_node
    cdef yyjson_mut_val *arr_node
    cdef object item

    if val is None:
        bt_json_writer_add_null(w, key_ptr, key_len)
    elif isinstance(val, bool):
        bt_json_writer_add_bool(w, key_ptr, key_len, 1 if val else 0)
    elif isinstance(val, int):
        bt_json_writer_add_int(w, key_ptr, key_len, <int64_t>val)
    elif isinstance(val, float):
        bt_json_writer_add_double(w, key_ptr, key_len, <double>val)
    elif isinstance(val, str):
        s_ptr = PyUnicode_AsUTF8AndSize(val, &s_len)
        bt_json_writer_add_str(w, key_ptr, key_len, s_ptr, <size_t>s_len)
    elif isinstance(val, list):
        k_node = yyjson_mut_strn(w.doc, key_ptr, key_len)
        arr_node = yyjson_mut_arr(w.doc)
        for item in (<list>val):
            if isinstance(item, str):
                s_ptr = PyUnicode_AsUTF8AndSize(item, &s_len)
                yyjson_mut_arr_append(arr_node, yyjson_mut_strn(w.doc, s_ptr, <size_t>s_len))
            elif isinstance(item, bool):
                yyjson_mut_arr_append(arr_node, yyjson_mut_bool(w.doc, 1 if item else 0))
            elif isinstance(item, int):
                yyjson_mut_arr_append(arr_node, yyjson_mut_sint(w.doc, <int64_t>item))
            elif isinstance(item, float):
                yyjson_mut_arr_append(arr_node, yyjson_mut_real(w.doc, <double>item))
            elif item is None:
                yyjson_mut_arr_append(arr_node, yyjson_mut_null(w.doc))
        yyjson_mut_obj_add(w.root, k_node, arr_node)


cdef int _serialize_schema_to_c_buf(
    object obj,
    CompiledSchemaSpec schema_spec,
    char *out_body_buf,
    size_t *out_body_len
):
    cdef bt_json_writer_t writer
    if bt_json_writer_init(&writer) != 0:
        return -1

    cdef int i
    cdef CompiledFieldSpec fspec
    cdef object val
    cdef bint is_dict = isinstance(obj, dict)

    for i in range(schema_spec.num_fields):
        fspec = <CompiledFieldSpec>(schema_spec.fields[i])
        if is_dict:
            val = (<dict>obj).get(fspec.name, fspec.default_val)
        else:
            val = PyObject_GenericGetAttr(obj, fspec.name)
        _write_py_value_to_yyjson(&writer, fspec._key_ptr, fspec._key_len, val)

    return bt_json_writer_finish(&writer, out_body_buf, 32768, out_body_len)


cdef int _serialize_dict_to_c_buf(
    dict d,
    char *out_body_buf,
    size_t *out_body_len
):
    cdef bt_json_writer_t writer
    if bt_json_writer_init(&writer) != 0:
        return -1

    cdef object k, v
    cdef const char *k_ptr = NULL
    cdef Py_ssize_t k_len = 0

    for k, v in d.items():
        if isinstance(k, str):
            k_ptr = PyUnicode_AsUTF8AndSize(k, &k_len)
            _write_py_value_to_yyjson(&writer, k_ptr, <size_t>k_len, v)

    return bt_json_writer_finish(&writer, out_body_buf, 32768, out_body_len)


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
    cdef object schema_instance
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

                if spec.source == SRC_BODY_SCHEMA:
                    schema_instance = _deserialize_schema_from_c_json(
                        spec.schema_spec, body, body_len,
                        out_body_buf, out_body_len, out_status_code
                    )
                    if schema_instance is None:
                        return 0  # 422 response already written into out_body_buf
                    kwargs[spec.arg_name] = schema_instance
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

                else:
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

        out_status_code[0] = entry.default_status_code

        # 1. Fast path if route defines `response_model=...`
        if entry.response_schema_spec is not None:
            return _serialize_schema_to_c_buf(result, entry.response_schema_spec, out_body_buf, out_body_len)

        # 2. Fast path if handler returns a `buckthorn.Schema` instance
        if hasattr(result, "__bt_schema_spec__"):
            return _serialize_schema_to_c_buf(
                result, <CompiledSchemaSpec>(result.__bt_schema_spec__), out_body_buf, out_body_len
            )

        # 3. Fast path if handler returns a Python `dict`
        if isinstance(result, dict):
            return _serialize_dict_to_c_buf(<dict>result, out_body_buf, out_body_len)

        # 4. Raw `bytes` or `str` return (Phases 1 & 2)
        if isinstance(result, bytes):
            PyBytes_AsStringAndSize(result, &raw_bytes_ptr, &raw_bytes_len)
            memcpy(out_body_buf, raw_bytes_ptr, <size_t>raw_bytes_len)
            out_body_len[0] = <size_t>raw_bytes_len
            return 0
        elif isinstance(result, str):
            encoded = (<str>result).encode("utf-8")
            PyBytes_AsStringAndSize(encoded, &raw_bytes_ptr, &raw_bytes_len)
            memcpy(out_body_buf, raw_bytes_ptr, <size_t>raw_bytes_len)
            out_body_len[0] = <size_t>raw_bytes_len
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

    cpdef void add_route(
        self,
        str method,
        str path,
        object fn,
        list param_specs=None,
        CompiledSchemaSpec response_schema_spec=None,
        int status_code=200,
    ):
        cdef bytes b_method = method.upper().encode("ascii")
        cdef bytes b_path = path.encode("utf-8")
        cdef RouteEntry entry = RouteEntry(fn, param_specs or [], response_schema_spec, status_code)
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

    cpdef object json(self):
        return _py_json.loads(self.body)


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

## 7. Step 5: Python Surface (`schema.py`, `status.py`, `app.py`, `testing.py`, `__init__.py`)

### 7.1 `buckthorn/schema.py`

```python
# buckthorn/schema.py
from typing import Any, Optional, get_origin, get_type_hints
from buckthorn._core import CompiledFieldSpec, CompiledSchemaSpec

_UNDEFINED = Ellipsis

_SCHEMA_TYPE_MAP = {
    int: 1,    # TYPE_INT
    float: 2,  # TYPE_FLOAT
    str: 3,    # TYPE_STR
    bool: 5,   # TYPE_BOOL
    list: 6,   # TYPE_LIST
}


class FieldInfo:
    def __init__(
        self,
        default: Any = _UNDEFINED,
        *,
        ge: Optional[float] = None,
        le: Optional[float] = None,
        gt: Optional[float] = None,
        lt: Optional[float] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
    ):
        self.default = default
        self.required = default is _UNDEFINED
        self.ge = ge
        self.le = le
        self.gt = gt
        self.lt = lt
        self.min_length = min_length
        self.max_length = max_length


def Field(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return FieldInfo(default=default, **kwargs)


class Schema:
    __bt_schema_spec__: CompiledSchemaSpec

    def __init_subclass__(cls, **kwargs: Any):
        super().__init_subclass__(**kwargs)
        hints = get_type_hints(cls)
        compiled_fields: list[CompiledFieldSpec] = []

        for field_name, annotation in hints.items():
            if field_name.startswith("__"):
                continue

            origin = get_origin(annotation) or annotation
            field_type = _SCHEMA_TYPE_MAP.get(origin, 3)

            raw_default = getattr(cls, field_name, _UNDEFINED)
            if isinstance(raw_default, FieldInfo):
                finfo = raw_default
                # Clean descriptor off the class so instance attribute lookup is unobstructed
                delattr(cls, field_name)
            else:
                finfo = FieldInfo(default=raw_default)

            compiled_fields.append(
                CompiledFieldSpec(
                    name=field_name,
                    field_type=field_type,
                    required=finfo.required,
                    default_val=None if finfo.default is _UNDEFINED else finfo.default,
                    ge=finfo.ge,
                    le=finfo.le,
                    gt=finfo.gt,
                    lt=finfo.lt,
                    min_length=finfo.min_length,
                    max_length=finfo.max_length,
                )
            )

        cls.__bt_schema_spec__ = CompiledSchemaSpec(cls, compiled_fields)

    def __init__(self, **kwargs: Any):
        for fspec in self.__bt_schema_spec__.fields:
            if fspec.name in kwargs:
                setattr(self, fspec.name, kwargs[fspec.name])
            elif not fspec.required:
                val = list(fspec.default_val) if fspec.is_list_default else fspec.default_val
                setattr(self, fspec.name, val)
            else:
                raise ValueError(f"Missing required schema field: {fspec.name}")

    def model_dump(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in self.__bt_schema_spec__.fields}

```

### 7.2 `buckthorn/status.py`

```python
# buckthorn/status.py
HTTP_200_OK = 200
HTTP_201_CREATED = 201
HTTP_204_NO_CONTENT = 204
HTTP_400_BAD_REQUEST = 400
HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403
HTTP_404_NOT_FOUND = 404
HTTP_422_UNPROCESSABLE_ENTITY = 422
HTTP_429_TOO_MANY_REQUESTS = 429
HTTP_500_INTERNAL_SERVER_ERROR = 500

```

### 7.3 `buckthorn/app.py`

```python
# buckthorn/app.py
import inspect
from typing import Any, Callable, Optional, Type, get_type_hints
from buckthorn._core import CoreRouter, CompiledParamSpec, Request
from buckthorn.params import ParamInfo, PathInfo, QueryInfo, _UNDEFINED
from buckthorn.schema import Schema

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

        # Detect buckthorn.Schema request body parameter
        if isinstance(annotation, type) and issubclass(annotation, Schema):
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=6,      # SRC_BODY_SCHEMA
                    param_type=7,  # TYPE_SCHEMA
                    required=True,
                    schema_spec=annotation.__bt_schema_spec__,
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
    def __init__(self, title: str = "Buckthorn", version: str = "0.3.0"):
        self.title = title
        self.version = version
        self._core = CoreRouter()

    def add_api_route(
        self,
        path: str,
        endpoint: Callable,
        methods: list[str],
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
    ) -> None:
        param_specs = _compile_route_params(path, endpoint)
        resp_spec = (
            response_model.__bt_schema_spec__
            if (response_model is not None and issubclass(response_model, Schema))
            else None
        )
        for method in methods:
            self._core.add_route(
                method.upper(),
                path,
                endpoint,
                param_specs,
                resp_spec,
                status_code,
            )

    def get(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
    ):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["GET"], response_model=response_model, status_code=status_code)
            return fn
        return decorator

    def post(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
    ):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["POST"], response_model=response_model, status_code=status_code)
            return fn
        return decorator

    def put(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
    ):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["PUT"], response_model=response_model, status_code=status_code)
            return fn
        return decorator

    def delete(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
    ):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["DELETE"], response_model=response_model, status_code=status_code)
            return fn
        return decorator

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self._core.run_server(host, port)

```

### 7.4 `buckthorn/testing.py`

```python
# buckthorn/testing.py
import json as _py_json
from typing import Any, Optional
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
        json: Optional[Any] = None,
    ) -> RawResponse:
        req_headers = dict(headers) if headers else {}
        if json is not None:
            body = _py_json.dumps(json, separators=(",", ":")).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

        hdr_lines = [f"{method.upper()} {path} HTTP/1.1", "Host: localhost"]
        for k, v in req_headers.items():
            hdr_lines.append(f"{k}: {v}")
        if body:
            hdr_lines.append(f"Content-Length: {len(body)}")
        raw_head = ("\r\n".join(hdr_lines) + "\r\n\r\n").encode("utf-8")
        return self._client.send_raw(raw_head + body)

    def get(self, path: str, headers: Optional[dict[str, str]] = None) -> RawResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        body: bytes = b"",
        headers: Optional[dict[str, str]] = None,
        json: Optional[Any] = None,
    ) -> RawResponse:
        return self.request("POST", path, headers=headers, body=body, json=json)

    def put(
        self,
        path: str,
        body: bytes = b"",
        headers: Optional[dict[str, str]] = None,
        json: Optional[Any] = None,
    ) -> RawResponse:
        return self.request("PUT", path, headers=headers, body=body, json=json)

    def delete(self, path: str, headers: Optional[dict[str, str]] = None) -> RawResponse:
        return self.request("DELETE", path, headers=headers)

    @property
    def arena_offset(self) -> int:
        return self._client.arena_offset

```

### 7.5 `buckthorn/__init__.py`

```python
# buckthorn/__init__.py
from buckthorn.app import Buckthorn
from buckthorn._core import Request
from buckthorn.params import Path, Query, Header, Cookie
from buckthorn.schema import Schema, Field
from buckthorn import status

__all__ = [
    "Buckthorn",
    "Request",
    "Path",
    "Query",
    "Header",
    "Cookie",
    "Schema",
    "Field",
    "status",
]

```

---

## 8. Step 6: Update `setup.py` to Compile `yyjson.c` & `bt_json.c`

Update `setup.py` so `buckthorn/c_vendor/yyjson.c` and `buckthorn/c_src/bt_json.c` are compiled into `buckthorn._core` with Link-Time Optimization (`-flto`):

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
            "buckthorn/c_src/bt_json.c",
            "buckthorn/c_vendor/picohttpparser.c",
            "buckthorn/c_vendor/rax.c",
            "buckthorn/c_vendor/yyjson.c",
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
    version="0.3.0",
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

Run these 3 commands sequentially to download `yyjson`, compile the updated C/Cython extension, and run Phases 1, 2, and 3 test suites:

```bash
# 1. Fetch yyjson.c and yyjson.h into buckthorn/c_vendor/
bash scripts/fetch_vendor.sh

# 2. Recompile C + Cython extension with yyjson.c and bt_json.c included
pip install -e ".[dev]" --no-build-isolation

# 3. Run Phase 1, Phase 2, and Phase 3 TDD suites + 80,000 RPS Speed Gate
pytest tests/phase1/test_core_router.py tests/phase2/test_params.py tests/phase3/test_schema_serde.py -v -s

```