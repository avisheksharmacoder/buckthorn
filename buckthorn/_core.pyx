# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, initializedcheck=False, infer_types=True
cimport cython
from libc.string cimport memcpy
from libc.stdio cimport snprintf
from libc.stdint cimport int64_t, uint8_t, uint32_t, uintptr_t
from cpython.ref cimport PyObject
from cpython.bytes cimport PyBytes_AsStringAndSize, PyBytes_FromStringAndSize
from cpython.bytearray cimport PyByteArray_AsString
from cpython.unicode cimport PyUnicode_DecodeUTF8, PyUnicode_AsUTF8AndSize
from cpython.object cimport PyObject_SetAttr, PyObject_GetAttr
import inspect
import asyncio
from asyncio import events
try:
    import uvloop
    _HAS_UVLOOP = True
except ImportError:
    _HAS_UVLOOP = False
import json as _py_json
from buckthorn.exceptions import HTTPException

from buckthorn._core cimport (
    phr_header, bt_arena_t, bt_arena_init, bt_arena_destroy,
    bt_slice_t, bt_val_error_t, BT_ERR_NONE,
    bt_find_query_param, bt_find_header, bt_find_cookie,
    bt_parse_int64_checked, bt_parse_double_checked, bt_validate_str_checked,
    yyjson_val, yyjson_mut_doc, yyjson_mut_val,
    yyjson_is_null, yyjson_is_bool, yyjson_is_int, yyjson_is_num, yyjson_is_str, yyjson_is_arr, yyjson_is_obj,
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
    BT_HANDLER_OK, BT_HANDLER_ERROR, BT_HANDLER_SUSPENDED,
    bt_router_create, bt_router_destroy, bt_router_insert,
    bt_router_set_async_tick_cb, bt_uv_complete_async_request,
    bt_handle_http_bytes_ex,
    bt_handle_http_bytes, bt_uv_server_run,
    bt_logger_t, bt_log_entry_t, bt_log_kv_t,
    BT_LOG_LEVEL_DEBUG, BT_LOG_LEVEL_INFO, BT_LOG_LEVEL_WARN, BT_LOG_LEVEL_ERROR,
    BT_LOG_VAL_INT, BT_LOG_VAL_FLOAT, BT_LOG_VAL_BOOL, BT_LOG_VAL_STR, BT_LOG_MAX_KVS,
    bt_logger_create, bt_logger_flush_and_stop, bt_logger_destroy,
    bt_logger_acquire_slot, bt_logger_publish_slot,
    bt_router_set_access_logger,
    bt_sec_result_t, BT_SEC_OK, BT_SEC_ERR_UNAUTHORIZED, BT_SEC_ERR_FORBIDDEN,
    bt_jwt_verify_hs256, bt_verify_api_key, bt_verify_basic_auth,
    bt_middleware_chain_t, bt_router_get_mw,
    bt_middleware_configure_cors, bt_middleware_configure_ratelimit,
    bt_middleware_configure_trusted_host, bt_middleware_configure_sec_headers,
    BT_WS_OP_TEXT, BT_WS_OP_BIN, BT_WS_OP_CLOSE,
    BT_WS_EVT_OPEN, BT_WS_EVT_MESSAGE, BT_WS_EVT_CLOSE,
    bt_router_set_ws_cb, bt_uv_ws_send_frame, bt_uv_ws_close_client,
    bt_ws_frame_t, bt_multipart_part_t,
    bt_ws_compute_accept, bt_ws_parse_frame, bt_ws_encode_frame,
    bt_multipart_extract_file,
    bt_router_set_max_body_size,
    bt_sanitize_header_str,
    yyjson_obj_iter, yyjson_arr_iter,
    yyjson_obj_iter_init, yyjson_obj_iter_next, yyjson_obj_iter_get_val,
    yyjson_arr_iter_init, yyjson_arr_iter_next,
    PyIter_Send, PySendResult, PYGEN_RETURN, PYGEN_ERROR, PYGEN_NEXT,
    PyUnicode_InternInPlace, Py_DECREF_ptr, PyCoro_CheckExact
)

cdef extern from "c_vendor/yyjson.h" nogil:
    yyjson_val *unsafe_yyjson_get_first(yyjson_val *ctn)
    yyjson_val *unsafe_yyjson_get_next(yyjson_val *val)

# Arena Output Buffer Capacities
DEF BT_MAX_HANDLER_BODY_CAP = 32768
DEF BT_MAX_EXTRA_HDRS_CAP   = 2048

# Parameter Source Constants
DEF SRC_PATH             = 1
DEF SRC_QUERY            = 2
DEF SRC_HEADER           = 3
DEF SRC_COOKIE           = 4
DEF SRC_REQUEST          = 5
DEF SRC_BODY_SCHEMA      = 6
DEF SRC_DEPENDS          = 7
DEF SRC_SECURITY         = 8
DEF SRC_UPLOAD_FILE      = 9
DEF SRC_BACKGROUND_TASKS = 10

# Parameter / Field Type Constants
DEF TYPE_INT              = 1
DEF TYPE_FLOAT            = 2
DEF TYPE_STR              = 3
DEF TYPE_REQUEST          = 4
DEF TYPE_BOOL             = 5
DEF TYPE_LIST             = 6
DEF TYPE_SCHEMA           = 7
DEF TYPE_DEPENDS          = 8
DEF TYPE_SECURITY         = 9
DEF TYPE_UPLOAD_FILE      = 10
DEF TYPE_BACKGROUND_TASKS = 11

DEF SEC_KIND_JWT          = 1
DEF SEC_KIND_APIKEY_HDR   = 2
DEF SEC_KIND_APIKEY_QUERY = 3
DEF SEC_KIND_HTTP_BASIC   = 4

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


cdef object _SHARED_ASYNC_LOOP = None
cdef bint _LOOP_READY = False
cdef int _PENDING_ASYNC_TASKS = 0

cdef inline object _get_or_create_async_loop():
    global _SHARED_ASYNC_LOOP, _LOOP_READY
    if not _LOOP_READY or _SHARED_ASYNC_LOOP is None:
        if _HAS_UVLOOP:
            _SHARED_ASYNC_LOOP = uvloop.new_event_loop()
        else:
            _SHARED_ASYNC_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_SHARED_ASYNC_LOOP)
        if hasattr(asyncio, "eager_task_factory") and hasattr(_SHARED_ASYNC_LOOP, "set_task_factory"):
            try:
                _SHARED_ASYNC_LOOP.set_task_factory(asyncio.eager_task_factory)
            except Exception:
                pass
        _LOOP_READY = True
    return _SHARED_ASYNC_LOOP


cdef object _drive_coro(object coro, object initial_fut, object loop):
    cdef object completion_fut = loop.create_future()
    cdef object cur_fut = initial_fut
    if cur_fut is None or not hasattr(cur_fut, "add_done_callback"):
        cur_fut = loop.create_future()
        loop.call_soon(cur_fut.set_result, None)
    else:
        try:
            setattr(cur_fut, "_asyncio_future_blocking", False)
        except Exception:
            pass

    def on_fut_done(fut):
        cdef object prev_loop = events._get_running_loop()
        if prev_loop is None:
            events._set_running_loop(loop)
        cdef object next_fut, exc
        try:
            exc = fut.exception() if fut is not None else None
            if exc is not None:
                next_fut = coro.throw(exc)
            else:
                next_fut = coro.send(fut.result() if fut is not None else None)
            if next_fut is None or not hasattr(next_fut, "add_done_callback"):
                next_fut = loop.create_future()
                loop.call_soon(next_fut.set_result, None)
            else:
                try:
                    setattr(next_fut, "_asyncio_future_blocking", False)
                except Exception:
                    pass
            next_fut.add_done_callback(on_fut_done)
        except StopIteration as stop:
            if not completion_fut.done():
                completion_fut.set_result(stop.value)
        except BaseException as e:
            if not completion_fut.done():
                completion_fut.set_exception(e)
        finally:
            if prev_loop is None:
                events._set_running_loop(None)

    cur_fut.add_done_callback(on_fut_done)
    return completion_fut


cpdef inline object _step_coroutine_sync(object coro):
    """
    Tier-1 Eager Fast Path using CPython 3.11+ PyIter_Send (zero StopIteration allocation)
    + Tier-2 Event Loop Fallback when an incomplete Future/awaitable is yielded.
    """
    cdef object loop = _get_or_create_async_loop()
    cdef object prev_loop = events._get_running_loop()
    if prev_loop is None:
        events._set_running_loop(loop)

    cdef PyObject *res_ptr = NULL
    cdef PySendResult send_rc
    cdef object first_yielded
    try:
        send_rc = PyIter_Send(coro, None, &res_ptr)
        if send_rc == PYGEN_RETURN:
            first_yielded = <object>res_ptr
            Py_DECREF_ptr(res_ptr)
            return first_yielded
        elif send_rc == PYGEN_ERROR:
            coro.close()
            raise
        first_yielded = <object>res_ptr
        Py_DECREF_ptr(res_ptr)
    finally:
        if prev_loop is None:
            events._set_running_loop(None)

    cdef object completion_fut = _drive_coro(coro, first_yielded, loop)
    try:
        return loop.run_until_complete(completion_fut)
    finally:
        coro.close()


