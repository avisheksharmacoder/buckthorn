/* buckthorn/c_src/bt_middleware.c */
#include "bt_middleware.h"
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <time.h>

static const char *RESP_400_HOST =
    "HTTP/1.1 400 Bad Request\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: 32\r\n"
    "Connection: keep-alive\r\n\r\n"
    "{\"detail\":\"Invalid Host header\"}";

static const char *RESP_429_RL =
    "HTTP/1.1 429 Too Many Requests\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: 30\r\n"
    "Connection: keep-alive\r\n\r\n"
    "{\"detail\":\"Too Many Requests\"}";

/* Fast 64-bit FNV-1a hash for client IP rate-limiting buckets */
static inline uint64_t fnv1a_hash(const char *data, size_t len) {
    uint64_t hash = 14695981039346656037ULL;
    for (size_t i = 0; i < len; i++) {
        hash ^= (uint8_t)data[i];
        hash *= 1099511628211ULL;
    }
    return hash ? hash : 1ULL;
}

void bt_middleware_init(bt_middleware_chain_t *mw) {
    memset(mw, 0, sizeof(bt_middleware_chain_t));
}

void bt_middleware_configure_cors(
    bt_middleware_chain_t *mw,
    const char *allow_origin, size_t origin_len,
    const char *allow_methods, size_t methods_len,
    const char *allow_headers, size_t headers_len
) {
    mw->cors_enabled = 1;

    int pre_len = snprintf(
        mw->cors_preflight_wire, sizeof(mw->cors_preflight_wire),
        "HTTP/1.1 204 No Content\r\n"
        "Access-Control-Allow-Origin: %.*s\r\n"
        "Access-Control-Allow-Methods: %.*s\r\n"
        "Access-Control-Allow-Headers: %.*s\r\n"
        "Access-Control-Max-Age: 600\r\n"
        "Content-Length: 0\r\n"
        "Connection: keep-alive\r\n\r\n",
        (int)origin_len, allow_origin,
        (int)methods_len, allow_methods,
        (int)headers_len, allow_headers
    );
    mw->cors_preflight_wire_len = pre_len > 0 ? (size_t)pre_len : 0;

    int app_len = snprintf(
        mw->cors_append_hdrs, sizeof(mw->cors_append_hdrs),
        "Access-Control-Allow-Origin: %.*s\r\n",
        (int)origin_len, allow_origin
    );
    mw->cors_append_hdrs_len = app_len > 0 ? (size_t)app_len : 0;
}

void bt_middleware_configure_ratelimit(
    bt_middleware_chain_t *mw,
    uint32_t max_requests,
    int64_t window_seconds
) {
    mw->ratelimit_enabled = 1;
    mw->rl_max_requests = max_requests;
    mw->rl_window_sec = window_seconds > 0 ? window_seconds : 60;
    memset(mw->rl_buckets, 0, sizeof(mw->rl_buckets));
}

void bt_middleware_configure_trusted_host(
    bt_middleware_chain_t *mw,
    const char *allowed_host, size_t host_len
) {
    mw->trusted_host_enabled = 1;
    size_t clen = host_len < sizeof(mw->allowed_host) - 1 ? host_len : sizeof(mw->allowed_host) - 1;
    memcpy(mw->allowed_host, allowed_host, clen);
    mw->allowed_host[clen] = '\0';
    mw->allowed_host_len = clen;
}

void bt_middleware_configure_sec_headers(
    bt_middleware_chain_t *mw,
    const char *frame_opts, size_t frame_len,
    const char *ctype_opts, size_t ctype_len,
    const char *hsts, size_t hsts_len
) {
    mw->sec_headers_enabled = 1;
    int len = snprintf(
        mw->sec_append_hdrs, sizeof(mw->sec_append_hdrs),
        "X-Frame-Options: %.*s\r\n"
        "X-Content-Type-Options: %.*s\r\n"
        "Strict-Transport-Security: %.*s\r\n",
        (int)frame_len, frame_opts,
        (int)ctype_len, ctype_opts,
        (int)hsts_len, hsts
    );
    mw->sec_append_hdrs_len = len > 0 ? (size_t)len : 0;
}

int bt_middleware_run_pre(
    bt_middleware_chain_t *mw,
    const char *method, size_t method_len,
    struct phr_header *headers, size_t num_headers,
    char *resp_wire_buf, size_t resp_wire_cap,
    size_t *out_wire_len,
    const char **out_body_ptr, size_t *out_body_len,
    int *out_status_code
) {
    (void)resp_wire_cap;

    /* 1. Trusted Host Check in C */
    if (mw->trusted_host_enabled && mw->allowed_host_len > 0) {
        if (!(mw->allowed_host_len == 1 && mw->allowed_host[0] == '*')) {
            bt_slice_t host_hdr;
            if (!bt_find_header(headers, num_headers, "Host", 4, &host_hdr) ||
                host_hdr.len != mw->allowed_host_len ||
                strncasecmp(host_hdr.ptr, mw->allowed_host, mw->allowed_host_len) != 0) {
                size_t len = strlen(RESP_400_HOST);
                memcpy(resp_wire_buf, RESP_400_HOST, len);
                *out_status_code = 400;
                *out_wire_len = len;
                *out_body_ptr = resp_wire_buf + (len - 32);
                *out_body_len = 32;
                return 1;
            }
        }
    }

    /* 2. Pure C CORS Preflight (`OPTIONS`) Fast Return */
    if (mw->cors_enabled && method_len == 7 && memcmp(method, "OPTIONS", 7) == 0) {
        memcpy(resp_wire_buf, mw->cors_preflight_wire, mw->cors_preflight_wire_len);
        *out_status_code = 204;
        *out_wire_len = mw->cors_preflight_wire_len;
        *out_body_ptr = resp_wire_buf + mw->cors_preflight_wire_len;
        *out_body_len = 0;
        return 1;
    }

    /* 3. Pure C FNV-1a Hash Table Rate Limiter */
    if (mw->ratelimit_enabled) {
        bt_slice_t client_ip;
        if (!bt_find_header(headers, num_headers, "X-Forwarded-For", 15, &client_ip)) {
            client_ip.ptr = "127.0.0.1";
            client_ip.len = 9;
        }
        uint64_t h = fnv1a_hash(client_ip.ptr, client_ip.len);
        bt_rl_bucket_t *b = &mw->rl_buckets[h & (BT_RL_BUCKETS - 1)];

        struct timespec ts;
#ifdef CLOCK_MONOTONIC_COARSE
        clock_gettime(CLOCK_MONOTONIC_COARSE, &ts);
#else
        clock_gettime(CLOCK_MONOTONIC, &ts);
#endif
        int64_t now_sec = (int64_t)ts.tv_sec;

        if (b->key_hash != h || (now_sec - b->window_start_sec) >= mw->rl_window_sec) {
            b->key_hash = h;
            b->window_start_sec = now_sec;
            b->count = 1;
        } else {
            b->count++;
            if (b->count > mw->rl_max_requests) {
                size_t len = strlen(RESP_429_RL);
                memcpy(resp_wire_buf, RESP_429_RL, len);
                *out_status_code = 429;
                *out_wire_len = len;
                *out_body_ptr = resp_wire_buf + (len - 30);
                *out_body_len = 30;
                return 1;
            }
        }
    }

    return 0;
}
