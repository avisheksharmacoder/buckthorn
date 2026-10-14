# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
cimport cython
from libc.string cimport memcpy
from libc.stdio cimport snprintf
from libc.stdint cimport int64_t, uint32_t
from cpython.bytes cimport PyBytes_AsStringAndSize, PyBytes_FromStringAndSize
from cpython.bytearray cimport PyByteArray_AsString
from cpython.unicode cimport PyUnicode_DecodeUTF8, PyUnicode_AsUTF8AndSize
from cpython.object cimport PyObject_SetAttr, PyObject_GetAttr
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
    bt_ws_frame_t, bt_multipart_part_t,
    bt_ws_compute_accept, bt_ws_parse_frame, bt_ws_encode_frame,
    bt_multipart_extract_file
)

# Parameter Source Constants
DEF SRC_PATH        = 1
DEF SRC_QUERY       = 2
DEF SRC_HEADER      = 3
DEF SRC_COOKIE      = 4
DEF SRC_REQUEST     = 5
DEF SRC_BODY_SCHEMA = 6
DEF SRC_DEPENDS     = 7
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
    """High-speed C-backed RFC 6455 WebSocket session & Pub/Sub handle."""
    cdef public str path
    cdef public bint accepted
    cdef public bint closed
    cdef public str accept_key
    cdef public list _in_frames
    cdef public list _out_frames
    cdef public dict _pubsub_registry_ref

    def __cinit__(self, str path="", dict pubsub_registry=None):
        self.path = path
        self.accepted = False
        self.closed = False
        self.accept_key = ""
        self._in_frames = []
        self._out_frames = []
        self._pubsub_registry_ref = pubsub_registry

    cpdef str compute_handshake(self, str sec_websocket_key):
        cdef bytes b_key = sec_websocket_key.encode("ascii")
        cdef char *k_ptr = NULL
        cdef Py_ssize_t k_len = 0
        cdef char out_accept[64]
        cdef size_t out_len = 0
        PyBytes_AsStringAndSize(b_key, &k_ptr, &k_len)
        if bt_ws_compute_accept(k_ptr, <size_t>k_len, out_accept, sizeof(out_accept), &out_len) != 0:
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
            self.closed = True
            return
        cdef str text = PyUnicode_DecodeUTF8(
            parsed.payload_ptr, <Py_ssize_t>parsed.payload_len, "strict"
        )
        self._in_frames.append(text)

    async def receive_text(self) -> str:
        if not self._in_frames:
            raise RuntimeError("No WebSocket frame available")
        return self._in_frames.pop(0)

    cpdef bytes _encode_server_frame(self, str text):
        cdef Py_ssize_t s_len = 0
        cdef const char *s_ptr = PyUnicode_AsUTF8AndSize(text, &s_len)
        cdef char frame_buf[16384]
        cdef size_t written = bt_ws_encode_frame(
            BT_WS_OP_TEXT, s_ptr, <size_t>s_len, 0, frame_buf, sizeof(frame_buf)
        )
        return PyBytes_FromStringAndSize(frame_buf, <Py_ssize_t>written)

    async def send_text(self, str data):
        self._out_frames.append(self._encode_server_frame(data))

    cpdef void push_published_frame(self, bytes encoded_frame):
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


cpdef bytes encode_client_masked_ws_frame(str text):
    """Encodes a client-to-server masked RFC 6455 frame in C for testing."""
    cdef Py_ssize_t s_len = 0
    cdef const char *s_ptr = PyUnicode_AsUTF8AndSize(text, &s_len)
    cdef char frame_buf[16384]
    cdef size_t written = bt_ws_encode_frame(
        BT_WS_OP_TEXT, s_ptr, <size_t>s_len, 1, frame_buf, sizeof(frame_buf)
    )
    return PyBytes_FromStringAndSize(frame_buf, <Py_ssize_t>written)


@cython.freelist(1024)
cdef class BackgroundTasks:
    """Zero-overhead Cython freelist queue for post-response background tasks."""
    cdef public list _tasks

    def __cinit__(self):
        self._tasks = []

    def add_task(self, object func, *args, **kwargs):
        cdef bint is_coro = inspect.iscoroutinefunction(func)
        self._tasks.append((func, args, kwargs, is_coro))

    cpdef void _execute_all(self):
        cdef tuple task_entry
        cdef object fn, args, kwargs, coro
        cdef bint is_coro
        for task_entry in self._tasks:
            fn = task_entry[0]
            args = task_entry[1]
            kwargs = task_entry[2]
            is_coro = <bint>(task_entry[3])
            if is_coro:
                coro = fn(*args, **kwargs)
                _step_coroutine_sync(coro)
            else:
                fn(*args, **kwargs)


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
                PyObject_SetAttr(instance, fspec.name, v_int)
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
                PyObject_SetAttr(instance, fspec.name, v_flt)
                continue

        elif fspec.field_type == TYPE_BOOL:
            err = bt_json_get_bool_checked(&jdoc, fspec._key_ptr, fspec._key_len, &found, &v_bool)
            if err != BT_ERR_NONE:
                _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                return None
            if found:
                PyObject_SetAttr(instance, fspec.name, True if v_bool else False)
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
                PyObject_SetAttr(instance, fspec.name, py_val)
                continue

        elif fspec.field_type == TYPE_LIST:
            raw_val = bt_json_get_raw_field(&jdoc, fspec._key_ptr, fspec._key_len, &found)
            if found:
                if not yyjson_is_arr(raw_val):
                    _write_422_error(SRC_BODY_SCHEMA, fspec._key_ptr, out_body_buf, out_body_len, out_status_code)
                    return None
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
            val = PyObject_GetAttr(obj, fspec.name)
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
    cdef size_t mlen = 30 if status_code == 401 else 33
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
        return _py_json.loads(PyBytes_FromStringAndSize(decode_buf, <Py_ssize_t>payload_len))

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
    """Returns 0 on success, or 1 if a 422 validation error was written to out_body_buf."""
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