async def _step_coroutine_async(object coro, object loop):
    """Asynchronous coroutine driver for execution contexts inside a running event loop."""
    cdef object prev_loop = events._get_running_loop()
    if prev_loop is None:
        events._set_running_loop(loop)

    cdef PyObject *res_ptr = NULL
    cdef PySendResult send_rc
    cdef object first_yielded
    try:
        send_rc = PyIter_Send(coro, None, &res_ptr)
        if send_rc == PYGEN_RETURN:
            first_yielded = <object>res_ptr
            Py_DECREF_ptr(res_ptr)
            return first_yielded
        elif send_rc == PYGEN_ERROR:
            coro.close()
            raise
        first_yielded = <object>res_ptr
        Py_DECREF_ptr(res_ptr)
    finally:
        if prev_loop is None:
            events._set_running_loop(None)

    cdef object completion_fut = _drive_coro(coro, first_yielded, loop)
    try:
        return await completion_fut
    finally:
        coro.close()


cdef int _cython_async_loop_tick() noexcept with gil:
    global _PENDING_ASYNC_TASKS, _SHARED_ASYNC_LOOP
    if _PENDING_ASYNC_TASKS <= 0 or _SHARED_ASYNC_LOOP is None:
        return 0
    try:
        _SHARED_ASYNC_LOOP.call_soon(_SHARED_ASYNC_LOOP.stop)
        _SHARED_ASYNC_LOOP.run_forever()
    except Exception:
        pass
    return _PENDING_ASYNC_TASKS


cdef inline int _safe_copy_to_body_buf(
    const char *src,
    Py_ssize_t src_len,
    char *out_body_buf,
    size_t *out_body_len
) noexcept nogil:
    if src_len < 0 or <size_t>src_len > BT_MAX_HANDLER_BODY_CAP:
        return -1
    if src_len > 0 and src != NULL:
        memcpy(out_body_buf, src, <size_t>src_len)
    out_body_len[0] = <size_t>src_len
    return 0


cdef void _flush_suspended_tcp_coro(
    uintptr_t client_addr,
    RouteEntry entry,
    object result,
    object caught_exc
):
    """Synchronous C stack helper so 34KB wire buffers are not stored inside heap coroutine frames."""
    cdef char body_buf[32768]
    cdef size_t body_len = 0
    cdef char extra_hdrs[2048]
    cdef size_t extra_hdrs_len = 0
    cdef int status_code = entry.default_status_code
    cdef char *raw_ptr = NULL
    cdef Py_ssize_t raw_len = 0
    cdef object schema_spec_attr

    if caught_exc is not None:
        if isinstance(caught_exc, HTTPException):
            status_code = <int>caught_exc.status_code
            _serialize_http_exception(caught_exc.detail, body_buf, &body_len)
        else:
            status_code = 500
            memcpy(body_buf, b"Internal Server Error", 21)
            body_len = 21
    else:
        try:
            if entry.response_schema_spec is not None:
                if _serialize_schema_to_c_buf(result, entry.response_schema_spec, body_buf, &body_len) != 0:
                    status_code = 500
            elif isinstance(result, dict):
                if _serialize_dict_to_c_buf(<dict>result, body_buf, &body_len) != 0:
                    status_code = 500
            elif isinstance(result, bytes):
                PyBytes_AsStringAndSize(result, &raw_ptr, &raw_len)
                if _safe_copy_to_body_buf(raw_ptr, raw_len, body_buf, &body_len) != 0:
                    status_code = 500
            elif isinstance(result, str):
                raw_ptr = <char *>PyUnicode_AsUTF8AndSize(result, &raw_len)
                if _safe_copy_to_body_buf(raw_ptr, raw_len, body_buf, &body_len) != 0:
                    status_code = 500
            else:
                schema_spec_attr = getattr(result, "__bt_schema_spec__", None)
                if schema_spec_attr is not None:
                    if _serialize_schema_to_c_buf(
                        result, <CompiledSchemaSpec>schema_spec_attr, body_buf, &body_len
                    ) != 0:
                        status_code = 500
        except Exception:
            status_code = 500
            memcpy(body_buf, b"Internal Server Error", 21)
            body_len = 21

    bt_uv_complete_async_request(
        <void *>client_addr, body_buf, body_len, extra_hdrs, extra_hdrs_len, status_code
    )


cdef void _schedule_suspended_tcp_coro(
    object coro,
    object first_yielded,
    RouteEntry entry,
    void *async_client_ctx,
    list teardown_stack,
    list bg_tasks_list
):
    global _PENDING_ASYNC_TASKS
    cdef uintptr_t client_addr = <uintptr_t>async_client_ctx
    cdef object loop = _get_or_create_async_loop()
    _PENDING_ASYNC_TASKS += 1

    async def _drive_and_flush():
        global _PENDING_ASYNC_TASKS
        cdef BackgroundTasks bg_runner
        cdef object result = None, caught_exc = None, completion_fut = None
        cdef bint is_ok = True

        try:
            completion_fut = _drive_coro(coro, first_yielded, loop)
            result = await completion_fut
        except BaseException as exc:
            caught_exc = exc
            is_ok = False
        finally:
            coro.close()
            if teardown_stack is not None and len(teardown_stack) > 0:
                await _teardown_generators_async(teardown_stack, loop)
            if bg_tasks_list is not None and len(bg_tasks_list) > 0 and is_ok:
                for bg_runner in bg_tasks_list:
                    await bg_runner._execute_all_async(loop)
            _PENDING_ASYNC_TASKS -= 1
            _flush_suspended_tcp_coro(client_addr, entry, result, caught_exc)

    loop.create_task(_drive_and_flush())


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
        cdef Py_ssize_t k_len = 0
        cdef const char *k_ptr = PyUnicode_AsUTF8AndSize(name, &k_len)
        cdef bt_slice_t val
        if k_ptr != NULL and bt_find_header(self._headers, self._num_headers, k_ptr, <size_t>k_len, &val):
            return PyUnicode_DecodeUTF8(val.ptr, <Py_ssize_t>val.len, "replace")
        return None

    cpdef object get_query(self, str name):
        cdef Py_ssize_t k_len = 0
        cdef const char *k_ptr = PyUnicode_AsUTF8AndSize(name, &k_len)
        cdef bt_slice_t val
        if k_ptr != NULL and bt_find_query_param(self._query, self._query_len, k_ptr, <size_t>k_len, &val):
            return PyUnicode_DecodeUTF8(val.ptr, <Py_ssize_t>val.len, "replace")
        return None


@cython.freelist(1024)
cdef class HTTPBasicCredentials:
    cdef public str username
    cdef public str password

    def __cinit__(self, str username="", str password=""):
        self.username = username
        self.password = password


@cython.freelist(1024)
cdef class UploadFile:
    """Zero-copy Cython wrapper around a multipart file slice."""
    cdef public str filename
    cdef public str content_type
    cdef const char *_data_ptr
    cdef size_t _data_len
    cdef bytes _cached_bytes

    def __cinit__(self, str filename="", str content_type="application/octet-stream"):
        self.filename = filename
        self.content_type = content_type
        self._data_ptr = NULL
        self._data_len = 0
        self._cached_bytes = None

    cdef inline void bind_slice(self, const char *ptr, size_t length) noexcept nogil:
        self._data_ptr = ptr
        self._data_len = length

    @property
    def size(self) -> int:
        return <int>self._data_len

    cpdef bytes read_sync(self):
        if self._cached_bytes is None:
            if self._data_ptr == NULL or self._data_len == 0:
                self._cached_bytes = b""
            else:
                self._cached_bytes = PyBytes_FromStringAndSize(
                    self._data_ptr, <Py_ssize_t>self._data_len
                )
        return self._cached_bytes

    async def read(self) -> bytes:
        return self.read_sync()


