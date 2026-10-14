# buckthorn/middleware.py
from typing import Sequence
from buckthorn._core import CoreRouter, MiddlewareResponse as Response


class CORSMiddleware:
    def __init__(
        self,
        allow_origins: Sequence[str] = ("*",),
        allow_methods: Sequence[str] = ("GET", "POST", "PUT", "DELETE", "OPTIONS"),
        allow_headers: Sequence[str] = ("*",),
    ):
        self.allow_origin = allow_origins[0] if allow_origins else "*"
        self.allow_methods = ", ".join(allow_methods)
        self.allow_headers = ", ".join(allow_headers)

    def install_c(self, core: CoreRouter) -> None:
        core.configure_cors(self.allow_origin, self.allow_methods, self.allow_headers)


class RateLimitMiddleware:
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = int(max_requests)
        self.window_seconds = int(window_seconds)

    def install_c(self, core: CoreRouter) -> None:
        core.configure_ratelimit(self.max_requests, self.window_seconds)


class TrustedHostMiddleware:
    def __init__(self, allowed_hosts: Sequence[str] = ("*",)):
        self.allowed_host = allowed_hosts[0] if allowed_hosts else "*"

    def install_c(self, core: CoreRouter) -> None:
        core.configure_trusted_host(self.allowed_host)


class SecurityHeadersMiddleware:
    def __init__(
        self,
        x_frame_options: str = "DENY",
        x_content_type_options: str = "nosniff",
        strict_transport_security: str = "max-age=31536000; includeSubDomains",
    ):
        self.x_frame_options = x_frame_options
        self.x_content_type_options = x_content_type_options
        self.strict_transport_security = strict_transport_security

    def install_c(self, core: CoreRouter) -> None:
        core.configure_sec_headers(
            self.x_frame_options,
            self.x_content_type_options,
            self.strict_transport_security,
        )


__all__ = [
    "CORSMiddleware",
    "RateLimitMiddleware",
    "TrustedHostMiddleware",
    "SecurityHeadersMiddleware",
    "Response",
]
