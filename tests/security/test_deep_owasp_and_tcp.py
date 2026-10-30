# tests/security/test_deep_owasp_and_tcp.py
import asyncio
import gc
import os
import signal
import socket
import time
import tracemalloc
import pytest

from buckthorn import (
    Buckthorn,
    Depends,
    Field,
    HTTPException,
    Query,
    Request,
    Schema,
    Security,
    UploadFile,
)
from buckthorn.security import HTTPBearer, create_jwt_token
from buckthorn.testing import RawTestClient


class AccountProfile(Schema):
    username: str = Field(..., min_length=3, max_length=32)
    age: int = Field(..., ge=0, le=150)
    balance: float = Field(0.0, ge=0.0, le=1_000_000.0)
    tags: list = Field(default=[])


def _build_torture_app() -> tuple[Buckthorn, dict[str, int]]:
    app = Buckthorn(title="Buckthorn Deep OWASP Torture", max_body_size=16384)
    bearer = HTTPBearer(jwt_secret="deep-owasp-secret-key-32-bytes!!")
    teardown_stats = {"opened": 0, "closed": 0}

    def tracked_db_session():
        teardown_stats["opened"] += 1
        try:
            yield "db_conn_active"
        finally:
            teardown_stats["closed"] += 1

    @app.post("/profiles", response_model=AccountProfile)
    def create_profile(profile: AccountProfile):
        # Intentionally mutate the list to test cross-request mutable default isolation
        profile.tags.append("mutated_in_handler")
        return profile

    @app.get("/calc")
    def calc_score(
        score: float = Query(..., ge=-100.0, le=100.0),
        count: int = Query(1, ge=1, le=1000),
    ):
        return {"score": score, "count": count}

    @app.post("/upload")
    async def upload_doc(doc: UploadFile):
        raw = await doc.read()
        return {"filename": doc.filename, "size": len(raw)}

    @app.get("/guarded")
    def guarded_route(
        fail: int = Query(0),
        db: str = Depends(tracked_db_session),
        claims: dict = Security(bearer, scopes=["admin"]),
    ):
        if fail == 1:
            raise HTTPException(409, detail="Simulated business conflict")
        if fail == 2:
            raise RuntimeError("Simulated unhandled crash inside route")
        return {"db": db, "sub": claims.get("sub")}

    @app.post("/async-echo")
    async def async_echo(req: Request, profile: AccountProfile):
        # Suspend on the event loop so libuv exercises BT_HANDLER_SUSPENDED
        await asyncio.sleep(0.005)
        trace_id = req.get_header("X-Trace-Id") or "missing"
        return {
            "username": profile.username,
            "age": profile.age,
            "trace_id": trace_id,
        }

    return app, teardown_stats


# ---------------------------------------------------------------------------
# Suite 1: RFC 8259 JSON Torture (Depth Bombs, \u0000, UINT64_MAX, Surrogates)
# ---------------------------------------------------------------------------
def test_rfc8259_json_torture_vectors():
    app, _ = _build_torture_app()
    with RawTestClient(app) as client:
        # 1. Deep nesting bomb (> 64 levels) -> rejected in C with 422, zero stack overflow
        nested_bomb = (
            b'{"username":"alice","age":30,"tags":'
            + (b"[" * 100)
            + b'"x"'
            + (b"]" * 100)
            + b"}"
        )
        res_bomb = client.post(
            "/profiles",
            body=nested_bomb,
            headers={"Content-Type": "application/json"},
        )
        assert res_bomb.status_code == 422

        # 2. Embedded \u0000 null byte inside string -> rejected with 422
        null_in_str = b'{"username":"admin\\u0000spoof","age":30,"tags":[]}'
        res_null = client.post(
            "/profiles",
            body=null_in_str,
            headers={"Content-Type": "application/json"},
        )
        assert res_null.status_code == 422

        # 3. Unpaired UTF-16 high surrogate (\uD800) -> rejected with 422
        bad_surrogate = b'{"username":"alice\\uD800","age":30,"tags":[]}'
        res_surr = client.post(
            "/profiles",
            body=bad_surrogate,
            headers={"Content-Type": "application/json"},
        )
        assert res_surr.status_code == 422

        # 4. UINT64_MAX (18446744073709551615) -> must NOT wrap around to -1!
        uint64_wrap = b'{"username":"alice","age":18446744073709551615,"tags":[]}'
        res_u64 = client.post(
            "/profiles",
            body=uint64_wrap,
            headers={"Content-Type": "application/json"},
        )
        assert res_u64.status_code == 422

        # 5. Out-of-order JSON keys -> yyjson_obj_iter_getn must wrap around and succeed
        reversed_keys = b'{"tags":["v1"],"balance":50.5,"age":28,"username":"reverse_ok"}'
        res_ok = client.post(
            "/profiles",
            body=reversed_keys,
            headers={"Content-Type": "application/json"},
        )
        assert res_ok.status_code == 200
        data = res_ok.json()
        assert data["username"] == "reverse_ok"
        assert data["age"] == 28
        assert data["tags"] == ["v1", "mutated_in_handler"]


