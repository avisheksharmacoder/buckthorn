# tests/phase13/test_native_async_io.py
import asyncio
import time
import pytest
from buckthorn import (
    Buckthorn,
    Depends,
    BackgroundTasks,
    HTTPException,
    Path,
    Schema,
    Field,
)
from buckthorn.testing import RawTestClient


def test_true_async_await_suspension_gather_and_taskgroup():
    app = Buckthorn()

    async def simulated_db_query(user_id: int) -> dict:
        # True suspension yielding a Future back to the event loop
        await asyncio.sleep(0.005)
        return {"user_id": user_id, "tier": "enterprise"}

    async def simulated_cache_fetch(key: str) -> str:
        await asyncio.sleep(0.005)
        return f"cached:{key}"

    @app.get("/async-profile/{user_id}")
    async def get_profile(user_id: int = Path(ge=1)):
        # Concurrent suspension using asyncio.gather
        user_data, cache_val = await asyncio.gather(
            simulated_db_query(user_id),
            simulated_cache_fetch(f"u:{user_id}"),
        )
        return {
            "user_id": user_data["user_id"],
            "tier": user_data["tier"],
            "cache": cache_val,
        }

    client = RawTestClient(app)
    res = client.get("/async-profile/42")
    assert res.status_code == 200
    assert res.json() == {
        "user_id": 42,
        "tier": "enterprise",
        "cache": "cached:u:42",
    }


def test_async_generator_dependency_with_real_await_in_setup_and_teardown():
    app = Buckthorn()
    lifecycle: list[str] = []

    async def get_async_db_session():
        await asyncio.sleep(0.002)
        lifecycle.append("session_connected")
        try:
            yield "asyncpg_tx_1"
        finally:
            await asyncio.sleep(0.002)
            lifecycle.append("session_committed_and_closed")

    async def get_current_actor(tx: str = Depends(get_async_db_session)):
        await asyncio.sleep(0.002)
        lifecycle.append(f"actor_loaded:{tx}")
        return "actor_admin"

    @app.post("/transactions")
    async def run_transaction(
        actor: str = Depends(get_current_actor),
        bg: BackgroundTasks = None,
    ):
        await asyncio.sleep(0.002)
        lifecycle.append(f"handler_awaited:{actor}")

        async def async_post_webhook():
            await asyncio.sleep(0.002)
            lifecycle.append("bg_webhook_awaited")

        bg.add_task(async_post_webhook)
        return {"actor": actor, "committed": True}

    client = RawTestClient(app)
    res = client.post("/transactions")
    assert res.status_code == 200
    assert res.json() == {"actor": "actor_admin", "committed": True}
    assert lifecycle == [
        "session_connected",
        "actor_loaded:asyncpg_tx_1",
        "handler_awaited:actor_admin",
        "session_committed_and_closed",
        "bg_webhook_awaited",
    ]


def test_eager_fast_path_preserves_80k_rps_alongside_suspending_routes():
    app = Buckthorn()

    @app.get("/fast-eager")
    async def fast_eager():
        # Defined as `async def` but does not suspend -> takes Tier-1 Eager Fast Path
        return b'{"fast":true}'

    @app.get("/slow-io")
    async def slow_io():
        # Suspends on event loop -> takes Tier-2 Suspended Future Path
        await asyncio.sleep(0.001)
        return {"slow": True}

    client = RawTestClient(app)

    # Verify suspending route works
    assert client.get("/slow-io").json() == {"slow": True}

    # Verify non-suspending `async def` route still exceeds >80,000 RPS with zero Task overhead
    raw_req = b"GET /fast-eager HTTP/1.1\r\nHost: localhost\r\n\r\n"
    for _ in range(500):
        client.send_raw(raw_req)

    iterations = 20_000
    t0 = time.perf_counter()
    for _ in range(iterations):
        res = client.send_raw(raw_req)
    elapsed = time.perf_counter() - t0

    rps = iterations / elapsed
    assert res.status_code == 200
    assert rps > 80_000, f"Expected >80,000 RPS on Tier-1 Eager async route, got {rps:,.0f} RPS"


def test_async_route_http_exception():
    app = Buckthorn()

    @app.get("/async-error")
    async def async_error():
        await asyncio.sleep(0.001)
        raise HTTPException(status_code=403, detail="Forbidden async resource")

    client = RawTestClient(app)
    res = client.get("/async-error")
    assert res.status_code == 403
    assert res.json() == {"detail": "Forbidden async resource"}
