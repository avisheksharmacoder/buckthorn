# buckthorn/cli.py
import argparse
import importlib
import os
import signal
import sys
from pathlib import Path
from typing import Any, Sequence
from buckthorn.compiler import transpile_module_to_pyx, compile_pyx_module


def parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="buckthorn",
        description="Buckthorn High-Performance Compiled C/Cython Web Framework CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. `buckthorn dev`
    dev_p = sub.add_parser("dev", help="Run Buckthorn app in development mode")
    dev_p.add_argument("target", help="Module:app target (e.g. main:app)")
    dev_p.add_argument("--host", default="127.0.0.1")
    dev_p.add_argument("--port", type=int, default=8000)
    dev_p.add_argument("--reload", action="store_true")

    # 2. `buckthorn build`
    build_p = sub.add_parser("build", help="Transpile Python app to Cython and compile to .so")
    build_p.add_argument("source", help="Path to Python source file (e.g. main.py)")
    build_p.add_argument("--prod", action="store_true", help="Enable production Cython optimizations")
    build_p.add_argument("--out-dir", default="dist_buckthorn", help="Output directory for .pyx and .so")

    # 3. `buckthorn run`
    run_p = sub.add_parser("run", help="Run Buckthorn app in production mode with SO_REUSEPORT workers")
    run_p.add_argument("target", help="Module:app target (e.g. main:app)")
    run_p.add_argument("--host", default="0.0.0.0")
    run_p.add_argument("--port", type=int, default=8000)
    run_p.add_argument("--workers", type=int, default=1)

    # 4. `buckthorn bench`
    bench_p = sub.add_parser("bench", help="Benchmark user app endpoints and evaluate speed gates")
    bench_p.add_argument("target", help="Module:app target (e.g. main:app)")
    bench_p.add_argument("--iterations", type=int, default=10_000, help="Number of benchmark iterations per endpoint (default: 10,000)")
    bench_p.add_argument("--gate", type=int, default=0, help="Minimum RPS speed gate requirement (e.g. 50000)")
    bench_p.add_argument("--path", default=None, help="Target specific route path (e.g. /health)")
    bench_p.add_argument("--method", default="GET", help="HTTP method for --path (default: GET)")
    bench_p.add_argument("--body", default=None, help="JSON body for POST/PUT requests")

    return parser.parse_args(argv)


def _load_target_app(target: str) -> Any:
    if ":" not in target:
        raise ValueError("Target must be formatted as 'module:app'")
    target_path, app_attr = target.split(":", 1)
    path_obj = Path(target_path)
    if path_obj.is_file() or target_path.endswith(".py"):
        resolved_path = path_obj.resolve()
        sys.path.insert(0, str(resolved_path.parent))
        mod_name = resolved_path.stem
        spec = importlib.util.spec_from_file_location(mod_name, str(resolved_path))
        if spec is not None and spec.loader is not None:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, app_attr)

    mod_name = target_path
    if mod_name.endswith(".py"):
        mod_name = mod_name[:-3]
    mod_name = mod_name.replace("/", ".").replace("\\", ".").strip(".")
    sys.path.insert(0, os.getcwd())
    mod = importlib.import_module(mod_name)
    return getattr(mod, app_attr)



def build_command(args: argparse.Namespace) -> Path:
    src_path = Path(args.source).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source file not found: {src_path}")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    mod_name = src_path.stem
    pyx_code = transpile_module_to_pyx(src_path.read_text(encoding="utf-8"))
    compile_pyx_module(pyx_code, module_name=mod_name, workdir=out_dir)

    so_files = list(out_dir.glob(f"{mod_name}*.so"))
    if not so_files:
        raise RuntimeError("Compilation finished but no .so binary was found")
    return so_files[0]


