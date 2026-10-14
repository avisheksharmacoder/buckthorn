/* buckthorn/c_src/bt_audit.c */
#include "bt_audit.h"
#include <ctype.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static inline int bt_path_is_safe(const char *path, size_t path_len) {
    if (!path || path_len == 0 || path[0] != '/') return 0;

    for (size_t i = 0; i < path_len; i++) {
        unsigned char c = (unsigned char)path[i];
        /* Reject control characters, spaces, backslashes, or null bytes */
        if (c <= 0x20 || c == 0x7F || c == '\\') {
            return 0;
        }
        /* Reject literal ".." path traversal segments */
        if (c == '.' && (i + 1 < path_len) && path[i + 1] == '.') {
            return 0;
        }
        /* Reject URL-encoded null byte "%00" or dot "%2e" / "%2E" */
        if (c == '%' && (i + 2 < path_len)) {
            char h1 = (char)((unsigned char)path[i + 1] | 0x20);
            char h2 = (char)((unsigned char)path[i + 2] | 0x20);
            if (h1 == '0' && h2 == '0') return 0;
            if (h1 == '2' && h2 == 'e') return 0;
            if (h1 == '5' && h2 == 'c') return 0; /* %5c = backslash */
        }
    }
    return 1;
}

bt_audit_status_t bt_audit_http_request(
    const char *path,
    size_t path_len,
    struct phr_header *headers,
    size_t num_headers,
    size_t body_len,
    size_t max_body_size
) {
    if (path_len > BT_MAX_URI_LEN) {
        return BT_AUDIT_ERR_URI_TOO_LONG;
    }

    if (!bt_path_is_safe(path, path_len)) {
        return BT_AUDIT_ERR_BAD_REQUEST;
    }

    if (max_body_size > 0 && body_len > max_body_size) {
        return BT_AUDIT_ERR_PAYLOAD_TOO_LARGE;
    }

    int host_count = 0;
    int cl_count = 0;
    int te_count = 0;
    size_t parsed_cl = 0;

    for (size_t i = 0; i < num_headers; i++) {
        const char *name = headers[i].name;
        size_t nlen = headers[i].name_len;
        const char *val = headers[i].value;
        size_t vlen = headers[i].value_len;

        if (!name || nlen == 0) {
            /* Reject obs-fold (multiline header continuations, deprecated in RFC 9112) */
            return BT_AUDIT_ERR_BAD_REQUEST;
        }

        /* Reject whitespace before colon in header name (RFC 9112 smuggling vector) */
        if (name[nlen - 1] == ' ' || name[nlen - 1] == '\t') {
            return BT_AUDIT_ERR_BAD_REQUEST;
        }

        char c0 = name[0];
        if (nlen == 4 && (c0 == 'H' || c0 == 'h') && strncasecmp(name, "Host", 4) == 0) {
            host_count++;
            if (host_count > 1) return BT_AUDIT_ERR_BAD_REQUEST;
        } else if (nlen == 14 && (c0 == 'C' || c0 == 'c') && strncasecmp(name, "Content-Length", 14) == 0) {
            cl_count++;
            if (cl_count > 1 || vlen == 0 || vlen > 19) {
                return BT_AUDIT_ERR_BAD_REQUEST;
            }
            /* Strictly require all digits in Content-Length (no +/- or spaces) */
            size_t accum = 0;
            for (size_t j = 0; j < vlen; j++) {
                if (val[j] < '0' || val[j] > '9') {
                    return BT_AUDIT_ERR_BAD_REQUEST;
                }
                size_t digit = (size_t)(val[j] - '0');
                if (accum > (SIZE_MAX - digit) / 10) {
                    return BT_AUDIT_ERR_PAYLOAD_TOO_LARGE;
                }
                accum = accum * 10 + digit;
            }
            parsed_cl = accum;
            if (max_body_size > 0 && parsed_cl > max_body_size) {
                return BT_AUDIT_ERR_PAYLOAD_TOO_LARGE;
            }
        } else if (nlen == 17 && (c0 == 'T' || c0 == 't') && strncasecmp(name, "Transfer-Encoding", 17) == 0) {
            te_count++;
        }
    }

    /* RFC 9112 Section 3.2: HTTP/1.1 requests MUST include exactly one Host header */
    if (host_count != 1) {
        return BT_AUDIT_ERR_BAD_REQUEST;
    }

    /* RFC 9112: Reject requests with both Content-Length and Transfer-Encoding (CL.TE / TE.CL) */
    if (te_count > 0 && cl_count > 0) {
        return BT_AUDIT_ERR_BAD_REQUEST;
    }

    return BT_AUDIT_OK;
}

size_t bt_sanitize_header_str(const char *in, size_t in_len, char *out, size_t out_cap) {
    if (!in || out_cap == 0) return 0;
    size_t j = 0;
    for (size_t i = 0; i < in_len && j < out_cap - 1; i++) {
        char c = in[i];
        /* Strip CR, LF, and NUL bytes to neutralize HTTP Response Splitting */
        if (c == '\r' || c == '\n' || c == '\0') {
            continue;
        }
        out[j++] = c;
    }
    out[j] = '\0';
    return j;
}