# ---------------------------------------------------------------------------
# Suite 2: Parameter Coercion, Multipart Substring Collision & Default Isolation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_query",
    [
        "/calc?score=nan&count=1",
        "/calc?score=NaN&count=1",
        "/calc?score=-nan&count=1",
        "/calc?score=inf&count=1",
        "/calc?score=-inf&count=1",
        "/calc?score=infinity&count=1",
        "/calc?score=50.0&count=9223372036854775808",  # INT64_MAX + 1
        "/calc?score=50.0&count=-9223372036854775809", # INT64_MIN - 1
    ],
)
def test_numeric_nan_inf_and_int64_overflow_rejected(bad_query: str):
    app, _ = _build_torture_app()
    with RawTestClient(app) as client:
        resp = client.get(bad_query)
        assert resp.status_code == 422


def test_multipart_filename_substring_collision_and_control_chars():
    app, _ = _build_torture_app()
    boundary = "BuckthornTortureBoundary"
    with RawTestClient(app) as client:
        # 1. `filename="doc"` placed BEFORE `name="wrong_field"` must NOT match `doc`!
        spoofed_body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; filename="doc"; name="wrong_field"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
            f"spoofed-content\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        res_spoof = client.post(
            "/upload",
            body=spoofed_body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert res_spoof.status_code == 422

        # 2. Control characters inside filename="evil\x07.txt" are rejected/stripped
        ctrl_fn_body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="doc"; filename="evil\x07.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
            f"hello\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        res_ctrl = client.post(
            "/upload",
            body=ctrl_fn_body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert res_ctrl.status_code == 200
        assert res_ctrl.json()["filename"] == ""


def test_mutable_default_list_isolation_across_requests():
    app, _ = _build_torture_app()
    with RawTestClient(app) as client:
        # Request 1 omits `tags` (uses default `[]`) and appends `"mutated_in_handler"`
        r1 = client.post("/profiles", json={"username": "user1", "age": 20})
        assert r1.status_code == 200
        assert r1.json()["tags"] == ["mutated_in_handler"]

        # Request 2 also omits `tags` -> must start with a fresh `[]`, NOT `["mutated_in_handler", ...]`!
        r2 = client.post("/profiles", json={"username": "user2", "age": 22})
        assert r2.status_code == 200
        assert r2.json()["tags"] == ["mutated_in_handler"]


# ---------------------------------------------------------------------------
# Suite 3: Cython Freelist, Refcount & Generator Teardown Leak Audit
# ---------------------------------------------------------------------------
def test_zero_memory_and_generator_leak_under_error_flood():
    app, teardown_stats = _build_torture_app()
    secret = "deep-owasp-secret-key-32-bytes!!"
    valid_token = create_jwt_token(
        {"sub": "auditor", "exp": int(time.time()) + 3600, "scopes": ["admin"]},
        secret,
    )
    auth_hdr = {"Authorization": f"Bearer {valid_token}"}

    with RawTestClient(app) as client:
        # Warmup Cython freelists and TLS pools
        for _ in range(100):
            client.get("/guarded?fail=0", headers=auth_hdr)
            client.get("/guarded?fail=1", headers=auth_hdr)
            client.get("/guarded?fail=2", headers=auth_hdr)
            client.get("/calc?score=not_a_float")

        gc.collect()
        tracemalloc.start()
        snap_before = tracemalloc.take_snapshot()

        # Flood 3,000 mixed error and generator-unwinding requests
        for _ in range(750):
            assert client.get("/guarded?fail=0", headers=auth_hdr).status_code == 200
            assert client.get("/guarded?fail=1", headers=auth_hdr).status_code == 409
            assert client.get("/guarded?fail=2", headers=auth_hdr).status_code == 500
            assert client.get("/guarded?fail=0").status_code == 401

        gc.collect()
        snap_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        # 1. Every single generator dependency that opened MUST have closed (even on 409 and 500!)
        assert teardown_stats["opened"] == teardown_stats["closed"]
        assert teardown_stats["closed"] >= 750 * 3

        # 2. Net Python heap diff after 3,000 error cycles must be negligible (< 32 KB)
        stats = snap_after.compare_to(snap_before, "lineno")
        net_bytes = sum(s.size_diff for s in stats)
        assert net_bytes < 32 * 1024, f"Potential Cython PyObject leak detected: +{net_bytes} bytes"
        assert client.arena_offset == 0


