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
    char *json_ptr = yyjson_mut_write_opts(w->doc, 0, &tls_write_alc, out_len, &err);
    if (!json_ptr || *out_len > out_cap) {
        return -1;
    }
    memcpy(out_buf, json_ptr, *out_len);
    return 0;
}
