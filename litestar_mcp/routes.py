# ruff: noqa: BLE001
"""MCP JSON-RPC 2.0 transport for Litestar applications."""

import json
from typing import TYPE_CHECKING, Any

from litestar import Controller, MediaType, Request, Response, post
from litestar.serialization import encode_json
from litestar.status_codes import HTTP_200_OK, HTTP_204_NO_CONTENT

from litestar_mcp.config import MCPConfig
from litestar_mcp.executor import execute_tool
from litestar_mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRouter,
    error_response,
    parse_request,
)
from litestar_mcp.schema import MCPResource, MCPTool
from litestar_mcp.schema_builder import generate_schema_for_handler
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler


def build_jsonrpc_router(
    config: MCPConfig,
    discovered_tools: "dict[str, BaseRouteHandler]",
    discovered_resources: "dict[str, BaseRouteHandler]",
    app_ref: Any = None,
) -> JSONRPCRouter:
    """Build and return a JSONRPCRouter wired to MCP method handlers.

    Args:
        config: The MCP plugin configuration.
        discovered_tools: Registered tool handlers.
        discovered_resources: Registered resource handlers.
        app_ref: Reference to the Litestar app (for OpenAPI access).

    Returns:
        A configured JSONRPCRouter.
    """
    router = JSONRPCRouter()

    # ── initialize ────────────────────────────────────────────────────
    async def handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
        server_name = config.name or "Litestar MCP Server"
        server_version = "1.0.0"

        if app_ref is not None:
            openapi_config = app_ref.openapi_config
            if openapi_config:
                server_name = config.name or openapi_config.title
                server_version = openapi_config.version

        return {
            "protocolVersion": "2025-11-25",
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"subscribe": True, "listChanged": True},
            },
            "serverInfo": {
                "name": server_name,
                "version": server_version,
            },
        }

    router.register("initialize", handle_initialize)

    # ── notifications/initialized ──────────────────────────────────────
    async def handle_initialized(params: dict[str, Any]) -> dict[str, Any]:
        return {}

    router.register("notifications/initialized", handle_initialized)

    # ── ping ───────────────────────────────────────────────────────────
    async def handle_ping(params: dict[str, Any]) -> dict[str, Any]:
        return {}

    router.register("ping", handle_ping)

    # ── tools/list ─────────────────────────────────────────────────────
    async def handle_tools_list(params: dict[str, Any]) -> dict[str, Any]:
        tools = []
        for name, handler in discovered_tools.items():
            fn = get_handler_function(handler)
            fn_doc = fn.__doc__
            description = fn_doc.strip() if fn_doc else f"Tool: {name}"
            input_schema = generate_schema_for_handler(handler)
            tools.append({
                "name": name,
                "description": description,
                "inputSchema": input_schema,
            })
        return {"tools": tools}

    router.register("tools/list", handle_tools_list)

    # ── tools/call ─────────────────────────────────────────────────────
    async def handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        if not tool_name:
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'")
            )

        if tool_name not in discovered_tools:
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message=f"Tool not found: {tool_name}")
            )

        handler = discovered_tools[tool_name]
        tool_args = params.get("arguments", {})

        try:
            result = await execute_tool(handler, app_ref, tool_args)
            result_text = encode_json(result).decode("utf-8")
        except Exception as exc:
            raise JSONRPCErrorException(
                JSONRPCError(code=INTERNAL_ERROR, message=f"Tool execution failed: {exc!s}")
            ) from exc

        return {
            "content": [{"type": "text", "text": result_text}],
        }

    router.register("tools/call", handle_tools_call)

    # ── resources/list ─────────────────────────────────────────────────
    async def handle_resources_list(params: dict[str, Any]) -> dict[str, Any]:
        resources = [
            {
                "uri": "litestar://openapi",
                "name": "openapi",
                "description": "OpenAPI schema for this Litestar application",
                "mimeType": "application/json",
            }
        ]
        for name, handler in discovered_resources.items():
            fn = get_handler_function(handler)
            description = fn.__doc__ or f"Resource: {name}"
            resources.append({
                "uri": f"litestar://{name}",
                "name": name,
                "description": description.strip(),
                "mimeType": "application/json",
            })
        return {"resources": resources}

    router.register("resources/list", handle_resources_list)

    # ── resources/read ─────────────────────────────────────────────────
    async def handle_resources_read(params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri", "")
        if not uri.startswith("litestar://"):
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message=f"Invalid resource URI: {uri}")
            )

        resource_name = uri[len("litestar://"):]

        if resource_name == "openapi" and app_ref is not None:
            openapi_schema = app_ref.openapi_schema
            return {
                "contents": [
                    {
                        "uri": "litestar://openapi",
                        "mimeType": "application/json",
                        "text": encode_json(openapi_schema).decode("utf-8"),
                    }
                ]
            }

        if resource_name not in discovered_resources:
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message=f"Resource not found: {resource_name}")
            )

        handler = discovered_resources[resource_name]
        try:
            result = await execute_tool(handler, app_ref, tool_args={})
            result_text = encode_json(result).decode("utf-8")
        except Exception as exc:
            raise JSONRPCErrorException(
                JSONRPCError(code=INTERNAL_ERROR, message=f"Resource read failed: {exc!s}")
            ) from exc

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": result_text,
                }
            ]
        }

    router.register("resources/read", handle_resources_read)

    return router


class MCPController(Controller):
    """MCP JSON-RPC 2.0 controller.

    All MCP operations are dispatched through a single POST endpoint.
    """

    @post("/", name="mcp_jsonrpc", media_type=MediaType.JSON, status_code=HTTP_200_OK)
    async def handle_jsonrpc(
        self,
        request: Request[Any, Any, Any],
        config: MCPConfig,
        discovered_tools: dict[str, Any],
        discovered_resources: dict[str, Any],
    ) -> Response[Any]:
        """Handle a JSON-RPC 2.0 request.

        This is the single MCP endpoint. All tool/resource operations arrive here.
        """
        # Parse raw body — we handle JSON errors ourselves for proper JSON-RPC error codes
        try:
            raw = json.loads(await request.body())
        except (json.JSONDecodeError, ValueError):
            return Response(
                content=error_response(None, JSONRPCError(code=PARSE_ERROR, message="Parse error")),
                status_code=HTTP_200_OK,
                media_type=MediaType.JSON,
            )

        # Validate JSON-RPC envelope
        try:
            rpc_request = parse_request(raw)
        except JSONRPCErrorException as exc:
            return Response(
                content=error_response(raw.get("id") if isinstance(raw, dict) else None, exc.error),
                status_code=HTTP_200_OK,
                media_type=MediaType.JSON,
            )

        # Build the router (lightweight — just wiring closures)
        router = build_jsonrpc_router(config, discovered_tools, discovered_resources, app_ref=request.app)

        # Dispatch
        result = await router.dispatch(rpc_request)

        if result is None:
            # Notification — no response body
            return Response(content=None, status_code=HTTP_204_NO_CONTENT)

        return Response(content=result, status_code=HTTP_200_OK, media_type=MediaType.JSON)
