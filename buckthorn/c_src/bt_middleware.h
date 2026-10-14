/* buckthorn/c_src/bt_middleware.h */
#ifndef BT_MIDDLEWARE_H
#define BT_MIDDLEWARE_H

#include <stddef.h>
#include <stdint.h>
#include "bt_params.h"

#define BT_RL_BUCKETS 256

typedef struct {
    uint64_t key_hash;
    int64_t window_start_sec;
    uint32_t count;
} bt_rl_bucket_t;

typedef struct {
    /* 1. Pure C CORS Middleware State */
    int cors_enabled;
    char cors_preflight_wire[1024];
    size_t cors_preflight_wire_len;
    char cors_append_hdrs[512];
    size_t cors_append_hdrs_len;

    /* 2. Pure C Token-Bucket / Fixed-Window Rate Limiter State */
    int ratelimit_enabled;
    uint32_t rl_max_requests;
    int64_t rl_window_sec;
    bt_rl_bucket_t rl_buckets[BT_RL_BUCKETS];

    /* 3. Pure C Trusted Host Middleware State */
    int trusted_host_enabled;
    char allowed_host[256];
    size_t allowed_host_len;

    /* 4. Pure C Security Headers State */
    int sec_headers_enabled;
    char sec_append_hdrs[512];
    size_t sec_append_hdrs_len;
} bt_middleware_chain_t;

void bt_middleware_init(bt_middleware_chain_t *mw);

void bt_middleware_configure_cors(
    bt_middleware_chain_t *mw,
    const char *allow_origin, size_t origin_len,
    const char *allow_methods, size_t methods_len,
    const char *allow_headers, size_t headers_len
);

void bt_middleware_configure_ratelimit(
    bt_middleware_chain_t *mw,
    uint32_t max_requests,
    int64_t window_seconds
);

void bt_middleware_configure_trusted_host(
    bt_middleware_chain_t *mw,
    const char *allowed_host, size_t host_len
);

void bt_middleware_configure_sec_headers(
    bt_middleware_chain_t *mw,
    const char *frame_opts, size_t frame_len,
    const char *ctype_opts, size_t ctype_len,
    const char *hsts, size_t hsts_len
);

/*
 * Executes all enabled C pre-request guards (TrustedHost -> CORS Preflight -> RateLimit).
 * Returns:
 *   0 if request is allowed to proceed to routing/handler.
 *   1 if a short-circuit HTTP response (204, 400, or 429) was written to resp_wire_buf.
 */
int bt_middleware_run_pre(
    bt_middleware_chain_t *mw,
    const char *method, size_t method_len,
    struct phr_header *headers, size_t num_headers,
    char *resp_wire_buf, size_t resp_wire_cap,
    size_t *out_wire_len,
    const char **out_body_ptr, size_t *out_body_len,
    int *out_status_code
);

#endif /* BT_MIDDLEWARE_H */
