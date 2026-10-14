# 15. Pre-Baked OpenAPI 3.1 & Interactive Documentation

## Overview
Interactive API documentation is essential for developer productivity and frontend integration. In most Python frameworks, generating the OpenAPI specification is a heavy runtime operation that inspects models and runs reflection routines repeatedly. Buckthorn introduces **Pre-Baked Static OpenAPI 3.1 Generation** at boot time, serving both **Swagger UI** (`/docs`) and **Scalar API Reference** (`/scalar`) with instant loading speeds.

---

## 1. Layman's Explanation
Imagine publishing a printed catalog for your company's products.
- In traditional web frameworks, whenever a customer asks to see the catalog, the staff runs around the warehouse taking photographs, writing down dimensions, and printing a new book on the spot. If 100 people ask for the catalog, the printer runs 100 times, grinding the office to a halt.
- Buckthorn compiles and prints the master catalog once when the server boots up. When a developer or frontend engineer requests the documentation, Buckthorn hands them the pre-printed book instantly.

Even better, Buckthorn provides two gorgeous reading interfaces out of the box: the classic **Swagger UI** and the modern, high-aesthetic **Scalar** documentation suite.

---

## 2. Technical Explanation
1. **Boot-Time Schema Generation:**
   - In [buckthorn/openapi.py](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/openapi.py), `generate_openapi_spec()` inspects the application's registered routes, parameters, path segments, request schemas, response models, and security schemes.
   - It generates a compliant **OpenAPI 3.1.0** specification dictionary.
2. **Pre-Baked JSON Caching:**
   - In [buckthorn/app.py](file:///home/avisheks/Documents/python-projects/Python-projects/buckthorn/buckthorn/app.py#L338-L343), the generated dictionary is cached in `self._openapi_schema_cache`.
   - When clients query `/openapi.json`, the pre-computed schema is returned immediately with zero dynamic reflection or schema rebuilding overhead.
3. **Dual Interactive Documentation Interfaces:**
   - **Swagger UI (`/docs`):** The trusted industry standard for testing endpoints, viewing payload schemas, and authorizing via OAuth2 / API keys.
   - **Scalar API Reference (`/scalar`):** A modern, dark-mode-first, high-aesthetic documentation UI featuring built-in code generators (cURL, Python, JavaScript, Go, Rust), request search, and interactive testing.
4. **Zero Third-Party Node / NPM Dependencies:**
   - Both interfaces are served via standalone, pre-rendered HTML templates loaded from modern CDNs. No Node.js build steps or local npm packages are required.

---

## 3. Code Example

```python
# openapi_docs_demo.py
from buckthorn import Buckthorn, Schema, Field

# 1. Initialize Buckthorn with Documentation URLs
app = Buckthorn(
    title="Enterprise Payments API",
    version="2.4.0",
    description="High-throughput payment gateway powered by Buckthorn.",
    docs_url="/docs",          # Swagger UI endpoint
    scalar_url="/scalar",      # Scalar API Reference endpoint
    openapi_url="/openapi.json" # Raw OpenAPI 3.1 JSON specification
)

# 2. Schemas with Rich Descriptions
class PaymentRequest(Schema):
    account_id: str = Field(min_length=8, max_length=16)
    amount_cents: int = Field(gt=0, le=10_000_000)
    currency: str = "USD"

class PaymentResponse(Schema):
    transaction_id: str
    status: str
    amount_cents: int
    currency: str

# 3. Documented Route Handlers
@app.post(
    "/payments/charge",
    response_model=PaymentResponse,
    status_code=201,
    tags=["Payments"],
    summary="Charge a payment account",
    description="Processes a synchronous payment charge against the specified account ID.",
)
def charge_payment(payload: PaymentRequest):
    return {
        "transaction_id": "tx_9988776655",
        "status": "succeeded",
        "amount_cents": payload.amount_cents,
        "currency": payload.currency,
    }

if __name__ == "__main__":
    print("Documentation available at:")
    print(" - Swagger UI: http://127.0.0.1:8000/docs")
    print(" - Scalar:     http://127.0.0.1:8000/scalar")
    app.run(host="127.0.0.1", port=8000)
```

---

## 4. Part-by-Part Code Explanation

1. `app = Buckthorn(title=..., version=..., docs_url="/docs", scalar_url="/scalar")`:
   - Configures the API title, version description, and documentation endpoints.
   - If you want to disable public documentation in production (for private internal microservices), simply pass `docs_url=None, scalar_url=None, openapi_url=None`.
2. `class PaymentRequest(Schema): ...`:
   - Field metadata (`min_length`, `ge`, `le`, defaults) is automatically mapped into OpenAPI JSON Schema constraints (`minimum`, `maximum`, `minLength`, `maxLength`).
3. `@app.post(..., tags=["Payments"], summary=..., description=...)`:
   - Attaches endpoint metadata.
   - The OpenAPI generator categorizes the route under the `Payments` tag in both Swagger UI and Scalar.
4. Visiting `http://127.0.0.1:8000/docs`:
   - Renders the full Swagger UI where developers can execute "Try it out" requests directly from their web browser.
5. Visiting `http://127.0.0.1:8000/scalar`:
   - Renders the next-generation Scalar reference with dark mode, interactive testing, and multi-language code snippets.

---

## 5. Why It Is Different From Other Python Frameworks

| Feature | Standard Python (FastAPI / Starlette) | Flask / Django REST Framework | Buckthorn |
|---|---|---|---|
| **OpenAPI Version** | OpenAPI 3.1 via Pydantic model inspection | Requires `flasgger` / `drf-spectacular` plugins | **Native OpenAPI 3.1.0** built-in |
| **Generation Overhead** | Evaluated on initial request and partially cached | Dynamic introspection on request | **Pre-baked and cached at boot time** |
| **Documentation UIs** | Swagger UI and ReDoc | Swagger UI (via plugin) | **Swagger UI AND next-gen Scalar** |
| **Scalar Integration** | Requires third-party community package | Not natively supported | **Built directly into the core framework** |
| **Zero-Reflection Serving** | Serves dynamic Python dictionaries | High latency | **Static pre-serialized JSON buffer** |
