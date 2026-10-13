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
