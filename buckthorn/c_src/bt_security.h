/* buckthorn/c_src/bt_security.h */
#ifndef BT_SECURITY_H
#define BT_SECURITY_H

#include <stddef.h>
#include <stdint.h>
#include "bt_params.h"
#include "bt_json.h"

#define BT_MAX_SCOPES 8

typedef enum {
    BT_SEC_OK = 0,
    BT_SEC_ERR_UNAUTHORIZED = 401,
    BT_SEC_ERR_FORBIDDEN = 403
} bt_sec_result_t;

/* Constant-time byte comparison using OpenSSL CRYPTO_memcmp */
int bt_const_time_eq(const char *a, size_t a_len, const char *b, size_t b_len);

/* Fast Base64URL and Standard Base64 decoders */
int bt_base64url_decode(const char *in, size_t in_len, uint8_t *out, size_t out_cap, size_t *out_len);
int bt_base64_decode(const char *in, size_t in_len, uint8_t *out, size_t out_cap, size_t *out_len);

/*
 * Pure C JWT HS256 verifier using OpenSSL HMAC(EVP_sha256()) + yyjson:
 * 1. Extracts Bearer token from Authorization header
 * 2. Verifies HMAC-SHA256 signature in constant time (CRYPTO_memcmp)
 * 3. Parses JSON payload into out_jdoc using zero-malloc yyjson pool
 * 4. Validates `exp` timestamp against current UNIX epoch
 * 5. Verifies all required OAuth2 scopes exist in payload `scopes` array
 */
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
);

/* Pure C API Key Header / Query extractor & constant-time verifier */
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
);

/* Pure C HTTP Basic Auth decoder & optional constant-time credential verifier */
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
);

#endif /* BT_SECURITY_H */
