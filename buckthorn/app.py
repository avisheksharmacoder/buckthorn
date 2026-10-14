# buckthorn/app.py
import inspect
import json as _py_json
from typing import Any, Callable, Optional, Sequence, Type, get_type_hints
from buckthorn._core import (
    BackgroundTasks,
    CoreRouter,
    CompiledDepStep,
    CompiledParamSpec,
    Request,
    UploadFile,
)
from buckthorn.logging import RingLogger
from buckthorn.openapi import (
    build_openapi_schema,
    get_scalar_ui_html,
    get_swagger_ui_html,
)
from buckthorn.params import (
    DependsInfo,
    FileInfo,
    ParamInfo,
    PathInfo,
    QueryInfo,
    SecurityInfo,
    _UNDEFINED,
)
from buckthorn.responses import HTMLResponse
from buckthorn.routing import APIRouter, _join_paths
from buckthorn.schema import Schema, get_schema_spec
from buckthorn.security import SecurityBase

_TYPE_MAP = {
    int: 1,      # TYPE_INT
    float: 2,    # TYPE_FLOAT
    str: 3,      # TYPE_STR
    Request: 4,  # TYPE_REQUEST
    bool: 5,     # TYPE_BOOL
    "int": 1,
    "float": 2,
    "str": 3,
    "bool": 5,
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

        annotation = hints.get(arg_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = str

        default_obj = param.default

        # 1. Check if parameter is a `Security(...)` or `Depends(...)` injection
        if isinstance(default_obj, DependsInfo):
            dep_fn = default_obj.dependency
            if dep_fn is None:
                if annotation is inspect.Parameter.empty:
                    raise ValueError(f"Depends() on '{arg_name}' requires a callable or type hint")
                dep_fn = annotation

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

        # 3. Check if parameter is `BackgroundTasks`
        if annotation is BackgroundTasks:
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=10,      # SRC_BACKGROUND_TASKS
                    param_type=11,  # TYPE_BACKGROUND_TASKS
                    required=True,
                )
            )
            continue

        # 4. Check if parameter is an `UploadFile` / `File(...)` multipart field
        if annotation is UploadFile or isinstance(default_obj, FileInfo):
            required = True
            if isinstance(default_obj, FileInfo):
                required = default_obj.required
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=9,       # SRC_UPLOAD_FILE
                    param_type=10,  # TYPE_UPLOAD_FILE
                    required=required,
                )
            )
            continue

        # 5. Check if parameter is a `buckthorn.Schema` JSON body
        schema_spec = get_schema_spec(annotation)
        if schema_spec is not None:
            specs.append(
                CompiledParamSpec(
                    arg_name=arg_name,
                    lookup_name=arg_name,
                    source=6,      # SRC_BODY_SCHEMA
                    param_type=7,  # TYPE_SCHEMA
                    required=True,
                    schema_spec=schema_spec,
                )
            )
            continue

        # 6. Path, Query, Header, or Cookie parameter
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

    endpoint_param_specs = _compile_callable_params(
        path, endpoint, dep_steps, cache_slot_map
    )
    return endpoint_param_specs, dep_steps


def _step_coro(coro: Any) -> Any:
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    finally:
        coro.close()


