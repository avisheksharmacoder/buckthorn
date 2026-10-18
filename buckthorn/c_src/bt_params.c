/* buckthorn/c_src/bt_params.c */
#include "bt_params.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

int bt_find_query_param(const char *query, size_t query_len,
                        const char *key, size_t key_len, bt_slice_t *out_val) {
    out_val->ptr = NULL;
    out_val->len = 0;
    out_val->found = 0;
    if (!query || query_len == 0) return 0;

    const char *cur = query;
    const char *end = query + query_len;

    while (cur < end) {
        const char *amp = (const char *)memchr(cur, '&', (size_t)(end - cur));
        const char *pair_end = amp ? amp : end;
        const char *eq = (const char *)memchr(cur, '=', (size_t)(pair_end - cur));

        if (eq) {
            size_t klen = (size_t)(eq - cur);
            if (klen == key_len && memcmp(cur, key, key_len) == 0) {
                out_val->ptr = eq + 1;
                out_val->len = (size_t)(pair_end - (eq + 1));
                out_val->found = 1;
                return 1;
            }
        }
        if (!amp) break;
        cur = amp + 1;
    }
    return 0;
}

int bt_find_header(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val) {
    out_val->ptr = NULL;
    out_val->len = 0;
    out_val->found = 0;

    for (size_t i = 0; i < num_headers; i++) {
        if (headers[i].name && headers[i].name_len == key_len && key_len > 0) {
            if (((headers[i].name[0] ^ key[0]) & ~0x20) == 0) {
                if (strncasecmp(headers[i].name, key, key_len) == 0) {
                    out_val->ptr = headers[i].value;
                    out_val->len = headers[i].value_len;
                    out_val->found = 1;
                    return 1;
                }
            }
        }
    }
    return 0;
}

int bt_find_cookie(struct phr_header *headers, size_t num_headers,
                   const char *key, size_t key_len, bt_slice_t *out_val) {
    bt_slice_t cookie_hdr;
    if (!bt_find_header(headers, num_headers, "Cookie", 6, &cookie_hdr)) {
        out_val->ptr = NULL;
        out_val->len = 0;
        out_val->found = 0;
        return 0;
    }

    const char *cur = cookie_hdr.ptr;
    const char *end = cookie_hdr.ptr + cookie_hdr.len;

    while (cur < end) {
        while (cur < end && (*cur == ' ' || *cur == '\t')) cur++;
        const char *semi = (const char *)memchr(cur, ';', (size_t)(end - cur));
        const char *pair_end = semi ? semi : end;
        const char *eq = (const char *)memchr(cur, '=', (size_t)(pair_end - cur));

        if (eq) {
            size_t klen = (size_t)(eq - cur);
            if (klen == key_len && memcmp(cur, key, key_len) == 0) {
                out_val->ptr = eq + 1;
                out_val->len = (size_t)(pair_end - (eq + 1));
                out_val->found = 1;
                return 1;
            }
        }
        if (!semi) break;
        cur = semi + 1;
    }

    out_val->ptr = NULL;
    out_val->len = 0;
    out_val->found = 0;
    return 0;
}

bt_val_error_t bt_parse_int64_checked(
    bt_slice_t slice,
    int has_ge, int64_t ge,
    int has_le, int64_t le,
    int has_gt, int64_t gt,
    int has_lt, int64_t lt,
    int64_t *out_val
) {
    if (!slice.found || slice.len == 0 || !slice.ptr) {
        return BT_ERR_TYPE_INT;
    }

    const char *p = slice.ptr;
    size_t rem = slice.len;
    int negative = 0;

    if (*p == '-') {
        negative = 1;
        p++;
        rem--;
    } else if (*p == '+') {
        p++;
        rem--;
    }

    if (rem == 0) {
        return BT_ERR_TYPE_INT;
    }

    uint64_t acc = 0;
    uint64_t limit = negative ? 9223372036854775808ULL : 9223372036854775807ULL;

    while (rem > 0) {
        char c = *p;
        if (c < '0' || c > '9') {
            return BT_ERR_TYPE_INT;
        }
        uint64_t d = (uint64_t)(c - '0');
        if (acc > (limit - d) / 10ULL) {
            return BT_ERR_TYPE_INT;
        }
        acc = acc * 10ULL + d;
        p++;
        rem--;
    }

    int64_t v;
    if (negative) {
        if (acc == 9223372036854775808ULL) {
            v = INT64_MIN;
        } else {
            v = -(int64_t)acc;
        }
    } else {
        v = (int64_t)acc;
    }

    if (has_ge && v < ge) return BT_ERR_NUMBER_GE;
    if (has_le && v > le) return BT_ERR_NUMBER_LE;
    if (has_gt && v <= gt) return BT_ERR_NUMBER_GT;
    if (has_lt && v >= lt) return BT_ERR_NUMBER_LT;

    *out_val = v;
    return BT_ERR_NONE;
}

bt_val_error_t bt_parse_double_checked(
    bt_slice_t slice,
    int has_ge, double ge,
    int has_le, double le,
    int has_gt, double gt,
    int has_lt, double lt,
    double *out_val
) {
    if (!slice.found || slice.len == 0 || slice.len >= 63) {
        return BT_ERR_TYPE_FLOAT;
    }
    char buf[64];
    memcpy(buf, slice.ptr, slice.len);
    buf[slice.len] = '\0';

    char *endptr = NULL;
    errno = 0;
    double v = strtod(buf, &endptr);
    if (errno != 0 || endptr != buf + slice.len) {
        return BT_ERR_TYPE_FLOAT;
    }

    if (has_ge && v < ge) return BT_ERR_NUMBER_GE;
    if (has_le && v > le) return BT_ERR_NUMBER_LE;
    if (has_gt && v <= gt) return BT_ERR_NUMBER_GT;
    if (has_lt && v >= lt) return BT_ERR_NUMBER_LT;

    *out_val = v;
    return BT_ERR_NONE;
}

bt_val_error_t bt_validate_str_checked(
    bt_slice_t slice,
    int has_min_len, size_t min_len,
    int has_max_len, size_t max_len
) {
    if (!slice.found) return BT_ERR_MISSING;
    if (has_min_len && slice.len < min_len) return BT_ERR_STR_MIN_LEN;
    if (has_max_len && slice.len > max_len) return BT_ERR_STR_MAX_LEN;
    return BT_ERR_NONE;
}
