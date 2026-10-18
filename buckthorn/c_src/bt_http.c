/* buckthorn/c_src/bt_http.c */
#include "bt_http.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <uv.h>
#include <signal.h>

static const char RESP_400[] =
    "HTTP/1.1 400 Bad Request\r\n"
    "Content-Length: 11\r\n"
    "Connection: close\r\n\r\n"
    "Bad Request";
#define RESP_400_LEN (sizeof(RESP_400) - 1)

static const char RESP_404[] =
    "HTTP/1.1 404 Not Found\r\n"
    "Content-Length: 9\r\n"
    "Connection: keep-alive\r\n\r\n"
    "Not Found";
#define RESP_404_LEN (sizeof(RESP_404) - 1)

static const char RESP_413[] =
    "HTTP/1.1 413 Content Too Large\r\n"
    "Content-Length: 17\r\n"
    "Connection: close\r\n\r\n"
    "Content Too Large";
#define RESP_413_LEN (sizeof(RESP_413) - 1)

static const char RESP_414[] =
    "HTTP/1.1 414 URI Too Long\r\n"
    "Content-Length: 12\r\n"
    "Connection: close\r\n\r\n"
    "URI Too Long";
#define RESP_414_LEN (sizeof(RESP_414) - 1)

static const char RESP_500[] =
    "HTTP/1.1 500 Internal Server Error\r\n"
    "Content-Length: 21\r\n"
    "Connection: close\r\n\r\n"
    "Internal Server Error";
