Below is the **Phase 4 Master Technical Specification** engineered for **Gemini Flash**.

It builds directly on top of Phases 1, 2, and 3 while keeping all previous tests 100% green. Copy and paste this entire specification into Gemini Flash to implement **Compile-Time Flattened `Depends()` Dependency Injection**, **Sub-Dependencies with Request-Scoped Caching (`use_cache=True`)**, **Generator (`yield` / `async yield`) Teardown Hooks**, **Class & Callable Instance Dependencies**, and **`HTTPException` Short-Circuiting**.

---

# PHASE 4 SPECIFICATION: Compile-Time Flattened `Depends()` Dependency Injection, Generator Teardown & `HTTPException`

## 1. System Prompt & Strict Guardrails for Gemini Flash

> **Instructions for the AI Implementer (Gemini Flash):**
> 1. **Backward Compatibility Gate:** All tests in `tests/phase1/`, `tests/phase2/`, and `tests/phase3/` MUST continue to pass without modification.
> 2. **Strict TDD Order:** Write `tests/phase4/test_depends_di.py` FIRST (Red Phase), then implement `buckthorn/exceptions.py`, update `buckthorn/params.py`, `buckthorn/c_src/bt_http.c`, `buckthorn/_core.pyx`, `buckthorn/app.py`, and `buckthorn/__init__.py` (Green Phase).
> 3. **Compile-Time DAG Flattening (No Runtime Graph Traversal):** Do NOT traverse the dependency tree recursively during a request. When a route is registered (`add_api_route`), flatten its entire recursive `Depends()` tree into a **topologically sorted flat tuple of `CompiledDepStep` objects** stored on `RouteEntry`.
> 4. **O(1) Slot Caching (`use_cache=True`):** Assign each unique dependency callable a fixed integer slot index (`0 .. num_dep_slots - 1`). During a request, store resolved dependency values in a pre-sized Python `list` (`dep_slots`). If multiple dependencies or the route handler depend on the same callable with `use_cache=True`, it appears only ONCE in the flat topological array and its result is read from `dep_slots[slot_idx]` in $O(1)$ time.
> 5. **Guaranteed LIFO Generator Teardown:** Support both synchronous generators (`def get_db(): yield db`) and asynchronous generators (`async def get_db(): yield db`). Active generators MUST be pushed onto a teardown stack and unwound in **reverse (LIFO) order** inside a `finally` block so cleanup executes whether the handler succeeds or raises `HTTPException`.
> 
> 

---

## 2. Phase 4 Target Directory Tree (New & Updated Files)

```text
buckthorn/
├── buckthorn/
│   ├── __init__.py                   # [UPDATED] Exports Depends, HTTPException
│   ├── app.py                        # [UPDATED] DAG topological compiler + route/app dependencies=[...]
│   ├── exceptions.py                 # [NEW] FastAPI-compatible HTTPException class
│   ├── params.py                     # [UPDATED] Adds Depends() and DependsInfo descriptor
│   ├── schema.py                     # (Unchanged from Phase 3)
│   ├── status.py                     # (Unchanged from Phase 3)
│   ├── testing.py                    # (Unchanged from Phase 3)
│   ├── _core.pxd                     # (Unchanged from Phase 3)
│   ├── _core.pyx                     # [UPDATED] Adds CompiledDepStep, flat DI executor, LIFO teardown & HTTPException handler
│   └── c_src/
│       └── bt_http.c                 # [UPDATED] Full HTTP status reason phrase table (401, 403, 404, 422, etc.)
└── tests/
    ├── phase1/
    │   └── test_core_router.py       # (Must remain 100% green)
    ├── phase2/
    │   └── test_params.py            # (Must remain 100% green)
    ├── phase3/
    │   └── test_schema_serde.py      # (Must remain 100% green)
    └── phase4/
        └── test_depends_di.py        # [NEW] Phase 4 TDD test suite

```

---

## 3. Step 1: TDD Red Phase — Write the Phase 4 Test Suite First

Create `tests/phase4/test_depends_di.py` before modifying any framework code:

