/* buckthorn/c_src/bt_params.c */
#include "bt_params.h"
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define BT_DECODE_RING_SIZE 16384

static _Thread_local char tls_decode_ring[BT_DECODE_RING_SIZE];
static _Thread_local size_t tls_decode_offset = 0;

static const int8_t HEX_DECODE_TABLE[256] = {
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
     0, 1, 2, 3, 4, 5, 6, 7, 8, 9,-1,-1,-1,-1,-1,-1,
    -1,10,11,12,13,14,15,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,10,11,12,13,14,15,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1
};

int bt_url_decode_slice(bt_slice_t *slice, int plus_as_space) {
    if (!slice || !slice->found || !slice->ptr || slice->len == 0) {
        return 0;
    }

    const char *src = slice->ptr;
    size_t len = slice->len;

    /* Fast path: if no '%' and no '+', keep the zero-copy wire slice untouched */
    const char *has_pct = (const char *)memchr(src, '%', len);
    const char *has_plus = plus_as_space ? (const char *)memchr(src, '+', len) : NULL;
    if (!has_pct && !has_plus) {
        return 0;
    }

    if (len >= BT_DECODE_RING_SIZE) {
        return -1;
    }
    if (tls_decode_offset + len + 1 > BT_DECODE_RING_SIZE) {
        tls_decode_offset = 0;
    }

    char *dst = tls_decode_ring + tls_decode_offset;
    size_t out_idx = 0;

    for (size_t i = 0; i < len; i++) {
        char c = src[i];
        if (c == '%') {
            if (i + 2 >= len) {
                return -1; /* Truncated %XX sequence */
            }
            int8_t hi = HEX_DECODE_TABLE[(unsigned char)src[i + 1]];
            int8_t lo = HEX_DECODE_TABLE[(unsigned char)src[i + 2]];
            if ((hi | lo) < 0) {
                return -1; /* Invalid hex digit */
            }
            uint8_t decoded = (uint8_t)((hi << 4) | lo);
            if (decoded == 0) {
                return -1; /* Reject %00 null byte injection */
            }
            dst[out_idx++] = (char)decoded;
            i += 2;
        } else if (plus_as_space && c == '+') {
            dst[out_idx++] = ' ';
        } else {
            dst[out_idx++] = c;
        }
    }

    dst[out_idx] = '\0';
    tls_decode_offset += out_idx + 1;
    slice->ptr = dst;
    slice->len = out_idx;
    return 0;
}

int bt_find_query_param(const char *query, size_t query_len,
                        const char *key, size_t key_len, bt_slice_t *out_val) {
    out_val->ptr = NULL;
    out_val->len = 0;
    out_val->found = 0;
    if (!query || query_len == 0 || !key || key_len == 0) return 0;

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
                if (bt_url_decode_slice(out_val, 1) != 0) {
                    out_val->ptr = NULL;
                    out_val->len = 0;
                    out_val->found = 0;
                    return 0;
                }
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
    if (!headers || !key || key_len == 0) return 0;

    const unsigned char k0 = (unsigned char)key[0];
    for (size_t i = 0; i < num_headers; i++) {
        if (headers[i].name && headers[i].name_len == key_len) {
            /* Fast case-insensitive first-byte pre-check before calling strncasecmp */
            if ((((unsigned char)headers[i].name[0] ^ k0) & ~0x20) == 0 &&
                strncasecmp(headers[i].name, key, key_len) == 0) {
                out_val->ptr = headers[i].value;
                out_val->len = headers[i].value_len;
                out_val->found = 1;
                return 1;
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
                if (bt_url_decode_slice(out_val, 0) != 0) {
                    out_val->ptr = NULL;
                    out_val->len = 0;
                    out_val->found = 0;
                    return 0;
                }
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
    if (!slice.found || !slice.ptr || slice.len == 0 || slice.len > 20) {
        return BT_ERR_TYPE_INT;
    }

    const char *p = slice.ptr;
    const char *end = p + slice.len;
    int neg = 0;

    if (*p == '-') {
        neg = 1;
        p++;
        if (p == end) return BT_ERR_TYPE_INT;
    } else if (*p == '+') {
        p++;
        if (p == end) return BT_ERR_TYPE_INT;
    }

    uint64_t acc = 0;
    const uint64_t limit = neg ? ((uint64_t)INT64_MAX + 1ULL) : (uint64_t)INT64_MAX;

    for (; p < end; p++) {
        uint8_t d = (uint8_t)((unsigned char)*p - (unsigned char)'0');
        if (d > 9) {
            return BT_ERR_TYPE_INT;
        }
        if (acc > (limit - (uint64_t)d) / 10ULL) {
            return BT_ERR_TYPE_INT;
        }
        acc = acc * 10ULL + (uint64_t)d;
    }

    int64_t v = neg ? (acc == ((uint64_t)INT64_MAX + 1ULL) ? INT64_MIN : -(int64_t)acc)
                    : (int64_t)acc;

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
    if (!slice.found || !slice.ptr || slice.len == 0 || slice.len >= 63) {
        return BT_ERR_TYPE_FLOAT;
    }

    /* Reject leading or trailing whitespace which strtod() would otherwise silently skip */
    unsigned char first = (unsigned char)slice.ptr[0];
    unsigned char last = (unsigned char)slice.ptr[slice.len - 1];
    if (first <= 0x20 || last <= 0x20) {
        return BT_ERR_TYPE_FLOAT;
    }

    char buf[64];
    memcpy(buf, slice.ptr, slice.len);
    buf[slice.len] = '\0';

    char *endptr = NULL;
    errno = 0;
    double v = strtod(buf, &endptr);
    /* Reject parse errors, trailing garbage, and IEEE-754 NaN / Infinity */
    if (errno != 0 || endptr != buf + slice.len || !isfinite(v)) {
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
