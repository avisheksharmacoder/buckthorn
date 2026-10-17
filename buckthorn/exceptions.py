# buckthorn/exceptions.py
from typing import Any, Optional


class HTTPException(Exception):
    __slots__ = ("status_code", "detail", "headers")

    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__()
        self.status_code = int(status_code)
        self.detail = detail if detail is not None else "HTTP Error"
        self.headers = headers

    def __repr__(self) -> str:
        return f"HTTPException(status_code={self.status_code!r}, detail={self.detail!r})"

    def __str__(self) -> str:
        return f"{self.status_code}: {self.detail}"
