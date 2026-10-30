/* buckthorn/c_src/bt_params.h */
#ifndef BT_PARAMS_H
#define BT_PARAMS_H

#include <stddef.h>
#include <stdint.h>
#include "../c_vendor/picohttpparser.h"

#define BT_MAX_PATH_SEGMENTS 16
#define BT_MAX_PATH_PARAMS   8

typedef struct {
    const char *ptr;
    size_t len;
    int found;
} bt_slice_t;

typedef enum {
    BT_ERR_NONE = 0,
    BT_ERR_MISSING = 1,
    BT_ERR_TYPE_INT = 2,
    BT_ERR_TYPE_FLOAT = 3,
    BT_ERR_NUMBER_GE = 4,
    BT_ERR_NUMBER_LE = 5,
    BT_ERR_NUMBER_GT = 6,
    BT_ERR_NUMBER_LT = 7,
    BT_ERR_STR_MIN_LEN = 8,
    BT_ERR_STR_MAX_LEN = 9
} bt_val_error_t;

/* Decodes %XX (and '+' if plus_as_space != 0) in-place via TLS scratch buffer when needed */
int bt_url_decode_slice(bt_slice_t *slice, int plus_as_space);

/* Zero-copy scanners over raw HTTP buffers */
int bt_find_query_param(const char *query, size_t query_len,
                        const char *key, size_t key_len, bt_slice_t *out_val);

int bt_find_header(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val);

int bt_find_cookie(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val);

/* C-level type coercion and numeric/length constraint validators */
bt_val_error_t bt_parse_int64_checked(
    bt_slice_t slice,
    int has_ge, int64_t ge,
    int has_le, int64_t le,
    int has_gt, int64_t gt,
    int has_lt, int64_t lt,
    int64_t *out_val
);

bt_val_error_t bt_parse_double_checked(
    bt_slice_t slice,
    int has_ge, double ge,
    int has_le, double le,
    int has_gt, double gt,
    int has_lt, double lt,
    double *out_val
);

bt_val_error_t bt_validate_str_checked(
    bt_slice_t slice,
    int has_min_len, size_t min_len,
    int has_max_len, size_t max_len
);

#endif /* BT_PARAMS_H */
