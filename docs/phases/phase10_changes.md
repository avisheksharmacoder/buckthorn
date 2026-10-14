# Phase 10: AST-to-Cython Production Transpiler, Coroutine Elision, `buckthorn` CLI & Linux Docker Build

## Overview
Phase 10 completes the 10-phase architecture of **Buckthorn**, providing an automated AOT (Ahead-of-Time) compilation pipeline, intelligent runtime optimization, and production deployment tooling:
* **The AST-to-Cython Transpiler (`buckthorn/compiler.py`):** Automatically analyzes user Python code, lowering `Schema` models into `@cython.freelist(4096) cdef class` structs with unboxed C types and local variables into native C types via `@cython.locals(...)`.
* **Coroutine Elision:** Automatically transforms non-awaiting `async def` route handlers into synchronous C functions, eliminating coroutine allocation and asyncio task frame overhead to achieve **>80,000 requests/sec** per single worker core on compiled binaries.
* **The `buckthorn` CLI (`buckthorn/cli.py`):** Provides developer commands: `buckthorn dev`, `buckthorn build --prod`, `buckthorn run --workers N` (multi-process Linux `SO_REUSEPORT`), and `buckthorn bench` (built-in microsecond speed gate evaluator).
* **Multi-Stage Production Docker Build (`Dockerfile`):** GCC `-O3 -march=native -msse4.2 -flto` build pipeline producing minimal production runtime images.

---

## Key Features & Architecture

### 1. AST-to-Cython Production Transpiler (`buckthorn/compiler.py`)
- Analyzes the Python Abstract Syntax Tree (`ast.parse`) of a developer's application module.
- **Lowering `Schema` Classes:**
  - Emits `@cython.freelist(4096)` and `cdef class <Name>:`.
  - Converts typed attributes (`int`, `float`, `str`, `bool`) to unboxed C fields (`cdef public long long`, `cdef public double`, `cdef public str`, `cdef public bint`).
  - Registers the compiled struct with `_bt_register_compiled_schema` so C-level `yyjson` deserialization and serialization bind directly to C descriptor slots (`tp_descr_set` / `tp_descr_get`) without creating Python dictionaries.
- **Dynamic Compilation (`compile_pyx_module`):**
  - Compiles the generated `.pyx` into an optimized `.so` shared library using GCC (`-O3`, `-march=native`, `-msse4.2`) and loads it dynamically into memory.

### 2. Coroutine Elision
- In standard Python frameworks, non-awaiting `async def` handlers incur a severe coroutine allocation and event loop scheduling penalty.
- The transpiler inspects every `ast.AsyncFunctionDef`. If the body contains zero `ast.Await`, `ast.AsyncFor`, or `ast.AsyncWith` AST nodes:
  1. It converts the handler into a synchronous `def` function with `# cython: binding=True`.
  2. It emits a companion `cpdef object _sync_fast_<name>(...)` entrypoint.
  3. When registered, `inspect.iscoroutinefunction(fn)` evaluates to `False`.
  4. The C dispatcher executes the handler synchronously with zero coroutine frames allocated at runtime.

### 3. Read-Only `PyTypeObject` Safe Schema Registry (`buckthorn/schema.py`)
- Compiled Cython `cdef class` types have immutable `PyTypeObject` definitions where `cls.__bt_schema_spec__ = ...` raises `TypeError`.
- Implemented `SCHEMA_SPEC_REGISTRY` and `get_schema_spec(cls_or_obj)` to safely retrieve schema specifications across pure Python and compiled `cdef class` types.
- Updated `buckthorn/_core.pyx` to use `PyObject_SetAttr` and `PyObject_GetAttr` instead of generic dictionary lookups.

### 4. The `buckthorn` CLI (`buckthorn/cli.py`)
- `buckthorn dev <target>`: Runs application in development mode with hot reload.
- `buckthorn build <source> --prod [--out-dir]`: Transpiles Python module to Cython `.pyx` and compiles to `.so` binary.
- `buckthorn run <target> [--workers N]`: Multi-process server pool using Linux kernel `SO_REUSEPORT` for core saturation.
- `buckthorn bench <target> [--gate N] [--iterations N]`: In-memory microsecond performance benchmark and speed gate evaluator with automated route discovery and SLA validation.

### 5. Multi-Stage Production Linux Dockerfile (`Dockerfile`)
- **Stage 1 (Builder):** Debian Bookworm with GCC, Python 3.12, `libuv1-dev`, `libssl-dev`. Fetches C vendor sources and installs Buckthorn with `-O3 -msse4.2 -flto`.
- **Stage 2 (Runtime):** Slim Debian image containing only runtime dependencies (`libuv1`, `libssl3`), running `buckthorn run main:app --workers 4`.

---

## File Changes Summary

| File | Status | Description |
|---|---|---|
| `buckthorn/compiler.py` | **NEW** | AST-to-Cython transpiler with Schema lowering, `@cython.locals`, coroutine elision, and dynamic `.so` compiler. |
| `buckthorn/cli.py` | **NEW** | CLI entrypoint supporting `dev`, `build`, `run`, and `bench`. |
| `Dockerfile` | **NEW** | Multi-stage production Linux Dockerfile with `-O3 -msse4.2 -flto`. |
| `buckthorn/schema.py` | **UPDATED** | Added `SCHEMA_SPEC_REGISTRY`, `get_schema_spec()`, and `register_compiled_schema()`. |
| `buckthorn/_core.pyx` | **UPDATED** | Replaced `PyObject_GenericSetAttr/GetAttr` with `PyObject_SetAttr/GetAttr` and updated `isawaitable` in middleware layer. |
| `buckthorn/app.py` | **UPDATED** | Imported and utilized `get_schema_spec()` and exposed `app.routes`. |
| `buckthorn/openapi.py` | **UPDATED** | Utilized `get_schema_spec()` for OpenAPI response model schemas. |
| `buckthorn/__init__.py` | **UPDATED** | Exported `transpile_module_to_pyx` and `compile_pyx_module`. |
| `pyproject.toml` | **UPDATED** | Bumped version to `1.0.0`, registered CLI script `buckthorn = "buckthorn.cli:main"`. |
| `tests/phase10/test_ast_transpiler_and_cli.py` | **NEW** | Phase 10 test suite covering transpilation, coroutine elision, binary compilation, and >80k RPS speed gate. |

---

## Test & Speed Gate Results

```text
tests/phase10/test_ast_transpiler_and_cli.py::test_ast_to_cython_transpiler_lowers_schema_and_elides_coroutines PASSED
tests/phase10/test_ast_transpiler_and_cli.py::test_transpiler_preserves_async_def_when_await_is_present PASSED
tests/phase10/test_ast_transpiler_and_cli.py::test_compile_pyx_module_end_to_end_binary_execution_and_speed_gate PASSED (>80k req/s)
tests/phase10/test_ast_transpiler_and_cli.py::test_buckthorn_cli_build_command_creates_compiled_extension PASSED

============================== 41 passed in 6.04s ==============================
```
