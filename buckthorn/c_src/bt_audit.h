/* buckthorn/c_src/bt_audit.h */
#ifndef BT_AUDIT_H
#define BT_AUDIT_H

#include <stddef.h>
#include <stdint.h>
#include "../c_vendor/picohttpparser.h"

#define BT_MAX_URI_LEN 4096

typedef enum {
    BT_AUDIT_OK = 0,
    BT_AUDIT_ERR_BAD_REQUEST = 400,
    BT_AUDIT_ERR_PAYLOAD_TOO_LARGE = 413,
    BT_AUDIT_ERR_URI_TOO_LONG = 414
} bt_audit_status_t;

/*
 * Audits raw HTTP/1.1 request framing, headers, and URL path in a single C pass:
 * 1. Enforces URI length <= BT_MAX_URI_LEN (414)
 * 2. Blocks path traversal ("..", "%2e%2e", "\\", "%00", null bytes) (400)
 * 3. Enforces exactly 1 Host header in HTTP/1.1 (RFC 9112) (400)
 * 4. Blocks CL.TE / TE.CL HTTP Request Smuggling (both Content-Length & Transfer-Encoding) (400)
 * 5. Blocks duplicate or malformed Content-Length headers (400)
 * 6. Enforces Content-Length and body_len <= max_body_size (413)
 */
bt_audit_status_t bt_audit_http_request(
    const char *path,
    size_t path_len,
    struct phr_header *headers,
    size_t num_headers,
    size_t body_len,
    size_t max_body_size
);

/* Strips \r, \n, and \0 bytes in-place to prevent CRLF HTTP Response Splitting */
size_t bt_sanitize_header_str(const char *in, size_t in_len, char *out, size_t out_cap);

#endif /* BT_AUDIT_H */
