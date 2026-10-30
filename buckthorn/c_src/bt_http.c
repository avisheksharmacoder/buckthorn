/* buckthorn/c_src/bt_http.c */
#include "bt_http.h"
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <uv.h>

#if defined(__GNUC__) || defined(__clang__)
#define BT_LIKELY(x)   __builtin_expect(!!(x), 1)
#define BT_UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define BT_LIKELY(x)   (x)
#define BT_UNLIKELY(x) (x)
#endif

static const char RESP_400[] =
    "HTTP/1.1 400 Bad Request\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Content-Length: 11\r\n"
    "Connection: close\r\n\r\n"
    "Bad Request";

static const char RESP_404[] =
    "HTTP/1.1 404 Not Found\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Content-Length: 9\r\n"
    "Connection: keep-alive\r\n\r\n"
    "Not Found";

static const char RESP_413[] =
    "HTTP/1.1 413 Content Too Large\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Content-Length: 17\r\n"
    "Connection: close\r\n\r\n"
    "Content Too Large";

static const char RESP_414[] =
    "HTTP/1.1 414 URI Too Long\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Content-Length: 12\r\n"
    "Connection: close\r\n\r\n"
    "URI Too Long";

static const char RESP_500[] =
    "HTTP/1.1 500 Internal Server Error\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Content-Length: 21\r\n"
    "Connection: close\r\n\r\n"
    "Internal Server Error";

static inline char *bt_write_u64_ascii(char *p, size_t val) {
    char tmp[24];
    int i = 0;
    if (val == 0) {
        *p++ = '0';
        return p;
    }
    while (val > 0) {
        tmp[i++] = (char)('0' + (val % 10));
        val /= 10;
    }
    while (i > 0) {
        *p++ = tmp[--i];
    }
    return p;
}

static size_t split_url_segments(const char *path, size_t path_len, bt_slice_t *out_segs) {
    size_t count = 0;
    const char *cur = path;
    const char *end = path + path_len;
    while (cur < end && *cur == '/') cur++;

    while (cur < end && count < BT_MAX_PATH_SEGMENTS) {
        const char *slash = (const char *)memchr(cur, '/', (size_t)(end - cur));
        const char *seg_end = slash ? slash : end;
        if (seg_end > cur) {
            out_segs[count].ptr = cur;
            out_segs[count].len = (size_t)(seg_end - cur);
            out_segs[count].found = 1;
            count++;
        }
        if (!slash) break;
        cur = slash + 1;
    }
    return count;
}

bt_router_t *bt_router_create(bt_handler_invoke_fn invoke_cb) {
    bt_router_t *r = (bt_router_t *)calloc(1, sizeof(bt_router_t));
    if (!r) return NULL;
    r->tree = raxNew();
    if (!r->tree) {
        free(r);
        return NULL;
    }
    r->invoke_cb = invoke_cb;
    r->max_body_size = 1024 * 1024;
    bt_middleware_init(&r->mw);
    return r;
}

void bt_router_set_max_body_size(bt_router_t *router, size_t max_body_size) {
    if (router) router->max_body_size = max_body_size;
}

void bt_router_set_async_tick_cb(bt_router_t *router, bt_async_tick_fn tick_cb) {
    if (router) router->tick_cb = tick_cb;
}

bt_middleware_chain_t *bt_router_get_mw(bt_router_t *router) {
    return router ? &router->mw : NULL;
}

void bt_router_destroy(bt_router_t *router) {
    if (!router) return;
    if (router->tree) raxFree(router->tree);
    free(router);
}

