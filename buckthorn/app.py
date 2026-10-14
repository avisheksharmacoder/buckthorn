# buckthorn/app.py
import inspect
from typing import Any, Callable, Optional, Sequence, Type, get_type_hints
from buckthorn._core import (
    CoreRouter,
    CompiledDepStep,
    CompiledParamSpec,
    Request,
)
from buckthorn.params import DependsInfo, ParamInfo, PathInfo, QueryInfo, SecurityInfo, _UNDEFINED
from buckthorn.schema import Schema
from buckthorn.logging import RingLogger
from buckthorn.security import SecurityBase

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


def _get_callable_target_for_signature(fn: Any) -> Any:
    if isinstance(fn, type):
        return fn.__init__
    if not inspect.isroutine(fn) and callable(fn):
        return fn.__call__
    return fn


def _compile_callable_params(
    path: str,
    target_callable: Callable[..., Any],
    dep_steps: list[CompiledDepStep],
    cache_slot_map: dict[Any, int],
) -> list[CompiledParamSpec]:
    sig_target = _get_callable_target_for_signature(target_callable)
    sig = inspect.signature(sig_target)
    if not sig.parameters:
        return []

    try:
        hints = get_type_hints(sig_target)
    except Exception:
        hints = {}

    path_param_names = _extract_path_param_names(path)
    specs: list[CompiledParamSpec] = []

    for arg_name, param in sig.parameters.items():
        if arg_name in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        raw_annotation = hints.get(arg_name, param.annotation)
        annotation = str if raw_annotation is inspect.Parameter.empty else raw_annotation

        default_obj = param.default

        # 1. Check if parameter is a `Security(...)` or `Depends(...)` injection
        if isinstance(default_obj, DependsInfo):
            dep_fn = default_obj.dependency
            if dep_fn is None:
                if raw_annotation is inspect.Parameter.empty:
                    raise ValueError(f"Depends() on '{arg_name}' requires a callable or type hint")
                dep_fn = raw_annotation

            # Fast C Security Guard Path (OpenSSL JWT / Constant-Time APIKey / Basic Auth)
            if isinstance(dep_fn, SecurityBase):
                scopes = default_obj.scopes if isinstance(default_obj, SecurityInfo) else []
                c_sec_spec = dep_fn.compile_c_spec(scopes=scopes)
                specs.append(
                    CompiledParamSpec(
                        arg_name=arg_name,
                        lookup_name=arg_name,
                        source=8,      # SRC_SECURITY
                        param_type=9,  # TYPE_SECURITY
                        required=True,
                        security_spec=c_sec_spec,
                    )
                )
                continue

            slot_idx = _register_dep_step(
                path, dep_fn, default_obj.use_cache, dep_steps, cache_slot_map
            )
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=7,      # SRC_DEPENDS
                    param_type=8,  # TYPE_DEPENDS
                    required=True,
                    dep_slot_index=slot_idx,
                )
            )
            continue

        # 2. Check if parameter is a zero-copy `Request` object
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

        # 3. Check if parameter is a `buckthorn.Schema` JSON body
        if isinstance(annotation, type) and issubclass(annotation, Schema):
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=6,      # SRC_BODY_SCHEMA
                    param_type=7,  # TYPE_SCHEMA
                    required=True,
                    schema_spec=annotation.__bt_schema_spec__,
                )
            )
            continue

        # 4. Path, Query, Header, or Cookie parameter
        param_type = _TYPE_MAP.get(annotation, 3)

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


def _register_dep_step(
    path: str,
    dep_callable: Callable[..., Any],
    use_cache: bool,
    dep_steps: list[CompiledDepStep],
    cache_slot_map: dict[Any, int],
) -> int:
    if use_cache and dep_callable in cache_slot_map:
        return cache_slot_map[dep_callable]

    # Recursively compile sub-dependencies first (post-order topological sort)
    sub_param_specs = _compile_callable_params(
        path, dep_callable, dep_steps, cache_slot_map
    )
    slot_idx = len(dep_steps)
    step = CompiledDepStep(dep_callable, slot_idx, sub_param_specs)
    dep_steps.append(step)

    if use_cache:
        cache_slot_map[dep_callable] = slot_idx

    return slot_idx


def _compile_route_plan(
    path: str,
    endpoint: Callable[..., Any],
    route_dependencies: Optional[Sequence[DependsInfo]] = None,
) -> tuple[list[CompiledParamSpec], list[CompiledDepStep]]:
    dep_steps: list[CompiledDepStep] = []
    cache_slot_map: dict[Any, int] = {}

    # 1. Compile route-level / app-level `dependencies=[Depends(...)]` guards first
    if route_dependencies:
        for dep_info in route_dependencies:
            if isinstance(dep_info, DependsInfo) and dep_info.dependency is not None:
                _register_dep_step(
                    path,
                    dep_info.dependency,
                    dep_info.use_cache,
                    dep_steps,
                    cache_slot_map,
                )

    # 2. Compile endpoint signature parameters & nested `Depends(...)` DAG
    endpoint_param_specs = _compile_callable_params(
        path, endpoint, dep_steps, cache_slot_map
    )
    return endpoint_param_specs, dep_steps


class Buckthorn:
    def __init__(
        self,
        title: str = "Buckthorn",
        version: str = "0.5.0",
        dependencies: Optional[Sequence[DependsInfo]] = None,
        log_file: str = "",
        log_capacity: int = 65536,
        access_log: bool = False,
    ):
        self.title = title
        self.version = version
        self.dependencies = list(dependencies) if dependencies else []
        self._core = CoreRouter()
        self.logger = RingLogger(filepath=log_file, capacity=log_capacity)
        if access_log:
            self._core.set_access_logger(self.logger._c_logger)

    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        methods: list[str],
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ) -> None:
        combined_deps = list(self.dependencies)
        if dependencies:
            combined_deps.extend(dependencies)

        param_specs, dep_steps = _compile_route_plan(path, endpoint, combined_deps)
        resp_spec = (
            response_model.__bt_schema_spec__
            if (response_model is not None and issubclass(response_model, Schema))
            else None
        )
        for method in methods:
            self._core.add_route(
                method.upper(),
                path,
                endpoint,
                param_specs,
                resp_spec,
                status_code,
                dep_steps,
            )

    def get(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["GET"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def post(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["POST"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def put(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["PUT"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def delete(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["DELETE"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
            )
            return fn
        return decorator

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self._core.run_server(host, port)
