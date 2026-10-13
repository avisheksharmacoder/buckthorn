# buckthorn/params.py
from typing import Any, Optional

_UNDEFINED = Ellipsis


class ParamInfo:
    source_id: int = 2  # Default SRC_QUERY

    def __init__(
        self,
        default: Any = _UNDEFINED,
        *,
        alias: Optional[str] = None,
        ge: Optional[float] = None,
        le: Optional[float] = None,
        gt: Optional[float] = None,
        lt: Optional[float] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
    ):
        self.default = default
        self.required = default is _UNDEFINED
        self.alias = alias
        self.ge = ge
        self.le = le
        self.gt = gt
        self.lt = lt
        self.min_length = min_length
        self.max_length = max_length


class PathInfo(ParamInfo):
    source_id = 1  # SRC_PATH

    def __init__(self, default: Any = _UNDEFINED, **kwargs: Any):
        super().__init__(default=default, **kwargs)
        self.required = True


class QueryInfo(ParamInfo):
    source_id = 2  # SRC_QUERY


class HeaderInfo(ParamInfo):
    source_id = 3  # SRC_HEADER


class CookieInfo(ParamInfo):
    source_id = 4  # SRC_COOKIE


def Path(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return PathInfo(default=default, **kwargs)


def Query(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return QueryInfo(default=default, **kwargs)


def Header(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return HeaderInfo(default=default, **kwargs)


def Cookie(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return CookieInfo(default=default, **kwargs)
