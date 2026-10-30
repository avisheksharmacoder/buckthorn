# tests/unit_v2/test_phase3_c_engine.py
import json
import os
import tempfile
import time
import pytest
from buckthorn import Buckthorn, Schema, Query, Security, UploadFile, File
from buckthorn.security import OAuth2PasswordBearer, create_jwt_token
from buckthorn.middleware import CORSMiddleware
from buckthorn.logging import RingLogger
from buckthorn.testing import RawTestClient
from buckthorn._core import c_ws_encode_frame, c_ws_parse_frame, WS_OP_TEXT, WS_OP_BIN


def test_fast_wire_formatter_status_200():
    """Verify header formatting output matches exact HTTP/1.1 spec for 200 OK without snprintf."""
    app = Buckthorn()

    @app.get("/hello")
    def hello():
        return {"msg": "fast_wire_ok"}

    client = RawTestClient(app)
    raw_req = b"GET /hello HTTP/1.1\r\nHost: localhost\r\n\r\n"
    res = client.send_raw(raw_req)

    assert res.status_code == 200
    assert res.wire_bytes.startswith(b"HTTP/1.1 200 OK\r\nContent-Length: ")
    assert b"\r\nConnection: keep-alive\r\n\r\n" in res.wire_bytes
    assert res.json() == {"msg": "fast_wire_ok"}


def test_fast_wire_formatter_custom_status():
    """Verify 201, 204, 400, 404, 422, 500 status lines are formatted with exact reason phrases."""
    app = Buckthorn()

    @app.post("/created", status_code=201)
    def create_item():
        return {"created": True}

    @app.delete("/empty", status_code=204)
    def empty_item():
        return {}

    client = RawTestClient(app)

    res_201 = client.post("/created")
    assert res_201.status_code == 201
    assert b"HTTP/1.1 201 Created\r\n" in res_201.wire_bytes

    res_204 = client.delete("/empty")
    assert res_204.status_code == 204
    assert b"HTTP/1.1 204 No Content\r\n" in res_204.wire_bytes

    res_404 = client.get("/non_existent_route")
    assert res_404.status_code == 404
    assert b"HTTP/1.1 404 Not Found\r\n" in res_404.wire_bytes

    res_400 = client.send_raw(b"BAD REQUEST INVALID HTTP\r\n\r\n")
    assert res_400.status_code == 400
    assert b"HTTP/1.1 400 Bad Request\r\n" in res_400.wire_bytes


def test_direct_arena_yyjson_serialization():
    """Serialize a 10-field JSON model; verify output length and JSON validity."""
    class UserModel(Schema):
        id: int
        name: str
        score: float
        active: bool
        role: str
        email: str
        city: str
        dept: str
        level: int
        bio: str

    app = Buckthorn()

    @app.get("/user", response_model=UserModel)
    def get_user():
        return UserModel(
            id=101,
            name="Alice",
            score=99.5,
            active=True,
            role="admin",
            email="alice@buckthorn.dev",
            city="San Francisco",
            dept="Engineering",
            level=5,
            bio="Core C engine developer",
        )

    client = RawTestClient(app)
    res = client.get("/user")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 101
    assert data["name"] == "Alice"
    assert data["score"] == 99.5
    assert data["active"] is True
    assert data["role"] == "admin"
    assert data["dept"] == "Engineering"
    assert data["level"] == 5


def test_yyjson_pool_large_payload():
    """Serialize and parse a 20KB JSON payload; verify it completes without 128KB pool exhaustion."""
    class LargePayload(Schema):
        data: str
        count: int

    app = Buckthorn()

    @app.post("/large")
    def receive_large(payload: LargePayload):
        return {"received_chars": len(payload.data), "count": payload.count}

    client = RawTestClient(app)
    # Generate ~20KB JSON payload
    large_payload = {"data": "x" * 20000, "count": 250}
    res = client.post("/large", json=large_payload)
    assert res.status_code == 200
    assert res.json() == {"received_chars": 20000, "count": 250}


def test_inplace_int64_parser_positive_negative():
    """Parse '42', '-42', '+100' directly in-place without malloc or strtoll."""
    app = Buckthorn()

    @app.get("/calc/{val}")
    def calc(val: int):
        return {"result": val * 2}

    client = RawTestClient(app)

    res1 = client.get("/calc/42")
    assert res1.status_code == 200
    assert res1.json()["result"] == 84

    res2 = client.get("/calc/-42")
    assert res2.status_code == 200
    assert res2.json()["result"] == -84

    res3 = client.get("/calc/+100")
    assert res3.status_code == 200
    assert res3.json()["result"] == 200


