# tests/phase10/test_ast_transpiler_and_cli.py
import inspect
import textwrap
import time
import pytest
from buckthorn.compiler import transpile_module_to_pyx, compile_pyx_module
from buckthorn.cli import build_command, parse_cli_args
from buckthorn.testing import RawTestClient


def test_ast_to_cython_transpiler_lowers_schema_and_elides_coroutines():
    user_code = textwrap.dedent("""
        from buckthorn import Buckthorn, Schema, Field

        app = Buckthorn(title="Compiled App")

        class CalcInput(Schema):
            a: int = Field(ge=1)
            b: float = Field(gt=0.0)
            label: str = "calc"

        @app.post("/compute/{multiplier}")
        async def compute(multiplier: int, payload: CalcInput):
            total: float = (payload.a * payload.b) * multiplier
            return {"label": payload.label, "total": total}
    """)

    pyx_code = transpile_module_to_pyx(user_code)

    # 1. Verify Cython optimization directives are emitted at the top
    assert "# cython: language_level=3, boundscheck=False" in pyx_code
    assert "binding=True" in pyx_code

    # 2. Verify Schema subclass was lowered to a Cython freelist `cdef class` with unboxed C types
    assert "@cython.freelist(4096)" in pyx_code
    assert "cdef class CalcInput:" in pyx_code
    assert "cdef public long long a" in pyx_code
    assert "cdef public double b" in pyx_code
    assert "cdef public str label" in pyx_code

    # 3. Verify Coroutine Elision: non-awaiting `async def compute` became sync `def compute`
    # and local typed variables (`total: float`) were lowered to `@cython.locals(total=cython.double)`
    assert "@cython.locals(multiplier=cython.longlong, total=cython.double)" in pyx_code
    assert "def compute(multiplier: int, payload: CalcInput):" in pyx_code
    assert "cpdef object _sync_fast_compute(" in pyx_code


def test_transpiler_preserves_async_def_when_await_is_present():
    async_code = textwrap.dedent("""
        import asyncio
        from buckthorn import Buckthorn

        app = Buckthorn()

        @app.get("/io")
        async def io_bound():
            await asyncio.sleep(0.001)
            return {"io": True}
    """)

    pyx_code = transpile_module_to_pyx(async_code)
    # Because `await` exists in the AST body, `async def io_bound` MUST NOT be elided
    assert "async def io_bound():" in pyx_code


def test_compile_pyx_module_end_to_end_binary_execution_and_speed_gate(tmp_path):
    user_code = textwrap.dedent("""
        from buckthorn import Buckthorn, Schema, Field, Path

        app = Buckthorn(title="Prod Binary API", version="1.0.0")

        class TradeOrder(Schema):
            symbol: str = Field(min_length=2, max_length=8)
            qty: int = Field(ge=1, le=10000)
            price: float = Field(gt=0.0)

        class TradeConfirmation(Schema):
            symbol: str
            notional: float
            fee: float

        @app.post("/trade/{account_id}", response_model=TradeConfirmation)
        async def execute_trade(account_id: int = Path(ge=1), order: TradeOrder = None):
            notional: float = order.qty * order.price
            fee: float = notional * 0.001
            return TradeConfirmation(symbol=order.symbol, notional=notional, fee=fee)
    """)

    pyx_code = transpile_module_to_pyx(user_code)
    compiled_mod = compile_pyx_module(pyx_code, module_name="prod_trade_app", workdir=tmp_path)

    # 1. Verify TradeOrder is a compiled C extension type with no Python __dict__
    assert not hasattr(compiled_mod.TradeOrder(symbol="AAPL", qty=10, price=150.0), "__dict__")

    # 2. Verify `execute_trade` had its coroutine elided in the compiled binary
    assert not inspect.iscoroutinefunction(compiled_mod.execute_trade)

    # 3. Run full HTTP + yyjson + Cython cdef struct validation against the compiled `.so` app
    client = RawTestClient(compiled_mod.app)
    res = client.post(
        "/trade/42",
        json={"symbol": "NVDA", "qty": 10, "price": 120.5},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "NVDA"
    assert pytest.approx(data["notional"], 0.001) == 1205.0
    assert pytest.approx(data["fee"], 0.0001) == 1.205

    # 4. Verify C-level validation still rejects invalid TradeOrder payloads with 422
    res_422 = client.post("/trade/42", json={"symbol": "N", "qty": 10, "price": -5.0})
    assert res_422.status_code == 422

    # 5. Production Binary Speed Gate (>80,000 RPS on full validated JSON trade endpoint)
    raw_req = (
        b"POST /trade/42 HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 43\r\n\r\n"
        b'{"symbol":"NVDA","qty":10,"price":120.5000}'
    )
    for _ in range(500):
        client.send_raw(raw_req)

    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        r = client.send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert r.status_code == 200
    assert rps > 80_000, f"Expected >80,000 RPS on compiled .so app, got {rps:,.0f} RPS"


def test_buckthorn_cli_build_command_creates_compiled_extension(tmp_path):
    src_file = tmp_path / "sample_service.py"
    src_file.write_text(
        textwrap.dedent("""
            from buckthorn import Buckthorn
            app = Buckthorn()

            @app.get("/status")
            async def status():
                x: int = 200
                return {"code": x}
        """),
        encoding="utf-8",
    )

    out_dir = tmp_path / "build_out"
    args = parse_cli_args(["build", str(src_file), "--prod", "--out-dir", str(out_dir)])
    so_path = build_command(args)

    assert so_path.exists()
    assert so_path.suffix == ".so"
    assert (out_dir / "sample_service.pyx").exists()


def test_cli_reloader_snapshot_and_diff(tmp_path):
    from buckthorn.cli import _snapshot_project_mtimes, _diff_snapshots

    code_file = tmp_path / "app.py"
    code_file.write_text("app = 1", encoding="utf-8")

    snap1 = _snapshot_project_mtimes(tmp_path)
    assert str(code_file) in snap1

    # No change
    assert _diff_snapshots(snap1, snap1) is None

    # Modify file
    time.sleep(0.01)
    code_file.write_text("app = 2", encoding="utf-8")
    snap2 = _snapshot_project_mtimes(tmp_path)
    changed = _diff_snapshots(snap1, snap2)
    assert changed is not None
    assert "app.py" in changed

