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

bt_router_t *bt_router_create(bt_handler_invoke_fn invoke_cb) {
    bt_router_t *r = (bt_router_t *)malloc(sizeof(bt_router_t));
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
    unsigned char key[512];
    if (method_len + 1 + path_len >= sizeof(key)) return -1;

    memcpy(key, method, method_len);
    key[method_len] = ':';
    memcpy(key + method_len + 1, path, path_len);
    size_t key_len = method_len + 1 + path_len;

    raxInsert(router->tree, key, key_len, handler_obj, NULL);
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

    /* Separate path and query string ('?') using zero-copy pointer arithmetic */
    const char *query = NULL;
    size_t query_len = 0;
    size_t clean_path_len = path_len;
    const char *qmark = (const char *)memchr(path, '?', path_len);
    if (qmark) {
        clean_path_len = (size_t)(qmark - path);
        query = qmark + 1;
        query_len = path_len - clean_path_len - 1;
    }

    /* Build lookup key "METHOD:/clean_path" inside the C Arena */
    size_t key_len = method_len + 1 + clean_path_len;
    unsigned char *lookup_key = (unsigned char *)bt_arena_alloc(arena, key_len);
    if (!lookup_key) return -2;

    memcpy(lookup_key, method, method_len);
    lookup_key[method_len] = ':';
    memcpy(lookup_key + method_len + 1, path, clean_path_len);

    void *handler_obj = raxFind(router->tree, lookup_key, key_len);
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

    /* Allocate temporary response body buffer inside the C Arena */
    char *handler_out_buf = (char *)bt_arena_alloc(arena, 32 * 1024);
    size_t handler_out_len = 0;
    int status_code = 200;

    int rc = router->invoke_cb(
        handler_obj,
        method, method_len,
        path, clean_path_len,
        query, query_len,
        headers, num_headers,
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

    /* Format HTTP/1.1 response header + body into resp_wire_buf */
    int hdr_len = snprintf(
        resp_wire_buf, resp_wire_cap,
        "HTTP/1.1 %d OK\r\n"
        "Content-Length: %zu\r\n"
        "Connection: keep-alive\r\n\r\n",
        status_code, handler_out_len
    );

    memcpy(resp_wire_buf + hdr_len, handler_out_buf, handler_out_len);
    out_result->status_code = status_code;
    out_result->http_wire_buf = resp_wire_buf;
    out_result->http_wire_len = (size_t)hdr_len + handler_out_len;
    out_result->body_ptr = resp_wire_buf + hdr_len;
    out_result->body_len = handler_out_len;

    /* Reset arena offset to 0 immediately after request completes */
    bt_arena_reset(arena);
    return 0;
}

/* =========================================================================
 * Native libuv TCP Server Glue (per-connection 64KB Arena + Read/Write)
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

    /* Enable SO_REUSEPORT on Linux if supported via raw fd */
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
