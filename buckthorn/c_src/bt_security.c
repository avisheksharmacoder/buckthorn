/* buckthorn/c_src/bt_security.c */
#include "bt_security.h"
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <string.h>
#include <strings.h>
#include <time.h>

static const int8_t B64_DECODE_TABLE[256] = {
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,62,-1,62,-1,63,
    52,53,54,55,56,57,58,59,60,61,-1,-1,-1,-2,-1,-1,
    -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10,11,12,13,14,
    15,16,17,18,19,20,21,22,23,24,25,-1,-1,-1,-1,63,
    -1,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,
    41,42,43,44,45,46,47,48,49,50,51,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
    -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1
};

int bt_const_time_eq(const char *a, size_t a_len, const char *b, size_t b_len) {
    if (a_len != b_len) return 0;
    if (a_len == 0) return 1;
    return CRYPTO_memcmp(a, b, a_len) == 0 ? 1 : 0;
}

/* Unified Base64 & Base64URL decoder (handles both padded and unpadded strings) */
int bt_base64url_decode(const char *in, size_t in_len, uint8_t *out, size_t out_cap, size_t *out_len) {
    while (in_len > 0 && in[in_len - 1] == '=') {
        in_len--;
    }
    if (in_len % 4 == 1) return -1;

    size_t expected_len = (in_len * 3) / 4;
    if (expected_len > out_cap) return -1;

    size_t j = 0;
    uint32_t buf = 0;
    int bits = 0;

    for (size_t i = 0; i < in_len; i++) {
        int8_t v = B64_DECODE_TABLE[(uint8_t)in[i]];
        if (v < 0) return -1;
        buf = (buf << 6) | (uint32_t)v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out[j++] = (uint8_t)((buf >> bits) & 0xFF);
        }
    }
    *out_len = j;
    return 0;
}

int bt_base64_decode(const char *in, size_t in_len, uint8_t *out, size_t out_cap, size_t *out_len) {
    return bt_base64url_decode(in, in_len, out, out_cap, out_len);
}

static int jwt_has_scope(yyjson_val *scopes_arr, const char *req_scope, size_t req_len) {
    if (!scopes_arr || !yyjson_is_arr(scopes_arr)) return 0;
    size_t idx, max;
    yyjson_val *elem;
    yyjson_arr_foreach(scopes_arr, idx, max, elem) {
        if (yyjson_is_str(elem)) {
            if (yyjson_get_len(elem) == req_len &&
                memcmp(yyjson_get_str(elem), req_scope, req_len) == 0) {
                return 1;
            }
        }
    }
    return 0;
}

