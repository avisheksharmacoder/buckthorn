# tests/phase9/test_background_and_openapi.py
from contextlib import asynccontextmanager
import time
import pytest
from buckthorn import (
    Buckthorn,
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Schema,
    Field,
    status,
)
from buckthorn.testing import RawTestClient


def test_lifespan_startup_shutdown_and_background_tasks():
    execution_log: list[str] = []

    @asynccontextmanager
    async def lifespan(app: Buckthorn):
        execution_log.append("lifespan:startup")
        yield
        execution_log.append("lifespan:shutdown")

    app = Buckthorn(title="Notification Service", version="2.1.0", lifespan=lifespan)

    def sync_audit_log(email: str, code: int):
        execution_log.append(f"bg_sync:{email}:{code}")

    async def async_send_email(email: str, *, template: str = "welcome"):
        execution_log.append(f"bg_async:{email}:{template}")

    @app.post("/register/{email}", status_code=status.HTTP_201_CREATED)
    async def register_user(email: str, bg: BackgroundTasks):
        execution_log.append(f"handler:{email}")
        bg.add_task(sync_audit_log, email, 201)
        bg.add_task(async_send_email, email, template="onboarding")
        return {"registered": email}

    with RawTestClient(app) as client:
        assert execution_log == ["lifespan:startup"]

        res = client.post("/register/alice@buckthorn.io")
        assert res.status_code == 201
        assert res.json() == {"registered": "alice@buckthorn.io"}

        # Verify handler executed BEFORE background tasks, and both sync + async tasks ran
        assert execution_log == [
            "lifespan:startup",
            "handler:alice@buckthorn.io",
            "bg_sync:alice@buckthorn.io:201",
            "bg_async:alice@buckthorn.io:onboarding",
        ]

    # Exiting `with RawTestClient(app)` must trigger lifespan shutdown
    assert execution_log[-1] == "lifespan:shutdown"


def test_modular_api_router_prefix_tags_and_dependencies():
    app = Buckthorn()
    audit_trail: list[str] = []

    def verify_org_header(x_org: str = Header(alias="X-Org", default="none")):
        if x_org != "buckthorn-hq":
            raise HTTPException(status_code=403, detail="Invalid Org")
        audit_trail.append(f"org:{x_org}")
        return x_org

    v1_router = APIRouter(
        prefix="/api/v1",
        tags=["v1"],
        dependencies=[Depends(verify_org_header)],
    )
    users_router = APIRouter(prefix="/users", tags=["users"])

    @users_router.get("/{user_id}")
    def get_user(user_id: int = Path(ge=1)):
        return {"user_id": user_id, "version": "v1"}

    v1_router.include_router(users_router)
    app.include_router(v1_router)

    client = RawTestClient(app)

    # 1. Missing X-Org header -> rejected by router-level dependency with 403
    res_403 = client.get("/api/v1/users/10")
    assert res_403.status_code == 403

    # 2. Valid X-Org header -> matches flattened C route "/api/v1/users/{user_id}"
    res_200 = client.get("/api/v1/users/42", headers={"X-Org": "buckthorn-hq"})
    assert res_200.status_code == 200
    assert res_200.json() == {"user_id": 42, "version": "v1"}
    assert audit_trail == ["org:buckthorn-hq"]


def test_openapi_3_1_schema_generation_and_docs_uis():
    class InvoiceCreate(Schema):
        customer_id: int = Field(ge=1)
        amount: float = Field(gt=0.0)
        currency: str = Field(default="USD", min_length=3, max_length=3)

    class InvoiceOut(Schema):
        invoice_id: int
        amount: float
        currency: str

    app = Buckthorn(title="Buckthorn Billing API", version="3.0.0")
    billing_router = APIRouter(prefix="/billing", tags=["Billing"])

    @billing_router.post(
        "/invoices/{region}",
        response_model=InvoiceOut,
        status_code=status.HTTP_201_CREATED,
    )
    def create_invoice(
        region: str,
        payload: InvoiceCreate,
        dry_run: bool = Query(default=False),
    ):
        return InvoiceOut(invoice_id=100, amount=payload.amount, currency=payload.currency)

    app.include_router(billing_router)
    client = RawTestClient(app)

    # 1. Inspect generated OpenAPI 3.1 JSON specification
    res_spec = client.get("/openapi.json")
    assert res_spec.status_code == 200
    spec = res_spec.json()

    assert spec["openapi"] == "3.1.0"
    assert spec["info"] == {"title": "Buckthorn Billing API", "version": "3.0.0"}
    assert "/billing/invoices/{region}" in spec["paths"]

    post_op = spec["paths"]["/billing/invoices/{region}"]["post"]
    assert "Billing" in post_op["tags"]
    assert post_op["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/InvoiceCreate"
    }
    assert "201" in post_op["responses"]

    schemas = spec["components"]["schemas"]
    assert "InvoiceCreate" in schemas
    assert "InvoiceOut" in schemas
    assert schemas["InvoiceCreate"]["properties"]["amount"]["exclusiveMinimum"] == 0.0
    assert "customer_id" in schemas["InvoiceCreate"]["required"]

    # 2. Verify interactive Swagger UI (/docs) and Scalar API Reference (/scalar)
    res_docs = client.get("/docs")
    assert res_docs.status_code == 200
    assert "text/html" in res_docs.headers["Content-Type"]
    assert b"SwaggerUIBundle" in res_docs.body

    res_scalar = client.get("/scalar")
    assert res_scalar.status_code == 200
    assert "text/html" in res_scalar.headers["Content-Type"]
    assert b"@scalar/api-reference" in res_scalar.body


def test_phase9_static_openapi_and_background_tasks_speed_gate():
    app = Buckthorn(title="Bench API", version="1.0.0")
    counter = 0

    def fast_bg_tick():
        nonlocal counter
        counter += 1

    @app.get("/bench/bg")
    def bench_bg(bg: BackgroundTasks):
        bg.add_task(fast_bg_tick)
        return b'{"ok":true}'

    client = RawTestClient(app)
    raw_bg_req = b"GET /bench/bg HTTP/1.1\r\nHost: localhost\r\n\r\n"
    raw_openapi_req = b"GET /openapi.json HTTP/1.1\r\nHost: localhost\r\n\r\n"

    # Warmup
    for _ in range(500):
        client.send_raw(raw_bg_req)
        client.send_raw(raw_openapi_req)

    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = client.send_raw(raw_bg_req)
    elapsed_bg = time.perf_counter() - t0
    rps_bg = iterations / elapsed_bg

    t1 = time.perf_counter()
    for _ in range(iterations):
        res_oa = client.send_raw(raw_openapi_req)
    elapsed_oa = time.perf_counter() - t1
    rps_oa = iterations / elapsed_oa

    assert res.status_code == 200
    assert res_oa.status_code == 200
    assert rps_bg > 75_000, f"Expected >75,000 RPS with BackgroundTasks, got {rps_bg:,.0f} RPS"
    assert rps_oa > 150_000, f"Expected >150,000 RPS on static /openapi.json, got {rps_oa:,.0f} RPS"
