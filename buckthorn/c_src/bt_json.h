/* buckthorn/c_src/bt_json.h */
#ifndef BT_JSON_H
#define BT_JSON_H

#include <stddef.h>
#include <stdint.h>
#include "bt_params.h"
#include "../c_vendor/yyjson.h"

/* Opaque handle wrapping yyjson_doc and root yyjson_val */
typedef struct {
    yyjson_doc *doc;
    yyjson_val *root;
    yyjson_obj_iter iter;
} bt_json_doc_t;

/* Mutable JSON writer backed by zero-malloc C pool buffer */
typedef struct {
    yyjson_mut_doc *doc;
    yyjson_mut_val *root;
} bt_json_writer_t;

/* Parse raw HTTP body bytes into a yyjson document using a zero-malloc C pool */
int bt_json_parse_obj(const char *buf, size_t len, bt_json_doc_t *out_doc);

/* Parse mutable buffer in-place using YYJSON_READ_INSITU */
int bt_json_parse_obj_insitu(char *buf, size_t len, bt_json_doc_t *out_doc);

/* Single-pass C field lookup + type check + constraint validation */
bt_val_error_t bt_json_get_int64_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_ge, int64_t ge,
    int has_le, int64_t le,
    int has_gt, int64_t gt,
    int has_lt, int64_t lt,
    int *out_found,
    int64_t *out_val
);

bt_val_error_t bt_json_get_double_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_ge, double ge,
    int has_le, double le,
    int has_gt, double gt,
    int has_lt, double lt,
    int *out_found,
    double *out_val
);

bt_val_error_t bt_json_get_bool_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int *out_found,
    int *out_val
);

bt_val_error_t bt_json_get_str_checked(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int has_min_len, size_t min_len,
    int has_max_len, size_t max_len,
    int *out_found,
    const char **out_str,
    size_t *out_len
);

yyjson_val *bt_json_get_raw_field(
    bt_json_doc_t *jdoc,
    const char *key, size_t key_len,
    int *out_found
);

/* Direct-to-buffer yyjson response writer */
int bt_json_writer_init(bt_json_writer_t *w);
void bt_json_writer_add_int(bt_json_writer_t *w, const char *key, size_t key_len, int64_t val);
void bt_json_writer_add_double(bt_json_writer_t *w, const char *key, size_t key_len, double val);
void bt_json_writer_add_bool(bt_json_writer_t *w, const char *key, size_t key_len, int val);
void bt_json_writer_add_str(bt_json_writer_t *w, const char *key, size_t key_len,
                            const char *val, size_t val_len);
void bt_json_writer_add_null(bt_json_writer_t *w, const char *key, size_t key_len);
int bt_json_writer_finish(bt_json_writer_t *w, char *out_buf, size_t out_cap, size_t *out_len);

#endif /* BT_JSON_H */
