# Buckthorn Agent Directives

## 1. Context Resolution Strategy
- **ALWAYS** read `aidocs/index.md` as your initial step when planning features, fixing bugs, or refactoring.
- Do **NOT** read all `.c`, `.pyx`, and `.py` files indiscriminately. Use this index to load ONLY the domain doc relevant to your task:
  - **C HTTP Engine, Arena Memory, Middleware, Security & Logger** -> `aidocs/architecture/c-engine-and-memory.md`
  - **Cython Bridge (`_core.pxd` / `_core.pyx`), Freelists & DI Execution** -> `aidocs/architecture/cython-bridge-and-freelist.md`
  - **Python API Surface, OpenAPI, CLI & AST-to-Cython Transpiler** -> `aidocs/architecture/python-surface-and-transpiler.md`
  - **C Struct Layouts, Slice Pointers & Error Enums** -> `aidocs/contracts/c-abi-and-structs.md`
  - **TDD Phases, Build Commands & RPS Benchmark Gates** -> `aidocs/contracts/tdd-and-performance-gates.md`

---

## 2. Repository Map (Source of Truth)
| Layer | Directory / Files | Responsibility |
| :--- | :--- | :--- |
| **Vendor C (Read-Only)** | `buckthorn/c_vendor/` | `picohttpparser.c/.h` (SIMD HTTP), `rax.c/.h` (Radix Tree), `yyjson.c/.h` (SIMD JSON). **NEVER modify vendor files.** |
| **Core C11 Engine** | `buckthorn/c_src/` | `bt_arena.h`, `bt_http.c/.h`, `bt_params.c/.h`, `bt_json.c/.h`, `bt_logger.c/.h`, `bt_security.c/.h`, `bt_middleware.c/.h`, `bt_realtime.c/.h`. |
| **Cython C-Bridge** | `buckthorn/_core.pxd`, `buckthorn/_core.pyx` | Zero-copy `cdef extern` bindings, `@cython.freelist` extension types (`Request`, `WebSocket`, `UploadFile`, `BackgroundTasks`), and flat DI runner. |
| **Python Developer API** | `buckthorn/*.py` | `app.py`, `schema.py`, `params.py`, `security.py`, `middleware.py`, `routing.py`, `responses.py`, `openapi.py`, `compiler.py`, `cli.py`. |
| **TDD Verification** | `tests/phase1/` .. `tests/phase10/` | Phase-gated functional tests and single-worker `>80,000 RPS` speed gates. |

---

## 3. The 7 Non-Negotiable Architectural Laws
1. **Zero Pure-Python Hot-Path Parsing:** Never use Python's `json`, `urllib.parse`, `http.cookies`, `re`, or `cgi` inside a request lifecycle. All parsing happens in C (`picohttpparser`, `bt_params.c`, `yyjson`, `bt_realtime.c`).
2. **Zero `malloc()` Per Request:** Use the per-connection 64KB `bt_arena_t` bump allocator (`bt_arena_alloc`) or the thread-local 64KB `yyjson` memory pools (`tls_read_pool` / `tls_write_pool`). Always reset the arena via `bt_arena_reset()` upon request completion.
3. **3-File Synchronization Rule for C Changes:** Whenever you add or modify a C function or struct in `buckthorn/c_src/*.c`, you **MUST** update:
   - The C header (`buckthorn/c_src/*.h`)
   - The Cython declaration (`buckthorn/_core.pxd`)
   - The `Extension(..., sources=[...])` list in `setup.py` (if adding a new `.c` file)
4. **`PyObject*` Garbage Collection Pinning:** Whenever a Python/Cython object (`RouteEntry`, `CythonRingLogger`) is cast to `void*` and stored in a C struct (`rax` tree or `bt_router_t`), you **MUST** keep a reference to it in a Python container (`self._handler_refs` or `self._access_logger_ref`) on `CoreRouter`. Otherwise, CPython's GC will free the object and cause a segmentation fault.
5. **No Borrowed Pointers in Background Threads:** The C11 Ring Logger (`bt_logger.c`) runs on a background `pthread` without the GIL. Never store `PyObject*` or borrowed `const char*` pointers in `bt_log_entry_t`; always `memcpy` strings into the fixed-size inline C arrays inside the ring buffer slot.
6. **`PyObject_SetAttr` / `PyObject_GetAttr` for Schemas:** Never use `PyObject_GenericSetAttr` or `__dict__` on `Schema` instances in `_core.pyx`. Production-compiled `Schema` models are Cython `cdef class` extension types without a `__dict__`; use `PyObject_SetAttr` and `PyObject_GetAttr` so Cython's C slot descriptors (`tp_descr_set`) are invoked.
7. **Strict TDD & Rebuild Workflow:** After any edit to `.c`, `.h`, `.pxd`, or `.pyx` files, you **MUST** recompile via `pip install -e ".[dev]" --no-build-isolation` before running `pytest`.