int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                     const char *path, size_t path_len, void *handler_obj) {
    if (!router || !method || !path || method_len == 0 ||
        method_len >= sizeof(router->param_routes[0].method) || path_len == 0) {
        return -1;
    }

    /* If route contains '{', register in parameterized C segment matcher */
    if (memchr(path, '{', path_len) != NULL) {
        if (router->num_param_routes >= BT_MAX_PARAM_ROUTES) return -1;

        bt_slice_t segs[BT_MAX_PATH_SEGMENTS];
        size_t num_segs = split_url_segments(path, path_len, segs);
        for (size_t i = 0; i < num_segs; i++) {
            if (segs[i].len >= sizeof(router->param_routes[0].segments[0].seg_str)) {
                return -1; /* Prevent fixed struct buffer overflow */
            }
        }

        bt_param_route_t *pr = &router->param_routes[router->num_param_routes++];
        memcpy(pr->method, method, method_len);
        pr->method[method_len] = '\0';
        pr->method_len = method_len;
        pr->handler_obj = handler_obj;
        pr->num_segments = num_segs;

        for (size_t i = 0; i < num_segs; i++) {
            pr->segments[i].seg_len = segs[i].len;
            memcpy(pr->segments[i].seg_str, segs[i].ptr, segs[i].len);
            pr->segments[i].seg_str[segs[i].len] = '\0';
            pr->segments[i].is_param = (segs[i].len > 2 && segs[i].ptr[0] == '{' &&
                                        segs[i].ptr[segs[i].len - 1] == '}') ? 1 : 0;
        }
        return 0;
    }

    /* Static route fast path -> insert directly into Redis rax radix tree */
    unsigned char key[512];
    if (method_len + 1 + path_len >= sizeof(key)) return -1;
    memcpy(key, method, method_len);
    key[method_len] = ':';
    memcpy(key + method_len + 1, path, path_len);
    size_t key_len = method_len + 1 + path_len;

    raxInsert(router->tree, key, key_len, handler_obj, NULL);
    return 0;
}

static void *bt_match_param_route(
    bt_router_t *router,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    bt_slice_t *out_params, size_t *out_num_params
) {
    *out_num_params = 0;
    if (router->num_param_routes == 0) return raxNotFound;

    bt_slice_t req_segs[BT_MAX_PATH_SEGMENTS];
    size_t num_req_segs = 0;
    int segs_split = 0;

    for (size_t r = 0; r < router->num_param_routes; r++) {
        bt_param_route_t *pr = &router->param_routes[r];
        if (pr->method_len != method_len || pr->method[0] != method[0]) continue;
        if (memcmp(pr->method, method, method_len) != 0) continue;

        if (!segs_split) {
            num_req_segs = split_url_segments(path, path_len, req_segs);
            segs_split = 1;
        }
        if (pr->num_segments != num_req_segs) continue;

        size_t p_idx = 0;
        int matched = 1;
        for (size_t s = 0; s < num_req_segs; s++) {
            if (pr->segments[s].is_param) {
                if (p_idx < BT_MAX_PATH_PARAMS) {
                    out_params[p_idx] = req_segs[s];
                    if (bt_url_decode_slice(&out_params[p_idx], 0) != 0) {
                        matched = 0;
                        break;
                    }
                    p_idx++;
                }
            } else {
                if (pr->segments[s].seg_len != req_segs[s].len ||
                    memcmp(pr->segments[s].seg_str, req_segs[s].ptr, req_segs[s].len) != 0) {
                    matched = 0;
                    break;
                }
            }
        }
        if (matched) {
            *out_num_params = p_idx;
            return pr->handler_obj;
        }
    }
    return raxNotFound;
}

static inline const char *bt_status_reason(int status_code) {
    switch (status_code) {
        case 200: return "OK";
        case 201: return "Created";
        case 204: return "No Content";
        case 400: return "Bad Request";
        case 401: return "Unauthorized";
        case 403: return "Forbidden";
        case 404: return "Not Found";
        case 409: return "Conflict";
        case 413: return "Content Too Large";
        case 414: return "URI Too Long";
        case 422: return "Unprocessable Entity";
        case 429: return "Too Many Requests";
        case 500: return "Internal Server Error";
        default:  return "OK";
    }
}

static inline int64_t bt_monotonic_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;
}

void bt_router_set_access_logger(bt_router_t *router, bt_logger_t *logger) {
    if (router) {
        router->access_logger = logger;
    }
}