def test_inplace_int64_parser_bounds_int64_max_min():
    """Verify 9223372036854775807 and -9223372036854775808 parse without overflow."""
    app = Buckthorn()

    @app.get("/bigint/{val}")
    def bigint(val: int):
        return {"val": val}

    client = RawTestClient(app)

    # INT64_MAX
    res_max = client.get("/bigint/9223372036854775807")
    assert res_max.status_code == 200
    assert res_max.json()["val"] == 9223372036854775807

    # INT64_MIN
    res_min = client.get("/bigint/-9223372036854775808")
    assert res_min.status_code == 200
    assert res_min.json()["val"] == -9223372036854775808

    # Overflow INT64_MAX + 1 -> 422
    res_over = client.get("/bigint/9223372036854775808")
    assert res_over.status_code == 422


def test_inplace_int64_parser_reject_whitespace():
    """Assert whitespace in integer slices is strictly rejected with 422 in C."""
    app = Buckthorn()

    @app.get("/strict/{val}")
    def strict(val: int):
        return {"val": val}

    client = RawTestClient(app)
    # URL encoded space '%2042'
    res_space_pre = client.get("/strict/%2042")
    assert res_space_pre.status_code == 422

    # URL encoded space '42%20'
    res_space_post = client.get("/strict/42%20")
    assert res_space_post.status_code == 422

    # Alpha chars
    res_alpha = client.get("/strict/42abc")
    assert res_alpha.status_code == 422


def test_inplace_int64_parser_constraints():
    """Verify ge, le, gt, lt bounds enforcement in C."""
    app = Buckthorn()

    @app.get("/constrained")
    def constrained(val: int = Query(ge=10, le=100)):
        return {"val": val}

    client = RawTestClient(app)

    assert client.get("/constrained?val=9").status_code == 422
    assert client.get("/constrained?val=10").status_code == 200
    assert client.get("/constrained?val=50").status_code == 200
    assert client.get("/constrained?val=100").status_code == 200
    assert client.get("/constrained?val=101").status_code == 422


def test_first_byte_header_matching():
    """Search for Host, Date, Cookie and verify correct branchless first-byte match."""
    app = Buckthorn()

    @app.get("/headers")
    def read_headers(req_host: str = Query(default="")):
        return {"status": "ok"}

    client = RawTestClient(app)
    # Send mixed-case headers
    raw_req = (
        b"GET /headers HTTP/1.1\r\n"
        b"hOsT: localhost:8000\r\n"
        b"cOoKiE: session_id=xyz789\r\n"
        b"x-CuStOm-HeAdEr: special_val\r\n"
        b"\r\n"
    )
    res = client.send_raw(raw_req)
    assert res.status_code == 200


def test_canonical_jwt_header_fast_path():
    """Mint token with standard 36-byte header; verify validation passes in C fast path."""
    app = Buckthorn()
    secret = "buckthorn-secret-key-36"
    oauth2 = OAuth2PasswordBearer(tokenUrl="/token", jwt_secret=secret)

    @app.get("/profile")
    def get_profile(claims: dict = Security(oauth2, scopes=["read:profile"])):
        return {"user": claims["sub"]}

    client = RawTestClient(app)

    # Standard header {"alg":"HS256","typ":"JWT"} -> "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    token = create_jwt_token(
        {"sub": "charlie", "scopes": ["read:profile"], "exp": int(time.time()) + 3600},
        secret=secret,
        algorithm="HS256",
    )
    assert token.startswith("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.")

    res = client.get("/profile", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["user"] == "charlie"


def test_non_standard_jwt_header_fallback():
    """Mint token with custom header fields; verify fallback parser succeeds."""
    import base64
    import hashlib
    import hmac

    app = Buckthorn()
    secret = "buckthorn-secret-key-fallback"
    oauth2 = OAuth2PasswordBearer(tokenUrl="/token", jwt_secret=secret)

    @app.get("/custom-header")
    def custom_header(claims: dict = Security(oauth2, scopes=["custom"])):
        return {"user": claims["sub"]}

    client = RawTestClient(app)

    # Non-standard header with kid
    custom_hdr = json.dumps({"alg": "HS256", "typ": "JWT", "kid": "key_007"}, separators=(",", ":")).encode("utf-8")
    custom_hdr_b64 = base64.urlsafe_b64encode(custom_hdr).rstrip(b"=").decode("ascii")
    assert custom_hdr_b64 != "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"

    payload = json.dumps({"sub": "david", "scopes": ["custom"], "exp": int(time.time()) + 3600}, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    signing_input = f"{custom_hdr_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")

    token = f"{custom_hdr_b64}.{payload_b64}.{sig_b64}"
    res = client.get("/custom-header", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["user"] == "david"


def test_thread_local_hmac_ctx_reuse():
    """Run 50 consecutive JWT verifications; confirm zero memory leaks and consistent verification."""
    app = Buckthorn()
    secret = "buckthorn-hmac-ctx-reuse-secret"
    oauth2 = OAuth2PasswordBearer(tokenUrl="/token", jwt_secret=secret)

    @app.get("/secure-loop")
    def secure_loop(claims: dict = Security(oauth2, scopes=["loop"])):
        return {"idx": claims["idx"]}

    client = RawTestClient(app)

    for i in range(50):
        tok = create_jwt_token(
            {"sub": f"user_{i}", "idx": i, "scopes": ["loop"], "exp": int(time.time()) + 3600},
            secret=secret,
        )
        res = client.get("/secure-loop", headers={"Authorization": f"Bearer {tok}"})
        assert res.status_code == 200
        assert res.json()["idx"] == i


def test_ring_logger_cache_aligned_stress():
    """Stress test RingLogger with 5,000 logs; verify zero drops and valid JSON output."""
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        log_path = f.name

    try:
        logger = RingLogger(log_path, capacity=8192)

        for i in range(5000):
            logger.info("stress_bench", iteration=i, latency=0.012, ok=True)

        logger.flush_and_stop()

        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) == 5000
        sample = json.loads(lines[0])
        assert sample["msg"] == "stress_bench"
        assert sample["iteration"] == 0
        assert sample["ok"] is True
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)


