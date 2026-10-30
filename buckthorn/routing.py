# buckthorn/routing.py
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Type
from buckthorn.params import DependsInfo
from buckthorn.schema import Schema


@dataclass
class RouteDefinition:
    path: str
    endpoint: Callable[..., Any]
    methods: list[str]
    response_model: Optional[Type[Schema]] = None
    status_code: int = 200
    dependencies: list[DependsInfo] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _join_paths(prefix: str, path: str) -> str:
    if not prefix:
        return path if path.startswith("/") else f"/{path}"
    clean_prefix = "/" + prefix.strip("/")
    clean_path = path.lstrip("/")
    if not clean_path:
        return clean_prefix
    return f"{clean_prefix}/{clean_path}"


class APIRouter:
    """FastAPI-compatible modular router that flattens into C rax routes at registration."""

    def __init__(
        self,
        *,
        prefix: str = "",
        tags: Optional[list[str]] = None,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ):
        self.prefix = prefix.rstrip("/") if prefix else ""
        self.tags = list(tags) if tags else []
        self.dependencies = list(dependencies) if dependencies else []
        self.routes: list[RouteDefinition] = []

    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        methods: list[str],
        *,
        response_model: Optional[Type[Schema]] = None,
        status_code: int = 200,
        dependencies: Optional[Sequence[DependsInfo]] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        full_path = _join_paths(self.prefix, path)
        merged_deps = list(self.dependencies)
        if dependencies:
            merged_deps.extend(dependencies)

        merged_tags = list(self.tags)
        if tags:
            for t in tags:
                if t not in merged_tags:
                    merged_tags.append(t)

        self.routes.append(
            RouteDefinition(
                path=full_path,
                endpoint=endpoint,
                methods=[m.upper() for m in methods],
                response_model=response_model,
                status_code=status_code,
                dependencies=merged_deps,
                tags=merged_tags,
            )
        )

    def include_router(
        self,
        router: "APIRouter",
        *,
        prefix: str = "",
        tags: Optional[list[str]] = None,
        dependencies: Optional[Sequence[DependsInfo]] = None,
    ) -> None:
        sub_prefix = _join_paths(self.prefix, prefix) if prefix else self.prefix
        for r in router.routes:
            combined_path = _join_paths(sub_prefix, r.path)
            combined_deps = list(self.dependencies)
            if dependencies:
                combined_deps.extend(dependencies)
            combined_deps.extend(r.dependencies)

            combined_tags = list(self.tags)
            if tags:
                for t in tags:
                    if t not in combined_tags:
                        combined_tags.append(t)
            for t in r.tags:
                if t not in combined_tags:
                    combined_tags.append(t)

            self.routes.append(
                RouteDefinition(
                    path=combined_path,
                    endpoint=r.endpoint,
                    methods=list(r.methods),
                    response_model=r.response_model,
                    status_code=r.status_code,
                    dependencies=combined_deps,
                    tags=combined_tags,
                )
            )

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

    def patch(
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
                ["PATCH"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
                tags=tags,
            )
            return fn
        return decorator

    def head(
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
                ["HEAD"],
                response_model=response_model,
                status_code=status_code,
                dependencies=dependencies,
                tags=tags,
            )
            return fn
        return decorator
