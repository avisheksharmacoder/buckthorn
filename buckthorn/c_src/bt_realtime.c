/* buckthorn/c_src/bt_realtime.c */
#define _GNU_SOURCE
#include "bt_realtime.h"
#include <openssl/evp.h>
#include <openssl/sha.h>
#include <string.h>
#include <strings.h>

static const char *WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

int bt_ws_compute_accept(
    const char *client_key,
    size_t client_key_len,
    char *out_accept,
    size_t out_cap,
    size_t *out_len
) {
    if (!client_key || client_key_len == 0 || client_key_len > 128 || out_cap < 32) {
        return -1;
    }

    char concat_buf[256];
    size_t guid_len = 36;
    memcpy(concat_buf, client_key, client_key_len);
    memcpy(concat_buf + client_key_len, WS_GUID, guid_len);

    unsigned char sha1_hash[SHA_DIGEST_LENGTH];
    SHA1((const unsigned char *)concat_buf, client_key_len + guid_len, sha1_hash);

    int encoded_len = EVP_EncodeBlock((unsigned char *)out_accept, sha1_hash, SHA_DIGEST_LENGTH);
    if (encoded_len <= 0) return -1;

    *out_len = (size_t)encoded_len;
    return 0;
}

/* Fast 8-byte word-aligned XOR unmasking + tail loop */
static inline void bt_ws_xor_unmask(char *payload, size_t len, const uint8_t mask[4]) {
    uint8_t mask8_bytes[8] = {
        mask[0], mask[1], mask[2], mask[3],
        mask[0], mask[1], mask[2], mask[3]
    };
    uint64_t mask64;
    memcpy(&mask64, mask8_bytes, 8);

    size_t i = 0;
    size_t vec_limit = len & ~((size_t)7);
    for (; i < vec_limit; i += 8) {
        uint64_t chunk;
        memcpy(&chunk, payload + i, 8);
        chunk ^= mask64;
        memcpy(payload + i, &chunk, 8);
    }
    for (; i < len; i++) {
        payload[i] ^= (char)mask[i & 3];
    }
}

int bt_ws_parse_frame(
    char *buf,
    size_t buf_len,
    bt_ws_frame_t *out_frame
) {
    if (buf_len < 2) return -1;

    uint8_t b0 = (uint8_t)buf[0];
    uint8_t b1 = (uint8_t)buf[1];

    out_frame->fin = (b0 >> 7) & 0x01;
    out_frame->opcode = b0 & 0x0F;
    out_frame->masked = (b1 >> 7) & 0x01;

    size_t payload_len = (size_t)(b1 & 0x7F);
    size_t pos = 2;

    if (payload_len == 126) {
        if (buf_len < 4) return -1;
        payload_len = ((size_t)(uint8_t)buf[2] << 8) | (size_t)(uint8_t)buf[3];
        pos = 4;
    } else if (payload_len == 127) {
        if (buf_len < 10) return -1;
        /* RFC 6455 Section 5.2: The most significant bit MUST be 0 */
        if ((uint8_t)buf[2] & 0x80) return -1;
        payload_len = 0;
        for (int i = 0; i < 8; i++) {
            payload_len = (payload_len << 8) | (size_t)(uint8_t)buf[2 + i];
        }
        pos = 10;
    }

    uint8_t mask_key[4] = {0, 0, 0, 0};
    if (out_frame->masked) {
        if (buf_len < pos + 4) return -1;
        memcpy(mask_key, buf + pos, 4);
        pos += 4;
    }

    /* Subtraction-safe bounds check: immune to `pos + payload_len` 64-bit wrap-around! */
    if (pos > buf_len || payload_len > (buf_len - pos)) {
        return -1;
    }

    char *payload_ptr = buf + pos;
    if (out_frame->masked && payload_len > 0) {
        bt_ws_xor_unmask(payload_ptr, payload_len, mask_key);
    }

    out_frame->payload_ptr = payload_ptr;
    out_frame->payload_len = payload_len;
    out_frame->frame_total_len = pos + payload_len;
    return 0;
}

