#!/usr/bin/env python3
"""
Buckthorn v1.0 Production Release Verification Suite
====================================================
Runs:
  1. In-place C11 + Cython extension compilation & PEP 561 artifact check
  2. Full pytest functional + OWASP/RFC security test suite
  3. All 10 Architectural RPS Speed Gates + Arena zero-leak verification
  4. Live libuv TCP fragmentation, HTTP/1.1 pipelining, and WebSocket 101 smoke test
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _banner(title: str) -> None:
    print("\n" + "=" * 98)
    print(f"🌲 {title}")
    print("=" * 98)


def stage_1_build_and_packaging_audit(skip_build: bool, use_asan: bool, portable: bool) -> None:
    _banner("STAGE 1: C11 / Cython Extension Build & PEP 561 Packaging Audit")

    env = os.environ.copy()
    if portable:
        env["BUCKTHORN_PORTABLE_WHEEL"] = "1"
    if use_asan:
        asan_flags = "-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer"
        env["CFLAGS"] = f"{env.get('CFLAGS', '')} {asan_flags}".strip()
        env["LDFLAGS"] = f"{env.get('LDFLAGS', '')} -fsanitize=address,undefined".strip()

    if not skip_build:
        cmd = [sys.executable, "setup.py", "build_ext", "--inplace"]
        print(f"[Build] Running: {' '.join(cmd)}")
        t0 = time.perf_counter()
        res = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env)
        if res.returncode != 0:
            print("❌ STAGE 1 FAILED: C11/Cython compilation failed.")
            sys.exit(1)
        print(f"[Build] Compiled C11 + Cython extension in {time.perf_counter() - t0:.2f}s")

    # Verify PEP 561 typing files exist
    py_typed = ROOT_DIR / "buckthorn" / "py.typed"
    core_pyi = ROOT_DIR / "buckthorn" / "_core.pyi"
    if not py_typed.exists():
        print(f"❌ STAGE 1 FAILED: Missing PEP 561 marker at {py_typed}")
        sys.exit(1)
    if not core_pyi.exists():
        print(f"❌ STAGE 1 FAILED: Missing type stub file at {core_pyi}")
        sys.exit(1)

    # Verify compiled C extension imports cleanly
    import buckthorn._core as _core  # noqa: F401
    print("✅ STAGE 1 PASSED: C11/Cython binary imported cleanly & PEP 561 stubs verified.")


def stage_2_run_pytest_suites(pytest_args: list[str]) -> None:
    _banner("STAGE 2: Pytest Functional, Property-Based & OWASP Security Suites")
    cmd = [sys.executable, "-m", "pytest", "tests/", "-v", *pytest_args]
    res = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if res.returncode != 0:
        print("❌ STAGE 2 FAILED: One or more pytest suites failed.")
        sys.exit(res.returncode)
    print("✅ STAGE 2 PASSED: All functional and security test suites passed.")


def _measure_op(
    op_fn: Callable[[], None],
    iterations: int,
) -> tuple[float, float, float, float]:
    """Returns (rps, avg_us, p50_us, p99_us) with zero timing overhead in the RPS loop."""
    warmup = min(500, max(50, iterations // 10))
    for _ in range(warmup):
        op_fn()

    perf_counter = time.perf_counter
    perf_counter_ns = time.perf_counter_ns

    t0 = perf_counter()
    for _ in range(iterations):
        op_fn()
    elapsed = perf_counter() - t0
    rps = iterations / elapsed

    sample_n = min(2000, iterations)
    lat_us = [0.0] * sample_n
    for i in range(sample_n):
        t_start = perf_counter_ns()
        op_fn()
        lat_us[i] = (perf_counter_ns() - t_start) * 0.001

    lat_us.sort()
    avg_us = statistics.mean(lat_us)
    p50_us = lat_us[int(sample_n * 0.50)]
    p99_us = lat_us[int(sample_n * 0.99)]
    return rps, avg_us, p50_us, p99_us


def stage_3_run_10_phase_speed_gates(iterations: int, scale: float) -> None:
    _banner(f"STAGE 3: 10-Phase Architectural RPS Speed Gates (Iterations={iterations:,}, Scale={scale:.2f}x)")

    from buckthorn import (
        BackgroundTasks,
        Buckthorn,
        Depends,
        Field,
        Header,
        Query,
        Schema,
        Security,
        UploadFile,
    )
    from buckthorn._core import WebSocket, encode_client_masked_ws_frame
    from buckthorn.compiler import compile_pyx_module, transpile_module_to_pyx
    from buckthorn.middleware import (
        CORSMiddleware,
        RateLimitMiddleware,
        SecurityHeadersMiddleware,
        TrustedHostMiddleware,
    )
    from buckthorn.security import HTTPBearer, create_jwt_token
    from buckthorn.testing import RawTestClient

    global AddressSubSchema, OrderBenchSchema

    class AddressSubSchema(Schema):
        city: str = Field(..., min_length=2, max_length=32)
        zip_code: int = Field(..., ge=10000, le=999999)

    class OrderBenchSchema(Schema):
        order_id: int = Field(..., ge=1, le=1_000_000)
        price: float = Field(..., gt=0.0, le=100_000.0)
        title: str = Field(..., min_length=2, max_length=64)
        address: AddressSubSchema

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "bench_ring.log")
        app = Buckthorn(
            title="Buckthorn Release Gate",
            version="1.0.0",
            log_file=log_path,
            access_log=False,
        )
        secret = "release-gate-hs256-secret-key-32b!"
        bearer = HTTPBearer(jwt_secret=secret)

        # Phase 1: Core Router
        @app.get("/p1/items/{item_id}")
        def p1_route(item_id: str):
            return {"id": item_id, "ok": True}

        # Phase 2: Params + URL Decoding
        @app.get("/p2/search/{user_id}")
        def p2_route(
            user_id: int,
            limit: int = Query(10, ge=1, le=100),
            x_token: str = Header(..., alias="X-Token", min_length=4, max_length=32),
        ):
            return {"uid": user_id, "limit": limit, "tok": x_token}

        # Phase 3: Schema + Nested Schema Serde
        @app.post("/p3/orders", response_model=OrderBenchSchema)
        def p3_route(order: OrderBenchSchema):
            return order

        # Phase 4: Flattened Depends() DI DAG
        def dep_config():
            return 10

        def dep_multiplier(base: int = Depends(dep_config)):
            yield base * 2

        @app.get("/p4/di")
        def p4_route(mult: int = Depends(dep_multiplier)):
            return {"val": mult}

        # Phase 5: RingLogger
        @app.get("/p5/log")
        def p5_route():
            app.logger.info("bench_evt", user_id=42, status="ok")
            return {"logged": True}

        # Phase 6: C Security JWT
        @app.get("/p6/vault")
        def p6_route(claims: dict = Security(bearer, scopes=["read:vault"])):
            return {"sub": claims["sub"]}

        # Phase 8: Multipart UploadFile
        @app.post("/p8/upload")
        def p8_upload(file: UploadFile):
            return {"name": file.filename, "size": file.size}

        # Phase 9A: BackgroundTasks
        sink = {"count": 0}

        def _bg_inc(delta: int):
            sink["count"] += delta

        @app.post("/p9/bg")
        def p9_bg(bg: BackgroundTasks):
            bg.add_task(_bg_inc, 1)
            return {"queued": True}

        # Middleware-enabled app for Phase 7
        mw_app = Buckthorn()
        mw_app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost"])
        mw_app.add_middleware(CORSMiddleware, allow_origins=["*"])
        mw_app.add_middleware(RateLimitMiddleware, max_requests=10_000_000, window_seconds=60)
        mw_app.add_middleware(SecurityHeadersMiddleware)

        @mw_app.get("/p7/mw")
        def p7_route():
            return {"mw": True}

        # Phase 10: AST-to-Cython Transpiled Module
        transpiled_src = '''
from buckthorn import Buckthorn, Schema, Field

class FastItem(Schema):
    item_id: int = Field(..., ge=1)
    price: float = Field(..., gt=0.0)
    name: str = Field(..., min_length=1)

app = Buckthorn()

@app.post("/p10/fast", response_model=FastItem)
async def compute_fast(item: FastItem, tax: float = 1.18):
    total: float = item.price * tax
    return FastItem(item_id=item.item_id, price=total, name=item.name)
'''
        pyx_code = transpile_module_to_pyx(transpiled_src)
        compiled_mod = compile_pyx_module(pyx_code, module_name="bt_release_p10", workdir=tmpdir)
        p10_app = compiled_mod.app

        client = RawTestClient(app)
        mw_client = RawTestClient(mw_app)
        p10_client = RawTestClient(p10_app)

        send_main = client.send_raw
        send_mw = mw_client.send_raw
        send_p10 = p10_client.send_raw

        jwt_tok = create_jwt_token(
            {"sub": "bench_user", "exp": int(time.time()) + 3600, "scopes": ["read:vault"]},
            secret,
        )

        req_p1 = b"GET /p1/items/42 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        req_p2 = b"GET /p2/search/99?limit=25 HTTP/1.1\r\nHost: localhost\r\nX-Token: secret123\r\n\r\n"
        body_p3 = b'{"order_id":101,"price":49.95,"title":" SSD ","address":{"city":"BLR","zip_code":560001}}'
        req_p3 = (
            f"POST /p3/orders HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body_p3)}\r\n\r\n"
        ).encode("ascii") + body_p3
        req_p4 = b"GET /p4/di HTTP/1.1\r\nHost: localhost\r\n\r\n"
        req_p5 = b"GET /p5/log HTTP/1.1\r\nHost: localhost\r\n\r\n"
        req_p6 = (
            f"GET /p6/vault HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {jwt_tok}\r\n\r\n"
        ).encode("ascii")
        req_p7 = b"GET /p7/mw HTTP/1.1\r\nHost: localhost\r\n\r\n"

        ws_session = WebSocket(path="/ws")
        masked_ws_frame = encode_client_masked_ws_frame("ping_payload_12345")

        def _p8_ws_cycle() -> None:
            ws_session.feed_raw_frame(masked_ws_frame)
            ws_session._in_frames.clear()
            ws_session._encode_server_frame("pong_payload_12345")

        req_p9_bg = b"POST /p9/bg HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
        req_p9_oa = b"GET /openapi.json HTTP/1.1\r\nHost: localhost\r\n\r\n"

        body_p10 = b'{"item_id":7,"price":100.0,"name":"GPU"}'
        req_p10 = (
            f"POST /p10/fast?tax=1.2 HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body_p10)}\r\n\r\n"
        ).encode("ascii") + body_p10

        # Sanity-check all status codes before timing
        assert send_main(req_p1).status_code == 200
        assert send_main(req_p2).status_code == 200
        assert send_main(req_p3).status_code == 200
        assert send_main(req_p4).status_code == 200
        assert send_main(req_p5).status_code == 200
        assert send_main(req_p6).status_code == 200
        assert send_mw(req_p7).status_code == 200
        assert send_main(req_p9_bg).status_code == 200
        assert send_main(req_p9_oa).status_code == 200
        assert send_p10(req_p10).status_code == 200

        gates = [
            ("Phase 1: Core C Radix & Param Router", lambda: send_main(req_p1), int(100_000 * scale), client),
            ("Phase 2: C Param & Header Validator", lambda: send_main(req_p2), int(80_000 * scale), client),
            ("Phase 3: yyjson Nested Schema Serde", lambda: send_main(req_p3), int(80_000 * scale), client),
            ("Phase 4: Flattened Depends() DI DAG", lambda: send_main(req_p4), int(75_000 * scale), client),
            ("Phase 5: Lock-Free C11 RingLogger", lambda: send_main(req_p5), int(80_000 * scale), client),
            ("Phase 6: C HS256 JWT + Scopes Auth", lambda: send_main(req_p6), int(75_000 * scale), client),
            ("Phase 7: C Tier-1 Middleware Chain", lambda: send_mw(req_p7), int(80_000 * scale), mw_client),
            ("Phase 8: C RFC 6455 WebSocket Frame", _p8_ws_cycle, int(60_000 * scale), client),
            ("Phase 9A: BackgroundTasks Freelist", lambda: send_main(req_p9_bg), int(75_000 * scale), client),
            ("Phase 9B: Pre-Cached /openapi.json", lambda: send_main(req_p9_oa), int(150_000 * scale), client),
            ("Phase 10: Compiled .so Cython Route", lambda: send_p10(req_p10), int(80_000 * scale), p10_client),
        ]

        print(f"| {'Phase & Subsystem':<38} | {'Throughput':>16} | {'Gate Floor':>14} | {'p50 Lat':>9} | {'p99 Lat':>9} | {'Arena':>5} | {'Status':>6} |")
        print("|" + "-" * 40 + "|" + "-" * 18 + "|" + "-" * 16 + "|" + "-" * 11 + "|" + "-" * 11 + "|" + "-" * 7 + "|" + "-" * 8 + "|")

        failed = False
        for label, fn, min_rps, active_client in gates:
            rps, _, p50_us, p99_us = _measure_op(fn, iterations)
            arena_ofs = active_client.arena_offset
            passed = (rps >= min_rps) and (arena_ofs == 0)
            if not passed:
                failed = True
            status_str = "PASS" if passed else "FAIL"
            print(
                f"| {label:<38} | {rps:>10,.0f} req/s | {min_rps:>8,d} req/s | "
                f"{p50_us:>7.1f} µs | {p99_us:>7.1f} µs | {arena_ofs:>4d}B | {status_str:>6} |"
            )

        app._shutdown()
        mw_app._shutdown()
        p10_app._shutdown()

        if failed:
            print("\n❌ STAGE 3 FAILED: One or more speed gates or arena invariants did not pass.")
            sys.exit(1)
        print("\n✅ STAGE 3 PASSED: All 10 Architectural Speed Gates & 0-byte Arena invariants passed.")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def stage_4_live_libuv_tcp_and_ws_smoke() -> None:
    _banner("STAGE 4: Live libuv TCP Fragmentation, HTTP/1.1 Pipelining & WebSocket 101 Gate")
    from buckthorn import Buckthorn, Field, Query, Request, Schema
    from buckthorn._core import encode_client_masked_ws_frame

    global EchoPayload

    class EchoPayload(Schema):
        user: str = Field(..., min_length=2, max_length=32)
        count: int = Field(..., ge=1, le=1000)

    app = Buckthorn()

    @app.get("/ping")
    def ping(x: int = Query(1)):
        return {"pong": x}

    @app.post("/async-echo")
    async def async_echo(req: Request, body: EchoPayload):
        await asyncio.sleep(0.003)
        return {"user": body.user, "count": body.count, "hdr": req.get_header("X-Id")}

    @app.websocket("/ws/{channel}")
    async def ws_endpoint(ws, channel: str):
        await ws.accept()
        msg = await ws.receive_text()
        await ws.send_text(f"{channel}:{msg}")
        await ws.close()

    port = _find_free_port()
    pid = os.fork()
    if pid == 0:
        app.run(host="127.0.0.1", port=port)
        os._exit(0)

    try:
        ready = False
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    ready = True
                    break
            except OSError:
                time.sleep(0.02)
        if not ready:
            raise RuntimeError("Live libuv worker did not bind in time")

        # 1. Mixed Sync + Suspended Async HTTP/1.1 Pipelining in 1 TCP packet
        b_json = b'{"user":"neo","count":99}'
        pipelined = (
            b"GET /ping?x=11 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            + (
                f"POST /async-echo HTTP/1.1\r\nHost: localhost\r\n"
                f"Content-Type: application/json\r\nX-Id: p2\r\n"
                f"Content-Length: {len(b_json)}\r\n\r\n"
            ).encode("ascii")
            + b_json
            + b"GET /ping?x=22 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(pipelined)
            buf = b""
            while buf.count(b"HTTP/1.1 200 OK") < 3:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            assert buf.count(b"HTTP/1.1 200 OK") == 3
            assert b'"pong":11' in buf
            assert b'"user":"neo"' in buf and b'"hdr":"p2"' in buf
            assert b'"pong":22' in buf

        # 2. Live TCP WebSocket 101 Upgrade + RFC 6455 Masked Frame Echo
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(
                b"GET /ws/prod HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
            )
            hs = sock.recv(1024)
            assert b"HTTP/1.1 101 Switching Protocols\r\n" in hs
            sock.sendall(encode_client_masked_ws_frame("live_ok"))
            ws_reply = sock.recv(1024)
            assert b"prod:live_ok" in ws_reply

        print("✅ STAGE 4 PASSED: Live libuv TCP pipelining, async suspension & WebSocket 101 verified.")
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Buckthorn v1.0 Release Verification Runner")
    parser.add_argument("--skip-build", action="store_true", help="Skip C11/Cython build_ext step")
    parser.add_argument("--asan", action="store_true", help="Compile with AddressSanitizer + UBSan")
    parser.add_argument("--portable", action="store_true", help="Compile with BUCKTHORN_PORTABLE_WHEEL=1")
    parser.add_argument("--iterations", type=int, default=20_000, help="Benchmark iterations per phase (default: 20,000)")
    parser.add_argument("--scale", type=float, default=1.0, help="RPS gate multiplier for slower CI runners (default: 1.0)")
    args, extra_pytest = parser.parse_known_args()

    t_start = time.perf_counter()
    stage_1_build_and_packaging_audit(args.skip_build, args.asan, args.portable)
    stage_2_run_pytest_suites(extra_pytest)
    stage_3_run_10_phase_speed_gates(args.iterations, args.scale)
    stage_4_live_libuv_tcp_and_ws_smoke()

    total_s = time.perf_counter() - t_start
    _banner(f"🎉 BUCKTHORN v1.0 RELEASE VERIFICATION COMPLETE ({total_s:.2f}s) — ALL GATES GREEN")


if __name__ == "__main__":
    main()
