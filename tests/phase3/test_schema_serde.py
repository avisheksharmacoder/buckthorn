# tests/phase3/test_schema_serde.py
import time
import pytest
from buckthorn import Buckthorn, Schema, Field, Path, Query, status
from buckthorn.testing import RawTestClient


class CreateProduct(Schema):
    sku: str = Field(min_length=3, max_length=16)
    price: float = Field(gt=0.0, le=100_000.0)
    qty: int = Field(default=1, ge=1, le=1000)
    in_stock: bool = True
    tags: list[str] = []


class ProductPublic(Schema):
    sku: str
    price: float
    qty: int
    tags: list[str]


def test_single_pass_json_validation_and_response_model_filtering():
    app = Buckthorn()

    @app.post("/products", response_model=ProductPublic, status_code=status.HTTP_201_CREATED)
    async def create_product(prod: CreateProduct):
        # Handler returns CreateProduct (which includes `in_stock`);
        # C response_model serializer must strip `in_stock` automatically
        return prod

    client = RawTestClient(app)

    # 1. Full valid payload
    res = client.post(
        "/products",
        json={
            "sku": "BK-900",
            "price": 49.95,
            "qty": 5,
            "in_stock": False,
            "tags": ["compiled", "cython"],
        },
    )
    assert res.status_code == 201
    assert res.json() == {
        "sku": "BK-900",
        "price": 49.95,
        "qty": 5,
        "tags": ["compiled", "cython"],
    }

    # 2. Omitted optional fields use Schema defaults without mutating shared list default
    res_defaults = client.post("/products", json={"sku": "BK-101", "price": 12.50})
    assert res_defaults.status_code == 201
    assert res_defaults.json() == {
        "sku": "BK-101",
        "price": 12.5,
        "qty": 1,
        "tags": [],
    }


def test_c_yyjson_schema_validation_errors_return_422():
    app = Buckthorn()
    handler_invoked = False

    @app.post("/products")
    def create_product(prod: CreateProduct):
        nonlocal handler_invoked
        handler_invoked = True
        return prod

    client = RawTestClient(app)

    # 1. Malformed JSON syntax -> 422 in C
    res_syntax = client.post("/products", body=b'{"sku": "BK-100", broken_json')
    assert res_syntax.status_code == 422
    assert not handler_invoked

    # 2. Missing required field (`price`) -> 422 in C
    res_missing = client.post("/products", json={"sku": "BK-100"})
    assert res_missing.status_code == 422
    assert not handler_invoked
    assert res_missing.json()["detail"][0]["loc"] == ["body", "price"]

    # 3. String constraint violation (`sku` < 3 chars) -> 422 in C
    res_short = client.post("/products", json={"sku": "AB", "price": 10.0})
    assert res_short.status_code == 422
    assert not handler_invoked
    assert res_short.json()["detail"][0]["loc"] == ["body", "sku"]

    # 4. Numeric constraint violation (`price` <= 0.0) -> 422 in C
    res_neg = client.post("/products", json={"sku": "BK-100", "price": -5.0})
    assert res_neg.status_code == 422
    assert not handler_invoked
    assert res_neg.json()["detail"][0]["loc"] == ["body", "price"]

    # 5. Wrong primitive type (`qty` is string instead of int) -> 422 in C
    res_type = client.post("/products", json={"sku": "BK-100", "price": 10.0, "qty": "five"})
    assert res_type.status_code == 422
    assert not handler_invoked
    assert res_type.json()["detail"][0]["loc"] == ["body", "qty"]


def test_mixed_path_query_and_schema_body_with_dict_response():
    app = Buckthorn()

    class OrderPatch(Schema):
        quantity: int = Field(ge=1, le=50)
        expedited: bool = False

    @app.post("/stores/{store_id}/orders")
    def create_store_order(
        store_id: int = Path(ge=1),
        currency: str = Query(default="USD", min_length=3, max_length=3),
        payload: OrderPatch = None,
    ):
        return {
            "store_id": store_id,
            "currency": currency,
            "quantity": payload.quantity,
            "expedited": payload.expedited,
        }

    client = RawTestClient(app)
    res = client.post(
        "/stores/12/orders?currency=EUR",
        json={"quantity": 8, "expedited": True},
    )
    assert res.status_code == 200
    assert res.json() == {
        "store_id": 12,
        "currency": "EUR",
        "quantity": 8,
        "expedited": True,
    }


def test_phase3_yyjson_schema_speed_gate():
    app = Buckthorn()

    @app.post("/bench/orders", response_model=ProductPublic)
    def bench_order(prod: CreateProduct):
        return prod

    client = RawTestClient(app)
    raw_req = (
        b"POST /bench/orders HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 71\r\n\r\n"
        b'{"sku":"BK-777","price":199.99,"qty":3,"in_stock":true,"tags":["a","b"]}'
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
    assert rps > 80_000, f"Expected >80,000 single-worker JSON Schema RPS, got {rps:,.0f} RPS"
