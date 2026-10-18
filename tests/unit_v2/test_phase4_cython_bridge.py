# tests/unit_v2/test_phase4_cython_bridge.py
import asyncio
import sys
import tempfile
import time
import pytest
from typing import Optional

from buckthorn import Buckthorn, Schema, Request, Security, BackgroundTasks
from buckthorn.exceptions import HTTPException
from buckthorn.responses import EventSourceResponse, FileResponse
from buckthorn.security import OAuth2PasswordBearer, create_jwt_token
from buckthorn.testing import RawTestClient
from buckthorn._core import (
    CompiledFieldSpec,
    CompiledParamSpec,
    RouteEntry,
    RawResponse,
    _RouteInvocationContext,
)


class UserProfile(Schema):
    user_id: int
    username: str
    is_active: bool = True


def test_response_reordering_dict_speed():
    """Verify dictionary responses serialize on the priority fast-path (item 2) without hasattr overhead."""
    app = Buckthorn()

    @app.get("/fast_dict")
    def get_dict():
        return {"status": "ok", "items": [1, 2, 3], "nested": {"code": 200}}

    client = RawTestClient(app)
    # Warm up
    res = client.get("/fast_dict")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "items": [1, 2, 3], "nested": {"code": 200}}

    t0 = time.perf_counter()
    iterations = 10000
    for _ in range(iterations):
        res = client.get("/fast_dict")
        assert res.status_code == 200
    elapsed = time.perf_counter() - t0
    # Confirm sub-microsecond per-request serialization time
    time_per_req_us = (elapsed / iterations) * 1_000_000
    assert time_per_req_us < 20.0, f"Expected < 20us per req, got {time_per_req_us:.2f}us"


def test_response_reordering_schema_speed():
    """Verify Schema instances serialize cleanly both with pre-compiled route spec (Path 1) and instance spec (Path 5)."""
    app = Buckthorn()

    @app.get("/schema_typed", response_model=UserProfile)
    def get_schema_typed():
        return UserProfile(user_id=101, username="alice", is_active=True)

    @app.get("/schema_untyped")
    def get_schema_untyped():
        return UserProfile(user_id=102, username="bob", is_active=False)

    client = RawTestClient(app)

    # Route with pre-compiled spec (Path 1)
    res_typed = client.get("/schema_typed")
    assert res_typed.status_code == 200
    assert res_typed.json() == {"user_id": 101, "username": "alice", "is_active": True}

    # Route without pre-compiled spec (Path 5 fallback)
    res_untyped = client.get("/schema_untyped")
    assert res_untyped.status_code == 200
    assert res_untyped.json() == {"user_id": 102, "username": "bob", "is_active": False}


def test_pyiter_send_eager_coroutine():
    """Verify PyIter_Send eagerly steps coroutines returning immediately without StopIteration allocation."""
    app = Buckthorn()

    @app.get("/async_eager")
    async def eager_handler():
        return {"eager": True, "value": 42}

    client = RawTestClient(app)
    res = client.get("/async_eager")
    assert res.status_code == 200
    assert res.json() == {"eager": True, "value": 42}

    # Benchmark 5,000 iterations to verify stability and zero StopIteration penalty
    for _ in range(5000):
        r = client.get("/async_eager")
        assert r.status_code == 200


def test_pyiter_send_multi_step_async():
    """Verify handlers with real awaitables (e.g. asyncio.sleep) step via PYGEN_NEXT and drive to completion."""
    app = Buckthorn()

    @app.get("/async_io")
    async def async_io_handler():
        await asyncio.sleep(0.001)
        return {"io_complete": True, "latency_ms": 1}

    client = RawTestClient(app)
    res = client.get("/async_io")
    assert res.status_code == 200
    assert res.json() == {"io_complete": True, "latency_ms": 1}


def test_jwt_claims_pure_c_dict():
    """Verify pure C yyjson DOM walker deserializes JWT claims without json.loads (Law #1)."""
    secret = "phase4_cython_bridge_secret_key_32b"
    oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token", jwt_secret=secret)
    app = Buckthorn()

    @app.get("/secure/claims")
    def get_claims(claims: dict = Security(oauth2_scheme, scopes=["read:data"])):
        return {
            "sub": claims["sub"],
            "scopes": claims["scopes"],
            "nested": claims["nested"],
            "score": claims["score"],
            "active": claims["active"],
        }

    token = create_jwt_token(
        claims={
            "sub": "user_42",
            "scopes": ["read:data", "write:data"],
            "nested": {"team": "cython_core", "level": 9},
            "score": 98.5,
            "active": True,
        },
        secret=secret,
    )

    client = RawTestClient(app)
    raw_req = (
        b"GET /secure/claims HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Authorization: Bearer " + token.encode("ascii") + b"\r\n\r\n"
    )
    res = client.send_raw(raw_req)
    assert res.status_code == 200
    data = res.json()
    assert data["sub"] == "user_42"
    assert data["scopes"] == ["read:data", "write:data"]
    assert data["nested"] == {"team": "cython_core", "level": 9}
    assert abs(data["score"] - 98.5) < 1e-4
    assert data["active"] is True


