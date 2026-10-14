# 02. C Radix-Tree Routing & Route Matching

## Overview
Buckthorn uses an in-memory **Radix Tree (Prefix Tree)** written in C (`rax.c`, the battle-tested data structure powering Redis). Routing decisions, parameter slicing, and 404 rejections occur at the native machine-code level before Python objects or stack frames are created.

---

## 1. Layman's Explanation
Think of standard web routing like searching through an address book page by page: if you have 100 pages, you might have to check all 100 until you find the right house. Regular expressions (used by most frameworks) make this even slower because they examine complex pattern rules for every single incoming URL.

Buckthorn’s Radix Tree works like a well-organized road network with signposts. When a request comes in for `/users/42/profile`, it immediately jumps from `/users/` to the ID branch, and then straight to `/profile`. It doesn't matter if your API has 10 routes or 1,000 routes: finding the destination takes virtually the exact same tiny fraction of a microsecond.

---

## 2. Technical Explanation
1. **The Radix Tree (`rax`):**
   - Routes are indexed by their normalized HTTP method and path string.
   - Buckthorn maintains separate trees per HTTP method (`GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`, `PATCH`, `HEAD`).
2. **Query String Stripping:**
   - Raw HTTP query strings (`?sort=asc&page=1`) are detected in pure C during `picohttpparser` framing and cleanly stripped from the path pointer before radix tree traversal.
3. **Parameterized Dynamic Segments (`{id}`):**
   - Dynamic path placeholders like `/items/{item_id}` are indexed with segment markers in the C tree.
   - When a match is made, the starting pointer and byte length of `{item_id}` are recorded into a lightweight `bt_slice_t` slice array inside the request's 64 KB C memory arena.
4. **Fast 404 Short-Circuit:**
   - If an incoming path does not match any node in the radix tree, the C router immediately formats a standard `404 Not Found` response directly into the client output buffer and flushes it via `libuv`.
   - **Zero GIL Acquisition:** Python is never invoked for 404 requests, immunizing your server against directory-scanning bots and DDoS path fuzzing.

---

## 3. Code Example

```python
# routing_example.py
from buckthorn import Buckthorn

app = Buckthorn(title="Routing Demo")

# 1. Static Route
@app.get("/status")
def get_status():
    return {"online": True}

# 2. Parameterized Single Path Segment
@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {"user_id": user_id, "role": "member"}

# 3. Multi-Segment Parameterized Path
@app.get("/organizations/{org_id}/teams/{team_slug}/members")
def get_team_members(org_id: int, team_slug: str):
    return {
        "org_id": org_id,
        "team_slug": team_slug,
        "members_count": 12,
    }

# 4. Standard HTTP Method Verbs
@app.post("/items")
def create_item():
    return {"created": True}

@app.put("/items/{item_id}")
def update_item(item_id: int):
    return {"updated_id": item_id}

@app.delete("/items/{item_id}")
def delete_item(item_id: int):
    return {"deleted_id": item_id}
```

---

## 4. Part-by-Part Code Explanation

1. `@app.get("/status")`:
   Registers the path `/status` under the `GET` radix tree. When a matching request arrives, the router invokes `get_status` with zero argument parsing overhead.
2. `@app.get("/users/{user_id}")`:
   Declares a parameterized dynamic route. At registration time, Buckthorn inspects `{user_id}` and maps it to the function parameter named `user_id`.
3. `def get_user(user_id: int)`:
   The type annotation `int` signals to the C engine that the extracted path slice must be parsed as a 64-bit integer (`bt_parse_int64_checked`). If the client sends `/users/abc`, the C validator immediately aborts with a `422 Unprocessable Entity` without calling the Python function.
4. `@app.get("/organizations/{org_id}/teams/{team_slug}/members")`:
   Demonstrates multi-segment path extraction. The C tree branches efficiently across multiple dynamic segments (`org_id` and `team_slug`).
5. `@app.post`, `@app.put`, `@app.delete`:
   Associates endpoints with respective HTTP verbs. Each method maintains an isolated `rax` tree, ensuring `GET /items` and `POST /items` never conflict or slow each other down.

---

## 5. Why It Is Different From Other Python Frameworks

| Aspect | Traditional Python Frameworks | Buckthorn |
|---|---|---|
| **Routing Algorithm** | Regular Expressions (`re.compile`) or Python nested dicts | Native C Radix Tree (`rax`) from Redis |
| **Lookup Time Complexity** | $O(N)$ with route count where $N$ is number of regex patterns | $O(K)$ where $K$ is URL path length, completely independent of total route count |
| **Invalid Route (404) Cost** | Allocates Python Request, executes ASGI middleware stack, generates Python 404 response | Instant C rejection via single memory buffer write (~25 ns) without acquiring the Python GIL |
| **Path Slicing** | Creates intermediate Python `str` substring objects for each parameter | Zero-copy pointer slices (`bt_slice_t`) recorded directly within a 64 KB C memory arena |
