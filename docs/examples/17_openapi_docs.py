"""
17_openapi_docs.py - Interactive OpenAPI 3.1, Swagger UI & Scalar UI
====================================================================

Modern web APIs require interactive documentation. Buckthorn automatically
inspects your route signatures, PEP 484 type annotations, `Path`/`Query` parameters,
and `Schema` models to generate an **OpenAPI 3.1.0** specification at application
startup.

Built-in Interactive Documentation Interfaces:
1. **OpenAPI Specification (`/openapi.json`):** Full OpenAPI 3.1 schema including
   parameter constraints (`minimum`, `exclusiveMinimum`, `minLength`), JSON Schema
   definitions, and response status codes.
2. **Swagger UI (`/docs`):** The classic, interactive API playground with "Try it out"
   functionality.
3. **Scalar UI (`/scalar`):** A modern, sleek, developer-first documentation UI
   with built-in dark mode and client SDK code snippets.

This example demonstrates:
- Customizing application metadata (`title`, `version`, `openapi_url`, `docs_url`, `scalar_url`).
- Adding tags and route descriptions for clean documentation categorization.
- Serving `/openapi.json`, `/docs`, and `/scalar`.

To run this live server in development mode:
    buckthorn dev docs/examples/17_openapi_docs.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/17_openapi_docs.py
"""

from buckthorn import Buckthorn, APIRouter, Schema, Field, Path, Query, status

# 1. Initialize Buckthorn with Documentation URLs configured
app = Buckthorn(
    title="Petstore Inventory API",
    version="2.5.0",
    openapi_url="/openapi.json",  # Set to None to disable in production
    docs_url="/docs",            # Swagger UI endpoint
    scalar_url="/scalar",        # Scalar UI endpoint
)


# ==============================================================================
# Domain Schemas (Will appear in OpenAPI `components.schemas`)
# ==============================================================================
class PetCreate(Schema):
    name: str = Field(min_length=2, max_length=50)
    species: str = Field(min_length=2, max_length=30)
    age_years: int = Field(ge=0, le=30)
    vaccinated: bool = False
    tags: list[str] = []


class PetOut(Schema):
    pet_id: int
    name: str
    species: str
    age_years: int
    vaccinated: bool


# ==============================================================================
# Modular Routers with OpenAPI Tags
# ==============================================================================
pets_router = APIRouter(prefix="/pets", tags=["Pets Management"])


@pets_router.post("/", response_model=PetOut, status_code=status.HTTP_201_CREATED)
def add_new_pet(pet_in: PetCreate):
    """Registers a new pet into the shelter inventory."""
    return PetOut(
        pet_id=101,
        name=pet_in.name,
        species=pet_in.species,
        age_years=pet_in.age_years,
        vaccinated=pet_in.vaccinated,
    )


@pets_router.get("/{pet_id}", response_model=PetOut)
def get_pet_details(
    pet_id: int = Path(ge=1),
    include_medical: bool = Query(default=False),
):
    """Retrieves full profile for an adopted or sheltered pet."""
    return PetOut(
        pet_id=pet_id,
        name="Golden Retriever",
        species="Canine",
        age_years=3,
        vaccinated=True,
    )


app.include_router(pets_router)


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 17_openapi_docs.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Verify OpenAPI 3.1.0 JSON Schema
    res_spec = client.get("/openapi.json")
    assert res_spec.status_code == 200
    spec = res_spec.json()
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["title"] == "Petstore Inventory API"
    assert spec["info"]["version"] == "2.5.0"
    assert "/pets" in spec["paths"]
    assert "/pets/{pet_id}" in spec["paths"]

    # Verify component schemas generated
    schemas = spec["components"]["schemas"]
    assert "PetCreate" in schemas
    assert "PetOut" in schemas
    assert schemas["PetCreate"]["properties"]["age_years"]["minimum"] == 0
    print(f"  ✓ OpenAPI 3.1.0 Spec Generated -> 200 (Schemas: {list(schemas.keys())})")

    # 2. Verify Swagger UI (/docs)
    res_docs = client.get("/docs")
    assert res_docs.status_code == 200
    assert "text/html" in res_docs.headers["Content-Type"]
    assert b"SwaggerUIBundle" in res_docs.body
    print(f"  ✓ Swagger UI HTML -> 200 at /docs")

    # 3. Verify Scalar UI (/scalar)
    res_scalar = client.get("/scalar")
    assert res_scalar.status_code == 200
    assert "text/html" in res_scalar.headers["Content-Type"]
    assert b"@scalar/api-reference" in res_scalar.body
    print(f"  ✓ Scalar UI HTML -> 200 at /scalar")

    print("\n✅ All 17_openapi_docs.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/17_openapi_docs.py:app")
    print("👉 View Swagger: http://127.0.0.1:8000/docs")
    print("👉 View Scalar:  http://127.0.0.1:8000/scalar")