#define RESP_500_LEN (sizeof(RESP_500) - 1)

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
    /* If route contains '{', register in parameterized C segment matcher */
    if (memchr(path, '{', path_len) != NULL) {
        if (router->num_param_routes >= BT_MAX_PARAM_ROUTES) return -1;
        if (method_len >= sizeof(router->param_routes[0].method)) return -1;
        bt_param_route_t *pr = &router->param_routes[router->num_param_routes++];
        memcpy(pr->method, method, method_len);
        pr->method[method_len] = '\0';
        pr->method_len = method_len;
        pr->handler_obj = handler_obj;

        bt_slice_t segs[BT_MAX_PATH_SEGMENTS];
        pr->num_segments = split_url_segments(path, path_len, segs);
        for (size_t i = 0; i < pr->num_segments; i++) {
            if (segs[i].len >= sizeof(pr->segments[i].seg_str)) return -1;
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
    size_t num_req_segs = split_url_segments(path, path_len, req_segs);

    for (size_t r = 0; r < router->num_param_routes; r++) {
        bt_param_route_t *pr = &router->param_routes[r];
        if (pr->num_segments != num_req_segs || pr->method_len != method_len) continue;
        if (memcmp(pr->method, method, method_len) != 0) continue;

        size_t p_idx = 0;
        int matched = 1;
        for (size_t s = 0; s < num_req_segs; s++) {
            if (pr->segments[s].is_param) {
                if (p_idx < BT_MAX_PATH_PARAMS) {
                    out_params[p_idx++] = req_segs[s];
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

static inline char *bt_write_u64_ascii(char *p, size_t val) {
    char tmp[24];
    int i = 0;
    if (val == 0) { *p++ = '0'; return p; }
    while (val > 0) { tmp[i++] = (char)('0' + (val % 10)); val /= 10; }
    while (i > 0) { *p++ = tmp[--i]; }
    return p;
}

static const char HTTP_200_PREFIX[] = "HTTP/1.1 200 OK\r\nContent-Length: ";
#define HTTP_200_PREFIX_LEN (sizeof(HTTP_200_PREFIX) - 1)

static const char KEEP_ALIVE_LINE[] = "\r\nConnection: keep-alive\r\n";
#define KEEP_ALIVE_LINE_LEN (sizeof(KEEP_ALIVE_LINE) - 1)

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
    if (resp_wire_cap < 128) goto wire_overflow;

    char *p = resp_wire_buf;
    if (status_code == 200) {
        memcpy(p, HTTP_200_PREFIX, HTTP_200_PREFIX_LEN);
        p += HTTP_200_PREFIX_LEN;
        p = bt_write_u64_ascii(p, handler_out_len);
        memcpy(p, KEEP_ALIVE_LINE, KEEP_ALIVE_LINE_LEN);
        p += KEEP_ALIVE_LINE_LEN;
    } else {
        const char *status_text = bt_status_reason(status_code);
        size_t st_len = strlen(status_text);
        memcpy(p, "HTTP/1.1 ", 9);
        p += 9;
        p = bt_write_u64_ascii(p, (size_t)status_code);
        *p++ = ' ';
        memcpy(p, status_text, st_len);
        p += st_len;
        memcpy(p, "\r\nContent-Length: ", 18);
        p += 18;
        p = bt_write_u64_ascii(p, handler_out_len);
        memcpy(p, KEEP_ALIVE_LINE, KEEP_ALIVE_LINE_LEN);
        p += KEEP_ALIVE_LINE_LEN;
    }

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

    if (hdr_len + 2 + handler_out_len > resp_wire_cap) {
    wire_overflow: ;
        size_t len = RESP_500_LEN;
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
    struct phr_header headers[BT_MAX_HEADERS];
    size_t num_headers = BT_MAX_HEADERS;

    int pret = phr_parse_request(
        raw_req, raw_req_len,
        &method, &method_len,
        &path, &path_len,
        &minor_version,
        headers, &num_headers, 0
    );

    if (pret <= 0) {
        size_t len = RESP_400_LEN;
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
    if (audit_rc != BT_AUDIT_OK) {
        const char *err_wire = (audit_rc == BT_AUDIT_ERR_PAYLOAD_TOO_LARGE) ? RESP_413 :
                               (audit_rc == BT_AUDIT_ERR_URI_TOO_LONG) ? RESP_414 : RESP_400;
        size_t b_len = (audit_rc == BT_AUDIT_ERR_PAYLOAD_TOO_LARGE) ? 17 :
                       (audit_rc == BT_AUDIT_ERR_URI_TOO_LONG) ? 12 : 11;
        size_t w_len = (audit_rc == BT_AUDIT_ERR_PAYLOAD_TOO_LARGE) ? RESP_413_LEN :
                       (audit_rc == BT_AUDIT_ERR_URI_TOO_LONG) ? RESP_414_LEN : RESP_400_LEN;
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

    size_t key_len = method_len + 1 + clean_path_len;
    unsigned char *lookup_key = (unsigned char *)bt_arena_alloc(arena, key_len);
    if (!lookup_key) return -2;

    memcpy(lookup_key, method, method_len);
    lookup_key[method_len] = ':';
    memcpy(lookup_key + method_len + 1, path, clean_path_len);

    bt_slice_t path_params[BT_MAX_PATH_PARAMS];
    size_t num_path_params = 0;

    /* 1. Try O(K) static route lookup in Redis rax */
    void *handler_obj = raxFind(router->tree, lookup_key, key_len);

    /* 2. Fallback to zero-copy C segment matcher for parameterized routes */
    if (handler_obj == raxNotFound) {
        handler_obj = bt_match_param_route(
            router, method, method_len, path, clean_path_len, path_params, &num_path_params
        );
    }

    if (handler_obj == raxNotFound) {
        size_t len = RESP_404_LEN;
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
        /* Coroutine suspended on an awaitable Future; response will be flushed by bt_uv_complete_async_request */
        bt_arena_reset(arena);
        return BT_HANDLER_SUSPENDED;
    }

    if (rc != 0) {
        size_t len = RESP_500_LEN;
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
 * Native libuv TCP Server Glue (Phase 13 Native Async/Await Suspension Support)
 * ========================================================================= */
typedef struct {
    uv_tcp_t handle;
    bt_router_t *router;
    bt_arena_t arena;
    char read_buf[8192];
    char write_buf[BT_RESP_BUF_SIZE];
    uv_write_t write_req;
    int in_flight_async_reqs;
    int is_closing;
} bt_uv_client_t;

static void on_alloc_buffer(uv_handle_t *handle, size_t suggested_size, uv_buf_t *buf);
static void on_uv_read(uv_stream_t *stream, ssize_t nread, const uv_buf_t *buf);
static void on_uv_async_idle_tick(uv_idle_t *handle);

static void on_client_close(uv_handle_t *handle) {
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    if (client->in_flight_async_reqs <= 0) {
        bt_arena_destroy(&client->arena);
        free(client);
    } else {
        client->is_closing = 1;
    }
}

static void on_alloc_buffer(uv_handle_t *handle, size_t suggested_size, uv_buf_t *buf) {
    (void)suggested_size;
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    buf->base = client->read_buf;
    buf->len = sizeof(client->read_buf);
}

static void on_write_complete(uv_write_t *req, int status) {
    (void)status;
    bt_uv_client_t *client = (bt_uv_client_t *)req->data;
    if (!client) return;
    if (client->is_closing) {
        if (client->in_flight_async_reqs <= 0 && uv_is_closing((const uv_handle_t *)&client->handle)) {
            bt_arena_destroy(&client->arena);
            free(client);
        }
        return;
    }
    if (!uv_is_closing((const uv_handle_t *)&client->handle)) {
        uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
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

    if (client->is_closing || uv_is_closing((const uv_handle_t *)&client->handle)) {
        if (client->in_flight_async_reqs <= 0) {
            bt_arena_destroy(&client->arena);
            free(client);
        }
        return;
    }

    bt_raw_response_t result;
    bt_format_http_wire_response(
        client->router,
        status_code,
        body_buf, body_len,
        extra_hdrs_buf, extra_hdrs_len,
        client->write_buf, sizeof(client->write_buf),
        &result
    );

    client->write_req.data = client;
    uv_buf_t wbuf = uv_buf_init(result.http_wire_buf, (unsigned int)result.http_wire_len);
    uv_write(&client->write_req, (uv_stream_t *)&client->handle, &wbuf, 1, on_write_complete);
}

static void on_uv_read(uv_stream_t *stream, ssize_t nread, const uv_buf_t *buf) {
    bt_uv_client_t *client = (bt_uv_client_t *)stream->data;
    if (nread > 0) {
        bt_raw_response_t result;
        int rc = bt_handle_http_bytes_ex(
            client->router,
            (void *)client,
            &client->arena,
            buf->base,
            (size_t)nread,
            client->write_buf,
            sizeof(client->write_buf),
            &result
        );
        if (rc == BT_HANDLER_SUSPENDED) {
            client->in_flight_async_reqs++;
            uv_read_stop(stream);
            if (client->router && client->router->async_idler_handle && !client->router->async_idler_active) {
                uv_idle_start((uv_idle_t *)client->router->async_idler_handle, on_uv_async_idle_tick);
                client->router->async_idler_active = 1;
            }
        } else {
            client->write_req.data = client;
            uv_buf_t wbuf = uv_buf_init(result.http_wire_buf, (unsigned int)result.http_wire_len);
            uv_write(&client->write_req, stream, &wbuf, 1, on_write_complete);
        }
    } else if (nread < 0) {
        client->is_closing = 1;
        if (!uv_is_closing((const uv_handle_t *)stream)) {
            uv_close((uv_handle_t *)stream, on_client_close);
        }
    }
}

static void on_uv_async_idle_tick(uv_idle_t *handle) {
    bt_router_t *router = (bt_router_t *)handle->data;
    if (router && router->tick_cb) {
        int pending = router->tick_cb();
        if (pending <= 0 && router->async_idler_active) {
            uv_idle_stop(handle);
            router->async_idler_active = 0;
        }
    }
}

static void on_new_connection(uv_stream_t *server, int status) {
    if (status < 0) return;
    bt_router_t *router = (bt_router_t *)server->data;

    bt_uv_client_t *client = (bt_uv_client_t *)malloc(sizeof(bt_uv_client_t));
    if (!client) return;
    bt_arena_init(&client->arena, BT_ARENA_DEFAULT_SIZE);
    client->router = router;
    client->in_flight_async_reqs = 0;
    client->is_closing = 0;

    uv_tcp_init(server->loop, &client->handle);
    client->handle.data = client;

    if (uv_accept(server, (uv_stream_t *)&client->handle) == 0) {
        uv_tcp_nodelay(&client->handle, 1);
        uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
    } else {
        uv_close((uv_handle_t *)&client->handle, on_client_close);
    }
}

static void on_server_signal(uv_signal_t *handle, int signum) {
    (void)signum;
    uv_stop(handle->loop);
}

int bt_uv_server_run(bt_router_t *router, const char *host, int port) {
    uv_loop_t *loop = uv_default_loop();
    uv_tcp_t server;
    uv_tcp_init_ex(loop, &server, AF_INET);
    server.data = router;

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

    uv_idle_t async_idler;
    uv_idle_init(loop, &async_idler);
    async_idler.data = router;
    router->async_idler_handle = (void *)&async_idler;
    router->async_idler_active = 0;

    int ret = uv_run(loop, UV_RUN_DEFAULT);

    if (router->async_idler_active) {
        uv_idle_stop(&async_idler);
        router->async_idler_active = 0;
    }
    router->async_idler_handle = NULL;
    uv_close((uv_handle_t *)&async_idler, NULL);

    uv_signal_stop(&sigint_handle);
    uv_close((uv_handle_t *)&sigint_handle, NULL);
    uv_signal_stop(&sigterm_handle);
    uv_close((uv_handle_t *)&sigterm_handle, NULL);
    uv_close((uv_handle_t *)&server, NULL);

    return ret;
}
