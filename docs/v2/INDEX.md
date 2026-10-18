# Buckthorn v2 Documentation

Welcome to the Buckthorn v2.0 Architecture and Optimization Documentation. This directory documents the design decisions, micro-architectural optimizations, correctness hardenings, and benchmark performance gates introduced in Buckthorn v2.0.

## 🚀 Optimization Phases

| Phase | Documentation Document | Scope & Focus Areas | Status |
| :---: | :--- | :--- | :---: |
| **Phase 1** | [**Phase 1: Python Surface & Testing Optimizations**](phase1_python_surface_optimizations.md) | Slotted `HTTPException`, direct Cython method binding in `RingLogger` & `RawTestClient`, cached headers in `FileResponse`, `Optional[T]` type-mapping bugfix, and async lifespan event loop driver. | ✅ Completed (12/12 Tests Passed) |
| **Phase 2** | [**Phase 2: Build Configuration & Toolchain Tuning**](phase2_build_flags_optimizations.md) | `-DYYJSON_DISABLE_NON_STANDARD=1` preprocessor defines, auto-vectorization flags, ARM64/x86_64 architecture gating, Cython 3.0+ directives, and `-flto`. | ✅ Completed (6/6 Tests Passed) |
| **Phase 3** | [**Phase 3: Core C Engine Upgrades**](phase3_core_c_engine_optimizations.md) | Inline base-10 int-to-ASCII wire formatter, direct arena `yyjson_mut_write_buf` serialization, in-place `bt_parse_int64_checked`, 36-byte JWT header fast-path, thread-local `HMAC_CTX` reuse, cache-line aligned lock-free logging, and dynamic idle handle gating. | ✅ Completed (16/16 Tests Passed) |
| **Phase 4** | [**Phase 4: Cython Bridge Integration & Hot-Path Handoff**](phase4_cython_bridge_optimizations.md) | CPython 3.11+ `PyIter_Send` eager coroutine stepping, reordered response serialization, recursive pure C `yyjson` DOM walker, zero-copy UTF-8 lookups, expanded Cython static freelists, 32KB buffer guard, string interning, and decoupled async closures. | ✅ Completed (14/14 Tests Passed) |
| **Phase 5** | [**Phase 5: CLI De-Biasing, Speed Gates & Full Suite Regression**](phase5_cli_and_benchmark_speed_gates.md) | Decoupled CLI benchmark harness, uninstrumented throughput loop, latency histogram sampling, 10-phase speed gates (>120k–1.83M RPS), and zero-leak memory verification. | ✅ Completed (8/8 Tests Passed) |

---

## 📋 Master Roadmap & Plan
- [5-Phase Master Optimization Plan](../../argonreport/optimizations/FIVE_PHASE_OPTIMIZATION_PLAN.md)
- [Master v2 Roadmap & Executive Synthesis](../../argonreport/optimizations/MASTER_V2_ROADMAP.md)
- [Architectural Laws & Core Contracts](../../aidocs/index.md)
