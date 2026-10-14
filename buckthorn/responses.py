# buckthorn/responses.py
import inspect
import os
from typing import Any, Optional


def _consume_async_or_sync_gen(gen: Any) -> list[str]:
    chunks: list[str] = []
    if inspect.isasyncgen(gen):
        while True:
            anext_coro = gen.__anext__()
            try:
                anext_coro.send(None)
            except StopAsyncIteration:
                break
            except StopIteration as stop:
                if stop.value is not None:
                    chunks.append(str(stop.value))
            finally:
                anext_coro.close()
    else:
        for item in gen:
            chunks.append(str(item))
    return chunks


class EventSourceResponse:
    """Server-Sent Events (SSE) streaming response."""
    __bt_custom_response__ = True

    def __init__(self, content: Any, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def _render_c_response(self) -> tuple[bytes, bytes, int]:
        chunks = _consume_async_or_sync_gen(self.content)
        body_bytes = "".join(chunks).encode("utf-8")
        hdr_bytes = (
            b"Content-Type: text/event-stream; charset=utf-8\r\n"
            b"Cache-Control: no-cache\r\n"
        )
        return body_bytes, hdr_bytes, self.status_code


class FileResponse:
    """Static file HTTP response."""
    __bt_custom_response__ = True

    def __init__(
        self,
        path: str,
        media_type: str = "application/octet-stream",
        filename: Optional[str] = None,
        status_code: int = 200,
    ):
        self.path = path
        self.media_type = media_type
        self.filename = filename or os.path.basename(path)
        self.status_code = status_code

    def _render_c_response(self) -> tuple[bytes, bytes, int]:
        with open(self.path, "rb") as f:
            body_bytes = f.read()
        safe_media = str(self.media_type).replace("\r", "").replace("\n", "").replace("\0", "")
        safe_fn = str(self.filename).replace("\r", "").replace("\n", "").replace("\0", "").replace('"', "")
        hdr_str = (
            f"Content-Type: {safe_media}\r\n"
            f'Content-Disposition: attachment; filename="{safe_fn}"\r\n'
        )
        return body_bytes, hdr_str.encode("utf-8"), self.status_code


class HTMLResponse:
    """Pre-encoded HTML HTTP response."""
    __bt_custom_response__ = True

    def __init__(self, content: str | bytes, status_code: int = 200):
        self.body_bytes = content.encode("utf-8") if isinstance(content, str) else content
        self.status_code = status_code
        self.hdr_bytes = b"Content-Type: text/html; charset=utf-8\r\n"

    def _render_c_response(self) -> tuple[bytes, bytes, int]:
        return self.body_bytes, self.hdr_bytes, self.status_code
