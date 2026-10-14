# tests/phase6/test_c_security.py
import base64
import time
import pytest
from buckthorn import Buckthorn, Security, Depends
from buckthorn.security import (
    HTTPBearer,
    OAuth2PasswordBearer,
    APIKeyHeader,
    HTTPBasic,
    HTTPBasicCredentials,
    create_jwt_token,
)
from buckthorn.testing import RawTestClient


def test_c_openssl_jwt_verification_expiration_and_scopes():
    app = Buckthorn()
    secret = "buckthorn-super-secret-hs256-key"
    oauth2 = OAuth2PasswordBearer(tokenUrl="/login", jwt_secret=secret)
    handler_called = False

    @app.get("/vault")
    async def read_vault(
        claims: dict = Security(oauth2, scopes=["vault:read", "admin"]),
    ):
        nonlocal handler_called
        handler_called = True
        return {"user": claims["sub"], "role": claims["role"]}

    client = RawTestClient(app)

    # 1. Missing Authorization header -> 401 in C without calling handler
    res_missing = client.get("/vault")
    assert res_missing.status_code == 401
    assert not handler_called

    # 2. Forged signature (signed with wrong key) -> 401 in C via OpenSSL CRYPTO_memcmp
    forged_token = create_jwt_token(
        {"sub": "hacker", "scopes": ["vault:read", "admin"]},
        secret="wrong-secret-key",
    )
    res_forged = client.get("/vault", headers={"Authorization": f"Bearer {forged_token}"})
    assert res_forged.status_code == 401
    assert not handler_called

    # 3. Expired token (`exp` in the past) -> 401 in C
    expired_token = create_jwt_token(
        {"sub": "alice", "scopes": ["vault:read", "admin"], "exp": int(time.time()) - 60},
        secret=secret,
    )
    res_expired = client.get("/vault", headers={"Authorization": f"Bearer {expired_token}"})
    assert res_expired.status_code == 401
    assert not handler_called

    # 4. Valid signature & exp, but missing required "admin" scope -> 403 Forbidden in C
    insufficient_scope_token = create_jwt_token(
        {"sub": "bob", "scopes": ["vault:read"], "exp": int(time.time()) + 3600},
        secret=secret,
    )
    res_403 = client.get("/vault", headers={"Authorization": f"Bearer {insufficient_scope_token}"})
    assert res_403.status_code == 403
    assert not handler_called

    # 5. Valid token with all required scopes -> 200 OK
    valid_token = create_jwt_token(
        {
            "sub": "alice",
            "role": "superuser",
            "scopes": ["vault:read", "admin", "billing"],
            "exp": int(time.time()) + 3600,
        },
        secret=secret,
    )
    res_200 = client.get("/vault", headers={"Authorization": f"Bearer {valid_token}"})
    assert res_200.status_code == 200
    assert handler_called
    assert res_200.json() == {"user": "alice", "role": "superuser"}


def test_c_api_key_header_and_http_basic_auth():
    app = Buckthorn()
    api_key_guard = APIKeyHeader(name="X-API-Key", expected_key="bt-prod-key-999")
    basic_auth = HTTPBasic(expected_username="admin", expected_password="correct-password")

    @app.get("/service-status")
    def service_status(key: str = Security(api_key_guard)):
        return {"authenticated_key": key}

    @app.get("/admin-panel")
    def admin_panel(creds: HTTPBasicCredentials = Depends(basic_auth)):
        return {"username": creds.username}

    client = RawTestClient(app)

    # 1. API Key checks (Missing -> 401, Wrong -> 403, Correct -> 200)
    assert client.get("/service-status").status_code == 401
    assert client.get("/service-status", headers={"X-API-Key": "wrong-key"}).status_code == 403
    res_key = client.get("/service-status", headers={"X-API-Key": "bt-prod-key-999"})
    assert res_key.status_code == 200
    assert res_key.json() == {"authenticated_key": "bt-prod-key-999"}

    # 2. HTTP Basic Auth checks in C
    bad_b64 = base64.b64encode(b"admin:wrong-pass").decode("ascii")
    assert client.get("/admin-panel", headers={"Authorization": f"Basic {bad_b64}"}).status_code == 401

    good_b64 = base64.b64encode(b"admin:correct-password").decode("ascii")
    res_basic = client.get("/admin-panel", headers={"Authorization": f"Basic {good_b64}"})
    assert res_basic.status_code == 200
    assert res_basic.json() == {"username": "admin"}


def test_phase6_c_jwt_security_speed_gate():
    app = Buckthorn()
    secret = "bench-jwt-secret-key"
    bearer = HTTPBearer(jwt_secret=secret)

    @app.get("/bench/secure")
    def bench_secure(claims: dict = Security(bearer, scopes=["read:metrics"])):
        return b'{"ok":true}'

    client = RawTestClient(app)
    token = create_jwt_token(
        {"sub": "user_1", "scopes": ["read:metrics"], "exp": int(time.time()) + 3600},
        secret=secret,
    )

    # 1. Benchmark valid OpenSSL HMAC-SHA256 JWT verification + scope check + handler
    raw_valid = (
        f"GET /bench/secure HTTP/1.1\r\n"
        f"Host: localhost\r\n"
        f"Authorization: Bearer {token}\r\n\r\n"
    ).encode("ascii")

    for _ in range(500):
        client.send_raw(raw_valid)

    iterations = 20_000
    import gc
    gc.collect()
    gc.disable()
    try:
        t0 = time.perf_counter()
        for _ in range(iterations):
            res = client.send_raw(raw_valid)
        elapsed_valid = time.perf_counter() - t0
    finally:
        gc.enable()
    rps_valid = iterations / elapsed_valid

    assert res.status_code == 200
    assert rps_valid > 75_000, f"Expected >75,000 RPS on JWT-verified route, got {rps_valid:,.0f} RPS"

    # 2. Benchmark fast-401 C rejection of forged token (must exceed 120,000+ RPS)
    raw_forged = (
        b"GET /bench/secure HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.forged_sig_bytes\r\n\r\n"
    )
    t1 = time.perf_counter()
    for _ in range(iterations):
        res_bad = client.send_raw(raw_forged)
    elapsed_reject = time.perf_counter() - t1
    rps_reject = iterations / elapsed_reject

    assert res_bad.status_code == 401
    assert rps_reject > 120_000, f"Expected >120,000 RPS on C 401 fast-reject, got {rps_reject:,.0f} RPS"
