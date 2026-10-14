# benchmarks/run_suite.py
"""
Buckthorn Automated In-Memory & Microsecond Benchmark Suite
===========================================================
Executes high-iteration throughput and latency benchmarks against Buckthorn apps
(both standard and compiled Cython production binaries) without requiring external tools.
"""

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).parent.parent.resolve()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from buckthorn.testing import RawTestClient
from buckthorn.compiler import transpile_module_to_pyx, compile_pyx_module


def run_all_endpoints(app, token: str, iterations: int, silent: bool = False) -> list[dict]:
    client = RawTestClient(app)

    # Pre-encode requests for maximum client speed
    req_health = (
        b"GET /health HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Accept: application/json\r\n\r\n"
    )

    req_inventory = (
        b"GET /stores/101/inventory/SKU-9942?currency=USD HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Tenant-ID: tenant-corp-01\r\n"
        b"Accept: application/json\r\n\r\n"
    )

    order_json = (
        b'{"sku":"SKU-9942","customer_email":"buyer@example.com",'
        b'"unit_price":49.99,"quantity":2,"shipping_method":"express",'
        b'"customer_note":"Urgent warehouse dispatch","tags":["retail","priority"]}'
    )
    req_checkout = (
        b"POST /stores/101/checkout HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"X-Tenant-ID: tenant-corp-01\r\n"
        b"Authorization: Bearer " + token.encode("ascii") + b"\r\n"
        b"Content-Length: " + str(len(order_json)).encode("ascii") + b"\r\n\r\n"
        + order_json
    )

    req_analytics = (
        b"GET /stores/101/analytics?period=today HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-API-Key: live_bench_api_key_999\r\n"
        b"Accept: application/json\r\n\r\n"
    )

    item_json = b'{"name":"Mechanical Keyboard","price":149.99,"quantity":2,"in_stock":true}'
    req_items = (
        b"POST /items HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(item_json)).encode("ascii") + b"\r\n\r\n"
        + item_json
    )

    if not silent:
        print(f"| {'Endpoint & Scenario':<40} | {'Throughput':>16} | {'Avg Lat':>10} | {'p50 Lat':>10} | {'p99 Lat':>10} |")
        print("|" + "-" * 42 + "|" + "-" * 18 + "|" + "-" * 12 + "|" + "-" * 12 + "|" + "-" * 12 + "|")

    results = []
    scenarios = [
        ("1. GET /health (Telemetry Probe)", req_health),
        ("2. GET /inventory (Path+Query+DI)", req_inventory),
        ("3. POST /items (yyjson Schema Valid)", req_items),
        ("4. GET /analytics (OpenSSL API-Key)", req_analytics),
        ("5. POST /checkout (JWT+yyjson+Filter)", req_checkout),
    ]

    for name, req in scenarios:
        res = run_benchmark(name, client, req, iterations, silent=silent)
        results.append(res)

    return results


def run_benchmark(name: str, client: RawTestClient, raw_request: bytes, iterations: int = 50_000, silent: bool = False) -> dict:
    # Warmup
    for _ in range(500):
        client.send_raw(raw_request)

    latencies_us: list[float] = []
    t0 = time.perf_counter()

    for _ in range(iterations):
        req_start = time.perf_counter_ns()
        resp = client.send_raw(raw_request)
        req_end = time.perf_counter_ns()
        if resp.status_code >= 400:
            raise RuntimeError(f"Request failed with status {resp.status_code}: {resp.body.decode()}")
        latencies_us.append((req_end - req_start) / 1000.0)

    total_time = time.perf_counter() - t0
    rps = iterations / total_time

    latencies_us.sort()
    p50 = latencies_us[int(iterations * 0.50)]
    p90 = latencies_us[int(iterations * 0.90)]
    p99 = latencies_us[int(iterations * 0.99)]
    avg = statistics.mean(latencies_us)

    if not silent:
        print(f"| {name:<40} | {rps:>10,.0f} req/s | {avg:>7.1f} µs | {p50:>7.1f} µs | {p99:>7.1f} µs |")
    return {"name": name, "rps": rps, "avg": avg, "p50": p50, "p99": p99}


def main():
    parser = argparse.ArgumentParser(description="Buckthorn Benchmark Runner")
    parser.add_argument("--mode", choices=["sync", "async", "compiled", "compare"], default="compare",
                        help="Benchmark mode: sync app, async app, compiled binary, or side-by-side compare")
    parser.add_argument("--iterations", type=int, default=20_000, help="Number of iterations per test")
    args = parser.parse_args()

    bench_dir = Path(__file__).parent.resolve()

    if args.mode == "compare":
        print("=" * 102)
        print(f"🌲 Buckthorn Performance Benchmark: Sync vs. Native Async Comparison")
        print(f"⚡ Iterations per endpoint: {args.iterations:,}")
        print("=" * 102)

        import benchmarks.app as sync_mod
        import benchmarks.async_app as async_mod

        print("\n[1/2] Running Synchronous Benchmark Suite...")
        sync_results = run_all_endpoints(sync_mod.app, sync_mod.BENCH_TOKEN, args.iterations, silent=True)

        print("[2/2] Running Native Asynchronous Benchmark Suite...")
        async_results = run_all_endpoints(async_mod.app, async_mod.BENCH_TOKEN, args.iterations, silent=True)

        print("\n" + "=" * 102)
        print(f"| {'Endpoint & Scenario':<38} | {'Sync Throughput':>17} | {'Async Throughput':>18} | {'Retention':>11} | {'Delta':>8} |")
        print("|" + "-" * 40 + "|" + "-" * 19 + "|" + "-" * 20 + "|" + "-" * 13 + "|" + "-" * 10 + "|")

        for s, a in zip(sync_results, async_results):
            retention = (a["rps"] / s["rps"]) * 100.0 if s["rps"] > 0 else 0.0
            delta_lat = a["avg"] - s["avg"]
            print(f"| {s['name']:<38} | {s['rps']:>11,.0f} req/s | {a['rps']:>12,.0f} req/s | {retention:>10.1f}% | {delta_lat:>+6.1f} µs |")

        print("=" * 102)
        print("💡 Traditional ASGI servers lose 60%–75% throughput on 'async def' endpoints.")
        print("   Buckthorn's 3-Tier Eager engine maintains high single-core throughput with zero Task allocation.")
        print("=" * 102)
        return

    print("=" * 88)
    print(f"🌲 Buckthorn High-Performance Benchmark Suite [Mode: {args.mode.upper()}]")
    print(f"⚡ Iterations per endpoint: {args.iterations:,}")
    print("=" * 88)

    if args.mode == "compiled":
        src = (bench_dir / "app.py").read_text(encoding="utf-8")
        pyx = transpile_module_to_pyx(src)
        out_dir = bench_dir / ".compiled_bench"
        mod = compile_pyx_module(pyx, module_name="compiled_bench_app", workdir=out_dir)
        app = mod.app
        token = mod.BENCH_TOKEN
    elif args.mode == "async":
        import benchmarks.async_app as bapp
        app = bapp.app
        token = bapp.BENCH_TOKEN
    else:
        import benchmarks.app as bapp
        app = bapp.app
        token = bapp.BENCH_TOKEN

    run_all_endpoints(app, token, args.iterations, silent=False)
    print("=" * 88)
    print(" Benchmark completed successfully.")


if __name__ == "__main__":
    main()
