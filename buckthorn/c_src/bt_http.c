/* buckthorn/c_src/bt_http.c */
#include "bt_http.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <uv.h>

static const char *RESP_400 =
    "HTTP/1.1 400 Bad Request\r\n"
    "Content-Length: 11\r\n"
    "Connection: close\r\n\r\n"
    "Bad Request";

static const char *RESP_404 =
    "HTTP/1.1 404 Not Found\r\n"
    "Content-Length: 9\r\n"
    "Connection: keep-alive\r\n\r\n"
    "Not Found";

static const char *RESP_500 =
    "HTTP/1.1 500 Internal Server Error\r\n"
    "Content-Length: 21\r\n"
    "Connection: close\r\n\r\n"
    "Internal Server Error";

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
    return r;
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
        bt_param_route_t *pr = &router->param_routes[router->num_param_routes++];
        memcpy(pr->method, method, method_len);
        pr->method[method_len] = '\0';
        pr->method_len = method_len;
        pr->handler_obj = handler_obj;

        bt_slice_t segs[BT_MAX_PATH_SEGMENTS];
        pr->num_segments = split_url_segments(path, path_len, segs);
        for (size_t i = 0; i < pr->num_segments; i++) {
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

int bt_handle_http_bytes(
    bt_router_t *router,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
) {
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
        size_t len = strlen(RESP_400);
        memcpy(resp_wire_buf, RESP_400, len);
        out_result->status_code = 400;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 11);
        out_result->body_len = 11;
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
        size_t len = strlen(RESP_404);
        memcpy(resp_wire_buf, RESP_404, len);
        out_result->status_code = 404;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 9);
        out_result->body_len = 9;
        bt_arena_reset(arena);
        return 0;
    }

    const char *body_ptr = raw_req + pret;
    size_t body_len = raw_req_len - (size_t)pret;

    char *handler_out_buf = (char *)bt_arena_alloc(arena, 32 * 1024);
    size_t handler_out_len = 0;
    int status_code = 200;

    int rc = router->invoke_cb(
        handler_obj,
        method, method_len,
        path, clean_path_len,
        query, query_len,
        headers, num_headers,
        path_params, num_path_params,
        body_ptr, body_len,
        handler_out_buf, &handler_out_len,
        &status_code
    );

    if (rc != 0) {
        size_t len = strlen(RESP_500);
        memcpy(resp_wire_buf, RESP_500, len);
        out_result->status_code = 500;
        out_result->http_wire_buf = resp_wire_buf;
        out_result->http_wire_len = len;
        out_result->body_ptr = resp_wire_buf + (len - 21);
        out_result->body_len = 21;
        bt_arena_reset(arena);
        return -1;
    }

    const char *status_text = bt_status_reason(status_code);

    int hdr_len = snprintf(
        resp_wire_buf, resp_wire_cap,
        "HTTP/1.1 %d %s\r\n"
        "Content-Length: %zu\r\n"
        "Connection: keep-alive\r\n\r\n",
        status_code, status_text, handler_out_len
    );

    memcpy(resp_wire_buf + hdr_len, handler_out_buf, handler_out_len);
    out_result->status_code = status_code;
    out_result->http_wire_buf = resp_wire_buf;
    out_result->http_wire_len = (size_t)hdr_len + handler_out_len;
    out_result->body_ptr = resp_wire_buf + hdr_len;
    out_result->body_len = handler_out_len;

    bt_arena_reset(arena);
    return 0;
}

/* =========================================================================
 * Native libuv TCP Server Glue (Unchanged from Phase 1)
 * ========================================================================= */
typedef struct {
    uv_tcp_t handle;
    bt_router_t *router;
    bt_arena_t arena;
    char read_buf[8192];
    char write_buf[BT_RESP_BUF_SIZE];
    uv_write_t write_req;
} bt_uv_client_t;

static void on_client_close(uv_handle_t *handle) {
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    bt_arena_destroy(&client->arena);
    free(client);
}

static void on_alloc_buffer(uv_handle_t *handle, size_t suggested_size, uv_buf_t *buf) {
    (void)suggested_size;
    bt_uv_client_t *client = (bt_uv_client_t *)handle->data;
    buf->base = client->read_buf;
    buf->len = sizeof(client->read_buf);
}

static void on_write_complete(uv_write_t *req, int status) {
    (void)req;
    (void)status;
}

static void on_uv_read(uv_stream_t *stream, ssize_t nread, const uv_buf_t *buf) {
    bt_uv_client_t *client = (bt_uv_client_t *)stream->data;
    if (nread > 0) {
        bt_raw_response_t result;
        bt_handle_http_bytes(
            client->router,
            &client->arena,
            buf->base,
            (size_t)nread,
            client->write_buf,
            sizeof(client->write_buf),
            &result
        );
        uv_buf_t wbuf = uv_buf_init(result.http_wire_buf, (unsigned int)result.http_wire_len);
        uv_write(&client->write_req, stream, &wbuf, 1, on_write_complete);
    } else if (nread < 0) {
        uv_close((uv_handle_t *)stream, on_client_close);
    }
}

static void on_new_connection(uv_stream_t *server, int status) {
    if (status < 0) return;
    bt_router_t *router = (bt_router_t *)server->data;

    bt_uv_client_t *client = (bt_uv_client_t *)malloc(sizeof(bt_uv_client_t));
    if (!client) return;
    bt_arena_init(&client->arena, BT_ARENA_DEFAULT_SIZE);
    client->router = router;

    uv_tcp_init(server->loop, &client->handle);
    client->handle.data = client;

    if (uv_accept(server, (uv_stream_t *)&client->handle) == 0) {
        uv_tcp_nodelay(&client->handle, 1);
        uv_read_start((uv_stream_t *)&client->handle, on_alloc_buffer, on_uv_read);
    } else {
        uv_close((uv_handle_t *)&client->handle, on_client_close);
    }
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

    return uv_run(loop, UV_RUN_DEFAULT);
}
