# buckthorn/app.py
import inspect
from typing import Callable, get_type_hints
from buckthorn._core import CoreRouter, CompiledParamSpec, Request
from buckthorn.params import ParamInfo, PathInfo, QueryInfo, _UNDEFINED

_TYPE_MAP = {
    int: 1,      # TYPE_INT
    float: 2,    # TYPE_FLOAT
    str: 3,      # TYPE_STR
    Request: 4,  # TYPE_REQUEST
}


def _extract_path_param_names(path: str) -> list[str]:
    names: list[str] = []
    for seg in path.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            raw_name = seg[1:-1].split(":")[0]
            names.append(raw_name)
    return names


def _compile_route_params(path: str, endpoint: Callable) -> list[CompiledParamSpec]:
    sig = inspect.signature(endpoint)
    if not sig.parameters:
        return []

    hints = get_type_hints(endpoint)
    path_param_names = _extract_path_param_names(path)
    specs: list[CompiledParamSpec] = []

    for arg_name, param in sig.parameters.items():
        annotation = hints.get(arg_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = str

        if annotation is Request:
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=5,      # SRC_REQUEST
                    param_type=4,  # TYPE_REQUEST
                    required=True,
                )
            )
            continue

        param_type = _TYPE_MAP.get(annotation, 3)
        default_obj = param.default

        if isinstance(default_obj, ParamInfo):
            info = default_obj
        elif arg_name in path_param_names:
            info = PathInfo()
        else:
            info = QueryInfo(
                default=_UNDEFINED if default_obj is inspect.Parameter.empty else default_obj
            )

        path_idx = path_param_names.index(arg_name) if arg_name in path_param_names else -1
        source = 1 if path_idx >= 0 else info.source_id
        lookup_name = info.alias or arg_name

        specs.append(
            CompiledParamSpec(
                arg_name=arg_name,
                lookup_name=lookup_name,
                source=source,
                param_type=param_type,
                path_index=path_idx,
                required=True if source == 1 else info.required,
                default_val=None if info.default is _UNDEFINED else info.default,
                ge=info.ge,
                le=info.le,
                gt=info.gt,
                lt=info.lt,
                min_length=info.min_length,
                max_length=info.max_length,
            )
        )

    return specs


class Buckthorn:
    def __init__(self, title: str = "Buckthorn", version: str = "0.2.0"):
        self.title = title
        self.version = version
        self._core = CoreRouter()

    def add_api_route(self, path: str, endpoint: Callable, methods: list[str]) -> None:
        param_specs = _compile_route_params(path, endpoint)
        for method in methods:
            self._core.add_route(method.upper(), path, endpoint, param_specs)

    def get(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["GET"])
            return fn
        return decorator

    def post(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["POST"])
            return fn
        return decorator

    def put(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["PUT"])
            return fn
        return decorator

    def delete(self, path: str):
        def decorator(fn: Callable) -> Callable:
            self.add_api_route(path, fn, ["DELETE"])
            return fn
        return decorator

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self._core.run_server(host, port)
