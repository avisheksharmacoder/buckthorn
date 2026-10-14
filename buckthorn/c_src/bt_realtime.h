/* buckthorn/c_src/bt_realtime.h */
#ifndef BT_REALTIME_H
#define BT_REALTIME_H

#include <stddef.h>
#include <stdint.h>
#include "bt_params.h"

#define BT_WS_OP_CONT  0x0
#define BT_WS_OP_TEXT  0x1
#define BT_WS_OP_BIN   0x2
#define BT_WS_OP_CLOSE 0x8
#define BT_WS_OP_PING  0x9
#define BT_WS_OP_PONG  0xA

typedef struct {
    uint8_t fin;
    uint8_t opcode;
    uint8_t masked;
    const char *payload_ptr;
    size_t payload_len;
    size_t frame_total_len;
} bt_ws_frame_t;

typedef struct {
    bt_slice_t field_name;
    bt_slice_t filename;
    bt_slice_t content_type;
    bt_slice_t data;
    int found;
} bt_multipart_part_t;

/* Computes RFC 6455 Sec-WebSocket-Accept Base64 string using OpenSSL SHA1 */
int bt_ws_compute_accept(
    const char *client_key,
    size_t client_key_len,
    char *out_accept,
    size_t out_cap,
    size_t *out_len
);

/* Parses an RFC 6455 WebSocket frame and unmasks payload in-place using 64-bit word XOR */
int bt_ws_parse_frame(
    char *buf,
    size_t buf_len,
    bt_ws_frame_t *out_frame
);

/* Encodes an RFC 6455 WebSocket frame (optionally masked for client testing) */
size_t bt_ws_encode_frame(
    uint8_t opcode,
    const char *payload,
    size_t payload_len,
    int mask_frame,
    char *out_buf,
    size_t out_cap
);

/* Zero-copy C multipart/form-data boundary scanner */
int bt_multipart_extract_file(
    struct phr_header *headers,
    size_t num_headers,
    const char *body,
    size_t body_len,
    const char *target_field,
    size_t target_field_len,
    bt_multipart_part_t *out_part
);

#endif /* BT_REALTIME_H */
