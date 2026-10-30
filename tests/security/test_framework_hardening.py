# tests/security/test_framework_hardening.py
import time
import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from buckthorn import (
    Buckthorn,
    Field,
    Header,
    Query,
    Response,
    Schema,
    Security,
)
from buckthorn.middleware import (
    CORSMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)
from buckthorn.security import HTTPBearer, create_jwt_token
from buckthorn.testing import RawTestClient


class PaymentPayload(Schema):
    account_id: int = Field(..., ge=1, le=10_000_000)
    amount: float = Field(..., gt=0.0, le=1_000_000.0)
    memo: str = Field("ok", min_length=1, max_length=64)


@pytest.fixture()
def hardened_app() -> Buckthorn:
    app = Buckthorn(title="Buckthorn <Security> Suite", max_body_size=4096)
    bearer = HTTPBearer(jwt_secret="super-secret-hs256-key-32-bytes!!")

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost"])
    app.add_middleware(CORSMiddleware, allow_origins=["https://app.example.com"])
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/items/{item_id}")
    def get_item(
        item_id: int,
        limit: int = Query(10, ge=1, le=100),
        x_trace: str = Header("none", max_length=32),
    ):
        return {"item_id": item_id, "limit": limit, "trace": x_trace}

    @app.post("/pay")
    def process_payment(payload: PaymentPayload):
        return {"status": "accepted", "account_id": payload.account_id}

    @app.get("/vault")
    def read_vault(claims: dict = Security(bearer, scopes=["vault:read"])):
        return {"sub": claims.get("sub")}

    @app.get("/oversized-output")
    def oversized_output(size: int = Query(1024)):
        return b"A" * size

    return app


# ---------------------------------------------------------------------------
# 1. HTTP/1.1 Request Framing & Desynchronization Guards (bt_audit.c)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw_http",
    [
        # Missing Host header (RFC 9112 violation)
        b"GET /items/1 HTTP/1.1\r\n\r\n",
        # Duplicate Host headers
        b"GET /items/1 HTTP/1.1\r\nHost: localhost\r\nHost: evil.example.com\r\n\r\n",
        # Conflicting Content-Length and Transfer-Encoding (CL.TE / TE.CL guard)
        b"POST /pay HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}",
        # Duplicate Content-Length headers
        b"POST /pay HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
        # Non-numeric / signed Content-Length
        b"POST /pay HTTP/1.1\r\nHost: localhost\r\nContent-Length: +2\r\n\r\n{}",
        # Space before colon in header name
        b"GET /items/1 HTTP/1.1\r\nHost : localhost\r\n\r\n",
        # Obs-fold multiline header continuation
        b"GET /items/1 HTTP/1.1\r\nHost: localhost\r\n X-Folded: 1\r\n\r\n",
    ],
)
def test_http_framing_anomalies_rejected_with_400(hardened_app: Buckthorn, raw_http: bytes):
    with RawTestClient(hardened_app) as client:
        resp = client.send_raw(raw_http)
        assert resp.status_code == 400
        assert client.arena_offset == 0  # Arena must always reset cleanly


def test_payload_and_uri_length_limits_enforced(hardened_app: Buckthorn):
    with RawTestClient(hardened_app) as client:
        # 1. URI > 4096 bytes -> 414 URI Too Long
        long_uri = "/items/" + ("1" * 4100)
        resp_414 = client.get(long_uri)
        assert resp_414.status_code == 414

        # 2. Body > max_body_size (4096) -> 413 Content Too Large
        huge_body = b'{"account_id":1,"amount":10.0,"memo":"' + (b"x" * 5000) + b'"}'
        resp_413 = client.post(
            "/pay",
            body=huge_body,
            headers={"Content-Type": "application/json"},
        )
        assert resp_413.status_code == 413


# ---------------------------------------------------------------------------
# 2. Path Traversal & Encoding Normalization Guards (bt_audit.c)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/items/..%2fadmin",
        "/items/%2e%2e/secret",
        "/items/%2E%2E/secret",
        "/items/1%00.json",
        "/items/1%5cadmin",
        "/items/..\\",
    ],
)
def test_path_traversal_vectors_blocked_in_c(hardened_app: Buckthorn, unsafe_path: str):
    with RawTestClient(hardened_app) as client:
        resp = client.get(unsafe_path)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 3. Arena & Output Buffer Boundary Safety (_core.pyx)
# ---------------------------------------------------------------------------
def test_arena_output_buffer_bounds_protection(hardened_app: Buckthorn):
    with RawTestClient(hardened_app) as client:
        # Within 32KB arena handler output limit -> 200 OK
        ok_resp = client.get("/oversized-output?size=16384")
        assert ok_resp.status_code == 200
        assert len(ok_resp.body) == 16384

        # Exceeding 32KB arena handler output limit -> clean 500 (no heap corruption)
        over_resp = client.get("/oversized-output?size=40000")
        assert over_resp.status_code == 500
        assert client.arena_offset == 0


