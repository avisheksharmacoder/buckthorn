"""
08_background_tasks.py - Freelist Post-Response Background Tasks
=================================================================

In many web services, certain operations (such as sending transactional emails,
emitting audit logs, or pinging external webhooks) do not need to block the
HTTP client from receiving its response.

Buckthorn provides `BackgroundTasks`, which is optimized with a 1,024-slot
Cython static freelist (`@cython.freelist(1024)`):
1. **Zero-Heap Allocation:** Popping `BackgroundTasks` from the pre-allocated C
   freelist incurs zero Python heap allocation overhead.
2. **Post-Response Dispatch:** Tasks are queued in C memory and executed
   immediately after the response wire bytes are rendered and flushed.
3. **Dual Execution:** Supports both standard synchronous functions and
   `async def` coroutine functions seamlessly.

This example demonstrates:
- Injecting `BackgroundTasks` into route handlers.
- Scheduling synchronous background tasks (`bg.add_task(sync_fn, ...)`).
- Scheduling asynchronous background tasks (`bg.add_task(async_fn, ...)`).
- Combining with the `@asynccontextmanager` application lifespan.

To run this live server in development mode:
    buckthorn dev docs/examples/08_background_tasks.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/08_background_tasks.py
"""

import asyncio
from contextlib import asynccontextmanager
from buckthorn import Buckthorn, BackgroundTasks, Schema, Field, status

# Educational execution log to verify timing
task_audit_log: list[str] = []


# ==============================================================================
# 1. Application Lifespan (Startup & Shutdown hooks)
# ==============================================================================
@asynccontextmanager
async def lifespan(app: Buckthorn):
    task_audit_log.append("system:startup")
    print("  [Lifespan] Warming up connection pools...")
    yield
    task_audit_log.append("system:shutdown")
    print("  [Lifespan] Cleaned up resources on shutdown.")


app = Buckthorn(
    title="Background Notification Service",
    version="1.0.0",
    lifespan=lifespan,
)


# ==============================================================================
# 2. Background Task Functions (Sync & Async)
# ==============================================================================
def write_audit_log(event_type: str, user_email: str):
    """Synchronous CPU/disk background task."""
    task_audit_log.append(f"sync_audit:{event_type}:{user_email}")
    print(f"  [Background Sync] Audit written for {user_email}")


async def send_welcome_email(user_email: str, template: str = "default"):
    """Asynchronous network I/O background task."""
    await asyncio.sleep(0.005)
    task_audit_log.append(f"async_email:{user_email}:{template}")
    print(f"  [Background Async] Sent '{template}' email to {user_email}")


# ==============================================================================
# 3. Route Handlers
# ==============================================================================
class UserSignup(Schema):
    email: str = Field(min_length=5, max_length=50)
    name: str = Field(min_length=2, max_length=30)


@app.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(data: UserSignup, bg: BackgroundTasks):
    """
    Registers a user and dispatches background tasks.
    The client receives a 201 response immediately without waiting for
    the audit log or the email to be transmitted!
    """
    task_audit_log.append(f"handler_done:{data.email}")

    # 1. Queue a synchronous background task
    bg.add_task(write_audit_log, "user_registered", data.email)

    # 2. Queue an asynchronous background task with kwargs
    bg.add_task(send_welcome_email, data.email, template="welcome_v2")

    return {
        "status": "created",
        "email": data.email,
        "message": "Account created. Background tasks queued.",
    }


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 08_background_tasks.py in-memory verification...")

    task_audit_log.clear()

    # Using context manager triggers the application lifespan startup & shutdown
    with RawTestClient(app) as client:
        assert task_audit_log == ["system:startup"]
        print("  ✓ Lifespan startup executed")

        # Send signup request
        payload = {"email": "alex@buckthorn.dev", "name": "Alex"}
        res = client.post("/signup", json=payload)
        assert res.status_code == 201
        assert res.json()["status"] == "created"
        print(f"  ✓ Signup response -> 201 {res.json()}")

        # Verify Execution Order:
        # 1. system:startup
        # 2. handler_done (Response sent to client FIRST!)
        # 3. sync_audit (Executed post-response)
        # 4. async_email (Executed post-response)
        assert task_audit_log == [
            "system:startup",
            "handler_done:alex@buckthorn.dev",
            "sync_audit:user_registered:alex@buckthorn.dev",
            "async_email:alex@buckthorn.dev:welcome_v2",
        ]
        print(f"  ✓ Post-response execution verified -> {task_audit_log[1:]}")

    # Exiting context triggers lifespan shutdown
    assert task_audit_log[-1] == "system:shutdown"
    print("  ✓ Lifespan shutdown executed")

    print("\n✅ All 08_background_tasks.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/08_background_tasks.py:app")