```python
# tests/phase4/test_depends_di.py
import time
import pytest
from buckthorn import (
    Buckthorn,
    Depends,
    HTTPException,
    Header,
    Path,
    Query,
    Schema,
    status,
)
from buckthorn.testing import RawTestClient


def test_dag_deduplication_and_lifo_generator_teardown():
    app = Buckthorn()
    events: list[str] = []

    async def get_db():
        events.append("db_open")
        try:
            yield "pg_conn"
        finally:
            events.append("db_close")

    def get_cache():
        events.append("cache_open")
        try:
            yield "redis_conn"
        finally:
            events.append("cache_close")

    async def get_user_repo(db: str = Depends(get_db), cache: str = Depends(get_cache)):
        events.append("user_repo_init")
        return f"UserRepo({db},{cache})"

    async def get_order_repo(db: str = Depends(get_db)):
        events.append("order_repo_init")
        return f"OrderRepo({db})"

    @app.get("/dashboard")
    async def dashboard(
        users: str = Depends(get_user_repo),
        orders: str = Depends(get_order_repo),
    ):
        events.append("handler_run")
        return {"users": users, "orders": orders}

    client = RawTestClient(app)
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert res.json() == {
        "users": "UserRepo(pg_conn,redis_conn)",
        "orders": "OrderRepo(pg_conn)",
    }

    # Verify:
    # 1. `get_db` was depended on by both repos, but ran ONLY ONCE (`use_cache=True`).
    # 2. Both sync and async generator `finally` blocks ran AFTER `handler_run` in LIFO order.
    assert events.count("db_open") == 1
    assert events.count("cache_open") == 1
    assert events == [
        "db_open",
        "cache_open",
        "user_repo_init",
        "order_repo_init",
        "handler_run",
        "cache_close",
        "db_close",
    ]


def test_use_cache_false_executes_dependency_per_branch():
    app = Buckthorn()
    counter = {"calls": 0}

    def get_request_nonce():
        counter["calls"] += 1
        return counter["calls"]

    @app.get("/nonces")
    def read_nonces(
        n1: int = Depends(get_request_nonce, use_cache=False),
        n2: int = Depends(get_request_nonce, use_cache=False),
    ):
        return {"n1": n1, "n2": n2}

    client = RawTestClient(app)
    res = client.get("/nonces")
    assert res.status_code == 200
    assert res.json() == {"n1": 1, "n2": 2}


def test_http_exception_in_dependency_or_handler_still_runs_generator_teardown():
    app = Buckthorn()
    state = {"db_active": False}

    async def get_db():
        state["db_active"] = True
        try:
            yield "conn"
        finally:
            state["db_active"] = False

    def require_api_key(x_api_key: str = Header(alias="X-Api-Key", default="none")):
        if x_api_key != "secret-123":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API Key",
            )
        return x_api_key

    @app.get("/secure/{item_id}", dependencies=[Depends(require_api_key)])
    async def get_secure_item(item_id: int = Path(ge=1), db: str = Depends(get_db)):
        if item_id == 999:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found",
            )
        return {"item_id": item_id, "db": db}

    client = RawTestClient(app)

    # 1. Dependency raises 401 -> handler never runs
    res_401 = client.get("/secure/10")
    assert res_401.status_code == 401
    assert res_401.json() == {"detail": "Invalid API Key"}
    assert state["db_active"] is False

    # 2. Handler raises 404 after get_db() yielded -> generator teardown MUST still close DB!
    res_404 = client.get("/secure/999", headers={"X-Api-Key": "secret-123"})
    assert res_404.status_code == 404
    assert res_404.json() == {"detail": "Item not found"}
    assert state["db_active"] is False

    # 3. Valid request -> 200 OK and DB closed after response
    res_200 = client.get("/secure/42", headers={"X-Api-Key": "secret-123"})
    assert res_200.status_code == 200
    assert res_200.json() == {"item_id": 42, "db": "conn"}
    assert state["db_active"] is False


def test_class_dependency_shorthand_and_callable_instance():
    app = Buckthorn()

    class Pagination:
        def __init__(
            self,
            page: int = Query(default=1, ge=1),
            size: int = Query(default=20, ge=1, le=100),
        ):
            self.page = page
            self.size = size
            self.offset = (page - 1) * size

    class RoleGuard:
        def __init__(self, required_role: str):
            self.required_role = required_role

        def __call__(self, x_role: str = Header(alias="X-Role", default="guest")):
            if x_role != self.required_role:
                raise HTTPException(status_code=403, detail="Insufficient role")
            return x_role

    @app.get("/items", dependencies=[Depends(RoleGuard("admin"))])
    def list_items(
        commons: Pagination = Depends(),  # FastAPI shorthand: infers `Pagination` from type hint
    ):
        return {"page": commons.page, "size": commons.size, "offset": commons.offset}

    client = RawTestClient(app)

    # Forbidden role -> 403
    res_403 = client.get("/items?page=3&size=10", headers={"X-Role": "viewer"})
    assert res_403.status_code == 403

    # Valid admin role -> 200 with resolved Pagination class instance
    res_200 = client.get("/items?page=3&size=10", headers={"X-Role": "admin"})
    assert res_200.status_code == 200
    assert res_200.json() == {"page": 3, "size": 10, "offset": 20}


def test_phase4_flattened_di_speed_gate():
    app = Buckthorn()

    def dep_config():
        return "prod"

    def dep_tenant( x_tenant: str = Header(alias="X-Tenant"), env: str = Depends(dep_config)):
        return f"{env}:{x_tenant}"

    def dep_auth(tenant: str = Depends(dep_tenant)):
        return tenant

    @app.get("/bench/di")
    def bench_di(auth: str = Depends(dep_auth), env: str = Depends(dep_config)):
        return {"auth": auth, "env": env}

    client = RawTestClient(app)
    raw_req = (
        b"GET /bench/di HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Tenant: acme\r\n\r\n"
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
    assert res.json() == {"auth": "prod:acme", "env": "prod"}
    assert rps > 75_000, f"Expected >75,000 RPS with 3 nested Depends(), got {rps:,.0f} RPS"

```