# ---------------------------------------------------------------------------
# 4. CRLF Header Injection Sanitization in User Middleware (_core.pyx)
# ---------------------------------------------------------------------------
def test_crlf_response_splitting_neutralized():
    app = Buckthorn()

    @app.middleware("http")
    async def inject_header_mw(req, call_next):
        res = await call_next(req)
        return Response(
            status_code=res.status_code,
            body=res.body,
            headers={"X-Custom": "safe\r\nX-Injected: pwned\r\n\r\n<script>"},
        )

    @app.get("/test")
    def index():
        return {"ok": True}

    with RawTestClient(app) as client:
        resp = client.get("/test")
        assert resp.status_code == 200
        assert "X-Injected" not in resp.headers
        assert resp.headers.get("X-Custom") == "safeX-Injected: pwned<script>"


# ---------------------------------------------------------------------------
# 5. JWT Cryptographic & Claim Integrity (bt_security.c)
# ---------------------------------------------------------------------------
def test_jwt_tampering_alg_none_and_scope_enforcement(hardened_app: Buckthorn):
    secret = "super-secret-hs256-key-32-bytes!!"
    with RawTestClient(hardened_app) as client:
        # 1. Expired token -> 401
        expired_tok = create_jwt_token(
            {"sub": "u1", "exp": int(time.time()) - 10, "scopes": ["vault:read"]},
            secret,
        )
        assert client.get("/vault", headers={"Authorization": f"Bearer {expired_tok}"}).status_code == 401

        # 2. Not-Before (nbf) in future -> 401
        future_nbf = create_jwt_token(
            {"sub": "u1", "nbf": int(time.time()) + 3600, "scopes": ["vault:read"]},
            secret,
        )
        assert client.get("/vault", headers={"Authorization": f"Bearer {future_nbf}"}).status_code == 401

        # 3. Valid signature but missing required scope -> 403
        wrong_scope = create_jwt_token(
            {"sub": "u1", "exp": int(time.time()) + 3600, "scopes": ["profile:read"]},
            secret,
        )
        assert client.get("/vault", headers={"Authorization": f"Bearer {wrong_scope}"}).status_code == 403

        # 4. Tampered payload with original signature -> 401
        valid_tok = create_jwt_token(
            {"sub": "u1", "exp": int(time.time()) + 3600, "scopes": ["vault:read"]},
            secret,
        )
        h_b64, p_b64, s_b64 = valid_tok.split(".")
        tampered = f"{h_b64}.{p_b64[:-2]}AA.{s_b64}"
        assert client.get("/vault", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401


# ---------------------------------------------------------------------------
# 6. Property-Based Fuzzing of C Parameter & yyjson Validators (Hypothesis)
# ---------------------------------------------------------------------------
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    item_id=st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=40),
    limit_str=st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=40),
)
def test_property_c_param_parsers_never_crash_or_leak_arena(
    hardened_app: Buckthorn, item_id: str, limit_str: str
):
    if "/" in item_id or "?" in item_id or "%" in item_id or ".." in item_id or "\\" in item_id:
        return
    with RawTestClient(hardened_app) as client:
        resp = client.get(f"/items/{item_id}?limit={limit_str}")
        assert resp.status_code in (200, 400, 422)
        assert client.arena_offset == 0


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    account_id=st.integers(min_value=-10**22, max_value=10**22),
    amount=st.floats(allow_nan=False, allow_infinity=False),
    memo=st.text(min_size=0, max_size=200),
)
def test_property_yyjson_schema_validator_invariants(
    hardened_app: Buckthorn, account_id: int, amount: float, memo: str
):
    with RawTestClient(hardened_app) as client:
        resp = client.post(
            "/pay",
            json={"account_id": account_id, "amount": amount, "memo": memo},
        )
        is_valid = (
            1 <= account_id <= 10_000_000
            and 0.0 < amount <= 1_000_000.0
            and 1 <= len(memo.encode("utf-8")) <= 64
            and "\x00" not in memo
        )
        if is_valid:
            assert resp.status_code == 200
        else:
            assert resp.status_code == 422
        assert client.arena_offset == 0


# ---------------------------------------------------------------------------
# 7. Phase 1 Hardening: NaN/Inf Rejection, Multipart Collisions & WS RFC 6455
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_float",
    ["nan", "NaN", "NAN", "-nan", "inf", "-inf", "+inf", "Infinity"]
)
def test_nan_inf_floats_rejected_with_422(bad_float: str):
    app = Buckthorn()

    @app.get("/calc")
    def calc(amount: float = Query(..., gt=0.0, le=1000.0)):
        return {"amount": amount}

    with RawTestClient(app) as client:
        resp = client.get(f"/calc?amount={bad_float}")
        assert resp.status_code == 422
        assert client.arena_offset == 0


