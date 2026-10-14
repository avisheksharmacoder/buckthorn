# 12. Post-Response Background Tasks

## Overview
Many web API operations need to trigger side-effects—such as sending welcome emails, dispatching webhooks, writing audit logs, or refreshing analytics caches—that should **never block** the client from receiving an instant HTTP response. Buckthorn features a zero-overhead, compiled **Post-Response Background Tasks Engine** (`BackgroundTasks`) backed by a 1,024-slot Cython freelist.

---

## 1. Layman's Explanation
Imagine ordering a meal at a fast-food drive-through:
- If the cashier hands you your bag of food, but then says "Wait! You can't leave yet—I have to file my sales receipt in the back office, sweep the floor, and send an email to the corporate manager," you would be annoyed by the pointless delay.
- The correct way is to hand you your food first so you can drive away immediately, and then the worker performs the cleanup chores while the counter is free.

Buckthorn’s `BackgroundTasks` does exactly this: it sends the completed HTTP response back to the client down the network wire first, and then executes your background functions immediately afterwards.

---

## 2. Technical Explanation
1. **The Cython Freelist Queue:**
   - The `BackgroundTasks` class is implemented in [buckthorn/_core.pyx](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/_core.pyx#L353-L379) using `@cython.freelist(1024)`.
   - When injected into a route parameter (`tasks: BackgroundTasks`), Buckthorn assigns it via `SRC_BACKGROUND_TASKS` from the pre-allocated freelist.
2. **Task Registration (`add_task`):**
   - Calling `tasks.add_task(func, *args, **kwargs)` appends the callable, arguments, keyword arguments, and coroutine detection flag into an internal Cython list.
3. **Post-Response Execution Timing:**
   - In [_execute_route_inner](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/_core.pyx#L1376-L1379), the response status code, headers, and body are rendered and committed to the C output buffer **first**.
   - In the `finally:` block, if `out_status_code[0] < 400`, Buckthorn executes `bg_runner._execute_all()`.
   - Both synchronous callables (`def`) and asynchronous coroutines (`async def`) are executed sequentially without requiring an external message queue or Redis broker for simple in-process tasks.

---

## 3. Code Example

```python
# background_tasks_demo.py
import time
from buckthorn import Buckthorn, BackgroundTasks, Schema

app = Buckthorn(title="Background Tasks Demo")

# Schema for new user registration
class RegisterUserRequest(Schema):
    username: str
    email: str

# 1. Background Side-Effect Functions (Sync and Async)
def send_welcome_email(email: str, username: str):
    # Simulate email dispatch (e.g. SMTP / SendGrid API call)
    print(f"[Email Worker] Dispatching welcome email to {email}...")
    time.sleep(0.5)  # Does NOT delay the client!
    print(f"[Email Worker] Email sent successfully to {username}!")

async def log_audit_trail(action: str, target: str):
    # Async audit log persistence
    print(f"[Audit Worker] Recording audit event: {action} on {target}")

# 2. Route Handler with Injected BackgroundTasks
@app.post("/register", status_code=201)
def register_user(
    payload: RegisterUserRequest,
    tasks: BackgroundTasks,
):
    # Step A: Perform core business logic instantly
    user_id = 1001

    # Step B: Schedule tasks to run AFTER response is sent
    tasks.add_task(send_welcome_email, email=payload.email, username=payload.username)
    tasks.add_task(log_audit_trail, action="USER_REGISTERED", target=payload.username)

    # Step C: Return HTTP 201 immediately to the client
    # The client receives this response in <1 millisecond!
    return {
        "status": "created",
        "user_id": user_id,
        "message": "User registered successfully.",
    }

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `def register_user(..., tasks: BackgroundTasks)`:
   - Buckthorn's parameter compiler automatically identifies `BackgroundTasks` type annotation as `SRC_BACKGROUND_TASKS`.
   - It instantiates/recycles a `BackgroundTasks` object from the Cython freelist and registers it in the active execution context.
2. `tasks.add_task(send_welcome_email, email=..., username=...)`:
   - Adds `send_welcome_email` to the task queue with its parameters.
   - The email function is **not** executed yet.
3. `return {"status": "created", ...}`:
   - The dictionary is serialized into C JSON buffers via `yyjson`.
   - The HTTP response status `201 Created` is committed.
4. Execution after response:
   - Buckthorn immediately proceeds to the `finally:` block of `_execute_route_inner`.
   - Because the status code is `201 (< 400)`, `tasks._execute_all()` is called.
   - `send_welcome_email` runs and sleeps for 0.5 seconds without adding a single millisecond to the HTTP client's round-trip time.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | FastAPI / Starlette | Flask / Django | Buckthorn |
|---|---|---|---|
| **Memory Allocation** | Allocates new Python class instances and lists on standard heap | Not natively supported; requires Celery or RQ | **1,024-entry Cython freelist** (`@cython.freelist(1024)`) |
| **Execution Trigger** | Starlette ASGI middleware wrapper iteration | Third-party background worker queues | **Direct Cython `finally:` invocation** |
| **Caller Latency Impact** | Low, but adds Python asyncio queue overhead | High if run synchronously | **Zero added client latency** |
| **Coroutine Handling** | Must run inside standard asyncio loop | N/A | **Native sync and compiled coroutine runner** |
| **Setup Complexity** | Requires Starlette BackgroundTasks import | Requires Redis, Celery, and worker daemons | **Built directly into the core engine** |