---

## 4. Step 2: Green Phase — Update `bt_http.c` Status Reason Phrase Helper

In `buckthorn/c_src/bt_http.c`, add a fast `switch` helper `bt_status_reason(int code)` right above `bt_handle_http_bytes` and use it when formatting the HTTP response header so `401`, `403`, `404`, `422`, `429`, etc. emit standard HTTP/1.1 reason phrases:

```c
static inline const char *bt_status_reason(int status_code) {
    switch (status_code) {
        case 200: return "OK";
        case 201: return "Created";
        case 204: return "No Content";
        case 400: return "Bad Request";
        case 401: return "Unauthorized";
        case 403: return "Forbidden";
        case 404: return "Not Found";
        case 409: return "Conflict";
        case 422: return "Unprocessable Entity";
        case 429: return "Too Many Requests";
        case 500: return "Internal Server Error";
        default:  return "OK";
    }
}

```

And inside `bt_handle_http_bytes` in `buckthorn/c_src/bt_http.c`, replace the `status_text` ternary with:

```c
    const char *status_text = bt_status_reason(status_code);

```

---

## 5. Step 3: Green Phase — Create `buckthorn/exceptions.py` & Update `buckthorn/params.py`

### 5.1 `buckthorn/exceptions.py`

```python
# buckthorn/exceptions.py
from typing import Any, Optional


class HTTPException(Exception):
    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = detail if detail is not None else "HTTP Error"
        self.headers = headers

```

### 5.2 Update `buckthorn/params.py` (Add `DependsInfo` and `Depends()`)

Append `DependsInfo` and `Depends` to `buckthorn/params.py`:

```python
# buckthorn/params.py
from typing import Any, Callable, Optional

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


class DependsInfo:
    def __init__(self, dependency: Optional[Callable[..., Any]] = None, *, use_cache: bool = True):
        self.dependency = dependency
        self.use_cache = bool(use_cache)


def Path(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return PathInfo(default=default, **kwargs)


def Query(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return QueryInfo(default=default, **kwargs)


def Header(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return HeaderInfo(default=default, **kwargs)


def Cookie(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return CookieInfo(default=default, **kwargs)


def Depends(dependency: Optional[Callable[..., Any]] = None, *, use_cache: bool = True) -> Any:
    return DependsInfo(dependency=dependency, use_cache=use_cache)

```

---

## 6. Step 4: Update `buckthorn/_core.pyx` with `CompiledDepStep`, Flat DI Executor & LIFO Teardown

Replace `buckthorn/_core.pyx` with the updated version below, which adds:

* `DEF SRC_DEPENDS = 7` and `DEF TYPE_DEPENDS = 8`
* `CompiledDepStep` (classifying each dependency into `DEP_SYNC_FUNC`, `DEP_CORO_FUNC`, `DEP_SYNC_GEN`, or `DEP_ASYNC_GEN` at registration time)
* `_resolve_kwargs_for_specs()` (unified C parameter extractor shared by both dependencies and route handlers)
* `_teardown_generators()` (LIFO cleanup of sync/async generator dependencies inside `finally:`)
* `HTTPException` interception and direct C `yyjson` error serialization

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
from buckthorn.exceptions import HTTPException

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
DEF SRC_DEPENDS     = 7

