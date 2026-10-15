"""
18_production_and_cli.py - AOT Compilation, Multi-Worker Server & CLI
======================================================================

Buckthorn provides a complete toolchain from local developer iteration to
ahead-of-time (AOT) binary compilation and multi-core production deployment.

---

### 🛠️ CLI Commands Overview:

1. **Development Server (`buckthorn dev`):**
   ```bash
   buckthorn dev main:app --host 127.0.0.1 --port 8000 --reload
   ```
   Runs your application in Python dev mode with live code reloading.

2. **Ahead-of-Time Binary Compilation (`buckthorn build`):**
   ```bash
   buckthorn build main.py --prod --out-dir dist_buckthorn
   ```
   - Transpiles Python AST into optimized Cython (`.pyx`).
   - Lowers all `class Order(Schema):` into `@cython.freelist(4096) cdef class Order`
     with unboxed C variables (`cdef public long long`, `cdef public double`).
   - Performs **Static Coroutine Elision**: non-awaiting `async def` handlers are
     turned into direct C functions (`_sync_fast_<name>`).
   - Invokes GCC/Clang with `-O3 -flto` to emit a high-performance native `.so` binary.

3. **Multi-Worker Production Server (`buckthorn run`):**
   ```bash
   buckthorn run main:app --workers 4 --host 0.0.0.0 --port 8000
   ```
   Forks N worker processes where each child worker binds directly to the same
   TCP port using the Linux kernel's `SO_REUSEPORT` feature. Incoming connections
   are balanced at the kernel level across all CPU cores with zero master-process
   bottlenecks.

4. **Built-in RPS Benchmark & Speed Gate (`buckthorn bench`):**
   ```bash
   buckthorn bench main:app --iterations 10000 --gate 80000
   ```
   Directly benchmarks application routes in-memory and enforces minimum RPS speed gates.

---

This example script demonstrates:
- How the AST transpiler converts Python source into Cython.
- Verifying coroutine elision and unboxed C schema types programmatically.
- How to structure your application entrypoint for production.

To run this script directly to verify AOT transpilation:
    python docs/examples/18_production_and_cli.py
"""

import textwrap
from buckthorn import Buckthorn, Schema, Field
from buckthorn.compiler import transpile_module_to_pyx

# Standard application entrypoint
app = Buckthorn(
    title="Production Ready Service",
    version="1.0.0",
)


class MetricsPayload(Schema):
    host_id: int = Field(ge=1)
    cpu_usage: float = Field(ge=0.0, le=100.0)
    service_name: str = "web_gateway"


@app.get("/status")
def get_service_status():
    """Ultra-fast Tier-0 health status."""
    return {"status": "healthy", "mode": "production"}


@app.post("/metrics")
async def record_metrics(data: MetricsPayload):
    """
    In Dev mode: Evaluated via Python yyjson registry.
    In Prod mode (`buckthorn build --prod`):
      1. Lowered to `@cython.freelist(4096) cdef class MetricsPayload`.
      2. Coroutine elided to synchronous `def record_metrics`.
      3. Compiled to native machine code (`.so`).
    """
    return {
        "recorded": True,
        "host": data.host_id,
        "load": data.cpu_usage,
    }


# ==============================================================================
# Self-Verification Test Runner: Demonstrating the AOT Transpiler
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 18_production_and_cli.py verification...")
    client = RawTestClient(app)

    # 1. Test routes in dev mode
    res_status = client.get("/status")
    assert res_status.status_code == 200
    assert res_status.json()["status"] == "healthy"
    print(f"  ✓ Dev Mode Route -> 200 {res_status.json()}")

    # 2. Test AOT AST Transpilation
    sample_code = textwrap.dedent("""
        from buckthorn import Buckthorn, Schema, Field

        app = Buckthorn()

        class Order(Schema):
            order_id: int = Field(ge=1)
            total: float = Field(gt=0.0)
            customer: str = "guest"

        @app.post("/orders")
        async def submit_order(order: Order):
            discount: float = order.total * 0.1
            return {"order_id": order.order_id, "discount": discount}
    """)

    print("\n  ⚙️ Transpiling Python AST to Cython (.pyx)...")
    pyx_output = transpile_module_to_pyx(sample_code)

    # Verify Cython optimization directives
    assert "# cython: language_level=3, boundscheck=False" in pyx_output
    assert "binding=True" in pyx_output

    # Verify Schema lowering to Cython freelist cdef class with unboxed C types
    assert "@cython.freelist(4096)" in pyx_output
    assert "cdef class Order:" in pyx_output
    assert "cdef public long long order_id" in pyx_output
    assert "cdef public double total" in pyx_output
    assert "cdef public str customer" in pyx_output
    print("  ✓ Schema lowered to Cython '@cython.freelist(4096) cdef class' with unboxed C types")

    # Verify Coroutine Elision: non-awaiting `async def` became `def` + `_sync_fast_submit_order`
    assert "def submit_order(order: Order):" in pyx_output
    assert "cpdef object _sync_fast_submit_order(" in pyx_output
    assert "@cython.locals(" in pyx_output
    print("  ✓ Coroutine Elision verified: non-awaiting 'async def' converted to direct C call")

    print("\n✅ All 18_production_and_cli.py verifications completed successfully!")
    print("👉 To run with live reload: buckthorn dev docs/examples/18_production_and_cli.py:app --reload")
    print("👉 To compile to .so:       buckthorn build docs/examples/18_production_and_cli.py --prod")
    print("👉 To run 4 Linux workers:  buckthorn run docs/examples/18_production_and_cli.py:app --workers 4")
