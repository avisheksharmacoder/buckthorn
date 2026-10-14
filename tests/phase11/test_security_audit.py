# tests/phase11/test_security_audit.py
import base64
import json
import time
import pytest
from buckthorn import Buckthorn, Request, Security, UploadFile, File
from buckthorn.security import HTTPBearer, create_jwt_token
from buckthorn.testing import RawTestClient


def test_audit1_http_request_smuggling_cl_te_and_duplicate_cl_rejected_in_c():
    app = Buckthorn(max_body_size=1024)

    @app.post("/transfer")
    def transfer():
        return {"transferred": True}

    client = RawTestClient(app)

    # 1. CL.TE Request Smuggling attempt (both Content-Length and Transfer-Encoding present) -> 400
    smuggle_cl_te = (
        b"POST /transfer HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Length: 6\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
        b"0\r\n\r\nG"
    )
    assert client.send_raw(smuggle_cl_te).status_code == 400

    # 2. Duplicate conflicting Content-Length headers -> 400
    smuggle_dup_cl = (
        b"POST /transfer HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Length: 2\r\n"
        b"Content-Length: 50\r\n\r\n"
        b"{}"
    )
    assert client.send_raw(smuggle_dup_cl).status_code == 400

    # 3. Missing Host header in HTTP/1.1 (RFC 9112 violation) -> 400
    missing_host = b"POST /transfer HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}"
    assert client.send_raw(missing_host).status_code == 400

    # 4. Duplicate Host headers -> 400
    dup_host = (
        b"POST /transfer HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Host: evil.com\r\n\r\n"
    )
    assert client.send_raw(dup_host).status_code == 400

    # 5. Payload exceeding max_body_size (1024 bytes) -> 413 Content Too Large in C
    oversized_body = b"A" * 2048
    res_413 = client.post("/transfer", body=oversized_body)
    assert res_413.status_code == 413


def test_audit2_and_audit4_url_path_traversal_and_null_bytes_blocked():
    app = Buckthorn()

    @app.get("/files/{name}")
    def read_file(name: str):
        return {"name": name}

    @app.post("/upload")
    async def upload(doc: UploadFile = File()):
        return {"safe_filename": doc.filename}

    client = RawTestClient(app)

    # 1. Raw dot-dot traversal in URL -> 400 in C
    assert client.get("/files/../../etc/passwd").status_code == 400

    # 2. URL-encoded dot-dot (%2e%2e) traversal in URL -> 400 in C
    assert client.get("/files/%2e%2e/secret").status_code == 400

    # 3. Null-byte injection (%00) in URL -> 400 in C
    assert client.get("/files/report.pdf%00.exe").status_code == 400

    # 4. Multipart UploadFile filename traversal stripped to basename in C
    res_up = client.post(
        "/upload",
        files={"doc": ("../../../etc/cron.d/backdoor.sh", b"echo pwn", "text/plain")},
    )
    assert res_up.status_code == 200
    assert res_up.json() == {"safe_filename": "backdoor.sh"}


def test_audit3_crlf_response_splitting_in_headers_neutralized():
    app = Buckthorn()

    @app.middleware("http")
    async def reflect_header_mw(request: Request, call_next):
        response = await call_next(request)
        # Attacker attempts CRLF injection via reflected header value
        untrusted = request.get_header("X-User-Input") or ""
        response.headers["X-Reflected"] = untrusted
        return response

    @app.get("/test-crlf")
    def test_crlf():
        return {"ok": True}

    client = RawTestClient(app)
    res = client.get(
        "/test-crlf",
        headers={"X-User-Input": "legit\r\nSet-Cookie: session=hijacked\r\nX-Injected: pwned"},
    )
    assert res.status_code == 200
    # Verify CRLF characters were stripped so Set-Cookie was NOT injected as a separate HTTP header
    assert "Set-Cookie" not in res.headers
    assert "X-Injected" not in res.headers


def test_audit5_jwt_alg_none_confusion_and_nbf_claim_enforced_in_c():
    app = Buckthorn()
    secret = "audit-jwt-secret"
    bearer = HTTPBearer(jwt_secret=secret)

    @app.get("/protected")
    def protected(claims: dict = Security(bearer)):
        return {"sub": claims["sub"]}

    client = RawTestClient(app)

    # 1. Construct a token with {"alg": "none"} signed with HMAC to test algorithm confusion
    hdr_none = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay_b64 = base64.urlsafe_b64encode(b'{"sub":"admin"}').rstrip(b"=").decode()
    import hmac, hashlib
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{hdr_none}.{pay_b64}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    alg_none_token = f"{hdr_none}.{pay_b64}.{sig}"

    res_alg = client.get("/protected", headers={"Authorization": f"Bearer {alg_none_token}"})
    assert res_alg.status_code == 401

    # 2. Token with `nbf` (Not Before) 1 hour in the future -> 401 in C
    future_nbf_token = create_jwt_token(
        {"sub": "alice", "nbf": int(time.time()) + 3600, "exp": int(time.time()) + 7200},
        secret=secret,
    )
    res_nbf = client.get("/protected", headers={"Authorization": f"Bearer {future_nbf_token}"})
    assert res_nbf.status_code == 401


def test_audit6_ring_logger_escapes_json_injection_payloads(tmp_path):
    log_file = tmp_path / "audit_safe.log"
    app = Buckthorn(log_file=str(log_file))

    @app.get("/log-test")
    def log_test():
        # Attacker attempts to break out of JSON string and inject `"admin":true`
        app.logger.info('login"\n,"admin":true,"msg":"', username='bob","role":"root')
        return {"logged": True}

    client = RawTestClient(app)
    client.get("/log-test")
    app.logger.flush_and_stop()

    raw_line = log_file.read_text(encoding="utf-8").strip()
    parsed = json.loads(raw_line)
    # Injected `"admin": true` and `"role": "root"` keys must NOT exist as top-level JSON keys!
    assert "admin" not in parsed
    assert "role" not in parsed