# Parameter / Field Type Constants
DEF TYPE_INT     = 1
DEF TYPE_FLOAT   = 2
DEF TYPE_STR     = 3
DEF TYPE_REQUEST = 4
DEF TYPE_BOOL    = 5
DEF TYPE_LIST    = 6
DEF TYPE_SCHEMA  = 7
DEF TYPE_DEPENDS = 8

# Dependency Callable Execution Kind Constants
DEF DEP_SYNC_FUNC = 1
DEF DEP_CORO_FUNC = 2
DEF DEP_SYNC_GEN  = 3
DEF DEP_ASYNC_GEN = 4


cdef inline int _detect_callable_kind(object fn):
    if inspect.isasyncgenfunction(fn):
        return DEP_ASYNC_GEN
    if inspect.isgeneratorfunction(fn):
        return DEP_SYNC_GEN
    if inspect.iscoroutinefunction(fn):
        return DEP_CORO_FUNC
    if not isinstance(fn, type) and callable(fn):
        call_attr = getattr(fn, "__call__", None)
        if call_attr is not None:
            if inspect.isasyncgenfunction(call_attr):
                return DEP_ASYNC_GEN
            if inspect.isgeneratorfunction(call_attr):
                return DEP_SYNC_GEN
            if inspect.iscoroutinefunction(call_attr):
                return DEP_CORO_FUNC
    return DEP_SYNC_FUNC


cdef inline object _step_coroutine_sync(object coro):
    cdef object value = None
    try:
        coro.send(None)
    except StopIteration as stop:
        value = stop.value
    finally:
        coro.close()
    return value


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
    cdef public str name
    cdef public bytes b_name
    cdef const char *_key_ptr
    cdef size_t _key_len
    cdef public int field_type
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
    cdef public int source       # SRC_PATH..SRC_DEPENDS
    cdef public int param_type   # TYPE_INT..TYPE_DEPENDS
    cdef public int path_index
    cdef public int dep_slot_index
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
        int dep_slot_index=-1,
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
        self.dep_slot_index = dep_slot_index
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


cdef class CompiledDepStep:
    """Single node in the compile-time flattened Dependency Injection execution array."""
    cdef public object fn
    cdef public int slot_index
    cdef public int dep_kind      # DEP_SYNC_FUNC, DEP_CORO_FUNC, DEP_SYNC_GEN, DEP_ASYNC_GEN
    cdef public tuple param_specs
    cdef public int num_params

    def __cinit__(self, object fn, int slot_index, list param_specs):
        self.fn = fn
        self.slot_index = slot_index
        self.dep_kind = _detect_callable_kind(fn)
        self.param_specs = tuple(param_specs)
        self.num_params = len(param_specs)


cdef class RouteEntry:
    cdef public object fn
    cdef public bint is_coroutine
    cdef public tuple param_specs
    cdef public int num_params
    cdef public tuple dep_steps
    cdef public int num_dep_steps
    cdef public CompiledSchemaSpec response_schema_spec
    cdef public int default_status_code

    def __cinit__(
        self,
        object fn,
        list param_specs,
        CompiledSchemaSpec response_schema_spec=None,
        int default_status_code=200,
        list dep_steps=None,
    ):
        self.fn = fn
        self.is_coroutine = inspect.iscoroutinefunction(fn)
        self.param_specs = tuple(param_specs)
        self.num_params = len(param_specs)
        cdef list steps = dep_steps or []
        self.dep_steps = tuple(steps)
        self.num_dep_steps = len(steps)
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