static int bt_format_http_wire_response(
    bt_router_t *router,
    int status_code,
    const char *handler_out_buf,
    size_t handler_out_len,
    const char *extra_hdrs_buf,
    size_t extra_hdrs_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
) {
    if (BT_UNLIKELY(resp_wire_cap < 256)) {
        goto wire_overflow;
    }

    char *p = resp_wire_buf;
    if (BT_LIKELY(status_code == 200)) {
        memcpy(p, "HTTP/1.1 200 OK\r\nContent-Length: ", 33);
        p += 33;
    } else {
        const char *status_text = bt_status_reason(status_code);
        size_t st_len = strlen(status_text);
        memcpy(p, "HTTP/1.1 ", 9);
        p += 9;
        p = bt_write_u64_ascii(p, (size_t)(status_code > 0 ? status_code : 500));
        *p++ = ' ';
        memcpy(p, status_text, st_len);
        p += st_len;
        memcpy(p, "\r\nContent-Length: ", 18);
        p += 18;
    }

    p = bt_write_u64_ascii(p, handler_out_len);
    memcpy(p, "\r\nConnection: keep-alive\r\n", 26);
    p += 26;

    size_t hdr_len = (size_t)(p - resp_wire_buf);

    if (router->mw.cors_enabled && router->mw.cors_append_hdrs_len > 0) {
        if (hdr_len + router->mw.cors_append_hdrs_len >= resp_wire_cap) goto wire_overflow;
        memcpy(resp_wire_buf + hdr_len, router->mw.cors_append_hdrs, router->mw.cors_append_hdrs_len);
        hdr_len += router->mw.cors_append_hdrs_len;
    }
    if (router->mw.sec_headers_enabled && router->mw.sec_append_hdrs_len > 0) {
        if (hdr_len + router->mw.sec_append_hdrs_len >= resp_wire_cap) goto wire_overflow;
        memcpy(resp_wire_buf + hdr_len, router->mw.sec_append_hdrs, router->mw.sec_append_hdrs_len);
        hdr_len += router->mw.sec_append_hdrs_len;
    }
    if (extra_hdrs_len > 0 && extra_hdrs_buf) {
        if (hdr_len + extra_hdrs_len >= resp_wire_cap) goto wire_overflow;
        memcpy(resp_wire_buf + hdr_len, extra_hdrs_buf, extra_hdrs_len);
        hdr_len += extra_hdrs_len;
    }

    if (BT_UNLIKELY(hdr_len + 2 + handler_out_len > resp_wire_cap)) {
    wire_overflow: ;
        const size_t len = sizeof(RESP_500) - 1;
        memcpy(resp_wire_buf, RESP_500, len);
        out_result->status_code = 500;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 21);
        out_result->body_len = 21;
        return -1;
    }

    resp_wire_buf[hdr_len++] = '\r';
    resp_wire_buf[hdr_len++] = '\n';

    if (handler_out_len > 0 && handler_out_buf) {
        memcpy(resp_wire_buf + hdr_len, handler_out_buf, handler_out_len);
    }
    out_result->status_code = status_code;
    out_result->http_wire_buf = resp_wire_buf;
    out_result->http_wire_len = hdr_len + handler_out_len;
    out_result->body_ptr = resp_wire_buf + hdr_len;
    out_result->body_len = handler_out_len;
    return 0;
}

