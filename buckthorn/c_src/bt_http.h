/* buckthorn/c_src/bt_http.h */
#ifndef BT_HTTP_H
#define BT_HTTP_H

#include <stddef.h>
#include <stdint.h>
#include "bt_arena.h"
#include "bt_params.h"
#include "../c_vendor/picohttpparser.h"
#include "../c_vendor/rax.h"
#include "bt_logger.h"
#include "bt_middleware.h"
#include "bt_audit.h"

#define BT_MAX_HEADERS 64
#define BT_RESP_BUF_SIZE (64 * 1024)
#define BT_MAX_PARAM_ROUTES 256

typedef int (*bt_handler_invoke_fn)(
    void *handler_obj,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    const char *query, size_t query_len,
    struct phr_header *headers, size_t num_headers,
    bt_slice_t *path_params, size_t num_path_params,
    const char *body, size_t body_len,
    char *out_body_buf, size_t *out_body_len,
    char *out_extra_hdrs, size_t *out_extra_hdrs_len,
    int *out_status_code
);

typedef struct {
    char seg_str[64];
    size_t seg_len;
    int is_param; /* 1 if {param}, 0 if literal segment */
} bt_route_seg_t;

typedef struct {
    char method[16];
    size_t method_len;
    size_t num_segments;
    bt_route_seg_t segments[BT_MAX_PATH_SEGMENTS];
    void *handler_obj;
} bt_param_route_t;

typedef struct {
    rax *tree;
    bt_handler_invoke_fn invoke_cb;
    size_t num_param_routes;
    bt_param_route_t param_routes[BT_MAX_PARAM_ROUTES];
    bt_logger_t *access_logger;
    bt_middleware_chain_t mw;
    size_t max_body_size;
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
void bt_router_set_max_body_size(bt_router_t *router, size_t max_body_size);

int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                     const char *path, size_t path_len, void *handler_obj);
void bt_router_set_access_logger(bt_router_t *router, bt_logger_t *logger);
bt_middleware_chain_t *bt_router_get_mw(bt_router_t *router);

int bt_handle_http_bytes(
    bt_router_t *router,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
);

int bt_uv_server_run(bt_router_t *router, const char *host, int port);

#endif /* BT_HTTP_H */
