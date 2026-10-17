# Buckthorn v2 Documentation

Welcome to the Buckthorn v2.0 Architecture and Optimization Documentation. This directory documents the design decisions, micro-architectural optimizations, correctness hardenings, and benchmark performance gates introduced in Buckthorn v2.0.

## 🚀 Optimization Phases

| Phase | Documentation Document | Scope & Focus Areas | Status |
| :---: | :--- | :--- | :---: |
| **Phase 1** | [**Phase 1: Python Surface & Testing Optimizations**](phase1_python_surface_optimizations.md) | Slotted `HTTPException`, direct Cython method binding in `RingLogger` & `RawTestClient`, cached headers in `FileResponse`, `Optional[T]` type-mapping bugfix, and async lifespan event loop driver. | ✅ Completed (12/12 Tests Passed) |
| **Phase 2** | [Phase 2: Build Configuration & Toolchain Tuning](../../argonreport/optimizations/FIVE_PHASE_OPTIMIZATION_PLAN.md#phase-2-build-configuration-preprocessor-flags--toolchain-tuning) | `-DYYJSON_DISABLE_NON_STANDARD=1` preprocessor defines, auto-vectorization flags, ARM64 portability, and Cython 3.0+ directives. | ⏳ Next Up |
| **Phase 3** | [Phase 3: Core C Engine Upgrades](../../argonreport/optimizations/FIVE_PHASE_OPTIMIZATION_PLAN.md#phase-3-core-c-engine-upgrades) | Inline base-10 int-to-ASCII wire formatter, direct arena `yyjson_mut_write_buf` serialization, in-place `bt_parse_int64_checked`, 36-byte JWT header fast-path, thread-local `HMAC_CTX` reuse, and cache-line aligned lock-free logging. | ⏳ Planned |
| **Phase 4** | [Phase 4: Cython Bridge Integration](../../argonreport/optimizations/FIVE_PHASE_OPTIMIZATION_PLAN.md#phase-4-cython-bridge-integration--hot-path-handoff-optimization) | Reordered response checks, `PyIter_Send` zero-allocation eager stepping, zero-copy `yyjson` dict claims, zero-copy header lookup, freelists, 32KB buffer overflow guard. | ⏳ Planned |
| **Phase 5** | [Phase 5: CLI De-Biasing & Speed Gates](../../argonreport/optimizations/FIVE_PHASE_OPTIMIZATION_PLAN.md#phase-5-end-to-end-tdd-verification-cli-de-biasing--benchmark-speed-gates) | Decoupled CLI benchmark harness, full rebuild, AddressSanitizer validation, and single-worker throughput gates (+15% to +40% RPS). | ⏳ Planned |

---

## 📋 Master Roadmap & Plan
- [5-Phase Master Optimization Plan](../../argonreport/optimizations/FIVE_PHASE_OPTIMIZATION_PLAN.md)
- [Master v2 Roadmap & Executive Synthesis](../../argonreport/optimizations/MASTER_V2_ROADMAP.md)
- [Architectural Laws & Core Contracts](../../aidocs/index.md)