int bt_handle_http_bytes_ex(
    bt_router_t *router,
    void *async_client_ctx,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
) {
    int64_t t_start_ns = router->access_logger ? bt_monotonic_ns() : 0;
    bt_arena_reset(arena);

    const char *method = NULL;
    size_t method_len = 0;
    const char *path = NULL;
    size_t path_len = 0;
    int minor_version = 0;

    /* Allocate headers and path_params in the arena so they remain valid across async suspension */
    struct phr_header *headers = (struct phr_header *)bt_arena_alloc(
        arena, sizeof(struct phr_header) * BT_MAX_HEADERS
    );
    bt_slice_t *path_params = (bt_slice_t *)bt_arena_alloc(
        arena, sizeof(bt_slice_t) * BT_MAX_PATH_PARAMS
    );
    if (BT_UNLIKELY(!headers || !path_params)) {
        return -2;
    }
    size_t num_headers = BT_MAX_HEADERS;

    int pret = phr_parse_request(
        raw_req, raw_req_len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers, 0
    );

    if (BT_UNLIKELY(pret <= 0 || method_len == 0 || method_len >= 16)) {
        const size_t len = sizeof(RESP_400) - 1;
        memcpy(resp_wire_buf, RESP_400, len);
        out_result->status_code = 400;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 11);
        out_result->body_len = 11;
        bt_arena_reset(arena);
        return -1;
    }

    const char *body_ptr = raw_req + pret;
    size_t body_len = raw_req_len - (size_t)pret;

    bt_audit_status_t audit_rc = bt_audit_http_request(
        path, path_len, headers, num_headers, body_len, router->max_body_size
    );
    if (BT_UNLIKELY(audit_rc != BT_AUDIT_OK)) {
        const char *err_wire = (audit_rc == BT_AUDIT_ERR_PAYLOAD_TOO_LARGE) ? RESP_413 :
                               (audit_rc == BT_AUDIT_ERR_URI_TOO_LONG) ? RESP_414 : RESP_400;
        size_t w_len = (audit_rc == BT_AUDIT_ERR_PAYLOAD_TOO_LARGE) ? (sizeof(RESP_413) - 1) :
                       (audit_rc == BT_AUDIT_ERR_URI_TOO_LONG) ? (sizeof(RESP_414) - 1) : (sizeof(RESP_400) - 1);
        size_t b_len = (audit_rc == BT_AUDIT_ERR_PAYLOAD_TOO_LARGE) ? 17 :
                       (audit_rc == BT_AUDIT_ERR_URI_TOO_LONG) ? 12 : 11;
        memcpy(resp_wire_buf, err_wire, w_len);
        out_result->status_code = (int)audit_rc;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = w_len;
        out_result->body_ptr = resp_wire_buf + (w_len - b_len);
        out_result->body_len = b_len;
        bt_arena_reset(arena);
        return -1;
    }

    const char *query = NULL;
    size_t query_len = 0;
    size_t clean_path_len = path_len;
    const char *qmark = (const char *)memchr(path, '?', path_len);
    if (qmark) {
        clean_path_len = (size_t)(qmark - path);
        query = qmark + 1;
        query_len = path_len - clean_path_len - 1;
    }

    /* Execute Tier-1 Pure C Pre-Request Middlewares (TrustedHost, CORS OPTIONS, RateLimit) */
    if (router->mw.trusted_host_enabled || router->mw.cors_enabled || router->mw.ratelimit_enabled) {
        size_t mw_wire_len = 0;
        const char *mw_body_ptr = NULL;
        size_t mw_body_len = 0;
        int mw_status = 200;

        if (bt_middleware_run_pre(
                &router->mw,
                method, method_len,
                headers, num_headers,
                resp_wire_buf, resp_wire_cap,
                &mw_wire_len,
                &mw_body_ptr, &mw_body_len,
                &mw_status
            ) != 0) {
            out_result->status_code = mw_status;
            out_result->http_wire_buf = resp_wire_buf;
            out_result->http_wire_len = mw_wire_len;
            out_result->body_ptr = mw_body_ptr;
            out_result->body_len = mw_body_len;
            if (router->access_logger) {
                bt_logger_log_http_access(
                    router->access_logger, method, method_len,
                    path, clean_path_len, mw_status, bt_monotonic_ns() - t_start_ns
                );
            }
            bt_arena_reset(arena);
            return 0;
        }
    }

    unsigned char lookup_key[512];
    size_t key_len = method_len + 1 + clean_path_len;
    size_t num_path_params = 0;
    void *handler_obj = raxNotFound;

    /* 1. Try O(K) static route lookup in Redis rax using stack buffer */
    if (BT_LIKELY(key_len < sizeof(lookup_key))) {
        memcpy(lookup_key, method, method_len);
        lookup_key[method_len] = ':';
        memcpy(lookup_key + method_len + 1, path, clean_path_len);
        handler_obj = raxFind(router->tree, lookup_key, key_len);
    }

    /* 2. Fallback to zero-copy C segment matcher for parameterized routes */
    if (handler_obj == raxNotFound) {
        handler_obj = bt_match_param_route(
            router, method, method_len, path, clean_path_len, path_params, &num_path_params
        );
    }

    if (handler_obj == raxNotFound) {
        const size_t len = sizeof(RESP_404) - 1;
        memcpy(resp_wire_buf, RESP_404, len);
        out_result->status_code = 404;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 9);
        out_result->body_len = 9;
        if (router->access_logger) {
            bt_logger_log_http_access(
                router->access_logger, method, method_len,
                path, clean_path_len, 404, bt_monotonic_ns() - t_start_ns
            );
        }
        bt_arena_reset(arena);
        return 0;
    }

    char *handler_out_buf = (char *)bt_arena_alloc(arena, 32 * 1024);
    size_t handler_out_len = 0;
    char *extra_hdrs_buf = (char *)bt_arena_alloc(arena, 2048);
    size_t extra_hdrs_len = 0;
    int status_code = 200;

    if (BT_UNLIKELY(!handler_out_buf || !extra_hdrs_buf)) {
        const size_t len = sizeof(RESP_500) - 1;
        memcpy(resp_wire_buf, RESP_500, len);
        out_result->status_code = 500;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 21);
        out_result->body_len = 21;
        bt_arena_reset(arena);
        return -1;
    }

    int rc = router->invoke_cb(
        handler_obj,
        async_client_ctx,
        method, method_len,
        path, clean_path_len,
        query, query_len,
        headers, num_headers,
        path_params, num_path_params,
        body_ptr, body_len,
        handler_out_buf, &handler_out_len,
        extra_hdrs_buf, &extra_hdrs_len,
        &status_code
    );

    if (rc == BT_HANDLER_SUSPENDED) {
        /* Keep arena alive while suspended; bt_uv_complete_async_request resets it on completion */
        return BT_HANDLER_SUSPENDED;
    }

    if (BT_UNLIKELY(rc != 0)) {
        const size_t len = sizeof(RESP_500) - 1;
        memcpy(resp_wire_buf, RESP_500, len);
        out_result->status_code = 500;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 21);
        out_result->body_len = 21;
        bt_arena_reset(arena);
        return -1;
    }

    if (bt_format_http_wire_response(
            router, status_code,
            handler_out_buf, handler_out_len,
            extra_hdrs_buf, extra_hdrs_len,
            resp_wire_buf, resp_wire_cap,
            out_result
        ) != 0) {
        bt_arena_reset(arena);
        return -1;
    }

    if (router->access_logger) {
        bt_logger_log_http_access(
            router->access_logger, method, method_len,
            path, clean_path_len, status_code, bt_monotonic_ns() - t_start_ns
        );
    }

    bt_arena_reset(arena);
    return 0;
}