@cython.freelist(1024)
cdef class WebSocket:
    """High-speed C-backed RFC 6455 WebSocket session & Pub/Sub handle (TestClient + Live TCP)."""
    cdef public str path
    cdef public bint accepted
    cdef public bint closed
    cdef public str accept_key
    cdef public list _in_frames
    cdef public list _out_frames
    cdef public dict _pubsub_registry_ref
    cdef uintptr_t _uv_client_ptr
    cdef object _rx_waiter

    def __cinit__(self, str path="", dict pubsub_registry=None):
        self.path = path
        self.accepted = False
        self.closed = False
        self.accept_key = ""
        self._in_frames = []
        self._out_frames = []
        self._pubsub_registry_ref = pubsub_registry
        self._uv_client_ptr = 0
        self._rx_waiter = None

    cdef inline void bind_uv_client(self, void *uv_client_ctx) noexcept nogil:
        self._uv_client_ptr = <uintptr_t>uv_client_ctx

    cdef inline void _deliver_live_message(self, str text):
        if self._rx_waiter is not None and not self._rx_waiter.done():
            waiter = self._rx_waiter
            self._rx_waiter = None
            waiter.set_result(text)
        else:
            self._in_frames.append(text)

    cdef inline void _deliver_live_close(self):
        self.closed = True
        if self._rx_waiter is not None and not self._rx_waiter.done():
            waiter = self._rx_waiter
            self._rx_waiter = None
            waiter.set_exception(RuntimeError("WebSocket connection closed by peer"))

    cpdef str compute_handshake(self, str sec_websocket_key):
        cdef Py_ssize_t k_len = 0
        cdef const char *k_ptr = PyUnicode_AsUTF8AndSize(sec_websocket_key, &k_len)
        cdef char out_accept[64]
        cdef size_t out_len = 0
        if k_ptr == NULL or bt_ws_compute_accept(k_ptr, <size_t>k_len, out_accept, sizeof(out_accept), &out_len) != 0:
            raise ValueError("Invalid Sec-WebSocket-Key")
        self.accept_key = PyUnicode_DecodeUTF8(out_accept, <Py_ssize_t>out_len, "strict")
        return self.accept_key

    async def accept(self):
        self.accepted = True

    cpdef void subscribe(self, str topic):
        if self._pubsub_registry_ref is not None:
            subs = self._pubsub_registry_ref.setdefault(topic, [])
            if self not in subs:
                subs.append(self)

    cpdef void feed_raw_frame(self, bytes raw_frame):
        """Parses and unmasks a raw client RFC 6455 frame in C using 64-bit XOR."""
        cdef char stack_buf[4096]
        cdef char *buf_ptr = NULL
        cdef Py_ssize_t raw_len = 0
        PyBytes_AsStringAndSize(raw_frame, &buf_ptr, &raw_len)
        if raw_len > 4096:
            mut_buf = bytearray(raw_frame)
            buf_ptr = PyByteArray_AsString(mut_buf)
        else:
            memcpy(stack_buf, buf_ptr, <size_t>raw_len)
            buf_ptr = stack_buf

        cdef bt_ws_frame_t parsed
        if bt_ws_parse_frame(buf_ptr, <size_t>raw_len, &parsed) != 0:
            raise ValueError("Malformed RFC 6455 WebSocket frame")
        if parsed.opcode == BT_WS_OP_CLOSE:
            self._deliver_live_close()
            return
        cdef str text = PyUnicode_DecodeUTF8(
            parsed.payload_ptr, <Py_ssize_t>parsed.payload_len, "strict"
        )
        self._deliver_live_message(text)

    async def receive_text(self) -> str:
        if self._in_frames:
            return self._in_frames.pop(0)
        if self.closed:
            raise RuntimeError("WebSocket connection is closed")
        if self._uv_client_ptr != 0:
            loop = _get_or_create_async_loop()
            self._rx_waiter = loop.create_future()
            return await self._rx_waiter
        raise RuntimeError("No WebSocket frame available")

    cpdef bytes _encode_server_frame(self, str text):
        cdef Py_ssize_t s_len = 0
        cdef const char *s_ptr = PyUnicode_AsUTF8AndSize(text, &s_len)
        cdef char frame_buf[16384]
        cdef size_t written = bt_ws_encode_frame(
            BT_WS_OP_TEXT, s_ptr, <size_t>s_len, 0, frame_buf, sizeof(frame_buf)
        )
        return PyBytes_FromStringAndSize(frame_buf, <Py_ssize_t>written)

    async def send_text(self, str data):
        cdef Py_ssize_t s_len = 0
        cdef const char *s_ptr = NULL
        cdef char frame_buf[16384]
        cdef size_t written = 0
        if self._uv_client_ptr != 0:
            s_ptr = PyUnicode_AsUTF8AndSize(data, &s_len)
            written = bt_ws_encode_frame(
                BT_WS_OP_TEXT, s_ptr, <size_t>s_len, 0, frame_buf, sizeof(frame_buf)
            )
            if written > 0:
                bt_uv_ws_send_frame(<void *>self._uv_client_ptr, frame_buf, written)
        else:
            self._out_frames.append(self._encode_server_frame(data))

    cpdef void push_published_frame(self, bytes encoded_frame):
        cdef char *f_ptr = NULL
        cdef Py_ssize_t f_len = 0
        if self._uv_client_ptr != 0:
            PyBytes_AsStringAndSize(encoded_frame, &f_ptr, &f_len)
            bt_uv_ws_send_frame(<void *>self._uv_client_ptr, f_ptr, <size_t>f_len)
        else:
            self._out_frames.append(encoded_frame)

    cpdef str pop_outgoing_text(self):
        if not self._out_frames:
            raise RuntimeError("No outgoing WebSocket frame")
        cdef bytes raw = self._out_frames.pop(0)
        cdef char stack_buf[4096]
        cdef char *buf_ptr = NULL
        cdef Py_ssize_t raw_len = 0
        PyBytes_AsStringAndSize(raw, &buf_ptr, &raw_len)
        if raw_len > 4096:
            mut_buf = bytearray(raw)
            buf_ptr = PyByteArray_AsString(mut_buf)
        else:
            memcpy(stack_buf, buf_ptr, <size_t>raw_len)
            buf_ptr = stack_buf

        cdef bt_ws_frame_t parsed
        bt_ws_parse_frame(buf_ptr, <size_t>raw_len, &parsed)
        return PyUnicode_DecodeUTF8(parsed.payload_ptr, <Py_ssize_t>parsed.payload_len, "strict")

    async def close(self, int code=1000):
        self.closed = True
        if self._uv_client_ptr != 0:
            bt_uv_ws_close_client(<void *>self._uv_client_ptr)
            self._uv_client_ptr = 0


cpdef bytes encode_client_masked_ws_frame(str text):
    """Encodes a client-to-server masked RFC 6455 frame in C for testing."""
    cdef Py_ssize_t s_len = 0
    cdef const char *s_ptr = PyUnicode_AsUTF8AndSize(text, &s_len)
    cdef char frame_buf[16384]
    cdef size_t written = bt_ws_encode_frame(
        BT_WS_OP_TEXT, s_ptr, <size_t>s_len, 1, frame_buf, sizeof(frame_buf)
    )
    return PyBytes_FromStringAndSize(frame_buf, <Py_ssize_t>written)


cpdef bytes c_ws_encode_frame(int opcode, bytes payload, bint mask_frame=False):
    """Encodes an arbitrary RFC 6455 frame in C supporting 16-bit and 64-bit lengths."""
    cdef Py_ssize_t p_len = 0
    cdef char *p_ptr = NULL
    PyBytes_AsStringAndSize(payload, &p_ptr, &p_len)
    cdef size_t ext_hdr = 8 if <size_t>p_len > 65535 else (2 if <size_t>p_len >= 126 else 0)
    cdef size_t out_cap = 2 + ext_hdr + (4 if mask_frame else 0) + <size_t>p_len + 64
    cdef bytearray out_ba = bytearray(out_cap)
    cdef char *out_ptr = PyByteArray_AsString(out_ba)
    cdef size_t written = bt_ws_encode_frame(
        <uint8_t>opcode, p_ptr, <size_t>p_len, 1 if mask_frame else 0, out_ptr, out_cap
    )
    return bytes(out_ba[:written])


cpdef tuple c_ws_parse_frame(bytes raw):
    """Parses an RFC 6455 frame in C and returns (status, opcode, fin, payload)."""
    cdef bytearray mut_buf = bytearray(raw)
    cdef char *buf_ptr = PyByteArray_AsString(mut_buf)
    cdef bt_ws_frame_t parsed
    cdef int rc = bt_ws_parse_frame(buf_ptr, len(raw), &parsed)
    if rc != 0:
        return (rc, 0, 0, b"")
    cdef bytes payload = PyBytes_FromStringAndSize(parsed.payload_ptr, <Py_ssize_t>parsed.payload_len)
    return (0, parsed.opcode, parsed.fin, payload)

WS_OP_TEXT = BT_WS_OP_TEXT
WS_OP_BIN = BT_WS_OP_BIN


@cython.freelist(1024)
cdef class BackgroundTasks:
    """Zero-overhead Cython freelist queue for post-response background tasks."""
    cdef public list _tasks

    def __cinit__(self):
        self._tasks = []

    def add_task(self, object func, *args, **kwargs):
        self._tasks.append((func, args, kwargs if kwargs else None))

    cpdef void _execute_all(self):
        cdef tuple task_entry
        cdef object fn, args, kwargs, res
        for task_entry in self._tasks:
            fn = task_entry[0]
            args = task_entry[1]
            kwargs = task_entry[2]
            if kwargs is None:
                res = fn(*args)
            else:
                res = fn(*args, **kwargs)
            if PyCoro_CheckExact(res) or inspect.iscoroutine(res):
                _step_coroutine_sync(res)

    async def _execute_all_async(self, object loop):
        cdef tuple task_entry
        cdef object fn, args, kwargs, res
        for task_entry in self._tasks:
            fn = task_entry[0]
            args = task_entry[1]
            kwargs = task_entry[2]
            if kwargs is None:
                res = fn(*args)
            else:
                res = fn(*args, **kwargs)
            if PyCoro_CheckExact(res) or inspect.iscoroutine(res):
                await _step_coroutine_async(res, loop)


cdef class CompiledSecuritySpec:
    cdef public int sec_kind
    cdef public bytes b_secret
    cdef const char *_secret_ptr
    cdef size_t _secret_len

    cdef public bytes b_key_name
    cdef const char *_key_name_ptr
    cdef size_t _key_name_len

    cdef public bytes b_expected_val
    cdef const char *_expected_val_ptr
    cdef size_t _expected_val_len

    cdef public bytes b_expected_pass
    cdef const char *_expected_pass_ptr
    cdef size_t _expected_pass_len

    cdef public list _scope_bytes_refs
    cdef const char *_scope_ptrs[8]
    cdef size_t _scope_lens[8]
    cdef size_t _num_scopes

    def __cinit__(
        self,
        int sec_kind,
        str secret="",
        str key_name="",
        str expected_val="",
        str expected_pass="",
        list scopes=None,
    ):
        cdef char *ptr = NULL
        cdef Py_ssize_t length = 0
        self.sec_kind = sec_kind

        self.b_secret = secret.encode("utf-8")
        PyBytes_AsStringAndSize(self.b_secret, &ptr, &length)
        self._secret_ptr = <const char *>ptr
        self._secret_len = <size_t>length

        self.b_key_name = key_name.encode("utf-8")
        PyBytes_AsStringAndSize(self.b_key_name, &ptr, &length)
        self._key_name_ptr = <const char *>ptr
        self._key_name_len = <size_t>length

        self.b_expected_val = expected_val.encode("utf-8")
        PyBytes_AsStringAndSize(self.b_expected_val, &ptr, &length)
        self._expected_val_ptr = <const char *>ptr
        self._expected_val_len = <size_t>length

        self.b_expected_pass = expected_pass.encode("utf-8")
        PyBytes_AsStringAndSize(self.b_expected_pass, &ptr, &length)
        self._expected_pass_ptr = <const char *>ptr
        self._expected_pass_len = <size_t>length

        self._scope_bytes_refs = []
        self._num_scopes = 0
        cdef str sc
        cdef bytes b_sc
        if scopes:
            for sc in scopes:
                if self._num_scopes >= 8:
                    break
                b_sc = sc.encode("utf-8")
                self._scope_bytes_refs.append(b_sc)
                PyBytes_AsStringAndSize(b_sc, &ptr, &length)
                self._scope_ptrs[self._num_scopes] = <const char *>ptr
                self._scope_lens[self._num_scopes] = <size_t>length
                self._num_scopes += 1


