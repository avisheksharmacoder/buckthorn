# tests/phase2/test_params.py
import json
import time
import pytest
from buckthorn import Buckthorn, Request, Path, Query, Header, Cookie
from buckthorn.testing import RawTestClient


def test_typed_path_query_header_and_cookie_extraction():
    app = Buckthorn()

    @app.get("/users/{user_id}/items/{sku}")
    async def get_user_item(
        user_id: int = Path(ge=1),
        sku: str = Path(min_length=3, max_length=12),
        limit: int = Query(default=20, ge=1, le=100),
        discount: float = Query(default=0.0, ge=0.0, lt=1.0),
        x_api_key: str = Header(alias="X-Api-Key", min_length=5),
        session_id: str = Cookie(default="guest"),
    ):
        return f"{user_id}|{sku}|{limit}|{discount:.2f}|{x_api_key}|{session_id}".encode()

    client = RawTestClient(app)

    # 1. Full valid request with query params, custom header, and cookie
    res = client.get(
        "/users/42/items/BOOK-99?limit=50&discount=0.25",
        headers={
            "X-Api-Key": "secret-token",
            "Cookie": "theme=dark; session_id=xyz-987; lang=en",
        },
    )
    assert res.status_code == 200
    assert res.body == b"42|BOOK-99|50|0.25|secret-token|xyz-987"

    # 2. Default values applied in Cython when optional Query/Cookie params are omitted
    res_defaults = client.get(
        "/users/7/items/PEN-01",
        headers={"X-Api-Key": "valid-key"},
    )
    assert res_defaults.status_code == 200
    assert res_defaults.body == b"7|PEN-01|20|0.00|valid-key|guest"


def test_c_level_validation_rejects_invalid_types_and_bounds_with_422():
    app = Buckthorn()
    handler_called = False

    @app.get("/products/{product_id}")
    def get_product(
        product_id: int = Path(gt=0, le=10_000),
        q: str = Query(min_length=3, max_length=10),
        x_tenant: str = Header(alias="X-Tenant"),
    ):
        nonlocal handler_called
        handler_called = True
        return b"ok"

    client = RawTestClient(app)

    # Case 1: Non-integer path parameter -> 422 in C before handler runs
    res1 = client.get("/products/not-a-number?q=shoes", headers={"X-Tenant": "acme"})
    assert res1.status_code == 422
    assert not handler_called
    err1 = json.loads(res1.body)
    assert err1["detail"][0]["loc"] == ["path", "product_id"]

    # Case 2: Path integer violates `gt=0` -> 422 in C
    res2 = client.get("/products/0?q=shoes", headers={"X-Tenant": "acme"})
    assert res2.status_code == 422
    assert not handler_called

    # Case 3: Query string violates `min_length=3` -> 422 in C
    res3 = client.get("/products/100?q=ab", headers={"X-Tenant": "acme"})
    assert res3.status_code == 422
    assert not handler_called
    err3 = json.loads(res3.body)
    assert err3["detail"][0]["loc"] == ["query", "q"]

    # Case 4: Missing required Header -> 422 in C
    res4 = client.get("/products/100?q=shoes")
    assert res4.status_code == 422
    assert not handler_called
    err4 = json.loads(res4.body)
    assert err4["detail"][0]["loc"] == ["header", "X-Tenant"]


def test_zero_copy_request_object_lazy_access():
    app = Buckthorn()

    @app.post("/inspect/{region}")
    def inspect_request(req: Request, region: str):
        user_agent = req.get_header("user-agent") or "unknown"
        mode = req.get_query("mode") or "standard"
        body_len = len(req.body)
        return f"{req.method}:{req.path}:{region}:{user_agent}:{mode}:{body_len}".encode()

    client = RawTestClient(app)
    res = client.post(
        "/inspect/eu-west?mode=turbo",
        body=b'{"hello":"world"}',
        headers={"User-Agent": "BuckthornBench/1.0"},
    )
    assert res.status_code == 200
    assert res.body == b"POST:/inspect/eu-west:eu-west:BuckthornBench/1.0:turbo:17"


def test_phase2_parameter_extraction_speed_gate():
    app = Buckthorn()

    @app.get("/bench/{org_id}/users/{user_id}")
    def bench_route(
        org_id: int,
        user_id: int = Path(ge=1),
        page: int = Query(default=1, ge=1, le=1000),
        x_token: str = Header(alias="X-Token"),
    ):
        return b'{"status":"ok"}'

    client = RawTestClient(app)
    raw_req = (
        b"GET /bench/10/users/999?page=5 HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Token: super-secret\r\n\r\n"
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
    assert rps > 80_000, f"Expected >80,000 RPS with 4 typed C params, got {rps:,.0f} RPS"


def test_url_percent_decoding_and_null_byte_rejection():
    import pathlib

    app = Buckthorn()

    @app.get("/users/{email}")
    def get_user(
        email: str = Path(),
        query: str = Query(),
        session: str = Cookie(default="none"),
    ):
        return f"{email}|{query}|{session}".encode()

    client = RawTestClient(app)

    # 1. Valid percent-encoded path, query with %20 and '+', and cookie with %3D
    res = client.get(
        "/users/alice%40example.com?query=hello%20world%21+2026",
        headers={"Cookie": "session=token%3Dabc"},
    )
    assert res.status_code == 200
    assert res.body == b"alice@example.com|hello world! 2026|token=abc"

    # 2. Null byte %00 in path is rejected by C audit -> 400 Bad Request
    res_path_null = client.get(
        "/users/alice%00admin?query=valid",
        headers={"Cookie": "session=token"},
    )
    assert res_path_null.status_code == 400

    # 3. Null byte %00 in query string is rejected by C audit -> 400 Bad Request
    res_query_null = client.get(
        "/users/alice?query=bad%00val",
        headers={"Cookie": "session=token"},
    )
    assert res_query_null.status_code == 400

    # 4. Null byte %00 in cookie is rejected by bt_url_decode_slice -> falls back to default "none"
    res_cookie_null = client.get(
        "/users/alice?query=valid",
        headers={"Cookie": "session=bad%00token"},
    )
    assert res_cookie_null.status_code == 200
    assert res_cookie_null.body == b"alice|valid|none"

    # 5. Truncated %XX sequence in query is rejected by C audit (or decoder) -> 400
    res_trunc = client.get(
        "/users/alice?query=bad%2",
        headers={"Cookie": "session=token"},
    )
    assert res_trunc.status_code == 400

    # 6. Invalid hex in query is rejected by C audit (or decoder) -> 400
    res_inv = client.get(
        "/users/alice?query=bad%ZZ",
        headers={"Cookie": "session=token"},
    )
    assert res_inv.status_code == 400


def test_pep561_typing_assets():
    import pathlib

    pkg_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "buckthorn"
    py_typed = pkg_dir / "py.typed"
    core_pyi = pkg_dir / "_core.pyi"

    assert py_typed.exists(), "buckthorn/py.typed marker file must exist for PEP 561 compliance"
    assert core_pyi.exists(), "buckthorn/_core.pyi type stub file must exist"
    assert "class CoreRouter:" in core_pyi.read_text(encoding="utf-8")
    assert "class Request:" in core_pyi.read_text(encoding="utf-8")