def run_command(args: argparse.Namespace) -> None:
    workers = max(1, int(getattr(args, "workers", 1)))
    if workers == 1:
        app = _load_target_app(args.target)
        app.run(host=args.host, port=args.port)
        return

    pids: list[int] = []
    for _ in range(workers):
        pid = os.fork()
        if pid == 0:
            # Child worker binds to the same port via Linux kernel SO_REUSEPORT
            app = _load_target_app(args.target)
            app.run(host=args.host, port=args.port)
            os._exit(0)
        else:
            pids.append(pid)

    def _sig_handler(signum: int, frame: Any) -> None:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)
    for pid in pids:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def bench_command(args: argparse.Namespace) -> None:
    import inspect
    import statistics
    import time
    from buckthorn.testing import RawTestClient

    app = _load_target_app(args.target)
    client = RawTestClient(app)
    iterations = max(100, int(args.iterations))
    speed_gate = int(getattr(args, "gate", 0))

    targets_to_run: list[tuple[str, str, bytes, Any]] = []

    if args.path:
        method = args.method.upper()
        path = args.path
        body_bytes = args.body.encode("utf-8") if args.body else b""
        headers = b"Content-Type: application/json\r\n" if body_bytes else b""
        req = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"{headers.decode('ascii')}"
            f"Content-Length: {len(body_bytes)}\r\n\r\n"
        ).encode("latin1") + body_bytes
        targets_to_run.append((method, path, req, None))
    else:
        # Discover routes from app
        routes_list = getattr(app, "routes", [])
        for r in routes_list:
            method = r.get("method", "GET")
            path = r.get("path", "/")
            endpoint = r.get("endpoint")

            candidate_path = path
            if "{" in candidate_path:
                import re
                candidate_path = re.sub(r"\{[a-zA-Z0-9_]+\}", "1", candidate_path)

            body_bytes = b"{}" if method in ("POST", "PUT", "PATCH") else b""
            headers = b"Content-Type: application/json\r\n" if body_bytes else b""
            req = (
                f"{method} {candidate_path} HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"{headers.decode('ascii')}"
                f"Content-Length: {len(body_bytes)}\r\n\r\n"
            ).encode("latin1") + body_bytes

            # Quick probe to see if route runs without complex authentication/data requirements
            probe_res = client.send_raw(req)
            if probe_res.status_code < 400:
                targets_to_run.append((method, candidate_path, req, endpoint))

        # Check documentation / built-in endpoints
        for extra in ["/health", "/docs", "/openapi.json"]:
            if not any(t[1] == extra for t in targets_to_run):
                req = f"GET {extra} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("latin1")
                probe = client.send_raw(req)
                if probe.status_code == 200:
                    targets_to_run.append(("GET", extra, req, None))

    if not targets_to_run:
        print("[Buckthorn] No benchmarkable endpoints discovered. Use --path (and optionally --method/--body).")
        return

    print("=" * 96)
    print("🌲 Buckthorn Performance Benchmark & Speed Gate Evaluator")
    print(f"🎯 Target App : {args.target}")
    print(f"⚡ Iterations : {iterations:,} reqs per endpoint")
    if speed_gate > 0:
        print(f"🛡️  Speed Gate : >= {speed_gate:,} RPS")
    print("=" * 96)

    gate_failed = False
    gate_hdr = " Gate |" if speed_gate > 0 else ""
    gate_sep = "------|" if speed_gate > 0 else ""
    print(f"| {'Method & Path':<35} | {'Handler Type':<12} | {'Throughput':>15} | {'Avg Lat':>9} | {'p50 Lat':>9} | {'p99 Lat':>9} |{gate_hdr}")
    print("|" + "-" * 37 + "|" + "-" * 14 + "|" + "-" * 17 + "|" + "-" * 11 + "|" + "-" * 11 + "|" + "-" * 11 + "|" + gate_sep)

    for method, path, req, endpoint in targets_to_run:
        send_raw = client.send_raw

        # 1. Warmup pass
        warmup_n = min(300, max(10, iterations // 10))
        for _ in range(warmup_n):
            send_raw(req)

        # 2. De-biased Raw Throughput Pass (Zero measurement instrumentation in tight loop)
        t0 = time.perf_counter()
        for _ in range(iterations):
            send_raw(req)
        total_time = time.perf_counter() - t0
        rps = iterations / total_time

        # 3. Latency Distribution Sampling Pass (Fixed sample size to avoid degrading throughput)
        sample_n = min(iterations, 2000)
        latencies_us: list[float] = []
        for _ in range(sample_n):
            req_start = time.perf_counter_ns()
            send_raw(req)
            req_end = time.perf_counter_ns()
            latencies_us.append((req_end - req_start) / 1000.0)

        latencies_us.sort()
        avg_us = statistics.mean(latencies_us)
        p50_us = latencies_us[int(sample_n * 0.50)]
        p99_us = latencies_us[min(int(sample_n * 0.99), sample_n - 1)]

        htype = "C / STATIC"
        if endpoint is not None:
            if inspect.iscoroutinefunction(endpoint):
                htype = "ASYNC"
            else:
                htype = "SYNC/ELIDED"

        gate_status = ""
        if speed_gate > 0:
            if rps >= speed_gate:
                gate_status = " PASS |"
            else:
                gate_status = " FAIL |"
                gate_failed = True

        name_col = f"{method} {path}"
        print(f"| {name_col:<35} | {htype:<12} | {rps:>10,.0f} req/s | {avg_us:>7.1f} µs | {p50_us:>7.1f} µs | {p99_us:>7.1f} µs |{gate_status}")

    print("=" * 96)
    if speed_gate > 0:
        if gate_failed:
            print(f"❌ SPEED GATE FAILED: One or more endpoints did not meet the {speed_gate:,} RPS requirement.")
            sys.exit(1)
        else:
            print(f"✅ SPEED GATE PASSED: All benchmarked endpoints exceeded {speed_gate:,} RPS.")


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_cli_args(argv)
    if args.command == "build":
        so_path = build_command(args)
        print(f"[Buckthorn] Compiled production extension: {so_path}")
    elif args.command in ("dev", "run"):
        run_command(args)
    elif args.command == "bench":
        bench_command(args)


if __name__ == "__main__":
    main()
