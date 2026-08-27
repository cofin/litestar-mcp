"""Stateless JSON-RPC 2.0 message parsing, routing, and error definitions."""

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass
class JSONRPCError:
    """A JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


@dataclass
class JSONRPCRequest:
    """A parsed JSON-RPC 2.0 request."""

    jsonrpc: str
    method: str
    id: Any | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_notification(self) -> bool:
        """Whether this is a notification without an id field."""
        return self.id is None


MethodHandler = Callable[[dict[str, Any], Any], Coroutine[Any, Any, dict[str, Any]]]


class JSONRPCRouter:
    """Dispatches JSON-RPC 2.0 requests to registered method handlers."""

    def __init__(self) -> None:
        self._methods: dict[str, MethodHandler] = {}

    @property
    def methods(self) -> dict[str, MethodHandler]:
        """Get registered method mappings."""
        return self._methods

    def register(self, method: str, handler: MethodHandler) -> None:
        """Register a handler for a JSON-RPC method."""
        self._methods[method] = handler

    async def dispatch(self, request: JSONRPCRequest, request_context: Any) -> dict[str, Any] | None:
        """Dispatch a JSON-RPC request to the appropriate handler."""
        handler = self._methods.get(request.method)
        if handler is None:
            if request.is_notification:
                return None
            return _error_response(
                request.id,
                JSONRPCError(code=METHOD_NOT_FOUND, message=f"Method not found: {request.method}"),
            )

        try:
            result = await handler(request.params, request_context)
        except JSONRPCErrorException as exc:
            if request.is_notification:
                return None
            return _error_response(request.id, exc.error)
        except Exception as exc:
            _logger.exception("JSON-RPC handler %r raised an unhandled exception", request.method)
            if request.is_notification:
                return None
            return _error_response(
                request.id,
                JSONRPCError(
                    code=INTERNAL_ERROR,
                    message="Internal error",
                    data={"error": type(exc).__name__, "detail": str(exc)},
                ),
            )
        else:
            if request.is_notification:
                return None
            return _success_response(request.id, result)


class JSONRPCErrorException(Exception):
    """Raised by method handlers to signal a structured JSON-RPC error."""

    def __init__(self, error: JSONRPCError) -> None:
        self.error = error
        super().__init__(error.message)


def parse_request(raw: Any) -> JSONRPCRequest:
    """Parse and validate a raw JSON body into a JSONRPCRequest."""
    if not isinstance(raw, dict):
        raise JSONRPCErrorException(JSONRPCError(code=INVALID_REQUEST, message="Request must be a JSON object"))

    if raw.get("jsonrpc") != "2.0":
        raise JSONRPCErrorException(
            JSONRPCError(code=INVALID_REQUEST, message="Missing or invalid 'jsonrpc' field; must be '2.0'")
        )

    method = raw.get("method")
    if not isinstance(method, str):
        raise JSONRPCErrorException(JSONRPCError(code=INVALID_REQUEST, message="Missing or invalid 'method' field"))

    return JSONRPCRequest(
        jsonrpc="2.0",
        method=method,
        id=raw.get("id"),
        params=raw.get("params", {}),
    )


def error_response(msg_id: Any, error: JSONRPCError) -> dict[str, Any]:
    """Build a public JSON-RPC error response dictionary."""
    return _error_response(msg_id, error)


def _success_response(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error_response(msg_id: Any, error: JSONRPCError) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": error.to_dict()}


__all__ = (
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "JSONRPCError",
    "JSONRPCErrorException",
    "JSONRPCRequest",
    "JSONRPCRouter",
    "MethodHandler",
    "error_response",
    "parse_request",
)
