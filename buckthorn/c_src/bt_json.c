/* buckthorn/c_src/bt_json.c */
#include "bt_json.h"
#include <limits.h>
#include <math.h>
#include <string.h>

/* Pre-allocated 128KB C pools for zero-malloc yyjson read and write per worker thread */
static _Thread_local char tls_read_pool[131072];
static _Thread_local yyjson_alc tls_read_alc;
static _Thread_local yyjson_obj_iter tls_read_iter;
static _Thread_local const yyjson_val *tls_read_iter_root = NULL;

static _Thread_local char tls_write_pool[131072];
static _Thread_local yyjson_alc tls_write_alc;

static inline yyjson_val *bt_lookup_obj_field(
    bt_json_doc_t *jdoc, const char *key, size_t key_len
) {
    if (yyjson_likely(tls_read_iter_root == jdoc->root)) {
        return yyjson_obj_iter_getn(&tls_read_iter, key, key_len);
    }
    return yyjson_obj_getn(jdoc->root, key, key_len);
}

int bt_json_parse_obj(const char *buf, size_t len, bt_json_doc_t *out_doc) {
    out_doc->doc = NULL;
    out_doc->root = NULL;
    if (!buf || len == 0) return -1;

    yyjson_alc_pool_init(&tls_read_alc, tls_read_pool, sizeof(tls_read_pool));

    /* yyjson_read_opts with pool allocator performs zero system mallocs */
    yyjson_doc *doc = yyjson_read_opts((char *)buf, len, 0, &tls_read_alc, NULL);
    if (!doc) return -1;

    yyjson_val *root = yyjson_doc_get_root(doc);
    if (!root || !unsafe_yyjson_is_obj(root)) {
        return -1;
    }

    out_doc->doc = doc;
    out_doc->root = root;
    yyjson_obj_iter_init(root, &tls_read_iter);
    tls_read_iter_root = root;
    return 0;
}