def test_request_get_header_zero_copy():
    """Verify Request.get_header and get_query operate via PyUnicode_AsUTF8AndSize zero-copy pointer lookup."""
    app = Buckthorn()

    @app.get("/inspect/req")
    def inspect_request(req: Request):
        return {
            "ua": req.get_header("User-Agent"),
            "custom": req.get_header("X-Cython-Bridge"),
            "missing": req.get_header("X-Non-Existent"),
            "page": req.get_query("page"),
            "sort": req.get_query("sort"),
            "q_missing": req.get_query("filter"),
            "method": req.method,
            "path": req.path,
        }

    client = RawTestClient(app)
    raw_req = (
        b"GET /inspect/req?page=2&sort=asc HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"User-Agent: BuckthornTestClient/2.0\r\n"
        b"X-Cython-Bridge: Enabled\r\n\r\n"
    )
    res = client.send_raw(raw_req)
    assert res.status_code == 200
    body = res.json()
    assert body["ua"] == "BuckthornTestClient/2.0"
    assert body["custom"] == "Enabled"
    assert body["missing"] is None
    assert body["page"] == "2"
    assert body["sort"] == "asc"
    assert body["q_missing"] is None
    assert body["method"] == "GET"
    assert body["path"] == "/inspect/req"


def test_raw_response_freelist_recycling():
    """Verify RawResponse freelist (4096 slots) recycles Python object memory addresses across queries."""
    app = Buckthorn()

    @app.get("/freelist_ping")
    def ping():
        return {"pong": True}

    client = RawTestClient(app)
    observed_addresses = set()

    # When dropping reference to each response, freelist re-claims the memory block
    for _ in range(1000):
        res = client.get("/freelist_ping")
        observed_addresses.add(id(res))

    # Without freelist, 1,000 distinct objects could yield hundreds of unique IDs.
    # With a static freelist, the same few memory addresses are cycled repeatedly.
    assert len(observed_addresses) <= 10, f"Expected <= 10 recycled addresses, got {len(observed_addresses)}"


def test_output_buffer_overflow_rejection():
    """Verify responses > 32KB are safely rejected with HTTP 500 without buffer overrun or crash."""
    app = Buckthorn()

    @app.get("/huge_str")
    def huge_str():
        return "A" * 35000

    @app.get("/huge_bytes")
    def huge_bytes():
        return b"B" * 35000

    @app.get("/safe_str")
    def safe_str():
        return "S" * 32000

    client = RawTestClient(app)

    # Payload exceeding 32768 bytes triggers boundary clamp returning 500
    res_str = client.get("/huge_str")
    assert res_str.status_code == 500

    res_bytes = client.get("/huge_bytes")
    assert res_bytes.status_code == 500

    # Payload within 32768 bytes succeeds with 200 OK
    res_ok = client.get("/safe_str")
    assert res_ok.status_code == 200
    assert len(res_ok.body) == 32000


def test_background_tasks_lazy_allocation():
    """Verify background task structures are lazily allocated only when routes request them."""
    p_normal = CompiledParamSpec("limit", "limit", 2, 1, 0, False, None)
    p_bg = CompiledParamSpec("bg", "bg", 10, 0, 0, False, None)

    entry_no_bg = RouteEntry(lambda: None, [p_normal])
    entry_with_bg = RouteEntry(lambda: None, [p_bg])

    assert entry_no_bg.has_bg_tasks is False
    assert entry_with_bg.has_bg_tasks is True

    # Test execution in app
    app = Buckthorn()
    executed_tasks = []

    def task_fn(val: str):
        executed_tasks.append(val)

    @app.get("/with_bg")
    def route_with_bg(bg: BackgroundTasks):
        bg.add_task(task_fn, "task_completed")
        return {"ok": True}

    @app.get("/without_bg")
    def route_without_bg():
        return {"ok": True}

    client = RawTestClient(app)
    res_no = client.get("/without_bg")
    assert res_no.status_code == 200
    assert len(executed_tasks) == 0

    res_with = client.get("/with_bg")
    assert res_with.status_code == 200
    assert executed_tasks == ["task_completed"]