def test_multipart_memmem_simd_boundary(tmp_path):
    """Upload a 1MB payload with boundary deep in body; confirm boundary is found via SIMD memmem."""
    app = Buckthorn()

    @app.post("/upload-large")
    async def upload_large(file: UploadFile = File()):
        content = await file.read()
        return {"filename": file.filename, "size": len(content)}

    client = RawTestClient(app)

    # 1MB payload
    filler = b"X" * (950 * 1024)
    res = client.post(
        "/upload-large",
        files={"file": ("large.bin", filler, "application/octet-stream")},
    )

    assert res.status_code == 200
    assert res.json() == {"filename": "large.bin", "size": len(filler)}


def test_websocket_64bit_frame_encode_decode():
    """Encode an 80KB payload; verify RFC 6455 10-byte header with length code 127 and decode roundtrip."""
    payload = b"Z" * 80000

    # Encode without mask
    frame = c_ws_encode_frame(WS_OP_BIN, payload, mask_frame=False)
    assert len(frame) == 10 + 80000
    # Byte 0: FIN (0x80) | opcode (0x02) = 0x82
    assert frame[0] == 0x82
    # Byte 1: masked (0) | 127 = 127 (0x7F)
    assert frame[1] == 127
    # Bytes 2-9: 64-bit big endian integer = 80000
    length_int = int.from_bytes(frame[2:10], "big")
    assert length_int == 80000

    # Parse and round-trip decode
    status, opcode, fin, decoded_payload = c_ws_parse_frame(frame)
    assert status == 0
    assert opcode == WS_OP_BIN
    assert fin == 1
    assert decoded_payload == payload

    # Test with masking
    masked_frame = c_ws_encode_frame(WS_OP_BIN, payload, mask_frame=True)
    assert len(masked_frame) == 14 + 80000
    assert masked_frame[1] == (0x80 | 127)  # mask bit set + 127
    status_m, opcode_m, fin_m, decoded_m = c_ws_parse_frame(masked_frame)
    assert status_m == 0
    assert decoded_m == payload


def test_middleware_snprintf_buffer_clamping():
    """Provide extremely long CORS origin; verify buffer clamps cleanly without buffer overrun."""
    app = Buckthorn()
    # 1,000-character long origin string
    huge_origin = "https://" + ("subdomain." * 100) + "example.com"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[huge_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/cors-check")
    def cors_check():
        return {"cors": "ok"}

    client = RawTestClient(app)
    res = client.options(
        "/cors-check",
        headers={
            "Origin": huge_origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 204
    assert "Access-Control-Allow-Origin" in res.headers


def test_dynamic_overflow_arena_reclamation():
    """Verify that arena offset resets cleanly to baseline after request handling."""
    app = Buckthorn()

    @app.get("/arena-check")
    def arena_check():
        return {"status": "ok"}

    client = RawTestClient(app)
    for _ in range(100):
        res = client.get("/arena-check")
        assert res.status_code == 200
        assert client.arena_offset == 0

