"""ASGI route handlers and controller for A2A JSON-RPC 2.0 and streaming."""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from litestar import Controller, MediaType, Request, Response, post
from litestar.di import NamedDependency
from litestar.exceptions import SerializationException
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.serialization import decode_json, encode_json
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.registry import A2ARegistry
from litestar_mcp.a2a.service import A2AHandlerService
from litestar_mcp.a2a.tasks import A2ATaskStore
from litestar_mcp.core.jsonrpc import (
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCErrorException,
    error_response,
    parse_request,
)

if TYPE_CHECKING:
    from litestar_mcp.core.jsonrpc import JSONRPCRequest


def _error(
    req_id: Any,
    code: int,
    message: str,
    data: Any = None,
    status_code: int = HTTP_200_OK,
) -> Response[dict[str, Any]]:
    """Build a standard JSON-RPC 2.0 error response."""
    payload = error_response(req_id, JSONRPCError(code=code, message=message, data=data))
    return Response(
        content=payload,
        status_code=status_code,
        media_type=MediaType.JSON,
    )


class A2AController(Controller):
    """Controller handling A2A JSON-RPC execution and real-time streaming."""

    @post("/", name="a2a_jsonrpc", status_code=HTTP_200_OK)
    async def handle_jsonrpc(
        self,
        request: Request[Any, Any, Any],
        config: NamedDependency[A2AConfig],
        registry: NamedDependency[A2ARegistry],
        service: NamedDependency[A2AHandlerService],
        task_store: NamedDependency[A2ATaskStore],
    ) -> Response[Any]:
        """Validate and dispatch incoming A2A JSON-RPC requests."""
        _ = (config, registry, task_store)
        if service.app is None:
            service.app = request.app
        try:
            body_bytes = await request.body()
            raw = decode_json(body_bytes)
        except (SerializationException, ValueError):
            return _error(None, code=PARSE_ERROR, message="Parse error", status_code=HTTP_400_BAD_REQUEST)

        try:
            rpc_request: JSONRPCRequest = parse_request(raw)
        except JSONRPCErrorException as exc:
            req_id = raw.get("id") if isinstance(raw, dict) else None
            return _error(
                req_id,
                code=exc.error.code,
                message=exc.error.message,
                data=exc.error.data,
                status_code=HTTP_400_BAD_REQUEST,
            )

        if rpc_request.method == "tasks/sendSubscribe":
            generator = service.stream_tasks_send_subscribe(rpc_request)

            async def event_stream() -> AsyncGenerator[ServerSentEventMessage, None]:
                async for event in generator:
                    yield ServerSentEventMessage(data=encode_json(event).decode("utf-8"))

            response = ServerSentEvent(event_stream())
            response.headers["X-Accel-Buffering"] = "no"
            return response

        err_code: int | None = None
        err_msg: str = ""
        err_data: Any = None
        try:
            result = await service.dispatch_request(rpc_request)
            if result is None:
                return _error(
                    rpc_request.id,
                    code=METHOD_NOT_FOUND,
                    message=f"Method not found: {rpc_request.method}",
                    status_code=HTTP_404_NOT_FOUND,
                )
            return Response(content=result, status_code=HTTP_200_OK, media_type=MediaType.JSON)
        except JSONRPCErrorException as exc:
            err_code, err_msg, err_data = exc.error.code, exc.error.message, exc.error.data
        except Exception as exc:
            err_code, err_msg = INTERNAL_ERROR, str(exc)

        return _error(rpc_request.id, code=err_code, message=err_msg, data=err_data)
