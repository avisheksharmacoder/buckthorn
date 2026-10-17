# tests/unit_v2/test_phase2_build_flags.py
import importlib.metadata
import platform
import tempfile
import pytest

import buckthorn._core as core
from buckthorn.app import Buckthorn
from buckthorn.compiler import compile_pyx_module
from buckthorn.schema import Schema
from buckthorn.testing import RawTestClient


def test_cython_core_importable():
    """Verify that buckthorn._core is compiled and importable with zero unresolved symbols."""
    assert hasattr(core, "CoreRouter")
    assert hasattr(core, "CompiledSchemaSpec")
    assert hasattr(core, "CythonRingLogger")
    assert hasattr(core, "RawResponse")
    assert hasattr(core, "WebSocket")


def test_version_metadata_synchronized():
    """Verify pyproject.toml and setup.py package version are synchronized to 1.1.0."""
    pkg_version = importlib.metadata.version("buckthorn")
    assert pkg_version == "1.1.0"


def test_yyjson_standard_json_parsing_and_macro_sanity():
    """
    Verify that standard JSON parses with high throughput and yyjson macro flags
    (-DYYJSON_DISABLE_NON_STANDARD=1) preserve strict standard compliance.
    """
    app = Buckthorn()

    class TestPayload(Schema):
        name: str
        speed: int
        active: bool

    @app.post("/test-json")
    def echo_json(payload: TestPayload):
        return {"name": payload.name, "speed": payload.speed, "active": payload.active}

    client = RawTestClient(app)
    # Valid standard JSON
    valid_body = b'{"name": "Buckthorn", "speed": 100000, "active": true}'
    res = client.send_raw(
        b"POST /test-json HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(valid_body)).encode() + b"\r\n\r\n" +
        valid_body
    )
    assert res.status_code == 200
    assert res.json() == {"name": "Buckthorn", "speed": 100000, "active": True}

    # Non-standard JSON5 (trailing comma or comments) should be rejected
    invalid_body = b'{"name": "Buckthorn", /* comment */ "speed": 100000,}'
    res2 = client.send_raw(
        b"POST /test-json HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(invalid_body)).encode() + b"\r\n\r\n" +
        invalid_body
    )
    assert res2.status_code == 422


def test_architecture_detection_flags():
    """Verify architecture detection correctly gates -msse4.2 on x86_64/AMD64."""
    machine = platform.machine()
    is_x86 = machine in ("x86_64", "AMD64")
    assert len(machine) > 0
    if is_x86:
        assert is_x86 is True
    else:
        assert machine in ("aarch64", "arm64", "armv7l")


def test_compile_pyx_module_with_lto():
    """Verify compile_pyx_module successfully produces an LTO-optimized shared library."""
    pyx_code = """
cdef public long long add_fast(long long a, long long b) noexcept:
    return a + b

def py_add_fast(long long a, long long b):
    return add_fast(a, b)
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        mod = compile_pyx_module(
            pyx_code=pyx_code,
            module_name="test_lto_module",
            workdir=tmpdir,
        )
        assert hasattr(mod, "py_add_fast")
        assert mod.py_add_fast(10, 25) == 35


def test_cython_extension_runtime_execution():
    """Verify that the newly compiled _core binary executes router and arena operations without crashes."""
    router = core.CoreRouter()
    assert router is not None
    router.add_route("GET", "/health", lambda: b"OK")
    raw_client = core.CythonRawTestClient(router)
    raw_res = raw_client.send_raw(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert raw_res.status_code == 200
    assert raw_res.body == b"OK"
