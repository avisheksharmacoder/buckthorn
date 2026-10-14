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
    void bt_router_set_access_logger(bt_router_t *router, bt_logger_t *logger)

cdef extern from "c_src/bt_logger.h" nogil:
    enum:
        BT_LOG_LEVEL_DEBUG
        BT_LOG_LEVEL_INFO
        BT_LOG_LEVEL_WARN
        BT_LOG_LEVEL_ERROR
        BT_LOG_VAL_INT
        BT_LOG_VAL_FLOAT
        BT_LOG_VAL_BOOL
        BT_LOG_VAL_STR
        BT_LOG_MAX_KVS

    ctypedef union bt_log_val_union_t:
        int64_t i_val
        double f_val
        int b_val
        char s_val[48]

    ctypedef struct bt_log_kv_t:
        char key[24]
        uint8_t key_len
        uint8_t val_type
        uint8_t str_len
        bt_log_val_union_t v

    ctypedef struct bt_log_entry_t:
        uint8_t level
        uint8_t msg_len
        uint8_t num_kvs
        char msg[96]
        bt_log_kv_t kvs[4]

    ctypedef struct bt_logger_t:
        pass

    bt_logger_t *bt_logger_create(const char *filepath, size_t capacity)
    void bt_logger_flush_and_stop(bt_logger_t *logger)
    void bt_logger_destroy(bt_logger_t *logger)
    bt_log_entry_t *bt_logger_acquire_slot(bt_logger_t *logger, uint8_t level,
                                           const char *msg, size_t msg_len)
    void bt_logger_publish_slot(bt_logger_t *logger)
