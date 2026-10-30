# buckthorn/responses.py
import inspect
import os
import stat
from typing import Any, Optional
from buckthorn._core import _step_coroutine_sync

_MAX_ARENA_BODY_BYTES = 32768


def _consume_async_or_sync_gen(gen: Any) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0

    if inspect.isasyncgen(gen):
        try:
            while True:
                try:
                    item = _step_coroutine_sync(gen.__anext__())
                except StopAsyncIteration:
                    break
                if item is not None:
                    b_chunk = item if isinstance(item, bytes) else str(item).encode("utf-8")
                    total_bytes += len(b_chunk)
                    if total_bytes > _MAX_ARENA_BODY_BYTES:
                        raise ValueError("EventSourceResponse stream exceeds 32KB arena buffer")
                    chunks.append(b_chunk)
        finally:
            try:
                _step_coroutine_sync(gen.aclose())
            except Exception:
                pass
    else:
        for item in gen:
            if item is not None:
                b_chunk = item if isinstance(item, bytes) else str(item).encode("utf-8")
                total_bytes += len(b_chunk)
                if total_bytes > _MAX_ARENA_BODY_BYTES:
                    raise ValueError("EventSourceResponse stream exceeds 32KB arena buffer")
                chunks.append(b_chunk)

    return b"".join(chunks)


class EventSourceResponse:
    """Server-Sent Events (SSE) streaming response."""
    __bt_custom_response__ = True
    _SSE_HDR_BYTES = (
        b"Content-Type: text/event-stream; charset=utf-8\r\n"
        b"Cache-Control: no-cache\r\n"
    )

    def __init__(self, content: Any, status_code: int = 200):
        self.content = content
        self.status_code = int(status_code)

    def _render_c_response(self) -> tuple[bytes, bytes, int]:
        body_bytes = _consume_async_or_sync_gen(self.content)
        return body_bytes, self._SSE_HDR_BYTES, self.status_code


class FileResponse:
    """Static file HTTP response with optional root_dir confinement and 32KB arena guard."""
    __bt_custom_response__ = True
    MAX_ARENA_FILE_BYTES = _MAX_ARENA_BODY_BYTES

    def __init__(
        self,
        path: str,
        media_type: str = "application/octet-stream",
        filename: Optional[str] = None,
        status_code: int = 200,
        root_dir: Optional[str] = None,
    ):
        real_path = os.path.realpath(path)
        if root_dir is not None:
            real_root = os.path.realpath(root_dir)
            if os.path.commonpath([real_path, real_root]) != real_root:
                raise PermissionError("FileResponse path escapes configured root_dir")

        self.path = real_path
        self.media_type = media_type
        self.filename = filename or os.path.basename(real_path)
        self.status_code = int(status_code)

        safe_media = (
            str(self.media_type)
            .replace("\r", "")
            .replace("\n", "")
            .replace("\0", "")
        )
        safe_fn = (
            str(self.filename)
            .replace("\r", "")
            .replace("\n", "")
            .replace("\0", "")
            .replace('"', "")
            .replace("\\", "")
        )
        self._cached_hdr_bytes = (
            f"Content-Type: {safe_media}\r\n"
            f'Content-Disposition: attachment; filename="{safe_fn}"\r\n'
        ).encode("utf-8")
        self._hdr_bytes = self._cached_hdr_bytes

    def _render_c_response(self) -> tuple[bytes, bytes, int]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError("FileResponse target must be a regular file")
            if st.st_size > self.MAX_ARENA_FILE_BYTES:
                raise ValueError("FileResponse file size exceeds 32KB arena capacity")
            body_bytes = os.read(fd, self.MAX_ARENA_FILE_BYTES)
        finally:
            os.close(fd)
        return body_bytes, self._cached_hdr_bytes, self.status_code


class HTMLResponse:
    """Pre-encoded HTML HTTP response."""
    __bt_custom_response__ = True
    _HTML_HDR_BYTES = b"Content-Type: text/html; charset=utf-8\r\n"

    def __init__(self, content: str | bytes, status_code: int = 200):
        self.body_bytes = content.encode("utf-8") if isinstance(content, str) else content
        self.status_code = int(status_code)
        self.hdr_bytes = self._HTML_HDR_BYTES

    def _render_c_response(self) -> tuple[bytes, bytes, int]:
        return self.body_bytes, self.hdr_bytes, self.status_code
