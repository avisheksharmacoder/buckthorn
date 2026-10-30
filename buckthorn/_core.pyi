# buckthorn/_core.pyi
from typing import Any, Callable, Optional

class Request:
    @property
    def method(self) -> str: ...
    @property
    def path(self) -> str: ...
    @property
    def body(self) -> bytes: ...
    def get_header(self, name: str) -> Optional[str]: ...
    def get_query(self, name: str) -> Optional[str]: ...

class HTTPBasicCredentials:
    username: str
    password: str
    def __init__(self, username: str = "", password: str = "") -> None: ...

class UploadFile:
    filename: str
    content_type: str
    def __init__(
        self,
        filename: str = "",
        content_type: str = "application/octet-stream",
    ) -> None: ...
    @property
    def size(self) -> int: ...
    def read_sync(self) -> bytes: ...
    async def read(self) -> bytes: ...

class WebSocket:
    path: str
    accepted: bool
    closed: bool
    accept_key: str
    def __init__(
        self,
        path: str = "",
        pubsub_registry: Optional[dict[str, list[WebSocket]]] = None,
    ) -> None: ...
    def compute_handshake(self, sec_websocket_key: str) -> str: ...
    async def accept(self) -> None: ...
    def subscribe(self, topic: str) -> None: ...
    def feed_raw_frame(self, raw_frame: bytes) -> None: ...
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    def push_published_frame(self, encoded_frame: bytes) -> None: ...
    def pop_outgoing_text(self) -> str: ...
    async def close(self, code: int = 1000) -> None: ...

def encode_client_masked_ws_frame(text: str) -> bytes: ...

class BackgroundTasks:
    def __init__(self) -> None: ...
    def add_task(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None: ...

class CompiledSecuritySpec:
    sec_kind: int
    def __init__(
        self,
        sec_kind: int,
        secret: str = "",
        key_name: str = "",
        expected_val: str = "",
        expected_pass: str = "",
        scopes: Optional[list[str]] = None,
    ) -> None: ...

class CompiledFieldSpec:
    name: str
    field_type: int
    required: bool
    default_val: Any
    is_list_default: bool
    sub_schema: Optional[CompiledSchemaSpec]
    def __init__(
        self,
        name: str,
        field_type: int,
        required: bool = True,
        default_val: Any = None,
        ge: Optional[float] = None,
        le: Optional[float] = None,
        gt: Optional[float] = None,
        lt: Optional[float] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        sub_schema: Optional[CompiledSchemaSpec] = None,
    ) -> None: ...

class CompiledSchemaSpec:
    schema_cls: type
    fields: tuple[CompiledFieldSpec, ...]
    num_fields: int
    def __init__(self, schema_cls: type, fields: list[CompiledFieldSpec]) -> None: ...

class CompiledParamSpec:
    arg_name: str
    source: int
    param_type: int
    path_index: int
    dep_slot_index: int
    required: bool
    default_val: Any
    schema_spec: Optional[CompiledSchemaSpec]
    security_spec: Optional[CompiledSecuritySpec]
    def __init__(
        self,
        arg_name: str,
        lookup_name: str,
        source: int,
        param_type: int,
        path_index: int = -1,
        required: bool = True,
        default_val: Any = None,
        ge: Optional[float] = None,
        le: Optional[float] = None,
        gt: Optional[float] = None,
        lt: Optional[float] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        schema_spec: Optional[CompiledSchemaSpec] = None,
        dep_slot_index: int = -1,
        security_spec: Optional[CompiledSecuritySpec] = None,
    ) -> None: ...

class CompiledDepStep:
    fn: Callable[..., Any]
    slot_index: int
    dep_kind: int
    param_specs: tuple[CompiledParamSpec, ...]
    num_params: int
    def __init__(
        self,
        fn: Callable[..., Any],
        slot_index: int,
        param_specs: list[CompiledParamSpec],
    ) -> None: ...

class MiddlewareResponse:
    status_code: int
    body: bytes
    headers: Optional[dict[str, str]]
    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b"",
        headers: Optional[dict[str, str]] = None,
    ) -> None: ...

class RouteEntry:
    fn: Callable[..., Any]
    is_coroutine: bool
    param_specs: tuple[CompiledParamSpec, ...]
    num_params: int
    dep_steps: tuple[CompiledDepStep, ...]
    num_dep_steps: int
    response_schema_spec: Optional[CompiledSchemaSpec]
    default_status_code: int
    user_middlewares: Optional[list[Callable[..., Any]]]
    def __init__(
        self,
        fn: Callable[..., Any],
        param_specs: list[CompiledParamSpec],
        response_schema_spec: Optional[CompiledSchemaSpec] = None,
        default_status_code: int = 200,
        dep_steps: Optional[list[CompiledDepStep]] = None,
        user_middlewares: Optional[list[Callable[..., Any]]] = None,
    ) -> None: ...

class CythonRingLogger:
    def __init__(self, filepath: str = "", capacity: int = 65536) -> None: ...
    def debug(self, msg: str, **kwargs: Any) -> None: ...
    def info(self, msg: str, **kwargs: Any) -> None: ...
    def warning(self, msg: str, **kwargs: Any) -> None: ...
    def error(self, msg: str, **kwargs: Any) -> None: ...
    def flush_and_stop(self) -> None: ...

class CoreRouter:
    def __init__(self) -> None: ...
    def add_ws_route(self, path: str, fn: Callable[..., Any], param_names: list[str]) -> None: ...
    def ws_publish(self, topic: str, message: str) -> None: ...
    def add_route(
        self,
        method: str,
        path: str,
        fn: Callable[..., Any],
        param_specs: Optional[list[CompiledParamSpec]] = None,
        response_schema_spec: Optional[CompiledSchemaSpec] = None,
        status_code: int = 200,
        dep_steps: Optional[list[CompiledDepStep]] = None,
    ) -> None: ...
    def run_server(self, host: str = "0.0.0.0", port: int = 8000) -> None: ...
    def set_access_logger(self, logger: Optional[CythonRingLogger]) -> None: ...
    def configure_cors(self, allow_origin: str, allow_methods: str, allow_headers: str) -> None: ...
    def configure_ratelimit(self, max_requests: int, window_seconds: int) -> None: ...
    def configure_trusted_host(self, allowed_host: str) -> None: ...
    def configure_sec_headers(self, frame_opts: str, ctype_opts: str, hsts: str) -> None: ...
    def set_max_body_size(self, max_body_size: int) -> None: ...
    def add_user_middleware(self, fn: Callable[..., Any]) -> None: ...

class RawResponse:
    @property
    def status_code(self) -> int: ...
    @property
    def body(self) -> bytes: ...
    @property
    def wire_bytes(self) -> bytes: ...
    @property
    def headers(self) -> dict[str, str]: ...
    def json(self) -> Any: ...

class CythonRawTestClient:
    def __init__(self, core_router: CoreRouter) -> None: ...
    @property
    def arena_offset(self) -> int: ...
    def send_raw(self, raw_request: bytes) -> RawResponse: ...
