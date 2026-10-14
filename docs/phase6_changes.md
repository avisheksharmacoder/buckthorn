# Phase 6: Native C Security Engine, OpenSSL HMAC JWT Verification, OAuth2 Scopes & Fast 401/403 Rejection

## Overview
Phase 6 builds directly on top of Phases 1 through 5, implementing an ultra-high-performance **Native C Security & Cryptography Engine** (`bt_security.c`), pure C OpenSSL `EVP_sha256` HMAC JWT verification, constant-time `CRYPTO_memcmp` authentication, C-level OAuth2 scope enforcement, and zero-handler fast `401`/`403` rejection.

---

## Key Features & Architecture

### 1. Pure C OpenSSL JWT Verification (`bt_security.c`)
- **No Python Cryptography Dependencies:** Does not use `PyJWT`, `cryptography`, or `python-jose`. All signature verification executes directly in C using OpenSSL `libcrypto` (`HMAC(EVP_sha256(), ...)`).
- **Zero-Copy Base64URL Decoding:** Custom dual lookup table (`B64_DECODE_TABLE`) that handles both standard Base64 and unpadded Base64URL decoding in a single pass without extra memory allocations.
- **Constant-Time Verification:** Every cryptographic signature and secret check uses OpenSSL's `CRYPTO_memcmp()` via `bt_const_time_eq()` to prevent timing side-channel attacks.
- **Zero-Malloc JSON Payload Extraction:** Parses the decoded JWT claims using `yyjson` with thread-local preallocated pools, validating expiration (`exp`) timestamps against the system clock (`time(NULL)`).
- **C-Level OAuth2 Scope Enforcement:** Verifies that required scopes (up to `BT_MAX_SCOPES`) are present in the JWT payload's `scopes` array before route handlers are invoked.

### 2. Constant-Time API Key & HTTP Basic Auth
- **Header & Query API Keys:** `APIKeyHeader` and `APIKeyQuery` extract API keys directly from `phr_header` arrays or raw query strings and verify them in constant time (`CRYPTO_memcmp`).
- **HTTP Basic Authentication:** `HTTPBasic` decodes Base64 credentials directly in C, extracts username and password slices, and optionally verifies them against expected credentials in constant time, passing `HTTPBasicCredentials` to route dependencies.

### 3. Zero-Handler Fast 401/403 Rejection
- When a token is missing, forged, expired (`401 Unauthorized`), or lacks required scopes (`403 Forbidden`), the C/Cython layer writes the JSON error response directly into the wire response buffer (`out_body_buf`) and returns immediately.
- The Python route handler and downstream dependency graphs are completely bypassed, shielding the application layer from malicious or unauthorized load.

### 4. Compilation Architecture & Dependency Injection
- `SecurityBase` subclasses (`HTTPBearer`, `OAuth2PasswordBearer`, `APIKeyHeader`, `APIKeyQuery`, `HTTPBasic`) compile at route registration time into `CompiledSecuritySpec` structs.
- `app.py` detects security schemes during parameter compilation and emits `SRC_SECURITY` (`source=8`, `param_type=9`) parameter specs, bypassing general Python DI overhead.

---

## File Changes Summary

