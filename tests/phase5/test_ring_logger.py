# tests/phase5/test_ring_logger.py
import json
import time
import pytest
from buckthorn import Buckthorn
from buckthorn.logging import RingLogger
from buckthorn.testing import RawTestClient


def test_lock_free_ring_logger_structured_json_and_types(tmp_path):
    log_file = tmp_path / "structured.log"
    logger = RingLogger(filepath=str(log_file), capacity=1024)

    logger.info("user_login", user_id=42, plan="pro", active=True, score=98.5)
    logger.warning("rate_near_limit", remaining=5)
    logger.error("payment_declined", order_id=9001, retry=False)
    logger.debug("cache_hit", key="user:42")

    logger.flush_and_stop()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4

    entry0 = json.loads(lines[0])
    assert entry0["level"] == "INFO"
    assert entry0["msg"] == "user_login"
    assert entry0["user_id"] == 42
    assert entry0["plan"] == "pro"
    assert entry0["active"] is True
    assert pytest.approx(entry0["score"], 0.01) == 98.5
    assert isinstance(entry0["ts_ns"], int) and entry0["ts_ns"] > 0

    entry1 = json.loads(lines[1])
    assert entry1["level"] == "WARN"
    assert entry1["msg"] == "rate_near_limit"
    assert entry1["remaining"] == 5

    entry2 = json.loads(lines[2])
    assert entry2["level"] == "ERROR"
    assert entry2["order_id"] == 9001
    assert entry2["retry"] is False

    entry3 = json.loads(lines[3])
    assert entry3["level"] == "DEBUG"
    assert entry3["key"] == "user:42"


def test_ring_logger_non_finite_float_json_safety(tmp_path):
    """Verify NaN and Infinity floats serialize as null in JSON (RFC 8259 compliance)."""
    log_file = tmp_path / "floats.log"
    logger = RingLogger(filepath=str(log_file), capacity=1024)

    logger.info("float_check", nan_val=float("nan"), inf_val=float("inf"))
    logger.flush_and_stop()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["nan_val"] is None
    assert entry["inf_val"] is None


def test_app_logger_and_pure_c_http_access_logging(tmp_path):
    log_file = tmp_path / "app_access.log"
    app = Buckthorn(log_file=str(log_file), access_log=True)

    @app.get("/checkout/{cart_id}")
    def checkout(cart_id: int):
        app.logger.info("checkout_started", cart_id=cart_id, currency="USD")
        return {"cart_id": cart_id, "ok": True}

    client = RawTestClient(app)
    res = client.get("/checkout/777")
    assert res.status_code == 200
    assert res.json() == {"cart_id": 777, "ok": True}

    # Also trigger a 404 in C to verify pure-C access logging records non-200 responses
    res_404 = client.get("/missing-route")
    assert res_404.status_code == 404

    app.logger.flush_and_stop()

    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").strip().splitlines()]
    # Expect: 1 application log ("checkout_started") + 2 C-level HTTP access logs ("http_access")
    assert len(lines) == 3

    app_log = next(l for l in lines if l["msg"] == "checkout_started")
    assert app_log["cart_id"] == 777
    assert app_log["currency"] == "USD"

    access_logs = [l for l in lines if l["msg"] == "http_access"]
    assert len(access_logs) == 2
    assert access_logs[0]["method"] == "GET"
    assert access_logs[0]["path"] == "/checkout/777"
    assert access_logs[0]["status"] == 200
    assert access_logs[0]["latency_ns"] >= 0

    assert access_logs[1]["path"] == "/missing-route"
    assert access_logs[1]["status"] == 404


def test_ring_logger_hot_path_nanosecond_budget_and_rps_gate(tmp_path):
    log_file = tmp_path / "bench.log"
    app = Buckthorn(log_file=str(log_file), access_log=True, log_capacity=65536)

    @app.get("/logged-ping")
    def logged_ping():
        app.logger.info("ping_hit", worker_id=1)
        return b"pong"

    client = RawTestClient(app)
    raw_req = b"GET /logged-ping HTTP/1.1\r\nHost: localhost\r\n\r\n"

    for _ in range(500):
        client.send_raw(raw_req)

    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = client.send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    app.logger.flush_and_stop()

    rps = iterations / elapsed
    assert res.status_code == 200
    # Even with 2 log events per request (1 app.logger.info + 1 C access log), single worker >80,000 RPS
    assert rps > 80_000, f"Expected >80,000 RPS with active C11 ring logging, got {rps:,.0f} RPS"