cdef class CompiledSchemaSpec:
    cdef public object schema_cls
    cdef public tuple fields
    cdef public int num_fields

    def __cinit__(self, object schema_cls, list fields):
        self.schema_cls = schema_cls
        self.fields = tuple(fields)
        self.num_fields = len(fields)


cdef class CompiledFieldSpec:
    cdef public str name
    cdef public bytes b_name
    cdef const char *_key_ptr
    cdef size_t _key_len
    cdef public int field_type
    cdef public bint required
    cdef public object default_val
    cdef public bint is_list_default
    cdef public CompiledSchemaSpec sub_schema

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
        CompiledSchemaSpec sub_schema=None,
    ):
        cdef PyObject *o_name = <PyObject *>name
        PyUnicode_InternInPlace(&o_name)
        self.name = <str>o_name
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
        self.sub_schema = sub_schema

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


cdef class CompiledParamSpec:
    cdef public str arg_name
    cdef public bytes lookup_key
    cdef const char *_key_ptr
    cdef size_t _key_len
    cdef public int source       # SRC_PATH..SRC_BACKGROUND_TASKS
    cdef public int param_type   # TYPE_INT..TYPE_BACKGROUND_TASKS
    cdef public int path_index
    cdef public int dep_slot_index
    cdef public bint required
    cdef public object default_val
    cdef public CompiledSchemaSpec schema_spec
    cdef public CompiledSecuritySpec security_spec

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
        CompiledSecuritySpec security_spec=None,
    ):
        cdef PyObject *o_arg = <PyObject *>arg_name
        PyUnicode_InternInPlace(&o_arg)
        self.arg_name = <str>o_arg
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
        self.security_spec = security_spec

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


@cython.freelist(1024)
cdef class MiddlewareResponse:
    cdef public int status_code
    cdef public bytes body
    cdef public dict headers

    def __cinit__(self, int status_code=200, bytes body=b"", dict headers=None):
        self.status_code = status_code
        self.body = body
        self.headers = headers if headers is not None else {}


cdef class RouteEntry:
    cdef public object fn
    cdef public bint is_coroutine
    cdef public tuple param_specs
    cdef public int num_params
    cdef public tuple dep_steps
    cdef public int num_dep_steps
    cdef public CompiledSchemaSpec response_schema_spec
    cdef public int default_status_code
    cdef public list user_middlewares
    cdef public bint has_bg_tasks

    def __cinit__(
        self,
        object fn,
        list param_specs,
        CompiledSchemaSpec response_schema_spec=None,
        int default_status_code=200,
        list dep_steps=None,
        list user_middlewares=None,
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
        self.user_middlewares = user_middlewares

        self.has_bg_tasks = False
        cdef CompiledParamSpec p_spec
        cdef CompiledDepStep d_step
        for p_spec in param_specs or []:
            if p_spec.source == SRC_BACKGROUND_TASKS:
                self.has_bg_tasks = True
                break
        if not self.has_bg_tasks and dep_steps:
            for d_step in dep_steps:
                for p_spec in d_step.param_specs:
                    if p_spec.source == SRC_BACKGROUND_TASKS:
                        self.has_bg_tasks = True
                        break
                if self.has_bg_tasks:
                    break


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
        '{"detail":[{"loc":["%s","%.128s"],"msg":"validation_error","type":"value_error"}]}',
        loc, key_ptr
    )
    if written < 0:
        written = 0
    elif written >= 1024:
        written = 1023
    out_body_len[0] = <size_t>written
    out_status_code[0] = 422


cdef object _yyjson_val_to_py(yyjson_val *val, int depth=0):
    if depth > 32:
        raise ValueError("JSON nesting depth limit (32) exceeded")
    if val == NULL or yyjson_is_null(val):
        return None
    elif yyjson_is_bool(val):
        return bool(yyjson_get_bool(val))
    elif yyjson_is_int(val):
        return yyjson_get_sint(val)
    elif yyjson_is_num(val):
        return yyjson_get_num(val)
    elif yyjson_is_str(val):
        return PyUnicode_DecodeUTF8(yyjson_get_str(val), <Py_ssize_t>yyjson_get_len(val), "strict")
    elif yyjson_is_arr(val):
        return _yyjson_arr_to_list(val, depth + 1)
    elif yyjson_is_obj(val):
        return _yyjson_obj_to_dict(val, depth + 1)
    return None


cdef dict _yyjson_obj_to_dict(yyjson_val *obj, int depth=0):
    """Converts a C yyjson object directly to a Python dict (eliminates Python json.loads)."""
    if depth > 32:
        raise ValueError("JSON nesting depth limit (32) exceeded")
    cdef dict res = {}
    if obj == NULL:
        return res
    cdef yyjson_obj_iter iter
    if not yyjson_obj_iter_init(obj, &iter):
        return res
    cdef yyjson_val *key
    cdef yyjson_val *val
    cdef object py_key, py_val
    while True:
        key = yyjson_obj_iter_next(&iter)
        if key == NULL:
            break
        val = yyjson_obj_iter_get_val(key)
        py_key = PyUnicode_DecodeUTF8(yyjson_get_str(key), <Py_ssize_t>yyjson_get_len(key), "strict")
        py_val = _yyjson_val_to_py(val, depth + 1)
        res[py_key] = py_val
    return res


cdef list _yyjson_arr_to_list(yyjson_val *arr, int depth=0):
    if depth > 32:
        raise ValueError("JSON nesting depth limit (32) exceeded")
    cdef list res = []
    if arr == NULL:
        return res
    cdef yyjson_arr_iter iter
    if not yyjson_arr_iter_init(arr, &iter):
        return res
    cdef yyjson_val *elem
    while True:
        elem = yyjson_arr_iter_next(&iter)
        if elem == NULL:
            break
        res.append(_yyjson_val_to_py(elem, depth + 1))
    return res


cdef list _parse_yyjson_array(yyjson_val *arr_val, int depth=0):
    return _yyjson_arr_to_list(arr_val, depth)


