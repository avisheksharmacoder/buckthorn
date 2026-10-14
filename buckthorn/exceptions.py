# buckthorn/exceptions.py
from typing import Any, Optional


class HTTPException(Exception):
    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = detail if detail is not None else "HTTP Error"
        self.headers = headers