int bt_json_parse_obj_insitu(char *buf, size_t len, bt_json_doc_t *out_doc) {
    out_doc->doc = NULL;
    out_doc->root = NULL;
    if (!buf || len == 0) return -1;

    yyjson_alc_pool_init(&tls_read_alc, tls_read_pool, sizeof(tls_read_pool));

    yyjson_doc *doc = yyjson_read_opts(buf, len, YYJSON_READ_INSITU, &tls_read_alc, NULL);
    if (!doc) return -1;

    yyjson_val *root = yyjson_doc_get_root(doc);
    if (!root || !unsafe_yyjson_is_obj(root)) {
        return -1;
    }

    out_doc->doc = doc;
    out_doc->root = root;
    yyjson_obj_iter_init(root, &tls_read_iter);
    tls_read_iter_root = root;
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
    yyjson_val *val = bt_lookup_obj_field(jdoc, key, key_len);
    if (!val || unsafe_yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    if (!unsafe_yyjson_is_int(val)) {
        return BT_ERR_TYPE_INT;
    }
    /* Prevent uint64_t > INT64_MAX from silently wrapping around to negative int64_t */
    if (unsafe_yyjson_is_uint(val) && unsafe_yyjson_get_uint(val) > (uint64_t)INT64_MAX) {
        return BT_ERR_TYPE_INT;
    }

    int64_t v = unsafe_yyjson_get_sint(val);
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
    yyjson_val *val = bt_lookup_obj_field(jdoc, key, key_len);
    if (!val || unsafe_yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    if (!unsafe_yyjson_is_num(val)) {
        return BT_ERR_TYPE_FLOAT;
    }

    double v = unsafe_yyjson_get_num(val);
    if (yyjson_unlikely(!isfinite(v))) {
        return BT_ERR_TYPE_FLOAT;
    }
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
    yyjson_val *val = bt_lookup_obj_field(jdoc, key, key_len);
    if (!val || unsafe_yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    if (!unsafe_yyjson_is_bool(val)) {
        return BT_ERR_TYPE_INT;
    }

    *out_val = unsafe_yyjson_get_bool(val) ? 1 : 0;
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
    yyjson_val *val = bt_lookup_obj_field(jdoc, key, key_len);
    if (!val || unsafe_yyjson_is_null(val)) {
        *out_found = 0;
        return BT_ERR_NONE;
    }
    *out_found = 1;

    if (!unsafe_yyjson_is_str(val)) {
        return BT_ERR_STR_MIN_LEN;
    }

    size_t slen = unsafe_yyjson_get_len(val);
    const char *s = unsafe_yyjson_get_str(val);

    /* OWASP ASVS V5: Reject embedded NUL bytes (\u0000) to prevent C/Python truncation mismatches */
    if (yyjson_unlikely(s && slen > 0 && memchr(s, '\0', slen) != NULL)) {
        return BT_ERR_STR_MIN_LEN;
    }

    if (has_min_len && slen < min_len) return BT_ERR_STR_MIN_LEN;
    if (has_max_len && slen > max_len) return BT_ERR_STR_MAX_LEN;

    *out_str = s;
    *out_len = slen;
    return BT_ERR_NONE;
}

yyjson_val *bt_json_get_raw_field(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int *out_found
) {
    yyjson_val *val = bt_lookup_obj_field(jdoc, key, key_len);
    if (!val || unsafe_yyjson_is_null(val)) {
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
    /* Pre-size value pool for up to 63 key-value pairs in the initial chunk */
    yyjson_mut_doc_set_val_pool_size(w->doc, 128);
    w->root = yyjson_mut_obj(w->doc);
    if (!w->root) return -1;
    yyjson_mut_doc_set_root(w->doc, w->root);
    return 0;
}

void bt_json_writer_add_int(bt_json_writer_t *w, const char *key, size_t key_len, int64_t val) {
    yyjson_mut_val *kv = unsafe_yyjson_mut_val(w->doc, 2);
    if (yyjson_unlikely(!kv)) return;
    kv[0].tag = ((uint64_t)key_len << YYJSON_TAG_BIT) | YYJSON_TYPE_STR | YYJSON_SUBTYPE_NOESC;
    kv[0].uni.str = key;
    kv[1].tag = YYJSON_TYPE_NUM | YYJSON_SUBTYPE_SINT;
    kv[1].uni.i64 = val;
    unsafe_yyjson_mut_obj_add(w->root, &kv[0], &kv[1], unsafe_yyjson_get_len(w->root));
}

void bt_json_writer_add_double(bt_json_writer_t *w, const char *key, size_t key_len, double val) {
    yyjson_mut_val *kv = unsafe_yyjson_mut_val(w->doc, 2);
    if (yyjson_unlikely(!kv)) return;
    kv[0].tag = ((uint64_t)key_len << YYJSON_TAG_BIT) | YYJSON_TYPE_STR | YYJSON_SUBTYPE_NOESC;
    kv[0].uni.str = key;
    kv[1].tag = YYJSON_TYPE_NUM | YYJSON_SUBTYPE_REAL;
    kv[1].uni.f64 = val;
    unsafe_yyjson_mut_obj_add(w->root, &kv[0], &kv[1], unsafe_yyjson_get_len(w->root));
}

void bt_json_writer_add_bool(bt_json_writer_t *w, const char *key, size_t key_len, int val) {
    yyjson_mut_val *kv = unsafe_yyjson_mut_val(w->doc, 2);
    if (yyjson_unlikely(!kv)) return;
    kv[0].tag = ((uint64_t)key_len << YYJSON_TAG_BIT) | YYJSON_TYPE_STR | YYJSON_SUBTYPE_NOESC;
    kv[0].uni.str = key;
    kv[1].tag = YYJSON_TYPE_BOOL | (val ? YYJSON_SUBTYPE_TRUE : YYJSON_SUBTYPE_FALSE);
    unsafe_yyjson_mut_obj_add(w->root, &kv[0], &kv[1], unsafe_yyjson_get_len(w->root));
}

void bt_json_writer_add_str(bt_json_writer_t *w, const char *key, size_t key_len,
                            const char *val, size_t val_len) {
    if (!val) {
        bt_json_writer_add_null(w, key, key_len);
        return;
    }
    yyjson_mut_val *kv = unsafe_yyjson_mut_val(w->doc, 2);
    if (yyjson_unlikely(!kv)) return;
    kv[0].tag = ((uint64_t)key_len << YYJSON_TAG_BIT) | YYJSON_TYPE_STR | YYJSON_SUBTYPE_NOESC;
    kv[0].uni.str = key;
    kv[1].tag = ((uint64_t)val_len << YYJSON_TAG_BIT) | YYJSON_TYPE_STR;
    kv[1].uni.str = val;
    unsafe_yyjson_mut_obj_add(w->root, &kv[0], &kv[1], unsafe_yyjson_get_len(w->root));
}

void bt_json_writer_add_null(bt_json_writer_t *w, const char *key, size_t key_len) {
    yyjson_mut_val *kv = unsafe_yyjson_mut_val(w->doc, 2);
    if (yyjson_unlikely(!kv)) return;
    kv[0].tag = ((uint64_t)key_len << YYJSON_TAG_BIT) | YYJSON_TYPE_STR | YYJSON_SUBTYPE_NOESC;
    kv[0].uni.str = key;
    kv[1].tag = YYJSON_TYPE_NULL;
    unsafe_yyjson_mut_obj_add(w->root, &kv[0], &kv[1], unsafe_yyjson_get_len(w->root));
}

int bt_json_writer_finish(bt_json_writer_t *w, char *out_buf, size_t out_cap, size_t *out_len) {
    yyjson_write_err err;
    /* Write directly into caller's arena out_buf without pool allocation or memcpy */
    size_t written = yyjson_mut_write_buf(out_buf, out_cap, w->doc, 0, &err);
    if (yyjson_unlikely(written == 0 && err.code != YYJSON_WRITE_SUCCESS)) {
        return -1;
    }
    *out_len = written;
    return 0;
}
