# buckthorn/security.py
import base64
import hashlib
import hmac
import json
from typing import Any, Optional, Sequence
from buckthorn._core import CompiledSecuritySpec, HTTPBasicCredentials
from buckthorn.params import Security, SecurityInfo


class SecurityBase:
    __bt_security_kind__: int = 0

    def compile_c_spec(self, scopes: Optional[Sequence[str]] = None) -> CompiledSecuritySpec:
        raise NotImplementedError


class HTTPBearer(SecurityBase):
    __bt_security_kind__ = 1  # SEC_KIND_JWT

    def __init__(self, *, jwt_secret: str, algorithms: Optional[list[str]] = None):
        self.jwt_secret = jwt_secret
        self.algorithms = algorithms or ["HS256"]

    def compile_c_spec(self, scopes: Optional[Sequence[str]] = None) -> CompiledSecuritySpec:
        return CompiledSecuritySpec(
            sec_kind=1,
            secret=self.jwt_secret,
            scopes=list(scopes) if scopes else [],
        )


class OAuth2PasswordBearer(HTTPBearer):
    def __init__(
        self,
        tokenUrl: str,
        *,
        jwt_secret: str,
        scopes: Optional[dict[str, str]] = None,
    ):
        super().__init__(jwt_secret=jwt_secret)
        self.tokenUrl = tokenUrl
        self.available_scopes = scopes or {}


class APIKeyHeader(SecurityBase):
    __bt_security_kind__ = 2  # SEC_KIND_APIKEY_HDR

    def __init__(self, *, name: str, expected_key: str = ""):
        self.name = name
        self.expected_key = expected_key

    def compile_c_spec(self, scopes: Optional[Sequence[str]] = None) -> CompiledSecuritySpec:
        return CompiledSecuritySpec(
            sec_kind=2,
            key_name=self.name,
            expected_val=self.expected_key,
        )


class APIKeyQuery(SecurityBase):
    __bt_security_kind__ = 3  # SEC_KIND_APIKEY_QUERY

    def __init__(self, *, name: str, expected_key: str = ""):
        self.name = name
        self.expected_key = expected_key

    def compile_c_spec(self, scopes: Optional[Sequence[str]] = None) -> CompiledSecuritySpec:
        return CompiledSecuritySpec(
            sec_kind=3,
            key_name=self.name,
            expected_val=self.expected_key,
        )


class HTTPBasic(SecurityBase):
    __bt_security_kind__ = 4  # SEC_KIND_HTTP_BASIC

    def __init__(self, *, expected_username: str = "", expected_password: str = ""):
        self.expected_username = expected_username
        self.expected_password = expected_password

    def compile_c_spec(self, scopes: Optional[Sequence[str]] = None) -> CompiledSecuritySpec:
        return CompiledSecuritySpec(
            sec_kind=4,
            expected_val=self.expected_username,
            expected_pass=self.expected_password,
        )


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_jwt_token(claims: dict[str, Any], secret: str, algorithm: str = "HS256") -> str:
    header_b64 = _b64url_encode(
        json.dumps({"alg": algorithm, "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(claims, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


__all__ = [
    "Security",
    "SecurityInfo",
    "SecurityBase",
    "HTTPBearer",
    "OAuth2PasswordBearer",
    "APIKeyHeader",
    "APIKeyQuery",
    "HTTPBasic",
    "HTTPBasicCredentials",
    "create_jwt_token",
]
