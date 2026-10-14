# tests/phase7/test_middlewares.py
import time
import pytest
from buckthorn import Buckthorn, Request
from buckthorn.middleware import (
    CORSMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)
from buckthorn.testing import RawTestClient


def test_c_cors_preflight_and_response_header_injection():
    app = Buckthorn()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://app.buckthorn.io"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    handler_called = False

    @app.get("/items")
    def get_items():
        nonlocal handler_called
        handler_called = True
        return {"items": [1, 2, 3]}

    client = RawTestClient(app)

    # 1. OPTIONS preflight request answered in 100% pure C without calling Python handler
    preflight = client.options(
        "/items",
        headers={
            "Origin": "https://app.buckthorn.io",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 204
    assert not handler_called
    assert preflight.headers["Access-Control-Allow-Origin"] == "https://app.buckthorn.io"
    assert "POST" in preflight.headers["Access-Control-Allow-Methods"]
    assert "Authorization" in preflight.headers["Access-Control-Allow-Headers"]

    # 2. Standard GET request receives pre-baked CORS header automatically
    res = client.get("/items", headers={"Origin": "https://app.buckthorn.io"})
    assert res.status_code == 200
    assert handler_called
    assert res.headers["Access-Control-Allow-Origin"] == "https://app.buckthorn.io"
    assert res.json() == {"items": [1, 2, 3]}


def test_c_token_bucket_rate_limiter_rejects_excess_requests_in_c():
    app = Buckthorn()
    app.add_middleware(RateLimitMiddleware, max_requests=3, window_seconds=60)

    calls = 0

    @app.get("/limited")
    def limited_endpoint():
        nonlocal calls
        calls += 1
        return {"call": calls}

    client = RawTestClient(app)

    # First 3 requests from client IP 10.0.0.1 succeed
    for i in range(1, 4):
        r = client.get("/limited", headers={"X-Forwarded-For": "10.0.0.1"})
        assert r.status_code == 200
        assert r.json() == {"call": i}

    # 4th request from 10.0.0.1 is rejected in pure C with 429 Too Many Requests
    r_429 = client.get("/limited", headers={"X-Forwarded-For": "10.0.0.1"})
    assert r_429.status_code == 429
    assert calls == 3  # Python handler was NOT called on the 4th request!
    assert r_429.json() == {"detail": "Too Many Requests"}

    # Different client IP (10.0.0.2) has its own independent C hash bucket and succeeds
    r_other_ip = client.get("/limited", headers={"X-Forwarded-For": "10.0.0.2"})
    assert r_other_ip.status_code == 200
    assert calls == 4


def test_c_trusted_host_and_security_headers_middleware():
    app = Buckthorn()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api.buckthorn.io"])
    app.add_middleware(
        SecurityHeadersMiddleware,
        x_frame_options="DENY",
        x_content_type_options="nosniff",
        strict_transport_security="max-age=63072000; includeSubDomains",
    )

    @app.get("/health")
    def health():
        return {"ok": True}

    client = RawTestClient(app)

    # 1. Request with default "Host: localhost" is rejected in C with 400 Bad Request
    res_bad_host = client.get("/health")
    assert res_bad_host.status_code == 400

    # 2. Request with matching "Host: api.buckthorn.io" succeeds and includes C security headers
    res_ok = client.get("/health", headers={"Host": "api.buckthorn.io"})
    assert res_ok.status_code == 200
    assert res_ok.headers["X-Frame-Options"] == "DENY"
    assert res_ok.headers["X-Content-Type-Options"] == "nosniff"
    assert "max-age=63072000" in res_ok.headers["Strict-Transport-Security"]


def test_user_http_middleware_hook_and_custom_response_headers():
    app = Buckthorn()
    trace: list[str] = []

    @app.middleware("http")
    async def custom_timing_middleware(request: Request, call_next):
        trace.append(f"before:{request.path}")
        response = await call_next(request)
        response.headers["X-Custom-Middleware"] = "Buckthorn-Hook"
        trace.append(f"after:{response.status_code}")
        return response

    @app.get("/hello")
    async def hello():
        trace.append("inside_handler")
        return {"msg": "world"}

    client = RawTestClient(app)
    res = client.get("/hello")
    assert res.status_code == 200
    assert res.json() == {"msg": "world"}
    assert res.headers["X-Custom-Middleware"] == "Buckthorn-Hook"
    assert trace == ["before:/hello", "inside_handler", "after:200"]


def test_phase7_c_middleware_pipeline_speed_gate():
    app = Buckthorn()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=1_000_000, window_seconds=60)

    @app.get("/bench/mw")
    def bench_mw():
        return b'{"ok":true}'

    client = RawTestClient(app)
    raw_req = (
        b"GET /bench/mw HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Origin: https://example.com\r\n"
        b"X-Forwarded-For: 192.168.1.50\r\n\r\n"
    )

    for _ in range(500):
        client.send_raw(raw_req)

    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = client.send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert rps > 80_000, f"Expected >80,000 RPS with 3 active C middlewares, got {rps:,.0f} RPS"