cdef object _deserialize_schema_from_jdoc(
    CompiledSchemaSpec schema_spec,
    bt_json_doc_t *jdoc,
    char *out_body_buf,
    size_t *out_body_len,
    int *out_status_code,
    int depth
):
    if depth > 16:
        _write_422_error(SRC_BODY_SCHEMA, "__depth__", out_body_buf, out_body_len, out_status_code)
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
    cdef yyjson_val *elem = NULL
    cdef size_t arr_n = 0, arr_idx = 0
    cdef list sub_list
    cdef bt_json_doc_t sub_doc
    cdef object py_val, sub_inst

    for i in range(schema_spec.num_fields):
        fspec = <CompiledFieldSpec>(schema_spec.fields[i])
        found = 0

        if fspec.field_type == TYPE_INT:
            err = bt_json_get_int64_checked(
                jdoc, fspec._key_ptr, fspec._key_len,
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
                PyObject_SetAttr(instance, fspec.name, v_int)
                continue

        elif fspec.field_type == TYPE_FLOAT:
            err = bt_json_get_double_checked(
                jdoc, fspec._key_ptr, fspec._key_len,
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
                PyObject_SetAttr(instance, fspec.name, v_flt)
                continue

        elif fspec.field_type == TYPE_BOOL:
            err = bt_json_get_bool_checked(jdoc, fspec._key_ptr, fspec._key_len, &found, &v_bool)
            if err != BT_ERR_NONE:
                _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return None
            if found:
                PyObject_SetAttr(instance, fspec.name, True if v_bool else False)
                continue

        elif fspec.field_type == TYPE_STR:
            err = bt_json_get_str_checked(
                jdoc, fspec._key_ptr, fspec._key_len,
                fspec.has_min_len, fspec.min_len,
                fspec.has_max_len, fspec.max_len,
                &found, &v_str, &v_str_len
            )
            if err != BT_ERR_NONE:
                _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return None
            if found:
                py_val = PyUnicode_DecodeUTF8(v_str, <Py_ssize_t>v_str_len, "strict")
                PyObject_SetAttr(instance, fspec.name, py_val)
                continue

        elif fspec.field_type == TYPE_SCHEMA:
            raw_val = bt_json_get_raw_field(jdoc, fspec._key_ptr, fspec._key_len, &found)
            if found:
                if not yyjson_is_obj(raw_val) or fspec.sub_schema is None:
                    _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                    return None
                sub_doc.doc = jdoc.doc
                sub_doc.root = raw_val
                sub_inst = _deserialize_schema_from_jdoc(
                    fspec.sub_schema, &sub_doc, out_body_buf, out_body_len, out_status_code, depth + 1
                )
                if sub_inst is None:
                    return None
                PyObject_SetAttr(instance, fspec.name, sub_inst)
                continue

        elif fspec.field_type == TYPE_LIST:
            raw_val = bt_json_get_raw_field(jdoc, fspec._key_ptr, fspec._key_len, &found)
            if found:
                if not yyjson_is_arr(raw_val):
                    _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                    return None
                if fspec.sub_schema is not None:
                    arr_n = yyjson_get_len(raw_val)
                    sub_list = []
                    if arr_n > 0:
                        elem = yyjson_arr_get_first(raw_val)
                        for arr_idx in range(arr_n):
                            if not yyjson_is_obj(elem):
                                _write_422_error(
                                    SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code
                                )
                                return None
                            sub_doc.doc = jdoc.doc
                            sub_doc.root = elem
                            sub_inst = _deserialize_schema_from_jdoc(
                                fspec.sub_schema, &sub_doc, out_body_buf, out_body_len, out_status_code, depth + 1
                            )
                            if sub_inst is None:
                                return None
                            sub_list.append(sub_inst)
                            elem = unsafe_yyjson_get_next(elem)
                    PyObject_SetAttr(instance, fspec.name, sub_list)
                else:
                    PyObject_SetAttr(instance, fspec.name, _parse_yyjson_array(raw_val))
                continue

        if fspec.required:
            _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
            return None
        if fspec.is_list_default:
            PyObject_SetAttr(instance, fspec.name, list(fspec.default_val))
        else:
            PyObject_SetAttr(instance, fspec.name, fspec.default_val)

    return instance


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
    return _deserialize_schema_from_jdoc(
        schema_spec, &jdoc, out_body_buf, out_body_len, out_status_code, 0
    )


cdef yyjson_mut_val *_build_schema_mut_obj(
    yyjson_mut_doc *doc,
    object obj,
    CompiledSchemaSpec schema_spec,
    int depth
):
    if depth > 16 or obj is None:
        return yyjson_mut_null(doc)

    cdef yyjson_mut_val *obj_node = yyjson_mut_obj(doc)
    cdef int i
    cdef CompiledFieldSpec fspec
    cdef object val
    cdef bint is_dict = isinstance(obj, dict)
    cdef yyjson_mut_val *k_node
    cdef yyjson_mut_val *v_node

    for i in range(schema_spec.num_fields):
        fspec = <CompiledFieldSpec>(schema_spec.fields[i])
        if is_dict:
            val = (<dict>obj).get(fspec.name, fspec.default_val)
        else:
            val = PyObject_GetAttr(obj, fspec.name)
        k_node = yyjson_mut_strn(doc, fspec._key_ptr, fspec._key_len)
        v_node = _py_val_to_mut_val(doc, val, fspec.sub_schema, depth + 1)
        yyjson_mut_obj_add(obj_node, k_node, v_node)

    return obj_node


cdef yyjson_mut_val *_py_val_to_mut_val(
    yyjson_mut_doc *doc,
    object val,
    CompiledSchemaSpec sub_schema,
    int depth
):
    if depth > 16 or val is None:
        return yyjson_mut_null(doc)

    cdef const char *s_ptr = NULL
    cdef Py_ssize_t s_len = 0
    cdef yyjson_mut_val *arr_node
    cdef yyjson_mut_val *obj_node
    cdef object item, k, v
    cdef object dyn_spec
    cdef str s_val

    if sub_schema is not None and not isinstance(val, list):
        return _build_schema_mut_obj(doc, val, sub_schema, depth)

    if isinstance(val, bool):
        return yyjson_mut_bool(doc, 1 if val else 0)
    if isinstance(val, int):
        if -9223372036854775808 <= val <= 9223372036854775807:
            return yyjson_mut_sint(doc, <int64_t>val)
        s_val = str(val)
        s_ptr = PyUnicode_AsUTF8AndSize(s_val, &s_len)
        return yyjson_mut_strn(doc, s_ptr, <size_t>s_len)
    if isinstance(val, float):
        return yyjson_mut_real(doc, <double>val)
    if isinstance(val, str):
        s_ptr = PyUnicode_AsUTF8AndSize(val, &s_len)
        return yyjson_mut_strn(doc, s_ptr, <size_t>s_len)
    if isinstance(val, list):
        arr_node = yyjson_mut_arr(doc)
        for item in (<list>val):
            if sub_schema is not None and item is not None:
                yyjson_mut_arr_append(arr_node, _build_schema_mut_obj(doc, item, sub_schema, depth + 1))
            else:
                yyjson_mut_arr_append(arr_node, _py_val_to_mut_val(doc, item, None, depth + 1))
        return arr_node

    dyn_spec = getattr(val, "__bt_schema_spec__", None)
    if dyn_spec is not None:
        return _build_schema_mut_obj(doc, val, <CompiledSchemaSpec>dyn_spec, depth)

    if isinstance(val, dict):
        obj_node = yyjson_mut_obj(doc)
        for k, v in (<dict>val).items():
            if isinstance(k, str):
                s_ptr = PyUnicode_AsUTF8AndSize(k, &s_len)
                yyjson_mut_obj_add(
                    obj_node,
                    yyjson_mut_strn(doc, s_ptr, <size_t>s_len),
                    _py_val_to_mut_val(doc, v, None, depth + 1)
                )
        return obj_node

    return yyjson_mut_null(doc)


cdef inline void _write_py_value_to_yyjson(
    bt_json_writer_t *w,
    const char *key_ptr,
    size_t key_len,
    object val,
    CompiledSchemaSpec sub_schema=None
):
    cdef const char *s_ptr = NULL
    cdef Py_ssize_t s_len = 0
    cdef yyjson_mut_val *k_node
    cdef yyjson_mut_val *v_node
    cdef str s_val

    if sub_schema is None:
        if val is None:
            bt_json_writer_add_null(w, key_ptr, key_len)
            return
        elif isinstance(val, bool):
            bt_json_writer_add_bool(w, key_ptr, key_len, 1 if val else 0)
            return
        elif isinstance(val, int):
            if -9223372036854775808 <= val <= 9223372036854775807:
                bt_json_writer_add_int(w, key_ptr, key_len, <int64_t>val)
            else:
                s_val = str(val)
                s_ptr = PyUnicode_AsUTF8AndSize(s_val, &s_len)
                bt_json_writer_add_str(w, key_ptr, key_len, s_ptr, <size_t>s_len)
            return
        elif isinstance(val, float):
            bt_json_writer_add_double(w, key_ptr, key_len, <double>val)
            return
        elif isinstance(val, str):
            s_ptr = PyUnicode_AsUTF8AndSize(val, &s_len)
            bt_json_writer_add_str(w, key_ptr, key_len, s_ptr, <size_t>s_len)
            return

    # Complex / nested value path (list, nested Schema, list[Schema], or nested dict)
    k_node = yyjson_mut_strn(w.doc, key_ptr, key_len)
    v_node = _py_val_to_mut_val(w.doc, val, sub_schema, 1)
    yyjson_mut_obj_add(w.root, k_node, v_node)


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
            val = PyObject_GetAttr(obj, fspec.name)
        _write_py_value_to_yyjson(&writer, fspec._key_ptr, fspec._key_len, val, fspec.sub_schema)

    return bt_json_writer_finish(&writer, out_body_buf, BT_MAX_HANDLER_BODY_CAP, out_body_len)


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
            _write_py_value_to_yyjson(&writer, k_ptr, <size_t>k_len, v, None)

    return bt_json_writer_finish(&writer, out_body_buf, BT_MAX_HANDLER_BODY_CAP, out_body_len)


cdef inline int _serialize_http_exception(
    object detail,
    char *out_body_buf,
    size_t *out_body_len
):
    cdef bt_json_writer_t writer
    if bt_json_writer_init(&writer) != 0:
        return -1
    _write_py_value_to_yyjson(&writer, "detail", 6, detail)
    return bt_json_writer_finish(&writer, out_body_buf, BT_MAX_HANDLER_BODY_CAP, out_body_len)


cdef inline void _write_auth_error(
    int status_code,
    char *out_body_buf,
    size_t *out_body_len,
    int *out_status_code
) noexcept nogil:
    cdef const char *msg = (
        '{"detail":"Not authenticated"}'
        if status_code == 401
        else '{"detail":"Not enough permissions"}'
    )
    cdef size_t mlen = 30 if status_code == 401 else 35
    memcpy(out_body_buf, msg, mlen)
    out_body_len[0] = mlen
    out_status_code[0] = status_code


cdef object _execute_c_security_spec(
    CompiledSecuritySpec sec,
    phr_header *headers,
    size_t num_headers,
    const char *query,
    size_t query_len,
    char *out_body_buf,
    size_t *out_body_len,
    int *out_status_code
):
    cdef bt_sec_result_t res
    cdef char decode_buf[4096]
    cdef size_t payload_len = 0
    cdef bt_json_doc_t jdoc
    cdef bt_slice_t slice_a
    cdef bt_slice_t slice_b
    cdef HTTPBasicCredentials creds

    if sec.sec_kind == SEC_KIND_JWT:
        res = bt_jwt_verify_hs256(
            headers, num_headers,
            sec._secret_ptr, sec._secret_len,
            sec._scope_ptrs, sec._scope_lens, sec._num_scopes,
            decode_buf, sizeof(decode_buf), &payload_len,
            &jdoc
        )
        if res != BT_SEC_OK:
            _write_auth_error(<int>res, out_body_buf, out_body_len, out_status_code)
            return None
        return _yyjson_obj_to_dict(jdoc.root)

    elif sec.sec_kind == SEC_KIND_APIKEY_HDR or sec.sec_kind == SEC_KIND_APIKEY_QUERY:
        res = bt_verify_api_key(
            headers, num_headers,
            query, query_len,
            1 if sec.sec_kind == SEC_KIND_APIKEY_QUERY else 0,
            sec._key_name_ptr, sec._key_name_len,
            sec._expected_val_ptr, sec._expected_val_len,
            &slice_a
        )
        if res != BT_SEC_OK:
            _write_auth_error(<int>res, out_body_buf, out_body_len, out_status_code)
            return None
        return PyUnicode_DecodeUTF8(slice_a.ptr, <Py_ssize_t>slice_a.len, "strict")

    elif sec.sec_kind == SEC_KIND_HTTP_BASIC:
        res = bt_verify_basic_auth(
            headers, num_headers,
            sec._expected_val_ptr, sec._expected_val_len,
            sec._expected_pass_ptr, sec._expected_pass_len,
            decode_buf, sizeof(decode_buf),
            &slice_a, &slice_b
        )
        if res != BT_SEC_OK:
            _write_auth_error(<int>res, out_body_buf, out_body_len, out_status_code)
            return None
        creds = HTTPBasicCredentials.__new__(HTTPBasicCredentials)
        creds.username = PyUnicode_DecodeUTF8(slice_a.ptr, <Py_ssize_t>slice_a.len, "strict")
        creds.password = PyUnicode_DecodeUTF8(slice_b.ptr, <Py_ssize_t>slice_b.len, "strict")
        return creds

    return None


cdef int _resolve_kwargs_for_specs(
    tuple param_specs,
    int num_params,
    list dep_slots,
    list bg_tasks_out,
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
    """Returns 0 on success, or 1 if a 422/401/403 error was written to out_body_buf."""
    cdef int i
    cdef CompiledParamSpec spec
    cdef bt_slice_t raw_slice
    cdef bt_val_error_t val_err
    cdef int64_t parsed_int = 0
    cdef double parsed_float = 0.0
    cdef Request req_obj
    cdef object schema_instance
    cdef object sec_val
    cdef bt_multipart_part_t mp_part
    cdef UploadFile up_file
    cdef BackgroundTasks bg_obj
    cdef str kwargs_val_str

    for i in range(num_params):
        spec = <CompiledParamSpec>(param_specs[i])

        if spec.source == SRC_BACKGROUND_TASKS:
            bg_obj = BackgroundTasks.__new__(BackgroundTasks)
            bg_obj._tasks = []
            out_kwargs[spec.arg_name] = bg_obj
            if bg_tasks_out is not None:
                bg_tasks_out.append(bg_obj)
            continue

        if spec.source == SRC_UPLOAD_FILE:
            if bt_multipart_extract_file(
                headers, num_headers,
                body, body_len,
                spec._key_ptr, spec._key_len,
                &mp_part
            ) == 0:
                if spec.required:
                    _write_422_error(SRC_BODY_SCHEMA, spec._key_ptr, out_body_buf, out_body_len, out_status_code)
                    return 1
                out_kwargs[spec.arg_name] = None
                continue

            up_file = UploadFile.__new__(UploadFile)
            up_file.filename = PyUnicode_DecodeUTF8(mp_part.filename.ptr, <Py_ssize_t>mp_part.filename.len, "replace") if mp_part.filename.found else ""
            up_file.content_type = PyUnicode_DecodeUTF8(mp_part.content_type.ptr, <Py_ssize_t>mp_part.content_type.len, "replace") if mp_part.content_type.found else "application/octet-stream"
            up_file.bind_slice(mp_part.data.ptr, mp_part.data.len)
            out_kwargs[spec.arg_name] = up_file
            continue

        if spec.source == SRC_SECURITY:
            sec_val = _execute_c_security_spec(
                spec.security_spec,
                headers, num_headers,
                query, query_len,
                out_body_buf, out_body_len, out_status_code
            )
            if sec_val is None:
                return 1
            out_kwargs[spec.arg_name] = sec_val
            continue

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
            try:
                kwargs_val_str = PyUnicode_DecodeUTF8(
                    raw_slice.ptr, <Py_ssize_t>raw_slice.len, "strict"
                )
            except UnicodeDecodeError:
                _write_422_error(spec.source, spec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return 1
            out_kwargs[spec.arg_name] = kwargs_val_str

    return 0


cdef inline void _teardown_generators(list teardown_stack):
    """Unwinds active sync and async generator dependencies in LIFO order."""
    cdef Py_ssize_t idx = len(teardown_stack) - 1
    cdef tuple entry
    cdef int kind
    cdef object gen_obj
    cdef object aclose_coro

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
                aclose_coro = gen_obj.aclose()
                _step_coroutine_sync(aclose_coro)
        except Exception:
            pass
        idx -= 1


async def _teardown_generators_async(list teardown_stack, object loop):
    """Asynchronously unwinds generator dependencies in LIFO order without reentrant loop.run_until_complete."""
    cdef Py_ssize_t idx = len(teardown_stack) - 1
    cdef tuple entry
    cdef int kind
    cdef object gen_obj
    cdef object aclose_coro

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
                aclose_coro = gen_obj.aclose()
                await _step_coroutine_async(aclose_coro, loop)
        except Exception:
            pass
        idx -= 1


cdef int _execute_route_inner(
    RouteEntry entry,
    void *async_client_ctx,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    phr_header *headers, size_t num_headers,
    bt_slice_t *path_params, size_t num_path_params,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    char *out_extra_hdrs, size_t *out_extra_hdrs_len,
    int *out_status_code
) noexcept:
    cdef object result = None
    cdef object coro_obj = None
    cdef object first_yielded = None
    cdef object completion_fut = None
    cdef object loop = None
    cdef object prev_loop = None
    cdef PyObject *send_res_ptr = NULL
    cdef PySendResult send_rc
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
    cdef object body_bytes_res, hdr_bytes_res, status_res
    cdef object schema_spec_attr
    cdef list bg_tasks_list = [] if entry.has_bg_tasks else None
    cdef BackgroundTasks bg_runner

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
                        bg_tasks_list,
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
                coro_obj = entry.fn()
            else:
                result = entry.fn()
        else:
            kwargs = {}
            if _resolve_kwargs_for_specs(
                entry.param_specs, entry.num_params, dep_slots,
                bg_tasks_list,
                method, method_len, path, path_len, query, query_len,
                headers, num_headers, path_params, num_path_params,
                body, body_len, kwargs,
                out_body_buf, out_body_len, out_status_code
            ) != 0:
                return 0

            if entry.is_coroutine:
                coro_obj = entry.fn(**kwargs)
            else:
                result = entry.fn(**kwargs)

        if entry.is_coroutine and coro_obj is not None:
            loop = _get_or_create_async_loop()
            prev_loop = events._get_running_loop()
            if prev_loop is None:
                events._set_running_loop(loop)
            try:
                send_rc = PyIter_Send(coro_obj, None, &send_res_ptr)
                if send_rc == PYGEN_RETURN:
                    result = <object>send_res_ptr
                    Py_DECREF_ptr(send_res_ptr)
                    coro_obj = None
                elif send_rc == PYGEN_ERROR:
                    coro_obj.close()
                    coro_obj = None
                    raise
                else:
                    first_yielded = <object>send_res_ptr
                    Py_DECREF_ptr(send_res_ptr)
            finally:
                if prev_loop is None:
                    events._set_running_loop(None)

            if coro_obj is not None:
                if async_client_ctx != NULL:
                    _schedule_suspended_tcp_coro(
                        coro_obj, first_yielded, entry, async_client_ctx,
                        teardown_stack, bg_tasks_list
                    )
                    teardown_stack = None
                    bg_tasks_list = None
                    return BT_HANDLER_SUSPENDED
                else:
                    completion_fut = _drive_coro(coro_obj, first_yielded, loop)
                    try:
                        result = loop.run_until_complete(completion_fut)
                    finally:
                        coro_obj.close()

        out_status_code[0] = entry.default_status_code

        # Fast C-type checks ordered by frequency (avoids raising/clearing AttributeError on hot path)
        if entry.response_schema_spec is not None:
            return _serialize_schema_to_c_buf(result, entry.response_schema_spec, out_body_buf, out_body_len)

        if isinstance(result, dict):
            return _serialize_dict_to_c_buf(<dict>result, out_body_buf, out_body_len)

        if isinstance(result, bytes):
            PyBytes_AsStringAndSize(result, &raw_bytes_ptr, &raw_bytes_len)
            return _safe_copy_to_body_buf(raw_bytes_ptr, raw_bytes_len, out_body_buf, out_body_len)

        if isinstance(result, str):
            raw_bytes_ptr = <char *>PyUnicode_AsUTF8AndSize(result, &raw_bytes_len)
            return _safe_copy_to_body_buf(raw_bytes_ptr, raw_bytes_len, out_body_buf, out_body_len)

        schema_spec_attr = getattr(result, "__bt_schema_spec__", None)
        if schema_spec_attr is not None:
            return _serialize_schema_to_c_buf(
                result, <CompiledSchemaSpec>schema_spec_attr, out_body_buf, out_body_len
            )

        if getattr(result, "__bt_custom_response__", False):
            body_bytes_res, hdr_bytes_res, status_res = result._render_c_response()
            out_status_code[0] = <int>status_res
            PyBytes_AsStringAndSize(body_bytes_res, &raw_bytes_ptr, &raw_bytes_len)
            if _safe_copy_to_body_buf(raw_bytes_ptr, raw_bytes_len, out_body_buf, out_body_len) != 0:
                return -1

            if hdr_bytes_res:
                PyBytes_AsStringAndSize(hdr_bytes_res, &raw_bytes_ptr, &raw_bytes_len)
                if raw_bytes_len < 0 or <size_t>raw_bytes_len > BT_MAX_EXTRA_HDRS_CAP:
                    return -1
                memcpy(out_extra_hdrs, raw_bytes_ptr, <size_t>raw_bytes_len)
                out_extra_hdrs_len[0] = <size_t>raw_bytes_len
            return 0

        return -1

    except HTTPException as http_exc:
        out_status_code[0] = <int>http_exc.status_code
        return _serialize_http_exception(http_exc.detail, out_body_buf, out_body_len)
    except Exception:
        return -1
    finally:
        if teardown_stack is not None and len(teardown_stack) > 0:
            _teardown_generators(teardown_stack)
        if bg_tasks_list is not None and len(bg_tasks_list) > 0 and out_status_code[0] < 400:
            for bg_runner in bg_tasks_list:
                bg_runner._execute_all()


@cython.freelist(1024)
cdef class _RouteInvocationContext:
    cdef RouteEntry entry
    cdef const char *method
    cdef size_t method_len
    cdef const char *path
    cdef size_t path_len
    cdef const char *query
    cdef size_t query_len
    cdef phr_header *headers
    cdef size_t num_headers
    cdef bt_slice_t *path_params
    cdef size_t num_path_params
    cdef const char *body
    cdef size_t body_len
    cdef char *out_body_buf
    cdef size_t *out_body_len
    cdef char *out_extra_hdrs
    cdef size_t *out_extra_hdrs_len
    cdef int *out_status_code

    cdef inline MiddlewareResponse execute_sync(self):
        cdef int rc = _execute_route_inner(
            self.entry, NULL, self.method, self.method_len, self.path, self.path_len,
            self.query, self.query_len, self.headers, self.num_headers,
            self.path_params, self.num_path_params,
            self.body, self.body_len, self.out_body_buf, self.out_body_len,
            self.out_extra_hdrs, self.out_extra_hdrs_len,
            self.out_status_code
        )
        if rc != 0:
            return MiddlewareResponse(500, b"Internal Server Error")
        cdef bytes b_body = PyBytes_FromStringAndSize(self.out_body_buf, <Py_ssize_t>self.out_body_len[0])
        return MiddlewareResponse(self.out_status_code[0], b_body)


cdef object _make_mw_layer(object fn, object nxt):
    async def _layer(object r):
        res = fn(r, nxt)
        if inspect.isawaitable(res):
            return await res
        return res
    return _layer


cdef int _cython_invoke_handler(
    void *handler_obj,
    void *async_client_ctx,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    phr_header *headers, size_t num_headers,
    bt_slice_t *path_params, size_t num_path_params,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    char *out_extra_hdrs, size_t *out_extra_hdrs_len,
    int *out_status_code
) noexcept with gil:
    cdef RouteEntry entry = <RouteEntry>handler_obj
    out_extra_hdrs_len[0] = 0

    if entry.user_middlewares is None or len(entry.user_middlewares) == 0:
        return _execute_route_inner(
            entry, async_client_ctx, method, method_len, path, path_len, query, query_len,
            headers, num_headers, path_params, num_path_params,
            body, body_len, out_body_buf, out_body_len,
            out_extra_hdrs, out_extra_hdrs_len,
            out_status_code
        )

    # User @app.middleware("http") execution path
    cdef Request req_obj = Request.__new__(Request)
    req_obj.bind_c(
        method, method_len, path, path_len,
        query, query_len, headers, num_headers, body, body_len
    )

    cdef _RouteInvocationContext ctx = _RouteInvocationContext.__new__(_RouteInvocationContext)
    ctx.entry = entry
    ctx.method = method
    ctx.method_len = method_len
    ctx.path = path
    ctx.path_len = path_len
    ctx.query = query
    ctx.query_len = query_len
    ctx.headers = headers
    ctx.num_headers = num_headers
    ctx.path_params = path_params
    ctx.num_path_params = num_path_params
    ctx.body = body
    ctx.body_len = body_len
    ctx.out_body_buf = out_body_buf
    ctx.out_body_len = out_body_len
    ctx.out_extra_hdrs = out_extra_hdrs
    ctx.out_extra_hdrs_len = out_extra_hdrs_len
    ctx.out_status_code = out_status_code

    async def _call_next_terminal(object _req=None):
        return ctx.execute_sync()

    cdef object next_fn = _call_next_terminal
    cdef Py_ssize_t idx = len(entry.user_middlewares) - 1
    while idx >= 0:
        next_fn = _make_mw_layer(entry.user_middlewares[idx], next_fn)
        idx -= 1

    cdef object mw_res
    cdef bytes b_out
    cdef char *b_ptr = NULL
    cdef Py_ssize_t b_len = 0
    cdef const char *k_raw = NULL
    cdef Py_ssize_t k_raw_len = 0
    cdef const char *v_raw = NULL
    cdef Py_ssize_t v_raw_len = 0
    cdef char clean_k_buf[256]
    cdef char clean_v_buf[1024]
    cdef size_t ck_len, cv_len, pos = 0
    cdef str k_str, v_str

    try:
        mw_res = _step_coroutine_sync(next_fn(req_obj))
        if isinstance(mw_res, MiddlewareResponse):
            out_status_code[0] = (<MiddlewareResponse>mw_res).status_code
            b_out = (<MiddlewareResponse>mw_res).body
            if b_out is not None:
                PyBytes_AsStringAndSize(b_out, &b_ptr, &b_len)
                if _safe_copy_to_body_buf(b_ptr, b_len, out_body_buf, out_body_len) != 0:
                    return -1

            if (<MiddlewareResponse>mw_res).headers:
                pos = 0
                for k, v in (<MiddlewareResponse>mw_res).headers.items():
                    k_str = k if isinstance(k, str) else str(k)
                    v_str = v if isinstance(v, str) else str(v)
                    k_raw = PyUnicode_AsUTF8AndSize(k_str, &k_raw_len)
                    v_raw = PyUnicode_AsUTF8AndSize(v_str, &v_raw_len)
                    ck_len = bt_sanitize_header_str(k_raw, <size_t>k_raw_len, clean_k_buf, sizeof(clean_k_buf))
                    cv_len = bt_sanitize_header_str(v_raw, <size_t>v_raw_len, clean_v_buf, sizeof(clean_v_buf))
                    if ck_len == 0 or pos + ck_len + cv_len + 4 > 2040:
                        break
                    memcpy(out_extra_hdrs + pos, clean_k_buf, ck_len)
                    pos += ck_len
                    out_extra_hdrs[pos] = b':'
                    out_extra_hdrs[pos + 1] = b' '
                    pos += 2
                    memcpy(out_extra_hdrs + pos, clean_v_buf, cv_len)
                    pos += cv_len
                    out_extra_hdrs[pos] = b'\r'
                    out_extra_hdrs[pos + 1] = b'\n'
                    pos += 2
                out_extra_hdrs_len[0] = pos
        elif isinstance(mw_res, dict):
            return _serialize_dict_to_c_buf(<dict>mw_res, out_body_buf, out_body_len)
        elif isinstance(mw_res, bytes):
            PyBytes_AsStringAndSize(mw_res, &b_ptr, &b_len)
            return _safe_copy_to_body_buf(b_ptr, b_len, out_body_buf, out_body_len)
        return 0
    except Exception:
        return -1


cdef inline size_t _copy_log_str_sanitized(char *dst, const char *src, size_t src_len, size_t max_len) noexcept nogil:
    cdef size_t n = src_len if src_len < max_len else max_len
    cdef size_t i
    cdef unsigned char c
    for i in range(n):
        c = <unsigned char>src[i]
        if c == b'"' or c == b'\\' or c == b'\r' or c == b'\n' or c < 0x20:
            dst[i] = b'_'
        else:
            dst[i] = <char>c
    return n


cdef class CythonRingLogger:
    cdef bt_logger_t *_logger

    def __cinit__(self, str filepath="", size_t capacity=65536):
        cdef bytes b_path = filepath.encode("utf-8") if filepath else b""
        cdef const char *c_path = <const char *>b_path if filepath else NULL
        self._logger = bt_logger_create(c_path, capacity)
        if self._logger == NULL:
            raise MemoryError("Failed to initialize C11 RingLogger")

    def __dealloc__(self):
        if self._logger != NULL:
            bt_logger_destroy(self._logger)
            self._logger = NULL

    cdef inline void _push_log(self, uint8_t level, str msg, dict kwargs):
        if self._logger == NULL:
            return
        cdef Py_ssize_t msg_len = 0
        cdef const char *msg_ptr = PyUnicode_AsUTF8AndSize(msg, &msg_len)
        cdef bt_log_entry_t *slot = bt_logger_acquire_slot(
            self._logger, level, msg_ptr, <size_t>msg_len
        )
        if slot == NULL:
            return

        cdef uint8_t kv_idx = 0
        cdef object k, v
        cdef const char *k_ptr = NULL
        cdef Py_ssize_t k_len = 0
        cdef const char *s_ptr = NULL
        cdef Py_ssize_t s_len = 0
        cdef size_t copy_klen, copy_slen
        cdef bt_log_kv_t *kv

        if kwargs is not None and len(kwargs) > 0:
            for k, v in kwargs.items():
                if kv_idx >= BT_LOG_MAX_KVS:
                    break
                if not isinstance(k, str):
                    continue
                k_ptr = PyUnicode_AsUTF8AndSize(k, &k_len)
                if k_ptr == NULL:
                    continue
                kv = &slot.kvs[kv_idx]
                copy_klen = _copy_log_str_sanitized(kv.key, k_ptr, <size_t>k_len, 23)
                kv.key_len = <uint8_t>copy_klen

                if isinstance(v, bool):
                    kv.val_type = BT_LOG_VAL_BOOL
                    kv.v.b_val = 1 if v else 0
                    kv_idx += 1
                elif isinstance(v, int):
                    kv.val_type = BT_LOG_VAL_INT
                    kv.v.i_val = <int64_t>v
                    kv_idx += 1
                elif isinstance(v, float):
                    kv.val_type = BT_LOG_VAL_FLOAT
                    kv.v.f_val = <double>v
                    kv_idx += 1
                elif isinstance(v, str):
                    s_ptr = PyUnicode_AsUTF8AndSize(v, &s_len)
                    if s_ptr != NULL:
                        kv.val_type = BT_LOG_VAL_STR
                        copy_slen = _copy_log_str_sanitized(kv.v.s_val, s_ptr, <size_t>s_len, 47)
                        kv.str_len = <uint8_t>copy_slen
                        kv_idx += 1

        slot.num_kvs = kv_idx
        bt_logger_publish_slot(self._logger)

    def debug(self, str msg, **kwargs):
        self._push_log(BT_LOG_LEVEL_DEBUG, msg, kwargs)

    def info(self, str msg, **kwargs):
        self._push_log(BT_LOG_LEVEL_INFO, msg, kwargs)

    def warning(self, str msg, **kwargs):
        self._push_log(BT_LOG_LEVEL_WARN, msg, kwargs)

    def error(self, str msg, **kwargs):
        self._push_log(BT_LOG_LEVEL_ERROR, msg, kwargs)

    cpdef void flush_and_stop(self):
        if self._logger != NULL:
            bt_logger_flush_and_stop(self._logger)


cdef object _match_ws_route_py(dict ws_routes, str req_path):
    if req_path in ws_routes:
        return ws_routes[req_path][0], {}
    cdef list req_segs = [s for s in req_path.strip("/").split("/") if s]
    cdef str pattern
    cdef tuple entry_tuple
    cdef list pat_segs
    cdef dict kwargs
    cdef bint ok
    cdef Py_ssize_t i
    for pattern, entry_tuple in ws_routes.items():
        if "{" not in pattern:
            continue
        pat_segs = [s for s in pattern.strip("/").split("/") if s]
        if len(pat_segs) != len(req_segs):
            continue
        kwargs = {}
        ok = True
        for i in range(len(pat_segs)):
            if pat_segs[i].startswith("{") and pat_segs[i].endswith("}"):
                kwargs[pat_segs[i][1:-1].split(":")[0]] = req_segs[i]
            elif pat_segs[i] != req_segs[i]:
                ok = False
                break
        if ok:
            return entry_tuple[0], kwargs
    return None, None


cdef int _cython_ws_event_cb(
    void *router_ctx,
    void *ws_client_ctx,
    int event_type,
    const char *path, size_t path_len,
    const char *payload, size_t payload_len
) noexcept with gil:
    global _PENDING_ASYNC_TASKS
    cdef CoreRouter router = <CoreRouter>router_ctx
    cdef uintptr_t client_key = <uintptr_t>ws_client_ctx
    cdef WebSocket ws
    cdef str req_path, msg_text
    cdef object fn, loop
    cdef dict path_kwargs

    try:
        if event_type == BT_WS_EVT_OPEN:
            req_path = PyUnicode_DecodeUTF8(path, <Py_ssize_t>path_len, "strict")
            fn, path_kwargs = _match_ws_route_py(router._ws_routes, req_path)
            if fn is None:
                return -1

            ws = WebSocket(path=req_path, pubsub_registry=router._ws_pubsub)
            ws.bind_uv_client(ws_client_ctx)
            router._live_ws_sessions[client_key] = ws

            loop = _get_or_create_async_loop()
            _PENDING_ASYNC_TASKS += 1

            async def _drive_live_ws():
                global _PENDING_ASYNC_TASKS
                try:
                    await _step_coroutine_async(fn(ws, **path_kwargs), loop)
                except Exception:
                    pass
                finally:
                    _PENDING_ASYNC_TASKS -= 1
                    router._live_ws_sessions.pop(client_key, None)
                    if not ws.closed:
                        await ws.close()

            loop.create_task(_drive_live_ws())
            return 0

        elif event_type == BT_WS_EVT_MESSAGE:
            ws = router._live_ws_sessions.get(client_key)
            if ws is not None:
                msg_text = PyUnicode_DecodeUTF8(payload, <Py_ssize_t>payload_len, "replace")
                ws._deliver_live_message(msg_text)
            return 0

        elif event_type == BT_WS_EVT_CLOSE:
            ws = router._live_ws_sessions.pop(client_key, None)
            if ws is not None:
                ws._uv_client_ptr = 0
                ws._deliver_live_close()
            return 0

        return 0
    except Exception:
        return -1


cdef class CoreRouter:
    cdef bt_router_t *_router
    cdef list _handler_refs
    cdef CythonRingLogger _access_logger_ref
    cdef public list _user_middlewares
    cdef public dict _ws_routes
    cdef public dict _ws_pubsub
    cdef public dict _live_ws_sessions

    def __cinit__(self):
        self._handler_refs = []
        self._user_middlewares = []
        self._ws_routes = {}
        self._ws_pubsub = {}
        self._live_ws_sessions = {}
        self._router = bt_router_create(_cython_invoke_handler)
        if self._router == NULL:
            raise MemoryError("Failed to allocate C router")
        bt_router_set_async_tick_cb(self._router, _cython_async_loop_tick)
        bt_router_set_ws_cb(self._router, _cython_ws_event_cb, <void *>self)

    def __dealloc__(self):
        if self._router != NULL:
            bt_router_destroy(self._router)
            self._router = NULL

    cpdef void add_ws_route(self, str path, object fn, list param_names):
        self._ws_routes[path] = (fn, param_names)

    cpdef void ws_publish(self, str topic, str message):
        cdef list subs = self._ws_pubsub.get(topic)
        if not subs:
            return
        cdef Py_ssize_t s_len = 0
        cdef const char *s_ptr = PyUnicode_AsUTF8AndSize(message, &s_len)
        cdef char frame_buf[16384]
        cdef size_t written = bt_ws_encode_frame(
            BT_WS_OP_TEXT, s_ptr, <size_t>s_len, 0, frame_buf, sizeof(frame_buf)
        )
        cdef bytes encoded = PyBytes_FromStringAndSize(frame_buf, <Py_ssize_t>written)
        cdef WebSocket ws
        cdef list active_subs = []
        for ws in subs:
            if not ws.closed:
                ws.push_published_frame(encoded)
                active_subs.append(ws)
        self._ws_pubsub[topic] = active_subs

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
            fn, param_specs or [], response_schema_spec, status_code, dep_steps or [],
            self._user_middlewares
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
        cdef const char *c_host = <const char *>b_host
        cdef int c_port = port
        cdef bt_router_t *r = self._router
        with nogil:
            bt_uv_server_run(r, c_host, c_port)

    cpdef void set_access_logger(self, CythonRingLogger logger):
        self._access_logger_ref = logger
        if logger is not None and logger._logger != NULL:
            bt_router_set_access_logger(self._router, logger._logger)
        else:
            bt_router_set_access_logger(self._router, NULL)

    cpdef void configure_cors(self, str allow_origin, str allow_methods, str allow_headers):
        cdef bytes b_orig = allow_origin.encode("utf-8")
        cdef bytes b_meth = allow_methods.encode("utf-8")
        cdef bytes b_hdrs = allow_headers.encode("utf-8")
        bt_middleware_configure_cors(
            bt_router_get_mw(self._router),
            <const char *>b_orig, <size_t>len(b_orig),
            <const char *>b_meth, <size_t>len(b_meth),
            <const char *>b_hdrs, <size_t>len(b_hdrs)
        )

    cpdef void configure_ratelimit(self, uint32_t max_requests, int64_t window_seconds):
        bt_middleware_configure_ratelimit(
            bt_router_get_mw(self._router), max_requests, window_seconds
        )

    cpdef void configure_trusted_host(self, str allowed_host):
        cdef bytes b_host = allowed_host.encode("utf-8")
        bt_middleware_configure_trusted_host(
            bt_router_get_mw(self._router), <const char *>b_host, <size_t>len(b_host)
        )

    cpdef void configure_sec_headers(self, str frame_opts, str ctype_opts, str hsts):
        cdef bytes b_f = frame_opts.encode("utf-8")
        cdef bytes b_c = ctype_opts.encode("utf-8")
        cdef bytes b_h = hsts.encode("utf-8")
        bt_middleware_configure_sec_headers(
            bt_router_get_mw(self._router),
            <const char *>b_f, <size_t>len(b_f),
            <const char *>b_c, <size_t>len(b_c),
            <const char *>b_h, <size_t>len(b_h)
        )

    cpdef void set_max_body_size(self, size_t max_body_size):
        bt_router_set_max_body_size(self._router, max_body_size)

    cpdef void add_user_middleware(self, object fn):
        self._user_middlewares.append(fn)


@cython.freelist(4096)
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

    @property
    def headers(self) -> dict:
        cdef bytes head_part = self.wire_bytes.split(b"\r\n\r\n", 1)[0]
        cdef list lines = head_part.split(b"\r\n")[1:]
        cdef dict result = {}
        cdef bytes line, k, v
        for line in lines:
            if b":" in line:
                k, v = line.split(b":", 1)
                result[k.decode("latin1").strip()] = v.decode("latin1").strip()
        return result


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
