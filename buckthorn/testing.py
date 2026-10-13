# buckthorn/testing.py
import json as _py_json
from typing import Any, Optional
from buckthorn.app import Buckthorn
from buckthorn._core import CythonRawTestClient, RawResponse


class RawTestClient:
    def __init__(self, app: Buckthorn):
        self._client = CythonRawTestClient(app._core)

    def send_raw(self, raw_http: bytes) -> RawResponse:
        return self._client.send_raw(raw_http)

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[dict[str, str]] = None,
        body: bytes = b"",
        json: Optional[Any] = None,
    ) -> RawResponse:
        req_headers = dict(headers) if headers else {}
        if json is not None:
            body = _py_json.dumps(json, separators=(",", ":")).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

        hdr_lines = [f"{method.upper()} {path} HTTP/1.1", "Host: localhost"]
        for k, v in req_headers.items():
            hdr_lines.append(f"{k}: {v}")
        if body:
            hdr_lines.append(f"Content-Length: {len(body)}")
        raw_head = ("\r\n".join(hdr_lines) + "\r\n\r\n").encode("utf-8")
        return self._client.send_raw(raw_head + body)

    def get(self, path: str, headers: Optional[dict[str, str]] = None) -> RawResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        body: bytes = b"",
        headers: Optional[dict[str, str]] = None,
        json: Optional[Any] = None,
    ) -> RawResponse:
        return self.request("POST", path, headers=headers, body=body, json=json)

    def put(
        self,
        path: str,
        body: bytes = b"",
        headers: Optional[dict[str, str]] = None,
        json: Optional[Any] = None,
    ) -> RawResponse:
        return self.request("PUT", path, headers=headers, body=body, json=json)

    def delete(self, path: str, headers: Optional[dict[str, str]] = None) -> RawResponse:
        return self.request("DELETE", path, headers=headers)

    @property
    def arena_offset(self) -> int:
        return self._client.arena_offset
