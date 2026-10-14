# Domain 2: Cython Bridge (`_core.pxd` / `_core.pyx`), Freelists & DI Execution

## 1. Scope & Files
- `buckthorn/_core.pxd` — `cdef extern ... nogil` declarations for all headers in `c_vendor/` and `c_src/`.
- `buckthorn/_core.pyx` — Cython extension types, parameter extraction loop, single-pass `yyjson` to `Schema` binder, flattened Dependency Injection runner, and LIFO generator teardown.

---

## 2. `@cython.freelist` Extension Types
To avoid Python heap allocation overhead on per-request objects, the following `cdef class` types use Cython static freelists:
- `@cython.freelist(4096) cdef class Request` — Wraps raw C pointers (`_method`, `_path`, `_query`, `_headers`, `_body`). Instantiated via `Request.__new__(Request)` + `req_obj.bind_c(...)` ONLY if a route or middleware requests `req: Request`.
- `@cython.freelist(1024) cdef class HTTPBasicCredentials` — Holds decoded `username` and `password`.
- `@cython.freelist(1024) cdef class UploadFile` — Wraps `bt_multipart_part_t` C slice (`_data_ptr`, `_data_len`); copies to Python `bytes` lazily only when `await file.read()` is called.
- `@cython.freelist(1024) cdef class WebSocket` — Manages RFC 6455 frame buffers and `_pubsub_registry_ref`.
- `@cython.freelist(1024) cdef class BackgroundTasks` — Collects `(func, args, kwargs, is_coro)` tuples and runs them after response serialization.
- `@cython.freelist(1024) cdef class MiddlewareResponse` — Used only when `@app.middleware("http")` user hooks are registered.

---

## 3. Parameter & Dependency Resolution Pipeline (`_resolve_kwargs_for_specs`)
Each route and dependency callable has its signature pre-compiled at registration time into a tuple of `CompiledParamSpec` objects. At runtime, `_resolve_kwargs_for_specs` loops over `param_specs` by integer `source` ID:
- `SRC_PATH (1)` / `SRC_QUERY (2)` / `SRC_HEADER (3)` / `SRC_COOKIE (4)`: Calls C slice finders (`bt_find_query_param`, etc.) and C validators (`bt_parse_int64_checked`, `bt_parse_double_checked`, `bt_validate_str_checked`). On error, writes a `422` JSON body via `_write_422_error` and short-circuits immediately.
- `SRC_REQUEST (5)`: Pops a `Request` instance from the 4096-slot Cython freelist and binds C pointers.
- `SRC_BODY_SCHEMA (6)`: Calls `_deserialize_schema_from_c_json()`, which parses the request body via `bt_json_parse_obj()`, validates every field in C, and populates the target `Schema` instance via `PyObject_SetAttr`.
- `SRC_DEPENDS (7)`: Reads the pre-computed dependency return value from `dep_slots[spec.dep_slot_index]` in $O(1)$ time.
- `SRC_SECURITY (8)`: Calls `_execute_c_security_spec()` (`bt_jwt_verify_hs256`, `bt_verify_api_key`, or `bt_verify_basic_auth`). Writes `401` or `403` directly on failure.
- `SRC_UPLOAD_FILE (9)`: Calls `bt_multipart_extract_file()` in C and binds the zero-copy slice to `UploadFile`.
- `SRC_BACKGROUND_TASKS (10)`: Allocates a freelist `BackgroundTasks` instance and appends it to `bg_tasks_out`.

---

## 4. Compile-Time Flattened Dependency Injection & LIFO Teardown
1. At route registration (`app.py`), `_compile_route_plan()` performs a post-order traversal of the `Depends()` DAG, deduplicating shared callables when `use_cache=True`, and emits a flat list of `CompiledDepStep` objects.
2. Each `CompiledDepStep` pre-classifies its callable kind into:
   - `DEP_SYNC_FUNC (1)`
   - `DEP_CORO_FUNC (2)`
   - `DEP_SYNC_GEN (3)`
   - `DEP_ASYNC_GEN (4)`
3. During `_execute_route_inner`, Buckthorn allocates `dep_slots = [None] * entry.num_dep_steps` and executes each step linearly.
4. Any sync or async generator dependency (`yield`) is appended to `teardown_stack`. Inside the `finally:` block of `_execute_route_inner`, `_teardown_generators(teardown_stack)` unwinds all active generators in **reverse (LIFO) order**, even if an `HTTPException` was raised.
