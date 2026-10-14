# tests/phase12/test_fuzz_parsers.py
import struct
import pytest
from hypothesis import given, settings, HealthCheck, strategies as st
from buckthorn import (
    Buckthorn,
    Request,
    Schema,
    Field,
    Path,
    Query,
    Header,
    Cookie,
    Security,
    UploadFile,
    File,
    WebSocket,
)
from buckthorn.security import HTTPBearer
from buckthorn.testing import RawTestClient


class FuzzOrderSchema(Schema):
    sku: str = Field(min_length=2, max_length=32)
    qty: int = Field(ge=1, le=10_000)
    price: float = Field(gt=0.0, le=1_000_000.0)
    active: bool = True


def _build_fuzz_target_app() -> Buckthorn:
    app = Buckthorn(max_body_size=8192)
    bearer = HTTPBearer(jwt_secret="fuzz-secret-key-32-bytes-long!!")

    @app.get("/items/{item_id}")
    def get_item(
        item_id: int = Path(ge=1, le=999_999),
        q: str = Query(default="all", min_length=1, max_length=64),
        x_tag: str = Header(alias="X-Tag", default="none"),
        sid: str = Cookie(default="anon"),
    ):
        return {"item_id": item_id, "q": q, "x_tag": x_tag, "sid": sid}

    @app.post("/orders")
    def create_order(order: FuzzOrderSchema):
        return order

    @app.get("/vault")
    def vault(claims: dict = Security(bearer, scopes=["read"])):
        return {"sub": claims.get("sub")}

    @app.post("/upload")
    async def upload(doc: UploadFile = File()):
        data = await doc.read()
        return {"filename": doc.filename, "size": len(data)}

    @app.websocket("/ws")
    async def ws_echo(ws: WebSocket):
        await ws.accept()
        msg = await ws.receive_text()
        await ws.send_text(msg)

    return app


def test_deterministic_64bit_integer_overflow_regressions():
    """
    Explicit regression tests for the 64-bit size_t wrap-around vulnerabilities
    in Content-Length parsing and RFC 6455 WebSocket 64-bit frame headers.
    """
    app = _build_fuzz_target_app()
    client = RawTestClient(app)

    # 1. Content-Length = 2^64 + 2 ("18446744073709551618") wraps size_t to 2 if unguarded!
    overflow_cl_req = (
        b"POST /orders HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 18446744073709551618\r\n\r\n"
        b"{}"
    )
    res_cl = client.send_raw(overflow_cl_req)
    assert res_cl.status_code in (400, 413)
    assert client.arena_offset == 0

    # 2. WebSocket 64-bit extended payload_len = 0xFFFFFFFFFFFFFFFC (wraps `pos + payload_len` to 10!)
    ws = WebSocket(path="/ws")
    malicious_ws_frame = (
        b"\x81\xfe"  # Initial valid check
    )
    # Opcode=0x81 (FIN+TEXT), Mask+Len=0xFF (Masked + 127), 8-byte len = 0xFFFFFFFFFFFFFFFC, 4-byte mask
    wraparound_ws_frame = b"\x81\xff" + struct.pack(">Q", 0xFFFFFFFFFFFFFFFC) + b"\x11\x22\x33\x44"
    with pytest.raises(ValueError):
        ws.feed_raw_frame(wraparound_ws_frame)

    # 3. Non-UTF8 raw bytes in URL query parameter must return 400/422, NEVER 500!
    bad_utf8_req = (
        b"GET /items/10?q=\xff\xfe\xfd HTTP/1.1\r\n"
        b"Host: localhost\r\n\r\n"
    )
    res_utf8 = client.send_raw(bad_utf8_req)
    assert res_utf8.status_code in (400, 422)
    assert client.arena_offset == 0


@settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(raw_bytes=st.binary(min_size=0, max_size=4096))
def test_fuzz_arbitrary_raw_tcp_bytes_never_crash_or_leak_arena(raw_bytes: bytes):
    app = _build_fuzz_target_app()
    client = RawTestClient(app)

    res = client.send_raw(raw_bytes)
    assert res.status_code in (200, 204, 400, 401, 403, 404, 413, 414, 422)
    assert client.arena_offset == 0


@settings(max_examples=250, deadline=None)
@given(
    method=st.sampled_from([b"GET", b"POST", b"PUT", b"DELETE", b"OPTIONS", b"PATCH", b"INVALID"]),
    path=st.binary(min_size=1, max_size=256),
    hdr_name=st.binary(min_size=1, max_size=64),
    hdr_val=st.binary(min_size=0, max_size=256),
    body=st.binary(min_size=0, max_size=1024),
)
def test_fuzz_structured_http_wire_mutations(
    method: bytes, path: bytes, hdr_name: bytes, hdr_val: bytes, body: bytes
):
    app = _build_fuzz_target_app()
    client = RawTestClient(app)

    wire = (
        method + b" " + path + b" HTTP/1.1\r\n"
        b"Host: localhost\r\n" +
        hdr_name + b": " + hdr_val + b"\r\n" +
        f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") +
        body
    )
    res = client.send_raw(wire)
    # Zero-500 invariant: mutated client requests must never trigger an unhandled 500
    assert res.status_code != 500
    assert client.arena_offset == 0


@settings(max_examples=250, deadline=None)
@given(
    json_payload=st.recursive(
        st.none()
        | st.booleans()
        | st.integers(min_value=-10**25, max_value=10**25)
        | st.floats(allow_nan=False, allow_infinity=False)
        | st.text(max_size=100),
        lambda children: st.lists(children, max_size=10)
        | st.dictionaries(st.text(max_size=20), children, max_size=10),
        max_leaves=15,
    )
)
def test_fuzz_yyjson_schema_validator_with_arbitrary_json_trees(json_payload):
    app = _build_fuzz_target_app()
    client = RawTestClient(app)

    res = client.post("/orders", json=json_payload)
    assert res.status_code in (200, 400, 413, 422)
    assert client.arena_offset == 0


@settings(max_examples=250, deadline=None)
@given(ws_bytes=st.binary(min_size=0, max_size=512))
def test_fuzz_websocket_rfc6455_c_frame_parser(ws_bytes: bytes):
    ws = WebSocket(path="/ws")
    try:
        ws.feed_raw_frame(ws_bytes)
    except (ValueError, UnicodeDecodeError):
        pass