cdef int _resolve_kwargs_for_specs(
    tuple param_specs,
    int num_params,
    list dep_slots,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    phr_header *headers, size_t num_headers,
    bt_slice_t *path_params, size_t num_path_params,
    const char *body, size_t body_len,
    dict out_kwargs,
    char *out_body_buf,
    size_t *out_body_len,
    int *out_status_code
):
    """Returns 0 on success, or 1 if a 422 validation error was written to out_body_buf."""
    cdef int i
    cdef CompiledParamSpec spec
    cdef bt_slice_t raw_slice
    cdef bt_val_error_t val_err
    cdef int64_t parsed_int = 0
    cdef double parsed_float = 0.0
    cdef Request req_obj
    cdef object schema_instance

    for i in range(num_params):
        spec = <CompiledParamSpec>(param_specs[i])

        if spec.source == SRC_DEPENDS:
            out_kwargs[spec.arg_name] = dep_slots[spec.dep_slot_index]
            continue

        if spec.source == SRC_REQUEST:
            req_obj = Request.__new__(Request)
            req_obj.bind_c(
                method, method_len, path, path_len,
                query, query_len, headers, num_headers, body, body_len
            )
            out_kwargs[spec.arg_name] = req_obj
            continue

        if spec.source == SRC_BODY_SCHEMA:
            schema_instance = _deserialize_schema_from_c_json(
                spec.schema_spec, body, body_len,
                out_body_buf, out_body_len, out_status_code
            )
            if schema_instance is None:
                return 1
            out_kwargs[spec.arg_name] = schema_instance
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
                return 1
            else:
                out_kwargs[spec.arg_name] = spec.default_val
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
                return 1
            out_kwargs[spec.arg_name] = parsed_int

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
                return 1
            out_kwargs[spec.arg_name] = parsed_float

        else:
            val_err = bt_validate_str_checked(
                raw_slice,
                spec.has_min_len, spec.min_len,
                spec.has_max_len, spec.max_len
            )
            if val_err != BT_ERR_NONE:
                _write_422_error(spec.source, spec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return 1
            out_kwargs[spec.arg_name] = PyUnicode_DecodeUTF8(
                raw_slice.ptr, <Py_ssize_t>raw_slice.len, "strict"
            )

    return 0


cdef inline void _teardown_generators(list teardown_stack):
    """Unwinds active sync and async generator dependencies in LIFO order."""
    cdef Py_ssize_t idx = len(teardown_stack) - 1
    cdef tuple entry
    cdef int kind
    cdef object gen_obj
    cdef object anext_coro

    while idx >= 0:
        entry = <tuple>(teardown_stack[idx])
        kind = <int>(entry[0])
        gen_obj = entry[1]
        try:
            if kind == DEP_SYNC_GEN:
                try:
                    next(gen_obj)
                except StopIteration:
                    pass
                finally:
                    gen_obj.close()
            elif kind == DEP_ASYNC_GEN:
                anext_coro = gen_obj.__anext__()
                try:
                    anext_coro.send(None)
                except (StopAsyncIteration, StopIteration):
                    pass
                finally:
                    anext_coro.close()
                    aclose_coro = gen_obj.aclose()
                    _step_coroutine_sync(aclose_coro)
        except Exception:
            pass
        idx -= 1


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
    cdef int d_idx
    cdef CompiledDepStep dep_step
    cdef dict dep_kwargs
    cdef dict kwargs
    cdef list dep_slots = None
    cdef list teardown_stack = None
    cdef object dep_obj
    cdef object anext_coro

    try:
        if entry.num_dep_steps > 0:
            dep_slots = [None] * entry.num_dep_steps
            teardown_stack = []

            for d_idx in range(entry.num_dep_steps):
                dep_step = <CompiledDepStep>(entry.dep_steps[d_idx])
                if dep_step.num_params == 0:
                    if dep_step.dep_kind == DEP_SYNC_FUNC:
                        dep_slots[dep_step.slot_index] = dep_step.fn()
                    elif dep_step.dep_kind == DEP_CORO_FUNC:
                        dep_slots[dep_step.slot_index] = _step_coroutine_sync(dep_step.fn())
                    elif dep_step.dep_kind == DEP_SYNC_GEN:
                        dep_obj = dep_step.fn()
                        teardown_stack.append((DEP_SYNC_GEN, dep_obj))
                        dep_slots[dep_step.slot_index] = next(dep_obj)
                    elif dep_step.dep_kind == DEP_ASYNC_GEN:
                        dep_obj = dep_step.fn()
                        teardown_stack.append((DEP_ASYNC_GEN, dep_obj))
                        anext_coro = dep_obj.__anext__()
                        dep_slots[dep_step.slot_index] = _step_coroutine_sync(anext_coro)
                else:
                    dep_kwargs = {}
                    if _resolve_kwargs_for_specs(
                        dep_step.param_specs, dep_step.num_params, dep_slots,
                        method, method_len, path, path_len, query, query_len,
                        headers, num_headers, path_params, num_path_params,
                        body, body_len, dep_kwargs,
                        out_body_buf, out_body_len, out_status_code
                    ) != 0:
                        return 0

                    if dep_step.dep_kind == DEP_SYNC_FUNC:
                        dep_slots[dep_step.slot_index] = dep_step.fn(**dep_kwargs)
                    elif dep_step.dep_kind == DEP_CORO_FUNC:
                        dep_slots[dep_step.slot_index] = _step_coroutine_sync(dep_step.fn(**dep_kwargs))
                    elif dep_step.dep_kind == DEP_SYNC_GEN:
                        dep_obj = dep_step.fn(**dep_kwargs)
                        teardown_stack.append((DEP_SYNC_GEN, dep_obj))
                        dep_slots[dep_step.slot_index] = next(dep_obj)
                    elif dep_step.dep_kind == DEP_ASYNC_GEN:
                        dep_obj = dep_step.fn(**dep_kwargs)
                        teardown_stack.append((DEP_ASYNC_GEN, dep_obj))
                        anext_coro = dep_obj.__anext__()
                        dep_slots[dep_step.slot_index] = _step_coroutine_sync(anext_coro)

        if entry.num_params == 0:
            if entry.is_coroutine:
                result = _step_coroutine_sync(entry.fn())
            else:
                result = entry.fn()
        else:
            kwargs = {}
            if _resolve_kwargs_for_specs(
                entry.param_specs, entry.num_params, dep_slots,
                method, method_len, path, path_len, query, query_len,
                headers, num_headers, path_params, num_path_params,
                body, body_len, kwargs,
                out_body_buf, out_body_len, out_status_code
            ) != 0:
                return 0

            if entry.is_coroutine:
                result = _step_coroutine_sync(entry.fn(**kwargs))
            else:
                result = entry.fn(**kwargs)

        out_status_code[0] = entry.default_status_code

        if entry.response_schema_spec is not None:
            return _serialize_schema_to_c_buf(result, entry.response_schema_spec, out_body_buf, out_body_len)

        if hasattr(result, "__bt_schema_spec__"):
            return _serialize_schema_to_c_buf(
                result, <CompiledSchemaSpec>(result.__bt_schema_spec__), out_body_buf, out_body_len
            )

        if isinstance(result, dict):
            return _serialize_dict_to_c_buf(<dict>result, out_body_buf, out_body_len)

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

    except HTTPException as http_exc:
        out_status_code[0] = <int>http_exc.status_code
        return _serialize_dict_to_c_buf({"detail": http_exc.detail}, out_body_buf, out_body_len)
    except Exception:
        return -1
    finally:
        if teardown_stack is not None and len(teardown_stack) > 0:
            _teardown_generators(teardown_stack)


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
        list dep_steps=None,
    ):
        cdef bytes b_method = method.upper().encode("ascii")
        cdef bytes b_path = path.encode("utf-8")
        cdef RouteEntry entry = RouteEntry(
            fn, param_specs or [], response_schema_spec, status_code, dep_steps or []
        )
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

