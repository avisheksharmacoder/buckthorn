# tests/phase4/test_depends_di.py
import time
import pytest
from buckthorn import (
    Buckthorn,
    Depends,
    HTTPException,
    Header,
    Path,
    Query,
    Schema,
    status,
)
from buckthorn.testing import RawTestClient


def test_dag_deduplication_and_lifo_generator_teardown():
    app = Buckthorn()
    events: list[str] = []

    async def get_db():
        events.append("db_open")
        try:
            yield "pg_conn"
        finally:
            events.append("db_close")

    def get_cache():
        events.append("cache_open")
        try:
            yield "redis_conn"
        finally:
            events.append("cache_close")

    async def get_user_repo(db: str = Depends(get_db), cache: str = Depends(get_cache)):
        events.append("user_repo_init")
        return f"UserRepo({db},{cache})"

    async def get_order_repo(db: str = Depends(get_db)):
        events.append("order_repo_init")
        return f"OrderRepo({db})"

    @app.get("/dashboard")
    async def dashboard(
        users: str = Depends(get_user_repo),
        orders: str = Depends(get_order_repo),
    ):
        events.append("handler_run")
        return {"users": users, "orders": orders}

    client = RawTestClient(app)
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert res.json() == {
        "users": "UserRepo(pg_conn,redis_conn)",
        "orders": "OrderRepo(pg_conn)",
    }

    # Verify:
    # 1. `get_db` was depended on by both repos, but ran ONLY ONCE (`use_cache=True`).
    # 2. Both sync and async generator `finally` blocks ran AFTER `handler_run` in LIFO order.
    assert events.count("db_open") == 1
    assert events.count("cache_open") == 1
    assert events == [
        "db_open",
        "cache_open",
        "user_repo_init",
        "order_repo_init",
        "handler_run",
        "cache_close",
        "db_close",
    ]


def test_use_cache_false_executes_dependency_per_branch():
    app = Buckthorn()
    counter = {"calls": 0}

    def get_request_nonce():
        counter["calls"] += 1
        return counter["calls"]

    @app.get("/nonces")
    def read_nonces(
        n1: int = Depends(get_request_nonce, use_cache=False),
        n2: int = Depends(get_request_nonce, use_cache=False),
    ):
        return {"n1": n1, "n2": n2}

    client = RawTestClient(app)
    res = client.get("/nonces")
    assert res.status_code == 200
    assert res.json() == {"n1": 1, "n2": 2}


def test_http_exception_in_dependency_or_handler_still_runs_generator_teardown():
    app = Buckthorn()
    state = {"db_active": False}

    async def get_db():
        state["db_active"] = True
        try:
            yield "conn"
        finally:
            state["db_active"] = False

    def require_api_key(x_api_key: str = Header(alias="X-Api-Key", default="none")):
        if x_api_key != "secret-123":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API Key",
            )
        return x_api_key

    @app.get("/secure/{item_id}", dependencies=[Depends(require_api_key)])
    async def get_secure_item(item_id: int = Path(ge=1), db: str = Depends(get_db)):
        if item_id == 999:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found",
            )
        return {"item_id": item_id, "db": db}

    client = RawTestClient(app)

    # 1. Dependency raises 401 -> handler never runs
    res_401 = client.get("/secure/10")
    assert res_401.status_code == 401
    assert res_401.json() == {"detail": "Invalid API Key"}
    assert state["db_active"] is False

    # 2. Handler raises 404 after get_db() yielded -> generator teardown MUST still close DB!
    res_404 = client.get("/secure/999", headers={"X-Api-Key": "secret-123"})
    assert res_404.status_code == 404
    assert res_404.json() == {"detail": "Item not found"}
    assert state["db_active"] is False

    # 3. Valid request -> 200 OK and DB closed after response
    res_200 = client.get("/secure/42", headers={"X-Api-Key": "secret-123"})
    assert res_200.status_code == 200
    assert res_200.json() == {"item_id": 42, "db": "conn"}
    assert state["db_active"] is False


def test_class_dependency_shorthand_and_callable_instance():
    app = Buckthorn()

    class Pagination:
        def __init__(
            self,
            page: int = Query(default=1, ge=1),
            size: int = Query(default=20, ge=1, le=100),
        ):
            self.page = page
            self.size = size
            self.offset = (page - 1) * size

    class RoleGuard:
        def __init__(self, required_role: str):
            self.required_role = required_role

        def __call__(self, x_role: str = Header(alias="X-Role", default="guest")):
            if x_role != self.required_role:
                raise HTTPException(status_code=403, detail="Insufficient role")
            return x_role

    @app.get("/items", dependencies=[Depends(RoleGuard("admin"))])
    def list_items(
        commons: Pagination = Depends(),  # FastAPI shorthand: infers `Pagination` from type hint
    ):
        return {"page": commons.page, "size": commons.size, "offset": commons.offset}

    client = RawTestClient(app)

    # Forbidden role -> 403
    res_403 = client.get("/items?page=3&size=10", headers={"X-Role": "viewer"})
    assert res_403.status_code == 403

    # Valid admin role -> 200 with resolved Pagination class instance
    res_200 = client.get("/items?page=3&size=10", headers={"X-Role": "admin"})
    assert res_200.status_code == 200
    assert res_200.json() == {"page": 3, "size": 10, "offset": 20}


def test_phase4_flattened_di_speed_gate():
    app = Buckthorn()

    def dep_config():
        return "prod"

    def dep_tenant( x_tenant: str = Header(alias="X-Tenant"), env: str = Depends(dep_config)):
        return f"{env}:{x_tenant}"

    def dep_auth(tenant: str = Depends(dep_tenant)):
        return tenant

    @app.get("/bench/di")
    def bench_di(auth: str = Depends(dep_auth), env: str = Depends(dep_config)):
        return {"auth": auth, "env": env}

    client = RawTestClient(app)
    raw_req = (
        b"GET /bench/di HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Tenant: acme\r\n\r\n"
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
    assert res.json() == {"auth": "prod:acme", "env": "prod"}
    assert rps > 75_000, f"Expected >75,000 RPS with 3 nested Depends(), got {rps:,.0f} RPS"
