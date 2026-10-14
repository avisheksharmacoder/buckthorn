# Buckthorn Developer Documentation: Getting Started

Welcome to the comprehensive, feature-by-feature developer documentation for **Buckthorn** — the high-throughput, microsecond-latency compiled C/Cython web framework for Python.

Each document is completely standalone and strictly organized into:
1. **Layman's Explanation** (intuitive real-world analogy)
2. **Technical Explanation** (deep architectural details, C structs, and memory layout)
3. **Runnable Code Example** (complete, copy-pasteable Python code)
4. **Part-by-Part Explanation** (line-by-line breakdown)
5. **Comparison with Other Frameworks** (FastAPI, Starlette, Flask, Django)

---

## Complete Feature Directory

| # | Feature Guide | Focus Area |
|---|---|---|
| 01 | [Installation & Quickstart](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/01_installation_and_quickstart.md) | Hello World, `libuv` event loop, run modes |
| 02 | [C Radix-Tree Routing & Matching](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/02_c_router_and_routing.md) | Redis `rax.c` tree, path slicing, zero-GIL 404s |
| 03 | [Parameters & Single-Pass C Validation](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/03_parameters_and_c_validation.md) | `Path`, `Query`, `Header`, `Cookie`, zero-allocation numerical bounds |
| 04 | [Zero-Copy Freelist Request Object](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/04_zero_copy_request.md) | 4,096-slot freelist, lazy header/cookie lookups |
| 05 | [Schemas & Single-Pass C yyjson](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/05_schemas_and_yyjson.md) | `Schema`, `Field(...)`, C JSON deserialization & response filtering |
| 06 | [Compile-Time Flattened Dependency Injection](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/06_dependency_injection.md) | DAG compilation, `dep_slots`, sync/async generators, LIFO teardown |
| 07 | [Native OpenSSL C Security & JWT](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/07_c_openssl_security_and_jwt.md) | C HS256 JWT validation, `HTTPBearer`, constant-time API keys (`CRYPTO_memcmp`) |
| 08 | [Dual-Layer Middlewares](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/08_dual_layer_middlewares.md) | Tier-1 C Guards (CORS, Rate Limiting, Hosts) & Tier-2 Cython HTTP Hooks |
| 09 | [RFC 6455 WebSockets & SIMD C Pub/Sub](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/09_websockets_and_c_pubsub.md) | 64-bit SIMD XOR unmasking, C handshake, in-memory topic broadcasting |
| 10 | [Server-Sent Events (SSE) & LLM Streaming](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/10_server_sent_events.md) | `EventSourceResponse` for AI/LLM token streaming |
| 11 | [Zero-Copy Multipart File Uploads & File I/O](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/11_multipart_and_file_io.md) | `UploadFile`, `File(...)`, SIMD boundary scanning, `FileResponse` |
| 12 | [Post-Response Background Tasks](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/12_background_tasks.md) | `BackgroundTasks`, 1,024-slot freelist, zero added client latency |
| 13 | [Lifespan Events & Lifecycle Management](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/13_lifespan_lifecycle.md) | Modern `@asynccontextmanager` startup & graceful shutdown |
| 14 | [Modular Routing with APIRouter](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/14_modular_api_router.md) | Compile-time prefix/tag/dependency flattening into C tree |
| 15 | [Pre-Baked OpenAPI 3.1 & Interactive Docs](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/15_openapi_and_interactive_docs.md) | Swagger UI (`/docs`), Scalar (`/scalar`), cached static JSON spec |
| 16 | [AOT AST Compiler & Coroutine Elision](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/16_ast_compiler_and_coroutine_elision.md) | `buckthorn.compiler`, coroutine elision, `cdef class` lowering |
| 17 | [The Buckthorn CLI & Production Deployment](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/17_cli_and_production_deployment.md) | `buckthorn` CLI, Linux `SO_REUSEPORT` multi-worker, Dockerfile |
| 18 | [Built-in Benchmarking & CI/CD Speed Gates](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/18_speed_gate_and_benchmarking.md) | In-memory benchmark runner, `--gate` performance CI/CD quality gate |
