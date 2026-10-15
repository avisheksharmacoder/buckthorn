"""
03_schemas_and_models.py - Native Schema Models & SIMD yyjson Serialization
=============================================================================

In Buckthorn, models are built using `buckthorn.Schema`. Unlike traditional
Python web frameworks that rely on pure-Python dictionary inspection or heavy
metaclasses, Buckthorn compiles your `Schema` definitions into high-speed C
struct validators backed by `yyjson`:

1. **Dev Mode:** Type annotations and `Field(...)` constraints are compiled
   into a `CompiledSchemaSpec` registry.
2. **Prod Mode:** The AST-to-Cython transpiler (`buckthorn build --prod`) compiles
   every `Schema` into a native Cython `@cython.freelist(4096) cdef class` with
   unboxed C variables (`cdef public long long`, `cdef public double`, etc.).
3. **Response Model Filtering:** Declaring `response_model=PublicSchema` automatically
   filters internal fields in C before writing to the network socket.

This example demonstrates:
- Declaring schemas with `int`, `float`, `str`, `bool`, and `list` fields.
- Using `Field(...)` for numeric bounds (`gt`, `le`, `ge`, `lt`) and string lengths (`min_length`, `max_length`).
- Using default values and mutable list defaults safely.
- Response model filtering with `response_model=...`.
- Exporting data with `schema_instance.model_dump()`.

To run this live server in development mode:
    buckthorn dev docs/examples/03_schemas_and_models.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/03_schemas_and_models.py
"""

from typing import Optional
from buckthorn import Buckthorn, Schema, Field, status

app = Buckthorn(
    title="Schemas & Serialization API",
    version="1.0.0",
)


# 1. Incoming Request Model
class UserCreate(Schema):
    username: str = Field(min_length=3, max_length=20)
    email: str = Field(min_length=5, max_length=50)
    age: int = Field(ge=18, le=120)
    is_premium: bool = False
    interests: list[str] = []


# 2. Outgoing Response Model (Filters out internal/sensitive fields)
class UserPublic(Schema):
    username: str
    email: str
    is_premium: bool
    interests: list[str]


# In-memory mock store
users_db: dict[str, UserCreate] = {}


# 3. Endpoint accepting UserCreate and returning filtered UserPublic
@app.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate):
    """
    Accepts incoming JSON, validates types and constraints in C,
    and returns only the public fields declared in UserPublic.
    """
    # Notice: user is an instance of UserCreate
    # You can access attributes directly with zero Python dict overhead
    users_db[user.username] = user

    # You can call .model_dump() just like Pydantic v2!
    user_dict = user.model_dump()
    assert isinstance(user_dict, dict)

    # Returning UserCreate directly: Buckthorn's C serializer filters it
    # down to the fields declared in `response_model=UserPublic` (age is omitted).
    return user


@app.get("/users/{username}", response_model=UserPublic)
def get_user(username: str):
    """Fetches an existing user."""
    user = users_db.get(username)
    if not user:
        return {"error": "User not found"}
    return user


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 03_schemas_and_models.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Valid user creation
    payload = {
        "username": "alice",
        "email": "alice@example.com",
        "age": 28,
        "is_premium": True,
        "interests": ["cython", "high-performance", "c11"],
    }
    res = client.post("/users", json=payload)
    assert res.status_code == 201
    created_data = res.json()
    assert created_data["username"] == "alice"
    assert created_data["is_premium"] is True
    # Verify that `age` was filtered out by `response_model=UserPublic`
    assert "age" not in created_data
    print(f"  ✓ User created & filtered -> 201 {created_data}")

    # 2. Verify optional fields and default values
    minimal_payload = {
        "username": "bob",
        "email": "bob@example.com",
        "age": 30,
    }
    res_min = client.post("/users", json=minimal_payload)
    assert res_min.status_code == 201
    min_data = res_min.json()
    assert min_data["is_premium"] is False
    assert min_data["interests"] == []
    print(f"  ✓ Default values applied -> 201 {min_data}")

    # 3. Invalid payload (age < 18 violates ge=18 constraint)
    invalid_payload = {
        "username": "charlie",
        "email": "charlie@example.com",
        "age": 16,
    }
    res_err = client.post("/users", json=invalid_payload)
    assert res_err.status_code == 422
    print(f"  ✓ C-Level Constraint Rejection (ge=18) -> 422 {res_err.json()}")

    print("\n✅ All 03_schemas_and_models.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/03_schemas_and_models.py:app")
