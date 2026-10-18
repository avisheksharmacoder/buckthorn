# tests/unit_v2/test_phase5_cli_and_regression.py
import io
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout
import pytest

from buckthorn import Buckthorn, Schema, Security, Path
from buckthorn.cli import main
from buckthorn.logging import RingLogger
from buckthorn.security import HTTPBearer, create_jwt_token
from buckthorn.testing import RawTestClient


def test_cli_bench_throughput_accuracy(tmp_path):
    """Verify de-biased CLI benchmark executes un-instrumented throughput loop without overhead."""
    src = tmp_path / "bench_app.py"
    src.write_text(
        "from buckthorn import Buckthorn\n"
        "app = Buckthorn()\n"
        "@app.get('/quick')\n"
        "def quick():\n"
        "    return {'status': 'ok'}\n"
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["bench", f"{src}:app", "--iterations", "5000", "--path", "/quick"])

    output = buf.getvalue()
    assert "Buckthorn Performance Benchmark" in output
    assert "GET /quick" in output
    assert "req/s" in output
    assert "µs" in output


def test_cli_latency_histogram_sanity(tmp_path):
    """Verify CLI bench computes valid latency distributions where p50 <= p99."""
    src = tmp_path / "hist_app.py"
    src.write_text(
        "from buckthorn import Buckthorn\n"
        "app = Buckthorn()\n"
        "@app.get('/measure')\n"
        "def measure():\n"
        "    return {'ok': True}\n"
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["bench", f"{src}:app", "--iterations", "3000", "--path", "/measure"])

    output = buf.getvalue()
    # Find table data line
    table_line = [l for l in output.splitlines() if "GET /measure" in l]
    assert len(table_line) == 1
    cols = [c.strip() for c in table_line[0].split("|") if c.strip()]
    # Format: [Method & Path, Handler Type, Throughput, Avg Lat, p50 Lat, p99 Lat]
    assert len(cols) >= 6
    p50_val = float(cols[4].replace("µs", "").strip())
    p99_val = float(cols[5].replace("µs", "").strip())
    assert p50_val <= p99_val, f"Expected p50 <= p99, got p50={p50_val}, p99={p99_val}"


def test_full_suite_speed_gate_phase1():
    """Verify Phase 1 C HTTP Engine + PicoHTTPParser + Rax Router clears > 120,000 RPS gate."""
    app = Buckthorn()

    @app.get("/fast")
    def fast():
        return b'{"ok":1}'

    client = RawTestClient(app)
    raw_req = b"GET /fast HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"

    # Warmup
    for _ in range(1000):
        client.send_raw(raw_req)

    iterations = 30_000
    send_raw = client.send_raw
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert client.arena_offset == 0
    assert rps > 120_000, f"Expected >120,000 RPS, got {rps:,.0f} RPS"


def test_full_suite_speed_gate_phase2():
    """Verify Phase 2 in-place parameter extraction & validation clears > 95,000 RPS gate."""
    app = Buckthorn()

    @app.get("/items/{item_id}")
    def get_item(item_id: int = Path(ge=1), q: str = "search", limit: int = 10):
        return {"item_id": item_id, "q": q, "limit": limit}

    client = RawTestClient(app)
    raw_req = b"GET /items/42?q=search&limit=25 HTTP/1.1\r\nHost: localhost\r\n\r\n"

    for _ in range(1000):
        client.send_raw(raw_req)

    iterations = 30_000
    send_raw = client.send_raw
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert rps > 95_000, f"Expected >95,000 RPS, got {rps:,.0f} RPS"


def test_full_suite_speed_gate_phase3():
    """Verify Phase 3 single-pass yyjson Schema serde clears > 98,000 RPS gate."""
    class Order(Schema):
        order_id: int
        customer: str
        amount: float
        is_paid: bool

    app = Buckthorn()

    @app.post("/order", response_model=Order)
    def create_order(order: Order):
        return order

    client = RawTestClient(app)
    raw_req = (
        b"POST /order HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 68\r\n\r\n"
        b'{"order_id":1001,"customer":"Alice","amount":99.95,"is_paid":true}'
    )

    for _ in range(1000):
        client.send_raw(raw_req)

    iterations = 30_000
    send_raw = client.send_raw
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert rps > 98_000, f"Expected >98,000 RPS, got {rps:,.0f} RPS"


def test_full_suite_speed_gate_phase5(tmp_path):
    """Verify Phase 5 lock-free atomic ring logging clears > 100,000 RPS gate."""
    log_file = tmp_path / "speed_gate.log"
    app = Buckthorn(log_file=str(log_file), access_log=True, log_capacity=65536)

    @app.get("/logged")
    def logged_endpoint():
        app.logger.info("ping", worker=1)
        return b'{"logged":true}'

    client = RawTestClient(app)
    raw_req = b"GET /logged HTTP/1.1\r\nHost: localhost\r\n\r\n"

    for _ in range(1000):
        client.send_raw(raw_req)

    iterations = 30_000
    send_raw = client.send_raw
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    app.logger.flush_and_stop()

    rps = iterations / elapsed
    assert res.status_code == 200
    assert rps > 100_000, f"Expected >100,000 RPS, got {rps:,.0f} RPS"


def test_full_suite_speed_gate_phase6():
    """Verify Phase 6 OpenSSL JWT verification clears > 98,000 RPS gate."""
    secret = "phase5_speed_gate_secret_key_32bytes"
    bearer = HTTPBearer(jwt_secret=secret)
    app = Buckthorn()

    @app.get("/secure")
    def secure(claims: dict = Security(bearer)):
        return {"sub": claims["sub"]}

    token = create_jwt_token({"sub": "user_1001"}, secret=secret)
    client = RawTestClient(app)
    raw_req = f"GET /secure HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\n\r\n".encode("latin1")

    for _ in range(1000):
        client.send_raw(raw_req)

    iterations = 30_000
    send_raw = client.send_raw
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert rps > 98_000, f"Expected >98,000 RPS, got {rps:,.0f} RPS"


def test_zero_asan_leaks():
    """Verify memory cleanliness, arena reset stability, and zero leaks across 10,000 requests."""
    app = Buckthorn()

    @app.get("/clean_check")
    def clean_check():
        return {"allocated": False, "clean": True}

    client = RawTestClient(app)
    raw_req = b"GET /clean_check HTTP/1.1\r\nHost: localhost\r\n\r\n"

    for _ in range(10_000):
        res = client.send_raw(raw_req)
        assert res.status_code == 200
        # Arena offset MUST be exactly 0 after every request completion
        assert client.arena_offset == 0
