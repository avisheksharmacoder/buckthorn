/* buckthorn/c_src/bt_http.h */
#ifndef BT_HTTP_H
#define BT_HTTP_H

#include <stddef.h>
#include <stdint.h>
#include "bt_arena.h"
#include "../c_vendor/picohttpparser.h"
#include "../c_vendor/rax.h"

#define BT_MAX_HEADERS 64
#define BT_RESP_BUF_SIZE (64 * 1024)

/* Callback signature invoked when C router matches a Cython/Python handler */
typedef int (*bt_handler_invoke_fn)(
    void *handler_obj,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    struct phr_header *headers, size_t num_headers,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    int *out_status_code
);

typedef struct {
    rax *tree;
    bt_handler_invoke_fn invoke_cb;
} bt_router_t;

typedef struct {
    int status_code;
    char *http_wire_buf;
    size_t http_wire_len;
    const char *body_ptr;
    size_t body_len;
} bt_raw_response_t;

bt_router_t *bt_router_create(bt_handler_invoke_fn invoke_cb);
void bt_router_destroy(bt_router_t *router);

/* Registers a route key formatted as "METHOD:/path" (e.g. "GET:/ping") into rax */
int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                     const char *path, size_t path_len, void *handler_obj);

/* Core zero-allocation C HTTP processor used by both RawTestClient and libuv */
int bt_handle_http_bytes(
    bt_router_t *router,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
);

/* Starts the native libuv TCP server with SO_REUSEPORT */
int bt_uv_server_run(bt_router_t *router, const char *host, int port);

#endif /* BT_HTTP_H */
