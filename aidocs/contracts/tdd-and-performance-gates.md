# Contracts: TDD Workflow, Compiler Flags & Performance Gates

## 1. Standard Build & Test Commands
Whenever you modify any code in `buckthorn/`, run these commands in order:

```bash
# 1. Ensure vendor C files exist (picohttpparser.c, rax.c, yyjson.c)
bash scripts/fetch_vendor.sh

# 2. Compile C11 + Cython extensions in-place
pip install -e ".[dev]" --no-build-isolation

# 3. Run full 10-Phase TDD suite
pytest tests/ -v -s
```

---

## 2. Inspecting Cython C-API Overhead (`cython -a`)

`setup.py` runs `cythonize(..., annotate=True)`, which generates `buckthorn/_core.html`.

- **Rule:** Before finalizing any change to `_core.pyx`, inspect the generated C/HTML output to verify that `nogil` C helpers and `cdef inline` functions do not accidentally box C integers/pointers into `PyObject*` inside tight loops.

---

## 3. AddressSanitizer (ASan) Debug Build (When Debugging Segfaults)

If a test crashes with `SIGSEGV` or `SIGABRT`, recompile with AddressSanitizer enabled to pinpoint the exact C file and line number:

```bash
CFLAGS="-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer" \
LDFLAGS="-fsanitize=address,undefined" \
pip install -e ".[dev]" --no-build-isolation --force-reinstall
```

---

## 4. Phase-by-Phase TDD Test Gates

Every pull request or agent task must keep all 10 test suites passing and above their single-worker RPS floors:

| Phase | Test Suite Path | Minimum Single-Worker Speed Gate |
| :--- | :--- | :--- |
| **Phase 1** | `tests/phase1/test_core_router.py` | `> 100,000 RPS` (Core C `picohttpparser` + `rax` + 64KB Arena) |
| **Phase 2** | `tests/phase2/test_params.py` | `> 80,000 RPS` (4 typed Path/Query/Header C extractions) |
| **Phase 3** | `tests/phase3/test_schema_serde.py` | `> 80,000 RPS` (Single-pass `yyjson` Schema validate + filter) |
| **Phase 4** | `tests/phase4/test_depends_di.py` | `> 75,000 RPS` (3 nested `Depends()` DAG nodes + caching) |
| **Phase 5** | `tests/phase5/test_ring_logger.py` | `> 80,000 RPS` (2 structured logs/req via C11 atomic ring buffer) |
| **Phase 6** | `tests/phase6/test_c_security.py` | `> 75,000 RPS` (Valid OpenSSL JWT) / `> 120,000 RPS` (C 401 reject) |
| **Phase 7** | `tests/phase7/test_middlewares.py` | `> 80,000 RPS` (Active C CORS + RateLimiter + SecurityHeaders) |
| **Phase 8** | `tests/phase8/test_realtime_and_files.py` | `> 60,000 cycles/sec` (Full RFC 6455 WebSocket session + 64-bit XOR) |
| **Phase 9** | `tests/phase9/test_background_and_openapi.py` | `> 75,000 RPS` (`BackgroundTasks`) / `> 150,000 RPS` (`/openapi.json`) |
| **Phase 10** | `tests/phase10/test_ast_transpiler_and_cli.py` | `> 80,000 RPS` (AST-to-Cython compiled `.so` binary endpoint) |
