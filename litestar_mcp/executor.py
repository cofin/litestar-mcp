"""Core execution logic for invoking MCP tools."""

import inspect
from typing import TYPE_CHECKING, Any, Optional, cast
from urllib.parse import urlencode

from litestar import Litestar
from litestar.connection import Request
from litestar.enums import ScopeType
from litestar.response import Response
from litestar.serialization import decode_json, encode_json
from litestar.serialization import default_serializer
from litestar.types import Empty
from litestar.types.asgi_types import ASGIVersion, HTTPScope, Receive, Send
from litestar.utils.sync import ensure_async_callable

from litestar_mcp.typing import schema_dump
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from litestar.handlers.base import BaseRouteHandler


def _encode_body(payload: Any) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    return encode_json(payload)


def _build_headers(base_request: Optional[Request[Any, Any, Any]], has_body: bool) -> "list[tuple[bytes, bytes]]":
    headers: dict[str, str] = {}
    if base_request is not None:
        for key, value in base_request.headers.items():
            headers[key] = value
    if has_body and "content-type" not in {k.lower(): v for k, v in headers.items()}:
        headers["content-type"] = "application/json"
    return [(key.encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()]


def _normalize_query_value(value: Any) -> "list[str]":
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _build_query_string(tool_args: "dict[str, Any]") -> bytes:
    if not tool_args:
        return b""
    query: dict[str, list[str]] = {}
    for key, value in tool_args.items():
        if value is None:
            continue
        query[key] = _normalize_query_value(value)
    return urlencode(query, doseq=True).encode("ascii")


def _extract_path_params(path_template: str, tool_args: "dict[str, Any]") -> "tuple[str, dict[str, str]]":
    import re
    from urllib.parse import quote

    pattern = re.compile(r"\{([^}:]+)(:[^}]+)?\}")
    path_params: dict[str, str] = {}

    def replace(match: "re.Match[str]") -> str:
        param_name = match.group(1)
        if param_name not in tool_args:
            msg = f"Missing path parameter: {param_name}"
            raise ValueError(msg)
        value = str(tool_args[param_name])
        path_params[param_name] = value
        return quote(value, safe="")

    rendered_path = pattern.sub(replace, path_template)
    return rendered_path, path_params


def _select_handler_path(handler: "BaseRouteHandler") -> str:
    if getattr(handler, "paths", None):
        return sorted(handler.paths)[0]
    return getattr(handler, "path", "/")


def _select_http_method(handler: "BaseRouteHandler") -> str:
    methods = sorted({method.upper() for method in handler.http_methods})
    return methods[0] if methods else "GET"


def _create_request(
    app: Litestar,
    handler: "BaseRouteHandler",
    tool_args: "dict[str, Any]",
    base_request: Optional[Request[Any, Any, Any]] = None,
) -> Request[Any, Any, Any]:
    path_template = _select_handler_path(handler)
    path, path_params = _extract_path_params(path_template, tool_args)
    method = _select_http_method(handler)

    body_payload: Any = None
    body_field = handler.parsed_data_field
    if body_field is not None and body_field in tool_args:
        body_payload = tool_args.get(body_field)

    query_args = {
        key: value
        for key, value in tool_args.items()
        if key not in path_params and not (body_field is not None and key == body_field)
    }

    body = _encode_body(body_payload)
    headers = _build_headers(base_request, has_body=bool(body))

    query_string = _build_query_string(query_args)
    scope: HTTPScope

    if base_request is not None:
        scope = cast("HTTPScope", dict(base_request.scope))
        scope.update(
            {
                "type": ScopeType.HTTP,
                "method": method,
                "path": path,
                "raw_path": path.encode("ascii", errors="ignore"),
                "query_string": query_string,
                "headers": headers,
                "path_params": path_params,
                "route_handler": handler,
            }
        )
    else:
        scope = {
            "type": ScopeType.HTTP,
            "asgi": ASGIVersion(spec_version="3.0", version="3.0"),
            "app": app,
            "litestar_app": app,
            "method": method,
            "scheme": "http",
            "server": ("litestar-mcp", 80),
            "client": ("127.0.0.1", 0),
            "root_path": "",
            "path": path,
            "raw_path": path.encode("ascii", errors="ignore"),
            "query_string": query_string,
            "headers": headers,
            "extensions": {},
            "path_params": path_params,
            "route_handler": handler,
            "state": {},
            "session": {},
            "user": None,
            "auth": None,
            "path_template": path_template,
        }

    body_sent = False

    async def receive() -> "dict[str, Any]":
        nonlocal body_sent
        if body_sent:
            return {"type": "http.disconnect"}
        body_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(_message: "dict[str, Any]") -> None:
        return None

    request_class = handler.request_class
    return request_class(scope, receive=cast("Receive", receive), send=cast("Send", send))


def _normalize_response_content(result: Response, request: Request[Any, Any, Any]) -> Any:
    content = result.content
    if inspect.isasyncgen(content):
        return content
    if isinstance(content, (str, int, float, bool, list, dict, type(None))):
        return content
    if isinstance(content, bytes):
        try:
            return decode_json(content)
        except Exception:
            return content.decode("utf-8", errors="replace")
    try:
        return default_serializer(content, type_encoders=result.response_type_encoders)
    except TypeError:
        return schema_dump(content) or content


async def _invoke_handler(handler: "BaseRouteHandler", request: Request[Any, Any, Any]) -> Any:
    if handler.guards:
        await handler.authorize_connection(connection=request)

    response_data: Any = None
    before_request_handler = handler.resolve_before_request()
    if before_request_handler is not None:
        response_data = await before_request_handler(request)

    if response_data is None:
        parameter_model = handler._get_kwargs_model_for_route(request.scope["path_params"].keys())
        parsed_kwargs: dict[str, Any] = {}

        if parameter_model.has_kwargs and handler.signature_model:
            kwargs = await parameter_model.to_kwargs(connection=request)
            if kwargs.get("data") is Empty:
                kwargs.pop("data", None)

            if parameter_model.dependency_batches:
                cleanup_group = await parameter_model.resolve_dependencies(request, kwargs)
                async with cleanup_group:
                    parsed_kwargs = handler.signature_model.parse_values_from_connection_kwargs(
                        connection=request, kwargs=kwargs
                    )
                    response_data = (
                        handler.fn(**parsed_kwargs)
                        if handler.has_sync_callable
                        else await handler.fn(**parsed_kwargs)
                    )
            else:
                parsed_kwargs = handler.signature_model.parse_values_from_connection_kwargs(connection=request, kwargs=kwargs)
                response_data = (
                    handler.fn(**parsed_kwargs) if handler.has_sync_callable else await handler.fn(**parsed_kwargs)
                )
        else:
            response_data = handler.fn() if handler.has_sync_callable else await handler.fn()

    if isinstance(response_data, Response):
        return _normalize_response_content(response_data, request)

    if handler.return_dto:
        response_data = handler.return_dto(request).data_to_encodable_type(response_data)

    if inspect.isasyncgen(response_data):
        return response_data

    if not isinstance(response_data, (str, int, float, bool, list, dict, type(None))):
        try:
            return handler.default_serializer(response_data)
        except TypeError:
            return schema_dump(response_data)

    return response_data


async def execute_tool(
    handler: "BaseRouteHandler",
    app: Litestar,
    tool_args: "dict[str, Any]",
    base_request: Optional[Request[Any, Any, Any]] = None,
) -> Any:
    """Execute a route handler with Litestar request lifecycle and tool arguments.

    Args:
        handler: The route handler to execute.
        app: The Litestar app instance.
        tool_args: A dictionary of arguments to pass to the tool.
        base_request: Optional request to copy auth/state context from.

    Returns:
        The handler result, potentially an AsyncGenerator for streaming tools.
    """
    request = _create_request(app, handler, tool_args, base_request=base_request)
    return await _invoke_handler(handler, request)


__all__ = ("execute_tool",)
