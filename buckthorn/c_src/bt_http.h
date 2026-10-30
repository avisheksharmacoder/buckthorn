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
#include "bt_realtime.h"

#define BT_MAX_HEADERS 64
#define BT_RESP_BUF_SIZE (64 * 1024)
#define BT_MAX_PARAM_ROUTES 256

/* Return codes for bt_handler_invoke_fn */
#define BT_HANDLER_OK 0
#define BT_HANDLER_ERROR -1
#define BT_HANDLER_SUSPENDED 2

/* WebSocket lifecycle events */
#define BT_WS_EVT_OPEN       1
#define BT_WS_EVT_MESSAGE    2
#define BT_WS_EVT_CLOSE      3

/* Callback invoked on every libuv tick when 1+ Python async tasks are in flight */
typedef int (*bt_async_tick_fn)(void);

typedef int (*bt_ws_event_fn)(
    void *router_ctx,
    void *ws_client_ctx,
    int event_type,
    const char *path, size_t path_len,
    const char *payload, size_t payload_len
);

typedef int (*bt_handler_invoke_fn)(
    void *handler_obj,
    void *async_client_ctx,
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
    bt_async_tick_fn tick_cb;
    bt_ws_event_fn ws_cb;
    void *ws_ctx;
    size_t num_param_routes;
    bt_param_route_t param_routes[BT_MAX_PARAM_ROUTES];
    bt_logger_t *access_logger;
    bt_middleware_chain_t mw;
    size_t max_body_size;
    void *async_idler_handle;
    int async_idler_active;
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
void bt_router_set_async_tick_cb(bt_router_t *router, bt_async_tick_fn tick_cb);
void bt_router_set_ws_cb(bt_router_t *router, bt_ws_event_fn ws_cb, void *router_ctx);

int bt_router_insert(bt_router_t *router, const char *method, size_t method_len,
                     const char *path, size_t path_len, void *handler_obj);
void bt_router_set_access_logger(bt_router_t *router, bt_logger_t *logger);
bt_middleware_chain_t *bt_router_get_mw(bt_router_t *router);

int bt_uv_ws_send_frame(void *ws_client_ctx, const char *frame_buf, size_t frame_len);
void bt_uv_ws_close_client(void *ws_client_ctx);

/* Called by Cython when a suspended async route handler resolves on the event loop */
void bt_uv_complete_async_request(
    void *async_client_ctx,
    const char *body_buf,
    size_t body_len,
    const char *extra_hdrs_buf,
    size_t extra_hdrs_len,
    int status_code
);

int bt_handle_http_bytes_ex(
    bt_router_t *router,
    void *async_client_ctx,
    bt_arena_t *arena,
    const char *raw_req,
    size_t raw_req_len,
    char *resp_wire_buf,
    size_t resp_wire_cap,
    bt_raw_response_t *out_result
);

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
