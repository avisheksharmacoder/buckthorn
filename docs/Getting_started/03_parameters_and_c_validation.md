# 03. Typed Parameters & C-Level Fast Validation

## Overview
Buckthorn provides declarative parameter extraction and validation for **Path**, **Query**, **Header**, and **Cookie** parameters. Validation checks (such as bounds checking and string lengths) are executed directly in C via `bt_params.c`, catching invalid input and formatting `422 Unprocessable Entity` JSON responses before Python bytecode executes.

---

## 1. Layman's Explanation
In most web frameworks, when someone sends a request with an invalid age like `age=-5` or a username that is too short, the framework boots up the full Python validation engine (like Pydantic), creates several Python objects to describe the error, and formats a response. This consumes CPU cycles and slows down the server.

Buckthorn places a C-level security guard directly at the gate. If someone sends an integer where a string was expected, or passes a number out of range, the guard in C rejects it on the spot in nanoseconds, sending back a helpful error message without bothering the Python interpreter.

---

## 2. Technical Explanation
1. **Compile-Time Plan (`CompiledParamSpec`):**
   When routes are registered on `Buckthorn`, their function signatures and default parameter markers (`Path(...)`, `Query(...)`, `Header(...)`, `Cookie(...)`) are inspected once at startup and flattened into an array of C-compatible `CompiledParamSpec` structs.
2. **Native C Parsers (`bt_params.c`):**
   - Integers are parsed using `bt_parse_int64_checked()` with bounds validation (`ge`, `le`, `gt`, `lt`).
   - Floats are parsed using fast ASCII-to-double arithmetic via `bt_parse_double_checked()`.
   - Strings are checked for bounds (`min_length`, `max_length`) using `bt_validate_str_checked()`.
   - Headers and Cookies use fast case-insensitive pointer comparisons directly against `picohttpparser` header arrays.
3. **C-Level 422 JSON Generator:**
   If any parameter fails parsing or constraint checks, the C engine writes a pre-formatted JSON error body directly into `out_body_buf` (`{"detail":[{"loc":["<source>","<name>"],"msg":"...","type":"..."}]}`), sets HTTP status `422`, and skips invoking the Python handler entirely.

---

## 3. Code Example

```python
# params_demo.py
from typing import Optional
from buckthorn import Buckthorn, Path, Query, Header, Cookie

app = Buckthorn(title="Parameters & Validation Demo")

@app.get("/items/{item_id}")
def get_item(
    item_id: int = Path(ge=1, le=100_000),
    q: Optional[str] = Query(default=None, min_length=2, max_length=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user_agent: str = Header(default="unknown", alias="User-Agent"),
    session_id: Optional[str] = Cookie(default=None),
):
    return {
        "item_id": item_id,
        "search_query": q,
        "page": page,
        "page_size": page_size,
        "client_agent": user_agent,
        "session_id": session_id,
    }
```

---

## 4. Part-by-Part Code Explanation

1. `item_id: int = Path(ge=1, le=100_000)`:
   - Identifies `item_id` as a required path parameter.
   - Enforces that `item_id` must be an integer between `1` and `100,000` inclusive.
   - Requesting `/items/0` or `/items/999999` instantly returns `422` in C.
2. `q: Optional[str] = Query(default=None, min_length=2, max_length=50)`:
   - Declares an optional query string parameter `?q=...`.
   - If omitted, defaults to `None`.
   - If provided, enforces that the string has between 2 and 50 characters.
3. `page: int = Query(default=1, ge=1)` & `page_size: int = Query(default=20, ge=1, le=100)`:
   - Enforces pagination rules (minimum page 1, page size capped between 1 and 100).
   - Automatically parses ASCII query strings into Python `int` objects.
4. `user_agent: str = Header(default="unknown", alias="User-Agent")`:
   - Extracts the HTTP request header `User-Agent`.
   - Case-insensitive C lookup searches the parsed headers in nanoseconds without allocating a Python dictionary of all request headers.
5. `session_id: Optional[str] = Cookie(default=None)`:
   - Searches the `Cookie` header for a cookie named `session_id`.
   - Returns the value as a Python string or `None` if absent.

---

## 5. Parameter Constraints Reference

| Constraint | Applicable Types | Description |
|---|---|---|
| `default` | All | Default value if the parameter is omitted by the client. |
| `ge` | `int`, `float` | Greater than or equal to ($x \ge \text{val}$). |
| `le` | `int`, `float` | Less than or equal to ($x \le \text{val}$). |
| `gt` | `int`, `float` | Strictly greater than ($x > \text{val}$). |
| `lt` | `int`, `float` | Strictly less than ($x < \text{val}$). |
| `min_length` | `str` | Minimum character length required. |
| `max_length` | `str` | Maximum character length permitted. |
| `alias` | `Header`, `Query` | Alternate lookup key (e.g., `X-API-Key` or `User-Agent`). |

---

## 6. Why It Is Different From Other Python Frameworks

| Capability | FastAPI (Pydantic) | Buckthorn |
|---|---|---|
| **Validation Layer** | Python object instantiation and Pydantic validation tree | Pure C integer arithmetic and string length scans (`bt_params.c`) |
| **Error Handling (422)** | Allocates `RequestValidationError`, serializes JSON error schema in Python | Direct-to-buffer JSON error formatting in C (~40 ns) |
| **Header/Cookie Lookup** | Builds Python dict from all headers, performs Python string lookups | Slices directly from C `picohttpparser` pointers without copying |
| **Throughput Under Load** | ~20,000 req/sec for validated routes | **>80,000 req/sec** on validated routes |
