# buckthorn/app.py
from typing import Callable
from buckthorn._core import CoreRouter


class Buckthorn:
    def __init__(self, title: str = "Buckthorn", version: str = "0.1.0"):
        self.title = title
        self.version = version
        self._core = CoreRouter()

    def add_api_route(self, path: str, endpoint: Callable, methods: list[str]) -> None:
        for method in methods:
            self._core.add_route(method.upper(), path, endpoint)

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