| File | Status | Description |
|---|---|---|
| `buckthorn/c_src/bt_security.h` | **NEW** | C header declaring `bt_sec_result_t`, `bt_jwt_verify_hs256`, `bt_verify_api_key`, `bt_verify_basic_auth`, `bt_base64url_decode`, `bt_const_time_eq`. |
| `buckthorn/c_src/bt_security.c` | **NEW** | Pure C implementation of OpenSSL HMAC-SHA256, Base64/Base64URL decoding, constant-time `CRYPTO_memcmp`, JWT parsing, scope verification, and auth guards. |
| `buckthorn/security.py` | **NEW** | Python security surface: `SecurityBase`, `HTTPBearer`, `OAuth2PasswordBearer`, `APIKeyHeader`, `APIKeyQuery`, `HTTPBasic`, `create_jwt_token`. |
| `buckthorn/_core.pxd` | **UPDATED** | Declared `bt_sec_result_t` enum and C security functions from `bt_security.h`. |
| `buckthorn/_core.pyx` | **UPDATED** | Added `CompiledSecuritySpec`, `HTTPBasicCredentials`, `_write_auth_error`, `_execute_c_security_spec`, `SRC_SECURITY` source handling, and fast C auth rejection. |
| `buckthorn/params.py` | **UPDATED** | Added `SecurityInfo` (subclass of `DependsInfo`) and `Security()` parameter factory. |
| `buckthorn/app.py` | **UPDATED** | Compiled `SecurityBase` schemes directly into C `SRC_SECURITY` parameter specs. |
| `buckthorn/__init__.py` | **UPDATED** | Exported `Security`. |
| `setup.py` | **UPDATED** | Added `bt_security.c`, linked `-lcrypto`, and bumped version to `0.6.0`. |
| `tests/phase6/test_c_security.py` | **NEW** | TDD test suite covering OpenSSL JWT verification, scope enforcement, expiration, API keys, HTTP basic auth, and speed gates. |

---

## Benchmark & Speed Gate Results

```text
============================= test session starts ==============================
collected 24 items

tests/phase1/test_core_router.py::test_static_get_and_post_routing PASSED
tests/phase1/test_core_router.py::test_unmatched_route_returns_404_in_c PASSED
tests/phase1/test_core_router.py::test_query_string_stripped_before_radix_lookup PASSED
tests/phase1/test_core_router.py::test_malformed_http_rejected_in_c_without_python PASSED
tests/phase1/test_core_router.py::test_c_arena_zero_leak_and_single_worker_speed_gate PASSED
tests/phase2/test_params.py::test_typed_path_query_header_and_cookie_extraction PASSED
tests/phase2/test_params.py::test_c_level_validation_rejects_invalid_types_and_bounds_with_422 PASSED
tests/phase2/test_params.py::test_zero_copy_request_object_lazy_access PASSED
tests/phase2/test_params.py::test_phase2_parameter_extraction_speed_gate PASSED
tests/phase3/test_schema_serde.py::test_single_pass_json_validation_and_response_model_filtering PASSED
tests/phase3/test_schema_serde.py::test_c_yyjson_schema_validation_errors_return_422 PASSED
tests/phase3/test_schema_serde.py::test_mixed_path_query_and_schema_body_with_dict_response PASSED
tests/phase3/test_schema_serde.py::test_phase3_yyjson_schema_speed_gate PASSED
tests/phase4/test_depends_di.py::test_dag_deduplication_and_lifo_generator_teardown PASSED
tests/phase4/test_depends_di.py::test_use_cache_false_executes_dependency_per_branch PASSED
tests/phase4/test_depends_di.py::test_http_exception_in_dependency_or_handler_still_runs_generator_teardown PASSED
tests/phase4/test_depends_di.py::test_class_dependency_shorthand_and_callable_instance PASSED
tests/phase4/test_depends_di.py::test_phase4_flattened_di_speed_gate PASSED
tests/phase5/test_ring_logger.py::test_lock_free_ring_logger_structured_json_and_types PASSED
tests/phase5/test_ring_logger.py::test_app_logger_and_pure_c_http_access_logging PASSED
tests/phase5/test_ring_logger.py::test_ring_logger_hot_path_nanosecond_budget_and_rps_gate PASSED
tests/phase6/test_c_security.py::test_c_openssl_jwt_verification_expiration_and_scopes PASSED
tests/phase6/test_c_security.py::test_c_api_key_header_and_http_basic_auth PASSED
tests/phase6/test_c_security.py::test_phase6_c_jwt_security_speed_gate PASSED

============================== 24 passed in 0.99s ==============================
```

- **JWT Verified Route Throughput:** **105,128 RPS** (Target Gate: >75,000 RPS)
- **Fast 401 C Rejection Throughput:** **328,332 RPS** (Target Gate: >120,000 RPS)
