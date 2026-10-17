# buckthorn/logging.py
from typing import Any
from buckthorn._core import CythonRingLogger


class RingLogger:
    """Non-blocking lock-free C11 SPSC ring buffer logger with pthread writev flushing."""

    def __init__(self, filepath: str = "", capacity: int = 65536):
        self._c_logger = CythonRingLogger(filepath=filepath, capacity=capacity)
        # Direct Cython method binding to bypass Python wrapper frames & **kwargs unpacking
        self.debug = self._c_logger.debug
        self.info = self._c_logger.info
        self.warning = self._c_logger.warning
        self.error = self._c_logger.error
        self.flush_and_stop = self._c_logger.flush_and_stop

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._c_logger.debug(msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._c_logger.info(msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._c_logger.warning(msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._c_logger.error(msg, **kwargs)

    def flush_and_stop(self) -> None:
        self._c_logger.flush_and_stop()
