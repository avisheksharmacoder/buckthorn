# tests/unit_v2/test_phase1_python_surface.py
import asyncio
import tempfile
import pytest
from typing import Optional, Union

from buckthorn.app import Buckthorn
from buckthorn.exceptions import HTTPException
from buckthorn.logging import RingLogger
from buckthorn.responses import FileResponse
from buckthorn.schema import Schema, unwrap_type_annotation, get_schema_spec
from buckthorn.testing import RawTestClient


def test_optional_int_schema_field_resolves_to_type_int():
    """Verify Optional[int] is unwrapped from Union and maps to TYPE_INT (1), not TYPE_STR (3)."""
    class UserProfile(Schema):
        user_id: int
        age: Optional[int] = None

    spec = get_schema_spec(UserProfile)
    assert spec is not None
    fields = {f.name: f for f in spec.fields}

    assert fields["user_id"].field_type == 1  # TYPE_INT
    assert fields["user_id"].required is True

    assert fields["age"].field_type == 1  # TYPE_INT (previously defaulted to 3 TYPE_STR)
    assert fields["age"].required is False
    assert fields["age"].default_val is None


def test_optional_float_schema_field_resolves_to_type_float():
    """Verify Optional[float] maps to TYPE_FLOAT (2)."""
    class Measurement(Schema):
        score: Optional[float] = None

    spec = get_schema_spec(Measurement)
    assert spec is not None
    fields = {f.name: f for f in spec.fields}
    assert fields["score"].field_type == 2  # TYPE_FLOAT
    assert fields["score"].required is False


def test_unwrap_type_annotation_helper():
    """Directly test the unwrap_type_annotation helper across scalar and complex types."""
    assert unwrap_type_annotation(int) == (int, False)
    assert unwrap_type_annotation(Optional[int]) == (int, True)
    assert unwrap_type_annotation(Union[str, None]) == (str, True)
    assert unwrap_type_annotation(list[str]) == (list, False)
    assert unwrap_type_annotation(Optional[list[int]]) == (list, True)


def test_optional_query_param_resolves_correctly():
    """Verify route with limit: Optional[int] = 10 compiles as TYPE_INT."""
    app = Buckthorn()

    @app.get("/items")
    def get_items(limit: Optional[int] = 10, offset: Optional[int] = None):
        return {"limit": limit, "offset": offset}

    client = RawTestClient(app)
    # Default parameters
    res = client.send_raw(b"GET /items HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res.status_code == 200
    assert res.json() == {"limit": 10, "offset": None}

    # Explicit integer parameter
    res2 = client.send_raw(b"GET /items?limit=50&offset=5 HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert res2.status_code == 200
    assert res2.json() == {"limit": 50, "offset": 5}


def test_schema_optional_serialization_roundtrip():
    """Test full HTTP POST payload deserialization with optional schema fields."""
    app = Buckthorn()

    class ItemPayload(Schema):
        title: str
        count: Optional[int] = None

    @app.post("/items")
    def create_item(payload: ItemPayload):
        return {"title": payload.title, "count": payload.count}

    client = RawTestClient(app)
    # Missing optional count field
    res = client.send_raw(
        b"POST /items HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 17\r\n\r\n"
        b'{"title": "Widget"}'
    )
    assert res.status_code == 200
    assert res.json() == {"title": "Widget", "count": None}

    # Provided optional count field
    res2 = client.send_raw(
        b"POST /items HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 29\r\n\r\n"
        b'{"title": "Widget", "count": 7}'
    )
    assert res2.status_code == 200
    assert res2.json() == {"title": "Widget", "count": 7}


def test_lifespan_multi_step_async_io():
    """Verify that multi-step async lifespan (with real await asyncio.sleep) runs without cancellation."""
    state = {"started": False, "stopped": False}

    class AsyncLifespanContext:
        async def __aenter__(self):
            await asyncio.sleep(0.01)
            state["started"] = True
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await asyncio.sleep(0.01)
            state["stopped"] = True

    app = Buckthorn(lifespan=lambda app_inst: AsyncLifespanContext())

    with RawTestClient(app) as client:
        assert state["started"] is True
        assert state["stopped"] is False

    assert state["stopped"] is True


def test_coro_close_on_exception_in_lifespan():
    """Verify that an exception raised in lifespan properly aborts and propagates."""
    class FailingLifespan:
        async def __aenter__(self):
            await asyncio.sleep(0.001)
            raise ValueError("Failed database connection")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    app = Buckthorn(lifespan=lambda app_inst: FailingLifespan())
    with pytest.raises(ValueError, match="Failed database connection"):
        with RawTestClient(app):
            pass


def test_http_exception_slots_no_dict():
    """Verify HTTPException uses __slots__ so attributes are stored in descriptor slots, not in dynamic __dict__."""
    exc = HTTPException(status_code=400, detail="Bad Request")
    assert exc.__dict__ == {}
    assert "status_code" not in exc.__dict__
    assert "detail" not in exc.__dict__
    assert exc.status_code == 400
    assert exc.detail == "Bad Request"
    assert exc.headers is None


def test_http_exception_no_args_tuple_and_formatting():
    """Verify HTTPException avoids self.args tuple allocation and formats string correctly."""
    exc = HTTPException(status_code=404, detail="Resource Not Found")
    assert exc.args == ()
    assert str(exc) == "404: Resource Not Found"
    assert repr(exc) == "HTTPException(status_code=404, detail='Resource Not Found')"


def test_file_response_cached_headers():
    """Verify FileResponse caches its pre-rendered headers at instantiation."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp.write(b"Buckthorn v2 Static Content")
        tmp.flush()
        tmp_path = tmp.name

    resp = FileResponse(tmp_path, media_type="text/plain", filename="sample.txt")
    assert hasattr(resp, "_cached_hdr_bytes")
    assert b"Content-Type: text/plain\r\n" in resp._cached_hdr_bytes
    assert b'filename="sample.txt"\r\n' in resp._cached_hdr_bytes

    # Render response should reuse the cached buffer directly
    body, hdrs, status = resp._render_c_response()
    assert body == b"Buckthorn v2 Static Content"
    assert hdrs is resp._cached_hdr_bytes
    assert status == 200


def test_ring_logger_direct_method_binding():
    """Verify RingLogger binds Cython C-methods directly on the instance to eliminate wrapper frames."""
    logger = RingLogger()
    assert logger.info == logger._c_logger.info
    assert logger.debug == logger._c_logger.debug
    assert logger.warning == logger._c_logger.warning
    assert logger.error == logger._c_logger.error
    assert logger.flush_and_stop == logger._c_logger.flush_and_stop
    logger.flush_and_stop()


def test_raw_test_client_send_raw_binding():
    """Verify RawTestClient.send_raw is bound directly to self._client.send_raw."""
    app = Buckthorn()
    client = RawTestClient(app)
    assert client.send_raw == client._client.send_raw
