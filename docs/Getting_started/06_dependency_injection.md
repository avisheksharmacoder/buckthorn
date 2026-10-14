# 06. Compile-Time Flattened Dependency Injection

## Overview
Dependency Injection (DI) is essential for clean software architecture, database session handling, and authentication. Traditional Python frameworks resolve dependency graphs dynamically on *every single incoming request*, creating recursive Python call stacks and CPU overhead. Buckthorn solves this by compiling dependency trees at boot time into a **Flattened Execution DAG** executed sequentially via direct array indexing.

---

## 1. Layman's Explanation
Imagine you are building a car on an assembly line. 
- In traditional frameworks, when a worker needs an engine, they stop, call the parts department, wait for the parts department to ask the warehouse, wait for the warehouse to find the bolts, assemble the parts on the spot, and hand it over. They repeat this conversation for every single car coming down the line.
- Buckthorn figures out the entire supply chain once before the factory even opens. It pre-arranges the parts in a straight, numbered tray (`Slot 0`, `Slot 1`, `Slot 2`). When a car arrives, the worker simply grabs the finished component from Slot 0, attaches it to Slot 1, and drops the finished assembly into Slot 2. No phone calls, no searching, and no delays.

When the car leaves the factory, any borrowed tools are put away in exact reverse order (LIFO - Last In, First Out).

---

## 2. Technical Explanation
1. **Compile-Time Graph Resolution:**
   - When a route decorator (`@app.get(...)`) is evaluated at application startup, Buckthorn inspects all parameters marked with `Depends(...)`.
   - The dependency compiler builds a Directed Acyclic Graph (DAG) of all parent and sub-dependencies.
   - It performs a topological sort and flattens the graph into a contiguous array of `CompiledDepStep` structs.
2. **Numeric Slot Allocation (`dep_slots`):**
   - Each unique dependency is assigned a fixed numeric slot index (`0, 1, 2, ...`).
   - At request time, Buckthorn allocates a single flat list: `dep_slots = [None] * num_dep_steps`.
   - Steps execute sequentially in topological order. Results are stored directly into `dep_slots[step.slot_index]`.
   - Sub-dependencies are passed as direct keyword arguments to dependent functions without runtime inspection.
3. **Full Generator & Async Lifecycle Support with LIFO Teardown:**
   - Both **sync generators** (`def get_db(): ... yield db ... finally: db.close()`) and **async generators** (`async def get_session(): ... yield session ... finally: await session.close()`) are natively supported.
   - **Eager vs. Suspended Async Execution:** Asynchronous dependencies execute via Buckthorn's 3-Tier Eager Coroutine Engine (see [Chapter 19](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/docs/Getting_started/19_native_async_await_and_suspension.md)). If an async dependency resolves without awaiting I/O, it completes eagerly on the active C stack with zero `asyncio.Task` overhead. If it awaits database or network I/O, it suspends cleanly, returning control to `libuv`.
   - When a generator dependency yields its value, that value is placed into `dep_slots` and the generator frame is suspended and pushed onto a teardown stack.
   - Once the HTTP response is formulated (even if an exception or HTTP 4xx/5xx occurred), the `finally:` block in [_execute_route_inner](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/_core.pyx#L1373-L1379) iterates through the teardown stack in **LIFO order**, executing `next()` for sync generators and `aclose()` for async generators to ensure database transactions commit or roll back and connections cleanly return to their pools without blocking the C event loop.
4. **Sub-Dependency Caching (`use_cache=True`):**
   - If multiple dependencies or route handlers depend on the same callable with `use_cache=True`, it executes exactly once per request. Subsequent steps read directly from the pre-computed `dep_slots` index.

---

## 3. Code Example

```python
# dependency_injection_demo.py
from typing import Generator
from buckthorn import Buckthorn, Depends, Header, HTTPException

app = Buckthorn(title="Dependency Injection Demo")

# 1. Low-Level Database Connection Dependency (Sync Generator)
class MockDatabaseSession:
    def __init__(self):
        self.is_active = True
        print("[DB] Connection opened.")

    def query(self, entity_id: int) -> dict:
        return {"id": entity_id, "name": f"Entity #{entity_id}", "status": "active"}

    def close(self):
        self.is_active = False
        print("[DB] Connection cleanly closed.")

def get_db_session() -> Generator[MockDatabaseSession, None, None]:
    db = MockDatabaseSession()
    try:
        yield db
    finally:
        db.close()

# 2. Intermediate Authentication Dependency (Sync Callable with Parameter Injection)
def get_current_user(x_api_key: str = Header(...)) -> dict:
    if x_api_key != "secret-token-123":
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return {"user_id": 99, "username": "alice", "tier": "premium"}

# 3. High-Level Composite Service Dependency
class ItemService:
    def __init__(self, db: MockDatabaseSession, user: dict):
        self.db = db
        self.user = user

    def fetch_item(self, item_id: int) -> dict:
        record = self.db.query(item_id)
        record["requested_by"] = self.user["username"]
        return record

def get_item_service(
    db: MockDatabaseSession = Depends(get_db_session),
    user: dict = Depends(get_current_user),
) -> ItemService:
    return ItemService(db=db, user=user)

# 4. Route Handler Consuming the Composite Dependency
@app.get("/items/{item_id}")
def read_item(
    item_id: int,
    service: ItemService = Depends(get_item_service),
):
    item = service.fetch_item(item_id)
    return item

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `def get_db_session() -> Generator[...]`:
   - Declares a generator dependency. The code before `yield db` initializes the resource (e.g. acquiring a connection from a pool).
   - The code inside `finally: db.close()` is guaranteed to execute after the client response is rendered.
2. `def get_current_user(x_api_key: str = Header(...))`:
   - Dependencies can themselves declare HTTP parameters (`Header`, `Query`, `Cookie`, `Path`). Buckthorn resolves them from C buffers before invoking `get_current_user`.
3. `def get_item_service(...)`:
   - Declares a composite dependency that depends on both `get_db_session` and `get_current_user`.
   - At boot time, Buckthorn determines the topological order:
     1. Step 0: `get_db_session` -> Slot 0
     2. Step 1: `get_current_user` -> Slot 1
     3. Step 2: `get_item_service(Slot 0, Slot 1)` -> Slot 2
4. `def read_item(item_id: int, service: ItemService = Depends(get_item_service))`:
   - When a request arrives, the route handler immediately receives `dep_slots[2]` as `service`.
   - Zero graph traversal, stack analysis, or dictionary lookups occur at runtime.
5. Guaranteed Teardown:
   - When `read_item` returns, the teardown stack executes in reverse order, closing `db` without delaying client response delivery.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | FastAPI / Starlette | Flask / Django | Buckthorn |
|---|---|---|---|
| **Graph Resolution** | Dynamic recursive traversal on **every single request** | None (manual manual instantiation or third-party containers) | **Boot-time static compilation** into flattened DAG array |
| **Execution Overhead** | Significant Python call stack overhead per request | High runtime overhead | **Direct array lookup** (`dep_slots[slot_index]`) in Cython |
| **Generator Teardown** | Background generator iteration via async context managers | Manual `teardown_appcontext` hooks | **Compiled LIFO teardown stack** (sync `next()` and non-blocking `aclose()`) |
| **Type Support** | Sync & Async functions/generators | Typically sync only | **Native Sync, Async, and Generator callables** with eager execution |
| **Per-Request Latency** | ~5 – 15 microseconds per dependency tree | High | **<0.5 microseconds** |
