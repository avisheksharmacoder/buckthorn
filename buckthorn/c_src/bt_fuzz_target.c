/* buckthorn/c_src/bt_fuzz_target.c */
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "bt_arena.h"
#include "bt_audit.h"
#include "bt_http.h"
#include "bt_json.h"
#include "bt_middleware.h"
#include "bt_params.h"
#include "bt_realtime.h"
#include "bt_security.h"

static bt_router_t *g_router = NULL;
static bt_arena_t g_arena;
static char g_resp_buf[65536];

static int dummy_c_handler(
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
) {
    (void)handler_obj; (void)method; (void)method_len;
    (void)path; (void)path_len; (void)path_params; (void)num_path_params;
    (void)out_extra_hdrs;

    /* Exercise C query/header/cookie scanners, JWT verifier, multipart scanner, and yyjson */
    bt_slice_t s;
    bt_find_query_param(query, query_len, "id", 2, &s);
    int64_t iv = 0;
    bt_parse_int64_checked(s, 1, 0, 1, 1000, 0, 0, 0, 0, &iv);

    bt_find_cookie(headers, num_headers, "sid", 3, &s);

    char jwt_buf[2048];
    size_t jwt_len = 0;
    bt_json_doc_t jdoc;
    const char *scopes[1] = {"read"};
    size_t scope_lens[1] = {4};
    bt_jwt_verify_hs256(
        headers, num_headers, "fuzz-secret", 11,
        scopes, scope_lens, 1, jwt_buf, sizeof(jwt_buf), &jwt_len, &jdoc
    );

    bt_multipart_part_t mp;
    bt_multipart_extract_file(headers, num_headers, body, body_len, "doc", 3, &mp);

    bt_json_parse_obj(body, body_len, &jdoc);

    memcpy(out_body_buf, "{\"ok\":true}", 11);
    *out_body_len = 11;
    *out_extra_hdrs_len = 0;
    *out_status_code = 200;
    return 0;
}

int LLVMFuzzerInitialize(int *argc, char ***argv) {
    (void)argc; (void)argv;
    bt_arena_init(&g_arena, 65536);
    g_router = bt_router_create(dummy_c_handler);
    bt_router_set_max_body_size(g_router, 16384);
    bt_router_insert(g_router, "GET", 3, "/ping", 5, (void *)1);
    bt_router_insert(g_router, "POST", 4, "/users/{id}/upload", 18, (void *)1);
    return 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0 || size > 32768) return 0;

    /* 1. Fuzz full HTTP + Audit + Radix + Params + Security + Multipart + yyjson pipeline */
    bt_raw_response_t out_res;
    bt_handle_http_bytes(
        g_router,
        &g_arena,
        (const char *)data,
        size,
        g_resp_buf,
        sizeof(g_resp_buf),
        &out_res
    );

    /* 2. Fuzz RFC 6455 WebSocket C frame parser and 64-bit XOR unmasker */
    if (size <= 4096) {
        char ws_copy[4096];
        memcpy(ws_copy, data, size);
        bt_ws_frame_t frame;
        bt_ws_parse_frame(ws_copy, size, &frame);
    }

    return 0;
}
