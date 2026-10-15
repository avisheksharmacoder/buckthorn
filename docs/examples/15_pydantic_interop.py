"""
15_pydantic_interop.py - Interoperability with Pydantic v2
===========================================================

Many existing Python codebases rely heavily on Pydantic v2 `BaseModel` for
data validation and serialization. When moving to or integrating with Buckthorn,
developers want to know:
1. How does Buckthorn `Schema` compare with Pydantic `BaseModel`?
2. Can I use existing Pydantic models in my application logic?
3. How do I convert between Buckthorn `Schema` and Pydantic `BaseModel`?

Architecture Comparison:
- **Pydantic v2 `BaseModel`:** Built on `pydantic-core` (Rust), but still instantiates
  Python dictionary-backed model instances on the Python heap for every request.
- **Buckthorn `Schema`:** Compiles down to C11 `yyjson` validators in Dev mode,
  and compiles into native Cython `@cython.freelist(4096) cdef class` structs with
  unboxed C variables (`cdef public long long`, `cdef public double`) in Prod mode.

This example demonstrates:
- Side-by-side definitions of Pydantic v2 and Buckthorn models.
- Receiving requests via Buckthorn's zero-copy C schema parser.
- Converting Buckthorn `Schema` into Pydantic models via `.model_dump()`.
- Returning converted data back to the client.

To run this live server in development mode:
    buckthorn dev docs/examples/15_pydantic_interop.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/15_pydantic_interop.py
"""

from pydantic import BaseModel, Field as PydanticField
from buckthorn import Buckthorn, Schema, Field, status

app = Buckthorn(
    title="Pydantic v2 Interoperability API",
    version="1.0.0",
)


# ==============================================================================
# 1. Side-by-Side Model Definition
# Notice how Buckthorn Schema syntax mirrors Pydantic v2 almost 1-to-1!
# ==============================================================================

# A. Standard Pydantic v2 Model (used by legacy services or external libraries)
class PydanticCustomer(BaseModel):
    customer_id: int = PydanticField(ge=1)
    full_name: str
    email: str
    vip_status: bool = False


# B. Buckthorn Native Schema (compiled in C for >100,000 RPS hot-path ingestion)
class BuckthornCustomer(Schema):
    customer_id: int = Field(ge=1)
    full_name: str = Field(min_length=2, max_length=50)
    email: str = Field(min_length=5, max_length=50)
    vip_status: bool = False


# ==============================================================================
# 2. Existing Business Logic that Expects a Pydantic Model
# ==============================================================================
def legacy_crm_process_customer(customer: PydanticCustomer) -> dict:
    """Simulates an existing business service that requires a Pydantic BaseModel."""
    assert isinstance(customer, PydanticCustomer)
    return {
        "crm_registered": True,
        "vip": customer.vip_status,
        "customer_tag": f"CRM_{customer.customer_id}",
    }


# ==============================================================================
# 3. Route Handlers: Ingest in C, Bridge to Pydantic
# ==============================================================================
@app.post("/customers", response_model=BuckthornCustomer, status_code=status.HTTP_201_CREATED)
def register_customer(cust_in: BuckthornCustomer):
    """
    1. Ingestion: Buckthorn parses incoming JSON in C using yyjson into `cust_in`.
    2. Bridging: Convert to Pydantic using `cust_in.model_dump()`.
    3. Business Logic: Pass the Pydantic instance into existing CRM tools.
    4. Outbound: Return BuckthornCustomer directly for zero-overhead C serialization.
    """
    # Convert Buckthorn Schema -> Pydantic BaseModel
    pydantic_obj = PydanticCustomer(**cust_in.model_dump())

    # Pass to existing legacy code
    crm_result = legacy_crm_process_customer(pydantic_obj)
    assert crm_result["crm_registered"] is True

    # Return native Buckthorn model (filtered and serialized in C)
    return cust_in


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 15_pydantic_interop.py in-memory verification...")
    client = RawTestClient(app)

    customer_payload = {
        "customer_id": 1001,
        "full_name": "Evelyn Reed",
        "email": "evelyn@reed-technologies.com",
        "vip_status": True,
    }

    res = client.post("/customers", json=customer_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["customer_id"] == 1001
    assert data["full_name"] == "Evelyn Reed"
    assert data["vip_status"] is True

    print(f"  ✓ Customer Ingested via yyjson C -> Bridged to Pydantic -> Returned 201")
    print(f"    Payload: {data}")

    # Verify C-level constraint rejection (customer_id < 1 violates ge=1)
    res_bad = client.post("/customers", json={**customer_payload, "customer_id": 0})
    assert res_bad.status_code == 422
    print(f"  ✓ C-Level Constraint Boundary (ge=1) -> 422 {res_bad.json()}")

    print("\n✅ All 15_pydantic_interop.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/15_pydantic_interop.py:app")