bt_sec_result_t bt_jwt_verify_hs256(
    struct phr_header *headers,
    size_t num_headers,
    const char *secret,
    size_t secret_len,
    const char **req_scopes,
    const size_t *req_scope_lens,
    size_t num_req_scopes,
    char *payload_buf,
    size_t payload_buf_cap,
    size_t *out_payload_len,
    bt_json_doc_t *out_jdoc
) {
    bt_slice_t auth_hdr;
    if (!bt_find_header(headers, num_headers, "Authorization", 13, &auth_hdr)) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    if (auth_hdr.len <= 7 || strncasecmp(auth_hdr.ptr, "Bearer ", 7) != 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    const char *tok = auth_hdr.ptr + 7;
    size_t tok_len = auth_hdr.len - 7;
    while (tok_len > 0 && (*tok == ' ' || *tok == '\t')) {
        tok++;
        tok_len--;
    }

    /* Locate first '.' and second '.' */
    const char *dot1 = (const char *)memchr(tok, '.', tok_len);
    if (!dot1) return BT_SEC_ERR_UNAUTHORIZED;

    size_t rem_len = tok_len - (size_t)(dot1 - tok) - 1;
    const char *dot2 = (const char *)memchr(dot1 + 1, '.', rem_len);
    if (!dot2) return BT_SEC_ERR_UNAUTHORIZED;

    size_t signing_input_len = (size_t)(dot2 - tok);
    const char *payload_b64 = dot1 + 1;
    size_t payload_b64_len = (size_t)(dot2 - payload_b64);
    const char *sig_b64 = dot2 + 1;
    size_t sig_b64_len = tok_len - signing_input_len - 1;

    /* 1. Decode signature from Base64URL */
    uint8_t sig_decoded[64];
    size_t sig_decoded_len = 0;
    if (bt_base64url_decode(sig_b64, sig_b64_len, sig_decoded, sizeof(sig_decoded), &sig_decoded_len) != 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    /* 2. Compute HMAC-SHA256 using OpenSSL libcrypto */
    uint8_t mac[EVP_MAX_MD_SIZE];
    unsigned int mac_len = 0;
    if (!HMAC(
            EVP_sha256(),
            secret, (int)secret_len,
            (const unsigned char *)tok, signing_input_len,
            mac, &mac_len
        )) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    /* 3. Constant-time signature verification */
    if ((size_t)mac_len != sig_decoded_len || CRYPTO_memcmp(mac, sig_decoded, mac_len) != 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    /* 4. Base64URL-decode payload into caller buffer and parse with yyjson */
    if (bt_base64url_decode(
            payload_b64, payload_b64_len,
            (uint8_t *)payload_buf, payload_buf_cap - 1, out_payload_len
        ) != 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }
    payload_buf[*out_payload_len] = '\0';

    if (bt_json_parse_obj(payload_buf, *out_payload_len, out_jdoc) != 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    /* 5. Check `exp` claim if present */
    yyjson_val *exp_val = yyjson_obj_getn(out_jdoc->root, "exp", 3);
    if (exp_val) {
        if (!yyjson_is_int(exp_val)) {
            return BT_SEC_ERR_UNAUTHORIZED;
        }
        int64_t exp_ts = yyjson_get_sint(exp_val);
        int64_t now_ts = (int64_t)time(NULL);
        if (exp_ts <= now_ts) {
            return BT_SEC_ERR_UNAUTHORIZED;
        }
    }

    /* 6. Check required OAuth2 scopes in C if specified */
    if (num_req_scopes > 0) {
        yyjson_val *scopes_arr = yyjson_obj_getn(out_jdoc->root, "scopes", 6);
        for (size_t i = 0; i < num_req_scopes; i++) {
            if (!jwt_has_scope(scopes_arr, req_scopes[i], req_scope_lens[i])) {
                return BT_SEC_ERR_FORBIDDEN;
            }
        }
    }

    return BT_SEC_OK;
}

bt_sec_result_t bt_verify_api_key(
    struct phr_header *headers,
    size_t num_headers,
    const char *query,
    size_t query_len,
    int is_query,
    const char *key_name,
    size_t key_name_len,
    const char *expected_val,
    size_t expected_val_len,
    bt_slice_t *out_key_slice
) {
    int found = 0;
    if (is_query) {
        found = bt_find_query_param(query, query_len, key_name, key_name_len, out_key_slice);
    } else {
        found = bt_find_header(headers, num_headers, key_name, key_name_len, out_key_slice);
    }

    if (!found || out_key_slice->len == 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    if (expected_val && expected_val_len > 0) {
        if (!bt_const_time_eq(out_key_slice->ptr, out_key_slice->len, expected_val, expected_val_len)) {
            return BT_SEC_ERR_FORBIDDEN;
        }
    }
    return BT_SEC_OK;
}

bt_sec_result_t bt_verify_basic_auth(
    struct phr_header *headers,
    size_t num_headers,
    const char *expected_user,
    size_t expected_user_len,
    const char *expected_pass,
    size_t expected_pass_len,
    char *decode_buf,
    size_t decode_buf_cap,
    bt_slice_t *out_user,
    bt_slice_t *out_pass
) {
    bt_slice_t auth_hdr;
    if (!bt_find_header(headers, num_headers, "Authorization", 13, &auth_hdr)) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    if (auth_hdr.len <= 6 || strncasecmp(auth_hdr.ptr, "Basic ", 6) != 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    const char *b64_ptr = auth_hdr.ptr + 6;
    size_t b64_len = auth_hdr.len - 6;
    while (b64_len > 0 && (*b64_ptr == ' ' || *b64_ptr == '\t')) {
        b64_ptr++;
        b64_len--;
    }

    size_t decoded_len = 0;
    if (bt_base64_decode(b64_ptr, b64_len, (uint8_t *)decode_buf, decode_buf_cap - 1, &decoded_len) != 0) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }
    decode_buf[decoded_len] = '\0';

    const char *colon = (const char *)memchr(decode_buf, ':', decoded_len);
    if (!colon) {
        return BT_SEC_ERR_UNAUTHORIZED;
    }

    out_user->ptr = decode_buf;
    out_user->len = (size_t)(colon - decode_buf);
    out_user->found = 1;

    out_pass->ptr = colon + 1;
    out_pass->len = decoded_len - out_user->len - 1;
    out_pass->found = 1;

    if (expected_user && expected_user_len > 0) {
        int user_ok = bt_const_time_eq(out_user->ptr, out_user->len, expected_user, expected_user_len);
        int pass_ok = bt_const_time_eq(out_pass->ptr, out_pass->len, expected_pass, expected_pass_len);
        if (!(user_ok & pass_ok)) {
            return BT_SEC_ERR_UNAUTHORIZED;
        }
    }

    return BT_SEC_OK;
}