int bt_handle_http_bytes(
    bt_router_t *router,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
) {
    return bt_handle_http_bytes_ex(
        router, NULL, arena, raw_req, raw_req_len, resp_wire_buf, resp_wire_cap, out_result
    );
}

/* =========================================================================
 * Native libuv TCP Server Glue
 * (15s Slowloris Timer + HTTP/1.1 Pipelining + Dual-Handle Refcounting)
 * ========================================================================= */
#define BT_TCP_READ_BUF_SIZE      (64 * 1024)
#define BT_CLIENT_IDLE_TIMEOUT_MS 15000

typedef struct {
    uv_tcp_t handle;
    uv_timer_t idle_timer;
    bt_router_t *router;
    uv_idle_t *async_idler;
    bt_arena_t arena;
    size_t read_len;
    size_t suspended_req_len;
    char read_buf[BT_TCP_READ_BUF_SIZE];
    char write_buf[BT_RESP_BUF_SIZE];
    uv_write_t write_req;
    int in_flight_async_reqs;
    int open_handles;
    int is_Reading;
    int is_closing;
} bt_uv_client_t;

typedef struct {
    bt_router_t *router;
    uv_idle_t async_idler;
} bt_uv_server_ctx_t;

static void on_alloc_buffer(uv_handle_t *handle, size_t suggested_size, uv_buf_t *buf);
static void on_uv_read(uv_stream_t *stream, ssize_t nread, const uv_buf_t *buf);
static void on_write_complete(uv_write_t *req, int status);
static void on_uv_async_idle_tick(uv_idle_t *handle);
static void bt_process_client_buffer(bt_uv_client_t *client);

static void on_client_handle_closed(uv_handle_t *handle) {
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    if (!client) return;
    client->open_handles--;
    if (client->open_handles <= 0 && client->in_flight_async_reqs <= 0) {
        bt_arena_destroy(&client->arena);
        free(client);
    }
}

static void bt_initiate_client_close(bt_uv_client_t *client) {
    if (!client) return;
    client->is_closing = 1;
    if (client->is_Reading) {
        uv_read_stop((uv_stream_t *)&client->handle);
        client->is_Reading = 0;
    }
    uv_timer_stop(&client->idle_timer);

    if (!uv_is_closing((const uv_handle_t *)&client->idle_timer)) {
        uv_close((uv_handle_t *)&client->idle_timer, on_client_handle_closed);
    }
    if (!uv_is_closing((const uv_handle_t *)&client->handle)) {
        uv_close((uv_handle_t *)&client->handle, on_client_handle_closed);
    }
}

static void on_client_idle_timeout(uv_timer_t *timer) {
    bt_uv_client_t *client = (bt_uv_client_t *)timer->data;
    /* Close Slowloris or idle keep-alive connection after 15 seconds of inactivity */
    bt_initiate_client_close(client);
}

static inline void bt_refresh_idle_timer(bt_uv_client_t *client) {
    if (!client->is_closing && !uv_is_closing((const uv_handle_t *)&client->idle_timer)) {
        uv_timer_start(&client->idle_timer, on_client_idle_timeout, BT_CLIENT_IDLE_TIMEOUT_MS, 0);
    }
}

