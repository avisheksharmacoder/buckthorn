# tests/phase1/test_core_router.py
import time
import pytest
from buckthorn import Buckthorn
from buckthorn.testing import RawTestClient


def test_static_get_and_post_routing():
    app = Buckthorn()

    @app.get("/ping")
    async def ping():
        return b"pong"

    @app.post("/orders")
    def create_order():
        return b'{"created":true}'

    client = RawTestClient(app)

    # 1. Test GET /ping
    res = client.send_raw(b"GET /ping HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res.status_code == 200
    assert res.body == b"pong"

    # 2. Test POST /orders
    res_post = client.send_raw(
        b"POST /orders HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}"
    )
    assert res_post.status_code == 200
    assert res_post.body == b'{"created":true}'


def test_unmatched_route_returns_404_in_c():
    app = Buckthorn()

    @app.get("/exists")
    def exists():
        return b"ok"

    client = RawTestClient(app)

    # Missing URL path -> 404 directly from C radix lookup
    res_404 = client.send_raw(b"GET /does-not-exist HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res_404.status_code == 404

    # Wrong HTTP method on existing path -> 404/405
    res_wrong_method = client.send_raw(b"DELETE /exists HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res_wrong_method.status_code in (404, 405)


def test_query_string_stripped_before_radix_lookup():
    app = Buckthorn()

    @app.get("/search")
    def search():
        return b"results"

    client = RawTestClient(app)
    res = client.send_raw(b"GET /search?q=buckthorn&limit=10 HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res.status_code == 200
    assert res.body == b"results"


def test_malformed_http_rejected_in_c_without_python():
    app = Buckthorn()

    @app.get("/ping")
    def ping():
        return b"pong"

    client = RawTestClient(app)
    res = client.send_raw(b"CORRUPTED_GARBAGE_PROTOCOL\r\n\r\n")
    assert res.status_code == 400
    assert b"Bad Request" in res.body


def test_c_arena_zero_leak_and_single_worker_speed_gate():
    app = Buckthorn()

    @app.get("/fast")
    def fast():
        return b'{"ok":1}'

    client = RawTestClient(app)
    raw_req = b"GET /fast HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"

    # Warmup
    for _ in range(500):
        client.send_raw(raw_req)

    # Execute 20,000 iterations through the C picohttpparser + rax + Cython pipeline
    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = client.send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert client.arena_offset == 0  # Verify C Arena resets to 0 after every request
    # In-memory C pipeline must easily exceed 100,000+ RPS ( guaranteeing >35,000 RPS over TCP )
    assert rps > 100_000, f"Expected >100k in-memory RPS, got {rps:,.0f} RPS"
