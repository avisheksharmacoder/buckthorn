# 14. Modular Routing with APIRouter

## Overview
Large enterprise applications cannot keep all route handlers in a single monolithic file. They require modular, domain-driven route partitioning across multiple modules (e.g., `auth.py`, `users.py`, `billing.py`, `analytics.py`). Buckthorn provides `APIRouter` with **Compile-Time Prefix Flattening**, ensuring that decomposing your code into dozens of sub-routers has **zero runtime performance penalty**.

---

## 1. Layman's Explanation
Think of a large department store with multiple floors and departments (Men's Clothing, Electronics, Home Goods).
- In traditional web frameworks, when you visit `store.com/electronics/audio/headphones`, the framework asks the main store if it has an `electronics` department, the electronics department asks if it has an `audio` section, and the audio section looks up `headphones`. Navigating through multiple layers of managers on every click slows down the store.
- Buckthorn takes all the departmental maps at opening time and compiles a single, master alphabetical directory at the front entrance in C. When a customer asks for `/electronics/audio/headphones`, the system immediately points them to the exact shelf in one single step.

You get all the organization of modular code, with all the speed of a single flat routing table.

---

## 2. Technical Explanation
1. **The `APIRouter` Class:**
   - Defined in [buckthorn/routing.py](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/routing.py).
   - Allows declaring endpoints using identical decorators to the main app: `@router.get()`, `@router.post()`, `@router.websocket()`, etc.
   - Accepts constructor parameters:
     - `prefix: str = ""`: A URL path prefix applied to all routes in this router.
     - `tags: Optional[list[str]] = None`: OpenAPI tags for interactive API documentation grouping.
     - `dependencies: Optional[Sequence[DependsInfo]] = None`: Router-level dependencies executed for every route in the router.
2. **Recursive Sub-Router Inclusion:**
   - Routers can include other sub-routers via `router.include_router(sub_router, prefix=..., tags=..., dependencies=...)`.
   - Nesting can be arbitrarily deep.
3. **Compile-Time Radix Tree Flattening:**
   - When `app.include_router(router)` is called, Buckthorn resolves all nested prefixes, combines route tags, and prepends router-level dependencies onto each route's dependency list.
   - Each route is inserted directly into the native C `rax` Radix Tree as a normalized absolute path.
   - **Zero Runtime Tree Traversals:** At request time, there is no sub-router hierarchy to evaluate. Slicing and route matching execute in one single pass in C.

---

## 3. Code Example

Structure of a modular project:
```text
my_project/
├── main.py
└── routers/
    ├── __init__.py
    ├── users.py
    └── items.py
```

### `routers/users.py`
```python
# routers/users.py
from buckthorn import APIRouter, Schema, HTTPException

router = APIRouter(prefix="/users", tags=["Users"])

class UserProfile(Schema):
    id: int
    username: str
    email: str

USERS_DB = {
    1: {"id": 1, "username": "alice", "email": "alice@example.com"},
    2: {"id": 2, "username": "bob", "email": "bob@example.com"},
}

@router.get("/")
def list_users():
    return list(USERS_DB.values())

@router.get("/{user_id}", response_model=UserProfile)
def get_user(user_id: int):
    user = USERS_DB.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
```

### `routers/items.py`
```python
# routers/items.py
from buckthorn import APIRouter

router = APIRouter(prefix="/items", tags=["Catalog"])

@router.get("/")
def list_items():
    return [{"item_id": 101, "title": "Wireless Mouse", "price": 29.99}]
```

### `main.py`
```python
# main.py
from buckthorn import Buckthorn
from routers.users import router as users_router
from routers.items import router as items_router

app = Buckthorn(title="Modular Store API", version="1.0.0")

# Mount sub-routers under a global /api/v1 prefix
app.include_router(users_router, prefix="/api/v1")
app.include_router(items_router, prefix="/api/v1")

@app.get("/")
def root():
    return {"status": "ok", "api_version": "v1"}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `router = APIRouter(prefix="/users", tags=["Users"])`:
   - Creates a modular router instance.
   - All routes defined on this router will automatically inherit the `/users` prefix.
   - Swagger and Scalar API documentation will group these endpoints under the `"Users"` category.
2. `@router.get("/{user_id}", response_model=UserProfile)`:
   - Registers a route relative to the router's prefix.
   - Supports all framework features: path parameter validation, schemas, dependency injection, and responses.
3. `app.include_router(users_router, prefix="/api/v1")`:
   - Mounts the router onto the application with an additional prefix.
   - Buckthorn concatenates `/api/v1` + `/users` + `/{user_id}` into `/api/v1/users/{user_id}`.
   - The compiled route is installed directly into the native C radix tree.
4. Client requests to `GET /api/v1/users/1`:
   - The C router matches `/api/v1/users/1` directly against the Redis `rax` tree with zero nested router hops.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | Flask Blueprints / Django URLs | FastAPI / Starlette APIRouter | Buckthorn |
|---|---|---|---|
| **Mounting Mechanism** | Regex pattern prepending | Python routing tree delegates to sub-routers | **Static compilation directly into C Radix Tree** |
| **Runtime Overhead** | High (nested regex evaluations) | Moderate (nested dispatch loops per router) | **Zero (Flat C Radix Tree lookup)** |
| **Dependency Inheritance** | Manual decorator duplication | Merged per request | **Flattened at compile-time into execution DAG** |
| **OpenAPI Merging** | Manual third-party plugins | Dynamic dictionary union | **Pre-baked static JSON schema** |
| **WebSocket Support** | Inconsistent across blueprints | Supported via ASGI | **Seamless across routers and sub-routers** |