size_t bt_ws_encode_frame(
    uint8_t opcode,
    const char *payload,
    size_t payload_len,
    int mask_frame,
    char *out_buf,
    size_t out_cap
) {
    size_t ext_hdr_len = 0;
    if (payload_len < 126) {
        ext_hdr_len = 0;
    } else if (payload_len <= 65535) {
        ext_hdr_len = 2;
    } else {
        ext_hdr_len = 8;
    }

    size_t hdr_len = 2 + ext_hdr_len + (mask_frame ? 4 : 0);
    if (hdr_len + payload_len > out_cap) return 0;

    out_buf[0] = (char)(0x80 | (opcode & 0x0F)); /* FIN = 1 */
    uint8_t mask_bit = mask_frame ? 0x80 : 0x00;

    size_t pos = 2;
    if (payload_len < 126) {
        out_buf[1] = (char)(mask_bit | (uint8_t)payload_len);
    } else if (payload_len <= 65535) {
        out_buf[1] = (char)(mask_bit | 126);
        out_buf[2] = (char)((payload_len >> 8) & 0xFF);
        out_buf[3] = (char)(payload_len & 0xFF);
        pos = 4;
    } else {
        out_buf[1] = (char)(mask_bit | 127);
        for (int i = 7; i >= 0; i--) {
            out_buf[2 + (7 - i)] = (char)((payload_len >> (i * 8)) & 0xFF);
        }
        pos = 10;
    }

    uint8_t mask_key[4] = {0x37, 0xFA, 0x21, 0x3D};
    if (mask_frame) {
        memcpy(out_buf + pos, mask_key, 4);
        pos += 4;
    }

    if (payload_len > 0 && payload) {
        memcpy(out_buf + pos, payload, payload_len);
        if (mask_frame) {
            bt_ws_xor_unmask(out_buf + pos, payload_len, mask_key);
        }
    }

    return pos + payload_len;
}

/* Portable SIMD-accelerated memmem helper for scanning multipart boundaries */
static const char *bt_memmem(const char *haystack, size_t hlen, const char *needle, size_t nlen) {
#if defined(__GLIBC__) || defined(_GNU_SOURCE)
    return (const char *)memmem(haystack, hlen, needle, nlen);
#else
    if (nlen == 0) return haystack;
    if (hlen < nlen) return NULL;
    const char *cur = haystack;
    const char *end = haystack + hlen - nlen;
    while (cur <= end) {
        const char *cand = (const char *)memchr(cur, needle[0], (size_t)(end - cur + 1));
        if (!cand) return NULL;
        if (memcmp(cand, needle, nlen) == 0) return cand;
        cur = cand + 1;
    }
    return NULL;
#endif
}

