"""
10_security_and_jwt.py - OpenSSL C11 JWT, API Key & Basic Authentication
========================================================================

In conventional Python frameworks, security validation (JWT signature checks,
header parsing, and timing-attack mitigation) happens in pure Python, creating
large overheads on protected endpoints.

Buckthorn pushes authentication and security directly into compiled C:
1. **Constant-Time Verification:** Uses OpenSSL's `CRYPTO_memcmp` to prevent
   side-channel timing attacks.
2. **Pure C OpenSSL HMAC-SHA256:** Verifies JWT signatures, validates `exp`
   (expiration timestamps), and checks OAuth2 scopes in C before Python handlers run.
3. **Instant 401 / 403 Rejection:** Invalid credentials or missing tokens are
   rejected immediately in C without allocating Python frame objects.

This example demonstrates:
- OpenSSL JWT verification with `OAuth2PasswordBearer` and `Security(..., scopes=[...])`.
- Creating valid tokens with `create_jwt_token`.
- API Key verification via `APIKeyHeader`.
- HTTP Basic Authentication via `HTTPBasic`.

To run this live server in development mode:
    buckthorn dev docs/examples/10_security_and_jwt.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/10_security_and_jwt.py
"""

import time
from buckthorn import Buckthorn, Security
from buckthorn.security import (
    OAuth2PasswordBearer,
    APIKeyHeader,
    HTTPBasic,
    HTTPBasicCredentials,
    create_jwt_token,
)

app = Buckthorn(
    title="OpenSSL Security & Authentication API",
    version="1.0.0",
)

JWT_SECRET = "super-secret-hmac-sha256-key-production"

# 1. OAuth2 / JWT Bearer scheme with scopes
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/token",
    jwt_secret=JWT_SECRET,
    scopes={"read:reports": "Read reports", "admin": "Admin access"},
)

# 2. API Key scheme (checks "X-API-Key" header)
api_key_scheme = APIKeyHeader(name="X-API-Key", expected_key="buckthorn_secret_api_key_99")

# 3. HTTP Basic Auth scheme
basic_auth_scheme = HTTPBasic(expected_username="admin", expected_password="securepassword123")


# ==============================================================================
# Protected Route Handlers
# ==============================================================================

# 1. JWT Protected Endpoint requiring "read:reports" and "admin" scopes
@app.get("/secure/reports")
def read_reports(claims: dict = Security(oauth2_scheme, scopes=["read:reports", "admin"])):
    """
    OpenSSL validates the token signature, checks expiration, and ensures
    both "read:reports" and "admin" scopes exist in the token payload.
    """
    return {
        "status": "authorized",
        "user": claims.get("sub"),
        "role": claims.get("role"),
    }


# 2. API Key Protected Endpoint
@app.get("/secure/webhook-listener")
def webhook_listener(key: str = Security(api_key_scheme)):
    """Validates the X-API-Key header in constant time using OpenSSL in C."""
    return {"status": "ok", "service": "webhook_accepted"}


# 3. HTTP Basic Auth Protected Endpoint
@app.get("/secure/admin-panel")
def admin_panel(credentials: HTTPBasicCredentials = Security(basic_auth_scheme)):
    """Validates Authorization: Basic <base64> in C."""
    return {"status": "authenticated", "user": credentials.username}


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 10_security_and_jwt.py in-memory verification...")
    client = RawTestClient(app)

    # 1. Test JWT Missing Token -> 401 Unauthorized in C
    res_no_token = client.get("/secure/reports")
    assert res_no_token.status_code == 401
    print(f"  ✓ Missing Token -> 401 {res_no_token.json()}")

    # 2. Test JWT Invalid Scope -> 403 Forbidden in C
    token_limited = create_jwt_token(
        {"sub": "charlie", "scopes": ["read:reports"], "exp": int(time.time()) + 3600},
        secret=JWT_SECRET,
    )
    res_limited = client.get(
        "/secure/reports",
        headers={"Authorization": f"Bearer {token_limited}"},
    )
    assert res_limited.status_code == 403
    print(f"  ✓ Insufficient Scopes -> 403 {res_limited.json()}")

    # 3. Test JWT Valid Token with proper scopes
    token_valid = create_jwt_token(
        {"sub": "alice", "role": "lead_dev", "scopes": ["read:reports", "admin"], "exp": int(time.time()) + 3600},
        secret=JWT_SECRET,
    )
    res_valid = client.get(
        "/secure/reports",
        headers={"Authorization": f"Bearer {token_valid}"},
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["user"] == "alice"
    print(f"  ✓ Valid JWT & Scopes -> 200 {res_valid.json()}")

    # 4. Test API Key Header (Missing -> 401, Wrong -> 403, Correct -> 200)
    res_key_missing = client.get("/secure/webhook-listener")
    assert res_key_missing.status_code == 401
    print(f"  ✓ Missing API Key -> 401 {res_key_missing.json()}")

    res_key_bad = client.get("/secure/webhook-listener", headers={"X-API-Key": "wrong_key"})
    assert res_key_bad.status_code == 403
    print(f"  ✓ Invalid API Key -> 403 {res_key_bad.json()}")

    res_key_ok = client.get("/secure/webhook-listener", headers={"X-API-Key": "buckthorn_secret_api_key_99"})
    assert res_key_ok.status_code == 200
    assert res_key_ok.json()["status"] == "ok"
    print(f"  ✓ Valid API Key -> 200 {res_key_ok.json()}")

    # 5. Test HTTP Basic Auth
    import base64
    valid_basic = base64.b64encode(b"admin:securepassword123").decode("ascii")
    res_basic = client.get("/secure/admin-panel", headers={"Authorization": f"Basic {valid_basic}"})
    assert res_basic.status_code == 200
    assert res_basic.json()["user"] == "admin"
    print(f"  ✓ Valid Basic Auth -> 200 {res_basic.json()}")

    print("\n✅ All 10_security_and_jwt.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/10_security_and_jwt.py:app")
