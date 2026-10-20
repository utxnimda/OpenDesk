"""Explicit method dispatch; this module knows no concrete feature modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .protocol import ProtocolError, Request


@dataclass(frozen=True)
class MethodResult:
    value: Any
    stop_worker: bool = False


MethodHandler = Callable[[dict[str, Any]], MethodResult]


class MethodRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, MethodHandler] = {}

    def register(self, method: str, handler: MethodHandler) -> None:
        if not method or method in self._handlers:
            raise ValueError("Method name must be nonempty and unique.")
        self._handlers[method] = handler

    @property
    def capabilities(self) -> list[str]:
        return sorted(self._handlers)

    def dispatch(self, request: Request) -> MethodResult:
        handler = self._handlers.get(request.method)
        if handler is None:
            raise ProtocolError("METHOD_NOT_FOUND", "Method is not registered.", request.request_id)
        try:
            return handler(request.params)
        except ProtocolError as error:
            # The dispatcher owns correlation, not individual handlers.
            raise ProtocolError(error.code, error.message, request.request_id) from error