# ---------------------------------------------------------------------------
# Suite 4: Live libuv TCP Packet Fragmentation & Concurrent Async Suspension
# ---------------------------------------------------------------------------
def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_live_libuv_tcp_packet_fragmentation_and_concurrent_async():
    app, _ = _build_torture_app()
    port = _find_free_port()

    pid = os.fork()
    if pid == 0:
        # Run live C libuv server in child process
        app.run(host="127.0.0.1", port=port)
        os._exit(0)

    try:
        # Wait for libuv socket to accept connections
        connected = False
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    connected = True
                    break
            except OSError:
                time.sleep(0.02)
        assert connected, "libuv server failed to start on ephemeral port"

        # Part A: Send a POST request split across 3 separate TCP packets
        body_bytes = b'{"username":"frag_user","age":42,"balance":10.5,"tags":["tcp"]}'
        raw_req = (
            f"POST /async-echo HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"X-Trace-Id: trace-frag-99\r\n"
            f"Content-Length: {len(body_bytes)}\r\n\r\n"
        ).encode("ascii") + body_bytes

        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # Packet 1: Partial HTTP headers (triggers phr_parse_request == -2)
            sock.sendall(raw_req[:35])
            time.sleep(0.015)
            # Packet 2: Rest of headers + first 15 bytes of JSON body (avail_body < Content-Length)
            sock.sendall(raw_req[35 : len(raw_req) - 25])
            time.sleep(0.015)
            # Packet 3: Remaining 25 bytes of JSON body
            sock.sendall(raw_req[len(raw_req) - 25 :])

            wire_resp = sock.recv(4096)
            assert b"HTTP/1.1 200 OK\r\n" in wire_resp
            assert b'"username":"frag_user"' in wire_resp
            assert b'"trace_id":"trace-frag-99"' in wire_resp

        # Part B: Fire 16 concurrent suspended async TCP requests to verify arena isolation
        async def _fire_one(idx: int) -> None:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            b_payload = f'{{"username":"user_{idx}","age":{idx}}}'.encode("utf-8")
            req_bytes = (
                f"POST /async-echo HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/json\r\n"
                f"X-Trace-Id: tid-{idx}\r\n"
                f"Content-Length: {len(b_payload)}\r\n\r\n"
            ).encode("ascii") + b_payload
            writer.write(req_bytes)
            await writer.drain()
            data = await reader.read(4096)
            writer.close()
            await writer.wait_closed()
            assert b"HTTP/1.1 200 OK" in data
            assert f'"username":"user_{idx}"'.encode() in data
            assert f'"trace_id":"tid-{idx}"'.encode() in data

        async def _run_concurrent_batch() -> None:
            await asyncio.gather(*[_fire_one(i) for i in range(1, 17)])

        asyncio.run(_run_concurrent_batch())

        # Part C: HTTP/1.1 Pipelining (Sync GET -> Suspended Async POST -> Sync GET in 1 packet)
        pipe_body = b'{"username":"pipelined_async","age":33}'
        pipelined_stream = (
            b"GET /calc?score=12.5&count=3 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            + (
                f"POST /async-echo HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/json\r\n"
                f"X-Trace-Id: pipe-2\r\n"
                f"Content-Length: {len(pipe_body)}\r\n\r\n"
            ).encode("ascii")
            + pipe_body
            + b"GET /calc?score=99.0&count=7 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )

        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            sock.sendall(pipelined_stream)
            collected = b""
            while collected.count(b"HTTP/1.1 200 OK") < 3:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                collected += chunk

            assert collected.count(b"HTTP/1.1 200 OK") == 3
            assert b'"score":12.5' in collected
            assert b'"username":"pipelined_async"' in collected
            assert b'"trace_id":"pipe-2"' in collected
            assert b'"score":99.0' in collected

    finally:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except OSError:
            pass