@pytest.mark.parametrize("ws_float", [" 10.5", "10.5 "])
def test_whitespace_floats_in_cookies_rejected_with_422(ws_float: str):
    from buckthorn import Cookie
    app = Buckthorn()

    @app.get("/check-cookie")
    def check_cookie(val: float = Cookie(...)):
        return {"val": val}

    with RawTestClient(app) as client:
        resp = client.get("/check-cookie", headers={"Cookie": f"val={ws_float}; path=/"})
        assert resp.status_code == 422
        assert client.arena_offset == 0


def test_multipart_filename_before_name_collision_and_control_chars():
    from buckthorn import UploadFile, File
    app = Buckthorn()

    @app.post("/upload")
    async def upload_file(doc: UploadFile = File()):
        content = await doc.read()
        return {"field": "doc", "filename": doc.filename, "content": content.decode("utf-8")}

    # 1. Test Content-Disposition where filename appears BEFORE name
    boundary = "TestBoundary12345"
    raw_multipart = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; filename="avatar.png"; name="doc"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
        f"file_payload\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    with RawTestClient(app) as client:
        resp = client.post(
            "/upload",
            body=raw_multipart,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "avatar.png"
        assert resp.json()["content"] == "file_payload"

    # 2. Test filename with control character (\x1f)
    raw_multipart_bad_fn = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="doc"; filename="avatar\x1f.png"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
        f"file_payload\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    with RawTestClient(app) as client:
        resp_bad = client.post(
            "/upload",
            body=raw_multipart_bad_fn,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert resp_bad.status_code == 200
        # Control char causes filename slice to be omitted / empty
        assert resp_bad.json()["filename"] == ""


def test_websocket_rfc6455_rsv_and_control_frame_rejection():
    from buckthorn import WebSocket
    app = Buckthorn()

    @app.websocket("/ws")
    async def ws_echo(ws: WebSocket):
        await ws.accept()
        msg = await ws.receive_text()
        await ws.send_text(msg)

    with RawTestClient(app) as client:
        with client.websocket_connect("/ws") as session:
            # 1. Frame with RSV1 bit set (b0 = 0xC1 instead of 0x81) must be rejected
            rsv_frame = bytes([0xC1, 0x80, 0x00, 0x00, 0x00, 0x00])
            with pytest.raises(ValueError, match="Malformed RFC 6455 WebSocket frame"):
                session.ws.feed_raw_frame(rsv_frame)

            # 2. Control frame (Ping = 0x89) with payload > 125 bytes must be rejected
            oversized_ping = bytes([0x89, 0x7E, 0x00, 0x80]) + (b"\x00" * 128)
            with pytest.raises(ValueError, match="Malformed RFC 6455 WebSocket frame"):
                session.ws.feed_raw_frame(oversized_ping)

            # 3. Fragmented control frame (FIN = 0, opcode = 0x8 -> b0 = 0x08) must be rejected
            frag_control = bytes([0x08, 0x80, 0x00, 0x00, 0x00, 0x00])
            with pytest.raises(ValueError, match="Malformed RFC 6455 WebSocket frame"):
                session.ws.feed_raw_frame(frag_control)


def test_json_nesting_depth_bomb_rejected():
    app = Buckthorn()

    class NestedPayload(Schema):
        data: list

    @app.post("/deep-json")
    def deep_json(payload: NestedPayload):
        return {"items": len(payload.data)}

    # Construct 100-level nested array: [[[[...1...]]]]
    depth = 100
    deep_body = (b"[" * depth) + b"1" + (b"]" * depth)
    with RawTestClient(app) as client:
        resp = client.post(
            "/deep-json",
            body=b'{"data":' + deep_body + b"}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422, 500)
        assert client.arena_offset == 0


def test_websocket_pubsub_zombie_pruning():
    from buckthorn import WebSocket
    app = Buckthorn()

    @app.websocket("/ws/{room}")
    async def ws_room(ws: WebSocket, room: str):
        await ws.accept()
        ws.subscribe(room)

    with RawTestClient(app) as client:
        with client.websocket_connect("/ws/alerts") as s1:
            s1._ensure_executed()
            with client.websocket_connect("/ws/alerts") as s2:
                s2._ensure_executed()
                assert len(app._core._ws_pubsub.get("alerts", [])) == 2

                # Close s1 and publish
                s1.ws.closed = True
                app.ws_publish("alerts", "update")

                # Verify s1 was pruned and only s2 remains
                remaining = app._core._ws_pubsub.get("alerts", [])
                assert len(remaining) == 1
                assert remaining[0] is s2.ws


def test_file_response_root_dir_jail_and_size_guard(tmp_path):
    from buckthorn.responses import FileResponse
    jail_dir = tmp_path / "jail"
    jail_dir.mkdir()
    safe_file = jail_dir / "safe.txt"
    safe_file.write_text("safe content")

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret content")

    # 1. Path within root_dir succeeds
    resp_ok = FileResponse(str(safe_file), root_dir=str(jail_dir))
    body, hdrs, status = resp_ok._render_c_response()
    assert body == b"safe content"

    # 2. Path escaping root_dir raises PermissionError
    with pytest.raises(PermissionError, match="escapes configured root_dir"):
        FileResponse(str(outside_file), root_dir=str(jail_dir))

    # 3. Path escaping root_dir via relative path or traversal raises PermissionError
    traversal_path = jail_dir / ".." / "secret.txt"
    with pytest.raises(PermissionError, match="escapes configured root_dir"):
        FileResponse(str(traversal_path), root_dir=str(jail_dir))

    # 4. Target is a directory raises PermissionError
    dir_target = FileResponse(str(jail_dir))
    with pytest.raises(PermissionError, match="must be a regular file"):
        dir_target._render_c_response()

    # 5. Target exceeds 32KB arena capacity raises ValueError
    oversized_file = jail_dir / "huge.bin"
    oversized_file.write_bytes(b"A" * 32769)
    over_resp = FileResponse(str(oversized_file))
    with pytest.raises(ValueError, match="exceeds 32KB arena capacity"):
        over_resp._render_c_response()


def test_event_source_response_async_stepper_and_cap():
    import asyncio
    from buckthorn.responses import EventSourceResponse

    # 1. Multi-step async generator with real await executes cleanly via _step_coroutine_sync
    async def async_event_gen():
        for i in range(3):
            await asyncio.sleep(0)
            yield f"data: event_{i}\n\n"

    sse = EventSourceResponse(async_event_gen())
    body, hdrs, status = sse._render_c_response()
    assert body == b"data: event_0\n\ndata: event_1\n\ndata: event_2\n\n"
    assert b"text/event-stream" in hdrs

    # 2. Stream exceeding 32KB raises ValueError
    async def huge_sse_gen():
        for _ in range(1000):
            yield "data: " + ("X" * 100) + "\n\n"

    sse_huge = EventSourceResponse(huge_sse_gen())
    with pytest.raises(ValueError, match="exceeds 32KB arena buffer"):
        sse_huge._render_c_response()


def test_oauth2_password_bearer_requires_jwt_secret():
    from buckthorn.security import OAuth2PasswordBearer
    with pytest.raises(TypeError, match="jwt_secret"):
        OAuth2PasswordBearer(tokenUrl="/token")


def test_uint64_overflow_in_json_rejected():
    """Verify uint64 values exceeding INT64_MAX do not silently wrap around to negative numbers (V10)."""
    class Order(Schema):
        qty: int = Field(..., le=100)

    app = Buckthorn()

    @app.post("/order")
    def create_order(o: Order):
        return {"qty": o.qty}

    client = RawTestClient(app)
    # UINT64_MAX: 18446744073709551615 wraps to -1 if unchecked, which would pass le=100
    res = client.post("/order", json={"qty": 18446744073709551615})
    assert res.status_code == 422


def test_excessive_method_token_rejected_with_400():
    """Verify HTTP methods exceeding 15 bytes are immediately rejected with 400 (V13)."""
    app = Buckthorn()

    @app.get("/ping")
    def ping():
        return {"ping": "pong"}

    client = RawTestClient(app)
    raw = b"EXTREMELYLONGMETHODTOKEN /ping HTTP/1.1\r\nHost: localhost\r\n\r\n"
    res = client.send_raw(raw)
    assert res.status_code == 400
    assert b"X-Content-Type-Options: nosniff\r\n" in res.wire_bytes


def test_error_responses_include_nosniff_header():
    """Verify 400 and 404 static wire responses include X-Content-Type-Options nosniff (V12)."""
    app = Buckthorn()

    client = RawTestClient(app)
    res_404 = client.get("/nonexistent")
    assert res_404.status_code == 404
    assert b"X-Content-Type-Options: nosniff\r\n" in res_404.wire_bytes

    res_400 = client.send_raw(b"BAD REQUEST INVALID\r\n\r\n")
    assert res_400.status_code == 400
    assert b"X-Content-Type-Options: nosniff\r\n" in res_400.wire_bytes


def test_cli_token_sanitization():
    """Verify CLI input sanitizer strips CRLF and whitespace (V16)."""
    from buckthorn.cli import _sanitize_http_token
    assert _sanitize_http_token("GET\r\nInjected: evil\r\n") == "GETInjected:evil"
    assert _sanitize_http_token("/path\r\nHost: evil") == "/pathHost:evil"