cdef int _execute_route_inner(
    RouteEntry entry,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    phr_header *headers, size_t num_headers,
    bt_slice_t *path_params, size_t num_path_params,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    char *out_extra_hdrs, size_t *out_extra_hdrs_len,
    int *out_status_code
) except -1:
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
    cdef object body_bytes_res, hdr_bytes_res, status_res
    cdef list bg_tasks_list = []
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
                result = _step_coroutine_sync(entry.fn())
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
                result = _step_coroutine_sync(entry.fn(**kwargs))
            else:
                result = entry.fn(**kwargs)

        out_status_code[0] = entry.default_status_code

        # Support EventSourceResponse (SSE) and FileResponse objects
        if hasattr(result, "__bt_custom_response__"):
            body_bytes_res, hdr_bytes_res, status_res = result._render_c_response()
            out_status_code[0] = <int>status_res
            PyBytes_AsStringAndSize(body_bytes_res, &raw_bytes_ptr, &raw_bytes_len)
            memcpy(out_body_buf, raw_bytes_ptr, <size_t>raw_bytes_len)
            out_body_len[0] = <size_t>raw_bytes_len

            if hdr_bytes_res:
                PyBytes_AsStringAndSize(hdr_bytes_res, &raw_bytes_ptr, &raw_bytes_len)
                memcpy(out_extra_hdrs, raw_bytes_ptr, <size_t>raw_bytes_len)
                out_extra_hdrs_len[0] = <size_t>raw_bytes_len
            return 0

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
        if len(bg_tasks_list) > 0 and out_status_code[0] < 400:
            for bg_runner in bg_tasks_list:
                bg_runner._execute_all()


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
            self.entry, self.method, self.method_len, self.path, self.path_len,
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
            entry, method, method_len, path, path_len, query, query_len,
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
    cdef bytes b_out, hdr_str
    cdef size_t hlen
    try:
        mw_res = _step_coroutine_sync(next_fn(req_obj))
        if isinstance(mw_res, MiddlewareResponse):
            out_status_code[0] = (<MiddlewareResponse>mw_res).status_code
            b_out = (<MiddlewareResponse>mw_res).body
            if b_out is not None:
                memcpy(out_body_buf, <const char *>b_out, <size_t>len(b_out))
                out_body_len[0] = <size_t>len(b_out)

            if (<MiddlewareResponse>mw_res).headers:
                hdr_str = "".join(
                    f"{k}: {v}\r\n" for k, v in (<MiddlewareResponse>mw_res).headers.items()
                ).encode("utf-8")
                hlen = <size_t>len(hdr_str)
                if hlen > 2040:
                    hlen = 2040
                memcpy(out_extra_hdrs, <const char *>hdr_str, hlen)
                out_extra_hdrs_len[0] = hlen
        elif isinstance(mw_res, dict):
            _serialize_dict_to_c_buf(<dict>mw_res, out_body_buf, out_body_len)
        elif isinstance(mw_res, bytes):
            memcpy(out_body_buf, <const char *>mw_res, <size_t>len(mw_res))
            out_body_len[0] = <size_t>len(mw_res)
        return 0
    except Exception:
        return -1


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
                kv = &slot.kvs[kv_idx]
                copy_klen = <size_t>k_len if k_len < 23 else 23
                memcpy(kv.key, k_ptr, copy_klen)
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
                    kv.val_type = BT_LOG_VAL_STR
                    s_ptr = PyUnicode_AsUTF8AndSize(v, &s_len)
                    copy_slen = <size_t>s_len if s_len < 47 else 47
                    memcpy(kv.v.s_val, s_ptr, copy_slen)
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


cdef class CoreRouter:
    cdef bt_router_t *_router
    cdef list _handler_refs
    cdef CythonRingLogger _access_logger_ref
    cdef public list _user_middlewares
    cdef public dict _ws_routes
    cdef public dict _ws_pubsub

    def __cinit__(self):
        self._handler_refs = []
        self._user_middlewares = []
        self._ws_routes = {}
        self._ws_pubsub = {}
        self._router = bt_router_create(_cython_invoke_handler)
        if self._router == NULL:
            raise MemoryError("Failed to allocate C router")

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
        for ws in subs:
            if not ws.closed:
                ws.push_published_frame(encoded)

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

    cpdef void add_user_middleware(self, object fn):
        self._user_middlewares.append(fn)


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
