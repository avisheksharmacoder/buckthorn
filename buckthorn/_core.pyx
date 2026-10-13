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