static inline void bt_consume_read_bytes(bt_uv_client_t *client, size_t consumed) {
    if (consumed == 0 || client->read_len == 0) return;
    if (client->read_len > consumed) {
        memmove(client->read_buf, client->read_buf + consumed, client->read_len - consumed);
        client->read_len -= consumed;
    } else {
        client->read_len = 0;
    }
}

/*
 * Inspects accumulated TCP bytes to determine if at least one full HTTP/1.1 request is buffered:
 *   Returns  0 if headers or Content-Length body are still incomplete (wait for next TCP packet)
 *   Returns  1 if a complete request of length *out_total_req_len is ready at buf[0 .. *out_total_req_len]
 *   Returns -1 if framing is malformed or exceeds buffer limits (dispatch immediately to emit 400/413)
 */
static int bt_tcp_request_is_complete(
    const char *buf,
    size_t len,
    size_t max_body_size,
    size_t *out_total_req_len
) {
    *out_total_req_len = len;
    const char *method = NULL;
    size_t method_len = 0;
    const char *path = NULL;
    size_t path_len = 0;
    int minor_version = 0;
    struct phr_header headers[BT_MAX_HEADERS];
    size_t num_headers = BT_MAX_HEADERS;

    int pret = phr_parse_request(
        buf, len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers, 0
    );

    if (pret == -2) {
        if (len >= BT_TCP_READ_BUF_SIZE) return -1;
        return 0; /* Partial HTTP headers; wait for next TCP read */
    }
    if (pret <= 0) {
        return -1; /* Malformed HTTP framing */
    }

    size_t hdr_bytes = (size_t)pret;
    size_t avail_body = len - hdr_bytes;
    size_t expected_cl = 0;
    int has_cl = 0;

    for (size_t i = 0; i < num_headers; i++) {
        if (headers[i].name && headers[i].name_len == 14 &&
            (((unsigned char)headers[i].name[0] | 0x20) == 'c') &&
            strncasecmp(headers[i].name, "Content-Length", 14) == 0) {
            if (has_cl || headers[i].value_len == 0 || headers[i].value_len > 19) {
                return -1;
            }
            has_cl = 1;
            for (size_t j = 0; j < headers[i].value_len; j++) {
                char c = headers[i].value[j];
                if (c < '0' || c > '9') return -1;
                size_t d = (size_t)(c - '0');
                if (expected_cl > (SIZE_MAX - d) / 10) return -1;
                expected_cl = expected_cl * 10 + d;
            }
        }
    }

    if (has_cl) {
        if (max_body_size > 0 && expected_cl > max_body_size) {
            return -1; /* Let bt_handle_http_bytes_ex emit 413 */
        }
        if (hdr_bytes + expected_cl > BT_TCP_READ_BUF_SIZE) {
            return -1;
        }
        if (avail_body < expected_cl) {
            return 0; /* Partial HTTP body; wait for remaining TCP packets */
        }
        *out_total_req_len = hdr_bytes + expected_cl;
    } else {
        /* Exact boundary for bodyless GET/HEAD/DELETE/OPTIONS in a pipelined stream */
        *out_total_req_len = hdr_bytes;
    }

    return 1;
}

static void on_alloc_buffer(uv_handle_t *handle, size_t suggested_size, uv_buf_t *buf) {
    (void)suggested_size;
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    if (client->read_len >= sizeof(client->read_buf)) {
        buf->base = NULL;
        buf->len = 0;
    } else {
        buf->base = client->read_buf + client->read_len;
        buf->len = sizeof(client->read_buf) - client->read_len;
    }
}

static void on_write_complete(uv_write_t *req, int status) {
    bt_uv_client_t *client = (bt_uv_client_t *)req->data;
    if (!client) return;

    if (status < 0 || client->is_closing) {
        bt_initiate_client_close(client);
        return;
    }

    bt_refresh_idle_timer(client);

    /* If pipelined requests remain in client->read_buf, process them immediately */
    if (client->read_len > 0 && client->in_flight_async_reqs == 0) {
        bt_process_client_buffer(client);
        return;
    }

    if (!client->is_closing && !client->is_Reading &&
        !uv_is_closing((const uv_handle_t *)&client->handle)) {
        uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
        client->is_Reading = 1;
    }
}