## 7. Step 5: Update `buckthorn/app.py` & `buckthorn/__init__.py`

### 7.1 `buckthorn/app.py` (Compile-Time Topological Sort & DAG Flattener)

```python
# buckthorn/app.py
import inspect
from typing import Any, Callable, Optional, Sequence, Type, get_type_hints
from buckthorn._core import (
    CoreRouter,
    CompiledDepStep,
    CompiledParamSpec,
    Request,
)
from buckthorn.params import DependsInfo, ParamInfo, PathInfo, QueryInfo, _UNDEFINED
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


def _get_callable_target_for_signature(fn: Any) -> Any:
    if isinstance(fn, type):
        return fn.__init__
    if not inspect.isroutine(fn) and callable(fn):
        return fn.__call__
    return fn


def _compile_callable_params(
    path: str,
    target_callable: Callable[..., Any],
    dep_steps: list[CompiledDepStep],
    cache_slot_map: dict[Any, int],
) -> list[CompiledParamSpec]:
    sig_target = _get_callable_target_for_signature(target_callable)
    sig = inspect.signature(sig_target)
    if not sig.parameters:
        return []

    try:
        hints = get_type_hints(sig_target)
    except Exception:
        hints = {}

    path_param_names = _extract_path_param_names(path)
    specs: list[CompiledParamSpec] = []

    for arg_name, param in sig.parameters.items():
        if arg_name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        annotation = hints.get(arg_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = str

        default_obj = param.default

        # 1. Check if parameter is a `Depends(...)` injection
        if isinstance(default_obj, DependsInfo):
            dep_fn = default_obj.dependency
            if dep_fn is None:
                if annotation is inspect.Parameter.empty:
                    raise ValueError(f"Depends() on '{arg_name}' requires a callable or type hint")
                dep_fn = annotation

            slot_idx = _register_dep_step(
                path, dep_fn, default_obj.use_cache, dep_steps, cache_slot_map
            )
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=7,      # SRC_DEPENDS
                    param_type=8,  # TYPE_DEPENDS
                    required=True,
                    dep_slot_index=slot_idx,
                )
            )
            continue

        # 2. Check if parameter is a zero-copy `Request` object
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

        # 3. Check if parameter is a `buckthorn.Schema` JSON body
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

        # 4. Path, Query, Header, or Cookie parameter
        param_type = _TYPE_MAP.get(annotation, 3)

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


def _register_dep_step(
    path: str,
    dep_callable: Callable[..., Any],
    use_cache: bool,
    dep_steps: list[CompiledDepStep],
    cache_slot_map: dict[Any, int],
) -> int:
    if use_cache and dep_callable in cache_slot_map:
        return cache_slot_map[dep_callable]

    # Recursively compile sub-dependencies first (post-order topological sort)
    sub_param_specs = _compile_callable_params(
        path, dep_callable, dep_steps, cache_slot_map
    )
    slot_idx = len(dep_steps)
    step = CompiledDepStep(dep_callable, slot_idx, sub_param_specs)
    dep_steps.append(step)

    if use_cache:
        cache_slot_map[dep_callable] = slot_idx

    return slot_idx


def _compile_route_plan(
    path: str,
    endpoint: Callable[..., Any],
    route_dependencies: Optional[Sequence[DependsInfo]] = None,
) -> tuple[list[CompiledParamSpec], list[CompiledDepStep]]:
    dep_steps: list[CompiledDepStep] = []
    cache_slot_map: dict[Any, int] = {}

    # 1. Compile route-level / app-level `dependencies=[Depends(...)]` guards first
    if route_dependencies:
        for dep_info in route_dependencies:
            if isinstance(dep_info, DependsInfo) and dep_info.dependency is not None:
                _register_dep_step(
                    path,
                    dep_info.dependency,
                    dep_info.use_cache,
                    dep_steps,
                    cache_slot_map,
                )

    # 2. Compile endpoint signature parameters & nested `Depends(...)` DAG
    endpoint_param_specs = _compile_callable_params(
        path, endpoint, dep_steps, cache_slot_map
    )
    return endpoint_param_specs, dep_steps


class Buckthorn:
    def __init__(
        self,
        title: str = "Buckthorn",
        version: str = "0.4.0",
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        self.title = title
        self.version = version
        self.dependencies = list(dependencies) if dependencies else []
        self._core = CoreRouter()

    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        methods: list[str],
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ) -> None:
        combined_deps = list(self.dependencies)
        if dependencies:
            combined_deps.extend(dependencies)

        param_specs, dep_steps = _compile_route_plan(path, endpoint, combined_deps)
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
                dep_steps,
            )

    def get(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["GET"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def post(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["POST"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def put(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["PUT"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def delete(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["DELETE"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self._core.run_server(host, port)

```

### 7.2 `buckthorn/__init__.py`

```python
# buckthorn/__init__.py
from buckthorn.app import Buckthorn
from buckthorn._core import Request
from buckthorn.exceptions import HTTPException
from buckthorn.params import Path, Query, Header, Cookie, Depends
from buckthorn.schema import Schema, Field
from buckthorn import status

__all__ = [
    "Buckthorn",
    "Request",
    "HTTPException",
    "Path",
    "Query",
    "Header",
    "Cookie",
    "Depends",
    "Schema",
    "Field",
    "status",
]

```

---

## 8. Step 6: Execution & Verification Checklist for Gemini Flash

Run these two commands sequentially to recompile the Cython extension and run Phases 1 through 4 TDD test suites:

```bash
# 1. Recompile C + Cython extension
pip install -e ".[dev]" --no-build-isolation

# 2. Run Phase 1, Phase 2, Phase 3, and Phase 4 TDD test suites + Speed Gates
pytest tests/phase1/ tests/phase2/ tests/phase3/ tests/phase4/ -v -s

```