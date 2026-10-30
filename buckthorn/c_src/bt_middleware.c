/* buckthorn/c_src/bt_middleware.c */
#include "bt_middleware.h"
#include "bt_audit.h"
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <time.h>

static const char RESP_400_HOST[] =
    "HTTP/1.1 400 Bad Request\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: 32\r\n"
    "Connection: keep-alive\r\n\r\n"
    "{\"detail\":\"Invalid Host header\"}";

static const char RESP_429_RL[] =
    "HTTP/1.1 429 Too Many Requests\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: 30\r\n"
    "Connection: keep-alive\r\n\r\n"
    "{\"detail\":\"Too Many Requests\"}";

static inline size_t bt_clamp_snprintf(int written, size_t buf_cap) {
    if (written <= 0 || buf_cap == 0) return 0;
    if ((size_t)written >= buf_cap) return buf_cap - 1;
    return (size_t)written;
}

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
    char clean_origin[256];
    char clean_methods[256];
    char clean_headers[256];

    size_t o_len = bt_sanitize_header_str(allow_origin, origin_len, clean_origin, sizeof(clean_origin));
    size_t m_len = bt_sanitize_header_str(allow_methods, methods_len, clean_methods, sizeof(clean_methods));
    size_t h_len = bt_sanitize_header_str(allow_headers, headers_len, clean_headers, sizeof(clean_headers));

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
        (int)o_len, clean_origin,
        (int)m_len, clean_methods,
        (int)h_len, clean_headers
    );
    mw->cors_preflight_wire_len = bt_clamp_snprintf(pre_len, sizeof(mw->cors_preflight_wire));

    int app_len = snprintf(
        mw->cors_append_hdrs, sizeof(mw->cors_append_hdrs),
        "Access-Control-Allow-Origin: %.*s\r\n",
        (int)o_len, clean_origin
    );
    mw->cors_append_hdrs_len = bt_clamp_snprintf(app_len, sizeof(mw->cors_append_hdrs));
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
    mw->allowed_host_len = bt_sanitize_header_str(
        allowed_host, host_len, mw->allowed_host, sizeof(mw->allowed_host)
    );
}

void bt_middleware_configure_sec_headers(
    bt_middleware_chain_t *mw,
    const char *frame_opts, size_t frame_len,
    const char *ctype_opts, size_t ctype_len,
    const char *hsts, size_t hsts_len
) {
    char clean_frame[128];
    char clean_ctype[128];
    char clean_hsts[256];

    size_t f_len = bt_sanitize_header_str(frame_opts, frame_len, clean_frame, sizeof(clean_frame));
    size_t c_len = bt_sanitize_header_str(ctype_opts, ctype_len, clean_ctype, sizeof(clean_ctype));
    size_t h_len = bt_sanitize_header_str(hsts, hsts_len, clean_hsts, sizeof(clean_hsts));

    mw->sec_headers_enabled = 1;
    int len = snprintf(
        mw->sec_append_hdrs, sizeof(mw->sec_append_hdrs),
        "X-Frame-Options: %.*s\r\n"
        "X-Content-Type-Options: %.*s\r\n"
        "Strict-Transport-Security: %.*s\r\n",
        (int)f_len, clean_frame,
        (int)c_len, clean_ctype,
        (int)h_len, clean_hsts
    );
    mw->sec_append_hdrs_len = bt_clamp_snprintf(len, sizeof(mw->sec_append_hdrs));
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
                const size_t len = sizeof(RESP_400_HOST) - 1;
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
        if (!bt_find_header(headers, num_headers, "X-Forwarded-For", 15, &client_ip) ||
            client_ip.len == 0) {
            client_ip.ptr = "127.0.0.1";
            client_ip.len = 9;
        } else {
            /* Extract only the first IP in X-Forwarded-For and trim whitespace */
            const char *comma = (const char *)memchr(client_ip.ptr, ',', client_ip.len);
            if (comma) {
                client_ip.len = (size_t)(comma - client_ip.ptr);
            }
            while (client_ip.len > 0 && (*client_ip.ptr == ' ' || *client_ip.ptr == '\t')) {
                client_ip.ptr++;
                client_ip.len--;
            }
            while (client_ip.len > 0 &&
                   (client_ip.ptr[client_ip.len - 1] == ' ' ||
                    client_ip.ptr[client_ip.len - 1] == '\t')) {
                client_ip.len--;
            }
            if (client_ip.len == 0) {
                client_ip.ptr = "127.0.0.1";
                client_ip.len = 9;
            }
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

        if (b->key_hash == 0 || (now_sec - b->window_start_sec) >= mw->rl_window_sec) {
            b->key_hash = h;
            b->window_start_sec = now_sec;
            b->count = 1;
        } else {
            /* Active window: never reset count on hash collision */
            b->key_hash = h;
            b->count++;
            if (b->count > mw->rl_max_requests) {
                const size_t len = sizeof(RESP_429_RL) - 1;
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