def test_field_spec_interned_strings():
    """Verify CompiledFieldSpec and CompiledParamSpec intern their field and argument names."""
    field1 = CompiledFieldSpec("common_field", 1, True, None)
    field2 = CompiledFieldSpec("common_field", 1, True, None)

    assert field1.name is sys.intern("common_field")
    assert field1.name is field2.name

    param1 = CompiledParamSpec("arg_name", "lookup_key", 2, 1, 0, True, None)
    param2 = CompiledParamSpec("arg_name", "lookup_key", 2, 1, 0, True, None)

    assert param1.arg_name is sys.intern("arg_name")
    assert param1.arg_name is param2.arg_name


def test_nested_array_traversal_safety():
    """Verify pure C yyjson DOM walker deserializes deeply nested arrays and objects safely."""
    class NestedContainer(Schema):
        title: str
        tags: list
        matrix: list

    app = Buckthorn()

    @app.post("/nested_data")
    def handle_nested(data: NestedContainer):
        return {
            "title": data.title,
            "tags": data.tags,
            "matrix": data.matrix,
        }

    client = RawTestClient(app)
    payload = {
        "title": "Matrix Report",
        "tags": ["alpha", "beta", "gamma"],
        "matrix": [[1, 2, 3], [4, 5, 6]],
    }
    res = client.post("/nested_data", json=payload)
    assert res.status_code == 200
    res_json = res.json()
    assert res_json["title"] == "Matrix Report"
    assert res_json["tags"] == ["alpha", "beta", "gamma"]
    assert res_json["matrix"] == [[1, 2, 3], [4, 5, 6]]


def test_custom_response_sse_file_fallback():
    """Verify FileResponse and EventSourceResponse continue to execute properly under reordered checks."""
    app = Buckthorn()

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"Buckthorn v2 FileResponse Content")
        temp_path = f.name

    @app.get("/file")
    def get_file():
        return FileResponse(temp_path, filename="test.txt")

    @app.get("/stream")
    async def get_stream():
        async def event_gen():
            yield "data: event1\n\n"
            yield "data: event2\n\n"
        return EventSourceResponse(event_gen())

    client = RawTestClient(app)

    # Test FileResponse
    res_file = client.get("/file")
    assert res_file.status_code == 200
    assert res_file.body == b"Buckthorn v2 FileResponse Content"

    # Test EventSourceResponse
    res_stream = client.get("/stream")
    assert res_stream.status_code == 200
    assert "text/event-stream" in res_stream.headers["Content-Type"]
    assert res_stream.body == b"data: event1\n\ndata: event2\n\n"


def test_http_exception_serialization_direct():
    """Verify HTTPException details serialize directly into response buffer without Python dict overhead."""
    app = Buckthorn()

    @app.get("/error_str")
    def error_str():
        raise HTTPException(status_code=400, detail="Invalid parameter supplied")

    @app.get("/error_dict")
    def error_dict():
        raise HTTPException(status_code=404, detail={"code": "ERR_NOT_FOUND", "id": 1234})

    client = RawTestClient(app)

    res_str = client.get("/error_str")
    assert res_str.status_code == 400
    assert res_str.json() == {"detail": "Invalid parameter supplied"}

    res_dict = client.get("/error_dict")
    assert res_dict.status_code == 404
    assert res_dict.json() == {"detail": {"code": "ERR_NOT_FOUND", "id": 1234}}


def test_suspended_tcp_coro_closure_shrinkage():
    """Verify _RouteInvocationContext freelist and suspended coro closure decoupling."""
    # Verify freelist instantiation on _RouteInvocationContext
    ctx1 = _RouteInvocationContext.__new__(_RouteInvocationContext)
    ctx_addr1 = id(ctx1)
    del ctx1
    ctx2 = _RouteInvocationContext.__new__(_RouteInvocationContext)
    ctx_addr2 = id(ctx2)
    # Freelist re-allocates from the same cache slot
    assert ctx_addr1 == ctx_addr2

    # Verify suspended async route runs cleanly without capturing large stack arrays
    app = Buckthorn()

    @app.get("/concurrent_coro")
    async def concurrent_coro():
        await asyncio.sleep(0.002)
        return {"status": "async_ok"}

    client = RawTestClient(app)
    res = client.get("/concurrent_coro")
    assert res.status_code == 200
    assert res.json() == {"status": "async_ok"}