void bt_uv_complete_async_request(
    void *async_client_ctx,
    const char *body_buf,
    size_t body_len,
    const char *extra_hdrs_buf,
    size_t extra_hdrs_len,
    int status_code
) {
    if (!async_client_ctx) return;
    bt_uv_client_t *client = (bt_uv_client_t *)async_client_ctx;
    client->in_flight_async_reqs--;

    /* Safe to consume the suspended request's bytes and reset the arena now */
    bt_consume_read_bytes(client, client->suspended_req_len);
    client->suspended_req_len = 0;
    bt_arena_reset(&client->arena);

    if (client->is_closing || uv_is_closing((const uv_handle_t *)&client->handle)) {
        if (client->open_handles <= 0 && client->in_flight_async_reqs <= 0) {
            bt_arena_destroy(&client->arena);
            free(client);
        } else {
            bt_initiate_client_close(client);
        }
        return;
    }

    bt_raw_response_t result;
    if (bt_format_http_wire_response(
            client->router,
            status_code,
            body_buf, body_len,
            extra_hdrs_buf, extra_hdrs_len,
            client->write_buf, sizeof(client->write_buf),
            &result
        ) != 0) {
        client->is_closing = 1;
    }

    client->write_req.data = client;
    uv_buf_t wbuf = uv_buf_init(result.http_wire_buf, (unsigned int)result.http_wire_len);
    uv_write(&client->write_req, (uv_stream_t *)&client->handle, &wbuf, 1, on_write_complete);
}

static void bt_process_client_buffer(bt_uv_client_t *client) {
    while (client->read_len > 0 && !client->is_closing && client->in_flight_async_reqs == 0) {
        size_t req_len = client->read_len;
        int ready = bt_tcp_request_is_complete(
            client->read_buf,
            client->read_len,
            client->router->max_body_size,
            &req_len
        );
        if (ready == 0) {
            /* Incomplete request: ensure socket reading is active and wait for next packet */
            if (!client->is_Reading && !uv_is_closing((const uv_handle_t *)&client->handle)) {
                uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
                client->is_Reading = 1;
            }
            return;
        }

        bt_raw_response_t result;
        int rc = bt_handle_http_bytes_ex(
            client->router,
            (void *)client,
            &client->arena,
            client->read_buf,
            req_len,
            client->write_buf,
            sizeof(client->write_buf),
            &result
        );

        if (rc == BT_HANDLER_SUSPENDED) {
            /* Keep read_buf intact while suspended so zero-copy Request/UploadFile slices stay valid */
            client->suspended_req_len = req_len;
            client->in_flight_async_reqs++;
            if (client->is_Reading) {
                uv_read_stop((uv_stream_t *)&client->handle);
                client->is_Reading = 0;
            }
            uv_timer_stop(&client->idle_timer);
            if (client->async_idler && !uv_is_active((const uv_handle_t *)client->async_idler)) {
                uv_idle_start(client->async_idler, on_uv_async_idle_tick);
            }
            return;
        }

        if (rc < 0) {
            /* 400, 413, 414, and 500 send Connection: close; discard any trailing pipelined bytes */
            client->is_closing = 1;
            client->read_len = 0;
            if (client->is_Reading) {
                uv_read_stop((uv_stream_t *)&client->handle);
                client->is_Reading = 0;
            }
        } else {
            bt_consume_read_bytes(client, req_len);
        }

        /* Fast path: attempt synchronous non-blocking write via uv_try_write */
        uv_buf_t wbuf = uv_buf_init(result.http_wire_buf, (unsigned int)result.http_wire_len);
        ssize_t written = uv_try_write((uv_stream_t *)&client->handle, &wbuf, 1);

        if (written == (ssize_t)result.http_wire_len) {
            if (client->is_closing) {
                bt_initiate_client_close(client);
                return;
            }
            bt_refresh_idle_timer(client);
            /* Continue loop immediately if another pipelined request is already in read_buf! */
            continue;
        }

        if (written > 0 && (size_t)written < result.http_wire_len) {
            size_t rem = result.http_wire_len - (size_t)written;
            memmove(client->write_buf, result.http_wire_buf + written, rem);
            wbuf = uv_buf_init(client->write_buf, (unsigned int)rem);
        } else if (written != UV_EAGAIN) {
            bt_initiate_client_close(client);
            return;
        }

        if (client->is_Reading) {
            uv_read_stop((uv_stream_t *)&client->handle);
            client->is_Reading = 0;
        }
        client->write_req.data = client;
        uv_write(&client->write_req, (uv_stream_t *)&client->handle, &wbuf, 1, on_write_complete);
        return;
    }

    if (!client->is_closing && client->in_flight_async_reqs == 0 && !client->is_Reading &&
        !uv_is_closing((const uv_handle_t *)&client->handle)) {
        uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
        client->is_Reading = 1;
    }
}

