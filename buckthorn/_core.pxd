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
