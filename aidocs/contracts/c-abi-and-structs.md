# Contracts: Cross-Language C Structs, Enums & Constants

## 1. Parameter Source IDs (`CompiledParamSpec.source`)
Must remain synchronized between `buckthorn/_core.pyx`, `buckthorn/app.py`, `buckthorn/params.py`, and `buckthorn/openapi.py`:

| Constant Name (`_core.pyx`) | Integer Value | Origin / Meaning |
| :--- | :--- | :--- |
| `SRC_PATH` | `1` | URL path segment (`/users/{id}`) |
| `SRC_QUERY` | `2` | URL query string (`?limit=10`) |
| `SRC_HEADER` | `3` | HTTP request header (`X-Api-Key`) |
| `SRC_COOKIE` | `4` | HTTP `Cookie` header key-value |
| `SRC_REQUEST` | `5` | Zero-copy `buckthorn.Request` instance |
| `SRC_BODY_SCHEMA` | `6` | JSON body parsed via `yyjson` into `Schema` |
| `SRC_DEPENDS` | `7` | Flattened `Depends()` slot lookup |
| `SRC_SECURITY` | `8` | C OpenSSL JWT / APIKey / Basic Auth guard |
| `SRC_UPLOAD_FILE` | `9` | C `multipart/form-data` `UploadFile` slice |
| `SRC_BACKGROUND_TASKS` | `10` | Post-response `BackgroundTasks` queue |

---

## 2. Parameter & Field Type IDs (`param_type` / `field_type`)
Must remain synchronized between `buckthorn/_core.pyx`, `buckthorn/app.py`, `buckthorn/schema.py`, `buckthorn/compiler.py`, and `buckthorn/openapi.py`:

| Constant Name (`_core.pyx`) | Integer Value | C / Python Type |
| :--- | :--- | :--- |
| `TYPE_INT` | `1` | `int64_t` / `long long` / Python `int` |
| `TYPE_FLOAT` | `2` | `double` / Python `float` |
| `TYPE_STR` | `3` | `bt_slice_t` UTF-8 / Python `str` |
| `TYPE_REQUEST` | `4` | `cdef class Request` |
| `TYPE_BOOL` | `5` | `bint` / `uint8_t` / Python `bool` |
| `TYPE_LIST` | `6` | `yyjson_val*` Array / Python `list` |
| `TYPE_SCHEMA` | `7` | `buckthorn.Schema` / `cdef class` |
| `TYPE_DEPENDS` | `8` | Resolved `Depends()` slot object |
| `TYPE_SECURITY` | `9` | Resolved `Security()` claims or credentials |
| `TYPE_UPLOAD_FILE` | `10` | `cdef class UploadFile` |
| `TYPE_BACKGROUND_TASKS` | `11` | `cdef class BackgroundTasks` |

---

## 3. Core C Struct Contracts
- **`bt_slice_t` (`bt_params.h`):**
  ```c
  typedef struct {
      const char *ptr;
      size_t len;
      int found;
  } bt_slice_t;
  ```
  *Contract:* `ptr` is **not** null-terminated! Always pass `slice.len` explicitly to `memcmp`, `strncasecmp`, `PyUnicode_DecodeUTF8`, or `%.*s` format specifiers.

- **`bt_val_error_t` (`bt_params.h`):**
  `BT_ERR_NONE (0)` indicates validation passed. Any non-zero value (`1..9`) triggers an immediate `422 Unprocessable Entity` response via `_write_422_error()`.

- **`bt_sec_result_t` (`bt_security.h`):**
  `BT_SEC_OK (0)` indicates authentication and authorization succeeded. `BT_SEC_ERR_UNAUTHORIZED (401)` or `BT_SEC_ERR_FORBIDDEN (403)` triggers an immediate C error payload via `_write_auth_error()`.
