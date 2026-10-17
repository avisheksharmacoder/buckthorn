# buckthorn/testing.py
import inspect
import json as _py_json
from typing import Any, Optional
from buckthorn.app import Buckthorn
from buckthorn._core import (
    CythonRawTestClient,
    RawResponse,
    WebSocket,
    encode_client_masked_ws_frame,
)


class _TestWebSocketSession:
    def __init__(self, app: Buckthorn, path: str):
        self.app = app
        self.path = path
        self.ws = WebSocket(path=path, pubsub_registry=app._core._ws_pubsub)
        self.handshake_status = 101
        self.accept_key = self.ws.compute_handshake("dGhlIHNhbXBsZSBub25jZQ==")
        self._fn = None
        self._kwargs: dict[str, Any] = {}
        self._executed = False

    def __enter__(self) -> "_TestWebSocketSession":
        if self.path in self.app._core._ws_routes:
            self._fn, _ = self.app._core._ws_routes[self.path]
            self._kwargs = {}
            return self

        req_segs = [s for s in self.path.strip("/").split("/") if s]
        for pattern, (fn, param_names) in self.app._core._ws_routes.items():
            pat_segs = [s for s in pattern.strip("/").split("/") if s]
            if len(req_segs) != len(pat_segs):
                continue
            matched = True
            extracted: dict[str, Any] = {}
            for r_seg, p_seg in zip(req_segs, pat_segs):
                if p_seg.startswith("{") and p_seg.endswith("}"):
                    pname = p_seg[1:-1].split(":")[0]
                    extracted[pname] = r_seg
                elif r_seg != p_seg:
                    matched = False
                    break
            if matched:
                self._fn = fn
                self._kwargs = extracted
                break
        if self._fn is None:
            raise ValueError(f"No WebSocket route matching {self.path}")
        return self

    def _ensure_executed(self) -> None:
        if self._executed:
            return
        self._executed = True
        sig_params = getattr(self._fn, "_bt_sig_params", None)
        if sig_params is None:
            sig = inspect.signature(self._fn)
            sig_params = tuple(sig.parameters.keys())
            setattr(self._fn, "_bt_sig_params", sig_params)
        call_kwargs = dict(self._kwargs)
        for name in sig_params:
            if name not in call_kwargs:
                call_kwargs[name] = self.ws
        coro = self._fn(**call_kwargs)
        if inspect.iscoroutine(coro):
            try:
                coro.send(None)
            except StopIteration:
                pass
            finally:
                coro.close()

    def send_text(self, text: str) -> None:
        masked_wire = encode_client_masked_ws_frame(text)
        self.ws.feed_raw_frame(masked_wire)
        self._ensure_executed()

    def receive_text(self) -> str:
        self._ensure_executed()
        return self.ws.pop_outgoing_text()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.ws.closed = True


class RawTestClient:
    def __init__(self, app: Buckthorn):
        self.app = app
        self._client = CythonRawTestClient(app._core)
        # Direct Cython method binding to eliminate Python wrapper method lookup in benchmark loops
        self.send_raw = self._client.send_raw

    def __enter__(self) -> "RawTestClient":
        self.app._startup()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.app._shutdown()

    def send_raw(self, raw_http: bytes) -> RawResponse:
        return self._client.send_raw(raw_http)

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[dict[str, str]] = None,
        body: bytes = b"",
        json: Optional[Any] = None,
        files: Optional[dict[str, tuple[str, bytes, str]]] = None,
    ) -> RawResponse:
        req_headers = dict(headers) if headers else {}
        if json is not None:
            body = _py_json.dumps(json, separators=(",", ":")).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif files is not None:
            boundary = "BuckthornBoundary7MA4YWxkTrZu0gW"
            parts: list[bytes] = []
            for field_name, (filename, file_bytes, content_type) in files.items():
                part_hdr = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8")
                parts.append(part_hdr + file_bytes + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode("utf-8"))
            body = b"".join(parts)
            req_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

        has_host = any(k.lower() == "host" for k in req_headers)
        hdr_lines = [f"{method.upper()} {path} HTTP/1.1"]
        if not has_host:
            hdr_lines.append("Host: localhost")
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
        files: Optional[dict[str, tuple[str, bytes, str]]] = None,
    ) -> RawResponse:
        return self.request("POST", path, headers=headers, body=body, json=json, files=files)

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

    def options(self, path: str, headers: Optional[dict[str, str]] = None) -> RawResponse:
        return self.request("OPTIONS", path, headers=headers)

    def websocket_connect(self, path: str) -> _TestWebSocketSession:
        return _TestWebSocketSession(self.app, path)

    @property
    def arena_offset(self) -> int:
        return self._client.arena_offset
