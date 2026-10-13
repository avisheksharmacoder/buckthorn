# buckthorn/testing.py
from buckthorn.app import Buckthorn
from buckthorn._core import CythonRawTestClient, RawResponse


class RawTestClient:
    def __init__(self, app: Buckthorn):
        self._client = CythonRawTestClient(app._core)

    def send_raw(self, raw_http: bytes) -> RawResponse:
        return self._client.send_raw(raw_http)

    @property
    def arena_offset(self) -> int:
        return self._client.arena_offset