class Buckthorn:
    def __init__(
        self,
        title: str = "Buckthorn",
        version: str = "0.9.0",
        dependencies: Optional[Sequence[DependsInfo]] = None,
        lifespan: Optional[Callable[[Any], Any]] = None,
        openapi_url: Optional[str] = "/openapi.json",
        docs_url: Optional[str] = "/docs",
        scalar_url: Optional[str] = "/scalar",
        log_file: str = "",
        log_capacity: int = 65536,
        access_log: bool = False,
    ):
        self.title = title
        self.version = version
        self.dependencies = list(dependencies) if dependencies else []
        self.lifespan = lifespan
        self._lifespan_ctx: Any = None
        self.openapi_url = openapi_url
        self.docs_url = docs_url
        self.scalar_url = scalar_url

        self._core = CoreRouter()
        self.logger = RingLogger(filepath=log_file, capacity=log_capacity)
        if access_log:
            self._core.set_access_logger(self.logger._c_logger)

        self._routes_meta: list[dict[str, Any]] = []
        self._openapi_schema_cache: Optional[dict[str, Any]] = None
        self._openapi_bytes_cache: Optional[bytes] = None

        self._register_docs_routes()

    def _register_docs_routes(self) -> None:
        if self.openapi_url:
            def _serve_openapi_json() -> bytes:
                if self._openapi_bytes_cache is None:
                    spec = self.openapi()
                    self._openapi_bytes_cache = _py_json.dumps(
                        spec, separators=(",", ":")
                    ).encode("utf-8")
                return self._openapi_bytes_cache

            self._core.add_route("GET", self.openapi_url, _serve_openapi_json)

        if self.docs_url and self.openapi_url:
            swagger_resp = HTMLResponse(get_swagger_ui_html(self.title, self.openapi_url))
            self._core.add_route("GET", self.docs_url, lambda: swagger_resp)

        if self.scalar_url and self.openapi_url:
            scalar_resp = HTMLResponse(get_scalar_ui_html(self.title, self.openapi_url))
            self._core.add_route("GET", self.scalar_url, lambda: scalar_resp)

    def openapi(self) -> dict[str, Any]:
        if self._openapi_schema_cache is None:
            self._openapi_schema_cache = build_openapi_schema(
                self.title, self.version, self._routes_meta
            )
        return self._openapi_schema_cache

    def _startup(self) -> None:
        if self.lifespan is not None and self._lifespan_ctx is None:
            self._lifespan_ctx = self.lifespan(self)
            _step_coro(self._lifespan_ctx.__aenter__())

    def _shutdown(self) -> None:
        if self._lifespan_ctx is not None:
            try:
                _step_coro(self._lifespan_ctx.__aexit__(None, None, None))
            finally:
                self._lifespan_ctx = None

    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        methods: list[str],
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        # Invalidate cached OpenAPI bytes when new routes are registered
        self._openapi_schema_cache = None
        self._openapi_bytes_cache = None

        combined_deps = list(self.dependencies)
        if dependencies:
            combined_deps.extend(dependencies)

        param_specs, dep_steps = _compile_route_plan(path, endpoint, combined_deps)
        resp_spec = get_schema_spec(response_model) if response_model is not None else None
        for method in methods:
            m_upper = method.upper()
            self._core.add_route(
                m_upper,
                path,
                endpoint,
                param_specs,
                resp_spec,
                status_code,
                dep_steps,
            )
            self._routes_meta.append(
                {
                    "path": path,
                    "method": m_upper,
                    "endpoint": endpoint,
                    "param_specs": param_specs,
                    "response_model": response_model,
                    "status_code": status_code,
                    "tags": list(tags) if tags else [],
                }
            )

    @property
    def routes(self) -> list[dict[str, Any]]:
        return list(self._routes_meta)

    def include_router(
        self,
        router: APIRouter,
        *,
        prefix: str = "",
        tags: Optional[list[str]] = None,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ) -> None:
        for r in router.routes:
            full_path = _join_paths(prefix, r.path) if prefix else r.path
            merged_deps: list[DependsInfo] = []
            if dependencies:
                merged_deps.extend(dependencies)
            merged_deps.extend(r.dependencies)

            merged_tags: list[str] = []
            if tags:
                merged_tags.extend(tags)
            for t in r.tags:
                if t not in merged_tags:
                    merged_tags.append(t)

            self.add_api_route(
                full_path,
                r.endpoint,
                r.methods,
                response_model=r.response_model,
                status_code=r.status_code,
                dependencies=merged_deps,
                tags=merged_tags,
            )

    def add_middleware(self, middleware_cls: Any, **options: Any) -> None:
        mw_instance = middleware_cls(**options) if isinstance(middleware_cls, type) else middleware_cls
        if hasattr(mw_instance, "install_c"):
            mw_instance.install_c(self._core)

    def middleware(self, middleware_type: str = "http"):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._core.add_user_middleware(fn)
            return fn
        return decorator

    def websocket(self, path: str):
        param_names = _extract_path_param_names(path)
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._core.add_ws_route(path, fn, param_names)
            return fn
        return decorator

    def ws_publish(self, topic: str, message: str) -> None:
        self._core.ws_publish(topic, message)

    def get(
        self,
        path: str,
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
        tags: Optional[list[str]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["GET"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
                tags=tags,
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
        tags: Optional[list[str]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["POST"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
                tags=tags,
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
        tags: Optional[list[str]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["PUT"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
                tags=tags,
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
        tags: Optional[list[str]] = None,
    ):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                ["DELETE"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
                tags=tags,
            )
            return fn
        return decorator

    def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self._startup()
        try:
            self._core.run_server(host, port)
        finally:
            self._shutdown()