static void on_uv_read(uv_stream_t *stream, ssize_t nread, const uv_buf_t *buf) {
    (void)buf;
    bt_uv_client_t *client = (bt_uv_client_t *)stream->data;
    if (nread > 0) {
        client->read_len += (size_t)nread;
        bt_refresh_idle_timer(client);
        bt_process_client_buffer(client);
    } else if (nread < 0) {
        bt_initiate_client_close(client);
    }
}

static void on_uv_async_idle_tick(uv_idle_t *handle) {
    bt_router_t *router = (bt_router_t *)handle->data;
    if (!router || !router->tick_cb || router->tick_cb() <= 0) {
        uv_idle_stop(handle);
    }
}

static void on_new_connection(uv_stream_t *server, int status) {
    if (status < 0) return;
    bt_uv_server_ctx_t *srv_ctx = (bt_uv_server_ctx_t *)server->data;

    bt_uv_client_t *client = (bt_uv_client_t *)malloc(sizeof(bt_uv_client_t));
    if (!client) return;
    if (bt_arena_init(&client->arena, BT_ARENA_DEFAULT_SIZE) != 0) {
        free(client);
        return;
    }
    client->router = srv_ctx->router;
    client->async_idler = &srv_ctx->async_idler;
    client->read_len = 0;
    client->suspended_req_len = 0;
    client->in_flight_async_reqs = 0;
    client->open_handles = 2; /* 1 for uv_tcp_t + 1 for uv_timer_t */
    client->is_Reading = 0;
    client->is_closing = 0;

    uv_tcp_init(server->loop, &client->handle);
    client->handle.data = client;

    uv_timer_init(server->loop, &client->idle_timer);
    client->idle_timer.data = client;

    if (uv_accept(server, (uv_stream_t *)&client->handle) == 0) {
        uv_tcp_nodelay(&client->handle, 1);
        bt_refresh_idle_timer(client);
        uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
        client->is_Reading = 1;
    } else {
        bt_initiate_client_close(client);
    }
}

static void on_server_signal(uv_signal_t *handle, int signum) {
    (void)signum;
    uv_stop(handle->loop);
}

int bt_uv_server_run(bt_router_t *router, const char *host, int port) {
    uv_loop_t *loop = uv_default_loop();
    bt_uv_server_ctx_t srv_ctx;
    srv_ctx.router = router;

    uv_tcp_t server;
    uv_tcp_init_ex(loop, &server, AF_INET);
    server.data = &srv_ctx;

    uv_os_fd_t fd;
    if (uv_fileno((const uv_handle_t *)&server, &fd) == 0) {
        int opt = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &opt, sizeof(opt));
    }

    struct sockaddr_in addr;
    uv_ip4_addr(host, port, &addr);
    uv_tcp_bind(&server, (const struct sockaddr *)&addr, 0);

    int r = uv_listen((uv_stream_t *)&server, 4096, on_new_connection);
    if (r) return r;

    uv_signal_t sigint_handle;
    uv_signal_init(loop, &sigint_handle);
    uv_signal_start(&sigint_handle, on_server_signal, SIGINT);

    uv_signal_t sigterm_handle;
    uv_signal_init(loop, &sigterm_handle);
    uv_signal_start(&sigterm_handle, on_server_signal, SIGTERM);

    uv_idle_init(loop, &srv_ctx.async_idler);
    srv_ctx.async_idler.data = router;

    int ret = uv_run(loop, UV_RUN_DEFAULT);

    if (uv_is_active((const uv_handle_t *)&srv_ctx.async_idler)) {
        uv_idle_stop(&srv_ctx.async_idler);
    }
    uv_close((uv_handle_t *)&srv_ctx.async_idler, NULL);

    uv_signal_stop(&sigint_handle);
    uv_close((uv_handle_t *)&sigint_handle, NULL);
    uv_signal_stop(&sigterm_handle);
    uv_close((uv_handle_t *)&sigterm_handle, NULL);
    uv_close((uv_handle_t *)&server, NULL);

    return ret;
}