int bt_multipart_extract_file(
    struct phr_header *headers,
    size_t num_headers,
    const char *body,
    size_t body_len,
    const char *target_field,
    size_t target_field_len,
    bt_multipart_part_t *out_part
) {
    memset(out_part, 0, sizeof(bt_multipart_part_t));
    if (!body || body_len == 0) return 0;

    bt_slice_t ctype_hdr;
    if (!bt_find_header(headers, num_headers, "Content-Type", 12, &ctype_hdr)) {
        return 0;
    }

    const char *b_tag = bt_memmem(ctype_hdr.ptr, ctype_hdr.len, "boundary=", 9);
    if (!b_tag) return 0;

    const char *b_val = b_tag + 9;
    size_t b_len = (size_t)((ctype_hdr.ptr + ctype_hdr.len) - b_val);
    const char *semi = (const char *)memchr(b_val, ';', b_len);
    if (semi) b_len = (size_t)(semi - b_val);

    if (b_len >= 2 && b_val[0] == '"' && b_val[b_len - 1] == '"') {
        b_val++;
        b_len -= 2;
    }
    if (b_len == 0 || b_len > 70) return 0;

    char boundary[128];
    if (b_len + 2 >= sizeof(boundary)) return 0;
    boundary[0] = '-';
    boundary[1] = '-';
    memcpy(boundary + 2, b_val, b_len);
    size_t delim_len = b_len + 2;

    const char *cur = bt_memmem(body, body_len, boundary, delim_len);
    const char *body_end = body + body_len;

    while (cur && cur < body_end) {
        cur += delim_len;
        if ((size_t)(body_end - cur) >= 2 && cur[0] == '-' && cur[1] == '-') {
            break; /* Final boundary "--boundary--" */
        }
        if ((size_t)(body_end - cur) >= 2 && cur[0] == '\r' && cur[1] == '\n') {
            cur += 2;
        }

        const char *hdr_end = bt_memmem(cur, (size_t)(body_end - cur), "\r\n\r\n", 4);
        if (!hdr_end) break;

        size_t part_hdrs_len = (size_t)(hdr_end - cur);
        const char *part_data_start = hdr_end + 4;
        const char *next_bound = bt_memmem(
            part_data_start, (size_t)(body_end - part_data_start), boundary, delim_len
        );
        if (!next_bound) break;

        /* Strip trailing \r\n before next boundary */
        size_t part_data_len = (size_t)(next_bound - part_data_start);
        if (part_data_len >= 2 &&
            part_data_start[part_data_len - 2] == '\r' &&
            part_data_start[part_data_len - 1] == '\n') {
            part_data_len -= 2;
        }

        /* Extract name="..." from Content-Disposition */
        const char *name_pos = bt_memmem(cur, part_hdrs_len, "name=\"", 6);
        if (name_pos) {
            const char *n_start = name_pos + 6;
            const char *n_end = (const char *)memchr(n_start, '"', (size_t)(hdr_end - n_start));
            if (n_end) {
                size_t n_len = (size_t)(n_end - n_start);
                if (target_field_len == 0 ||
                    (n_len == target_field_len && memcmp(n_start, target_field, n_len) == 0)) {
                    out_part->field_name.ptr = n_start;
                    out_part->field_name.len = n_len;
                    out_part->field_name.found = 1;

                    /* Extract filename="..." */
                    const char *fn_pos = bt_memmem(cur, part_hdrs_len, "filename=\"", 10);
                    if (fn_pos) {
                        const char *fn_start = fn_pos + 10;
                        const char *fn_end = (const char *)memchr(
                            fn_start, '"', (size_t)(hdr_end - fn_start)
                        );
                        if (fn_end) {
                            /* Strip any directory path traversal prefixes (/ or \) */
                            for (const char *p = fn_start; p < fn_end; p++) {
                                if (*p == '/' || *p == '\\') {
                                    fn_start = p + 1;
                                }
                            }
                            out_part->filename.ptr = fn_start;
                            out_part->filename.len = (size_t)(fn_end - fn_start);
                            out_part->filename.found = 1;
                        }
                    }

                    /* Extract part Content-Type */
                    const char *ct_pos = bt_memmem(cur, part_hdrs_len, "Content-Type:", 13);
                    if (ct_pos) {
                        const char *ct_start = ct_pos + 13;
                        while (ct_start < hdr_end && (*ct_start == ' ' || *ct_start == '\t')) {
                            ct_start++;
                        }
                        const char *ct_end = bt_memmem(
                            ct_start, (size_t)(hdr_end - ct_start), "\r\n", 2
                        );
                        if (!ct_end) ct_end = hdr_end;
                        out_part->content_type.ptr = ct_start;
                        out_part->content_type.len = (size_t)(ct_end - ct_start);
                        out_part->content_type.found = 1;
                    }

                    out_part->data.ptr = part_data_start;
                    out_part->data.len = part_data_len;
                    out_part->data.found = 1;
                    out_part->found = 1;
                    return 1;
                }
            }
        }

        cur = next_bound;
    }

    return 0;
}
