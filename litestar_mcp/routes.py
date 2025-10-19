# ruff: noqa: BLE001
"""MCP-compatible REST API routes for Litestar applications."""

import inspect
from collections.abc import AsyncGenerator
from typing import Any, Union

from litestar import Controller, Request, Response, get, post
from litestar.exceptions import NotFoundException
from litestar.handlers import BaseRouteHandler
from litestar.response import Stream
from litestar.serialization import encode_json

from litestar_mcp.config import MCPConfig
from litestar_mcp.executor import execute_tool
from litestar_mcp.schema import MCPResource, MCPTool, ServerCapabilities
from litestar_mcp.schema_builder import generate_schema_for_handler
from litestar_mcp.sse import format_sse_event
from litestar_mcp.utils import get_handler_function


class MCPController(Controller):
    """MCP-compatible REST API controller that proxies to discovered routes."""

    @get("/", name="mcp_info")
    async def get_server_info(
        self,
        request: Request[Any, Any, Any],
        config: MCPConfig,
        discovered_tools: dict[str, BaseRouteHandler],
        discovered_resources: dict[str, BaseRouteHandler],
    ) -> dict[str, Any]:
        """Get MCP server information and capabilities."""
        capabilities = ServerCapabilities(
            resources={
                "list_resources": True,
                "get_resource": True,
                "openapi": True,
            },
            tools={
                "list_tools": True,
                "call_tool": True,
            },
        )

        openapi_config = request.app.openapi_config
        server_name = config.name or (openapi_config.title if openapi_config else "Litestar MCP Server")
        server_version = openapi_config.version if openapi_config else "1.0.0"

        return {
            "server_name": server_name,
            "server_version": server_version,
            "protocol_version": "1.0.0",
            "capabilities": {
                "resources": capabilities.resources,
                "tools": capabilities.tools,
                "transports": ["http", "sse"],
            },
            "discovered": {
                "tools": len(discovered_tools),
                "resources": len(discovered_resources),
            },
        }

    @get("/resources", name="list_resources")
    async def list_resources(
        self, request: Request[Any, Any, Any], discovered_resources: dict[str, BaseRouteHandler]
    ) -> dict[str, Any]:
        """List all available MCP resources."""
        resources = []

        resources.append(
            MCPResource(
                uri="litestar://openapi",
                name="openapi",
                description="OpenAPI schema for this Litestar application",
                mime_type="application/json",
            )
        )

        for name, handler in discovered_resources.items():
            description = handler.__doc__ or f"Resource: {name}"
            resources.append(
                MCPResource(
                    uri=f"litestar://{name}", name=name, description=description.strip(), mime_type="application/json"
                )
            )

        return {
            "resources": [
                {
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mimeType": resource.mime_type,
                }
                for resource in resources
            ]
        }

    @get("/resources/{resource_name:str}", name="get_resource")
    async def get_resource(
        self, resource_name: str, request: Request[Any, Any, Any], discovered_resources: dict[str, BaseRouteHandler]
    ) -> dict[str, Any]:
        """Get a specific MCP resource by name."""

        if resource_name == "openapi":
            openapi_schema = request.app.openapi_schema
            return {
                "content": {
                    "uri": "litestar://openapi",
                    "mimeType": "application/json",
                    "text": encode_json(openapi_schema).decode("utf-8"),
                }
            }

        if resource_name not in discovered_resources:
            raise NotFoundException(detail=f"Resource '{resource_name}' not found")

        handler = discovered_resources[resource_name]

        try:
            # Execute the resource handler using the shared executor
            # Resources typically have no arguments
            result = await execute_tool(handler, request.app, tool_args={})

            # Encode the result as JSON text
            result_text = encode_json(result).decode("utf-8")
        except Exception as e:
            raise NotFoundException(detail=f"Failed to fetch resource '{resource_name}': {e!s}") from e
        else:
            return {
                "content": {
                    "uri": f"litestar://{resource_name}",
                    "mimeType": "application/json",
                    "text": result_text,
                }
            }

    @get("/tools", name="list_tools")
    async def list_tools(self, discovered_tools: dict[str, BaseRouteHandler]) -> dict[str, Any]:
        """List all available MCP tools."""
        tools = []

        for name, handler in discovered_tools.items():
            # Get description from handler function docstring
            fn = get_handler_function(handler)
            fn_doc = fn.__doc__
            description = fn_doc.strip() if fn_doc else f"Tool: {name}"

            # Generate JSON schema from handler signature
            input_schema = generate_schema_for_handler(handler)

            tools.append(MCPTool(name=name, description=description, input_schema=input_schema))

        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in tools
            ]
        }

    @post("/tools/{tool_name:str}", name="call_tool", status_code=200)
    async def call_tool(
        self,
        tool_name: str,
        data: dict[str, Any],
        request: Request[Any, Any, Any],
        discovered_tools: dict[str, BaseRouteHandler],
    ) -> dict[str, Any]:
        """Execute an MCP tool."""

        if tool_name not in discovered_tools:
            raise NotFoundException(detail=f"Tool '{tool_name}' not found")

        handler = discovered_tools[tool_name]
        tool_args = data.get("arguments", {})

        try:
            # Execute the tool using the shared executor
            result = await execute_tool(handler, request.app, tool_args)

            # The result must be serializable.
            # We encode it to a JSON string to be safe.
            result_text = encode_json(result).decode("utf-8")
        except Exception as e:
            return {
                "error": {
                    "code": -1,
                    "message": f"Tool execution failed: {e!s}",
                }
            }
        else:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": result_text,
                    }
                ]
            }

    @post("/messages", name="mcp_messages", status_code=200)
    async def handle_mcp_messages(  # noqa: C901, PLR0911, PLR0915
        self,
        data: "dict[str, Any]",
        request: Request[Any, Any, Any],
        discovered_tools: dict[str, BaseRouteHandler],
        discovered_resources: dict[str, BaseRouteHandler],
    ) -> "Union[dict[str, Any], Response[Any]]":
        """Unified MCP message handler with optional SSE streaming.

        Handles all MCP operations through a single endpoint:
        - tools/list: List available tools
        - tools/call: Execute a tool (with optional SSE streaming)
        - resources/list: List available resources
        - resources/read: Get resource content

        Automatically upgrades to SSE for streaming tools (AsyncGenerator return type).

        Args:
            data: MCP message with method and params
            request: Litestar request instance
            discovered_tools: Dictionary of discovered MCP tools
            discovered_resources: Dictionary of discovered MCP resources

        Returns:
            Standard HTTP JSON response or SSE stream response

        Raises:
            NotFoundException: If method is unknown or resource/tool not found
        """
        method = data.get("method")
        params = data.get("params", {})

        if method == "tools/list":
            tools = []
            for name, handler in discovered_tools.items():
                fn = get_handler_function(handler)
                fn_doc = fn.__doc__
                description = fn_doc.strip() if fn_doc else f"Tool: {name}"
                input_schema = generate_schema_for_handler(handler)
                tools.append(MCPTool(name=name, description=description, input_schema=input_schema))

            return {
                "result": {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.input_schema,
                        }
                        for tool in tools
                    ]
                }
            }

        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            if not tool_name or tool_name not in discovered_tools:
                raise NotFoundException(detail=f"Tool '{tool_name}' not found")

            handler = discovered_tools[tool_name]

            if self._should_stream_tool(handler):

                async def tool_stream() -> AsyncGenerator[str, None]:
                    try:
                        result = await execute_tool(handler, request.app, tool_args)

                        if inspect.isasyncgen(result):
                            async for chunk in result:
                                yield format_sse_event("result", chunk)
                        else:
                            yield format_sse_event(
                                "result", {"content": [{"type": "text", "text": encode_json(result).decode()}]}
                            )

                        yield format_sse_event("done", {})

                    except Exception as e:
                        yield format_sse_event("error", {"code": -1, "message": f"Tool execution failed: {e!s}"})

                return Stream(
                    tool_stream(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            try:
                result = await execute_tool(handler, request.app, tool_args)
                result_text = encode_json(result).decode("utf-8")
                return {  # noqa: TRY300
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": result_text,
                            }
                        ]
                    }
                }
            except Exception as e:
                return {
                    "result": {
                        "error": {
                            "code": -1,
                            "message": f"Tool execution failed: {e!s}",
                        }
                    }
                }

        elif method == "resources/list":
            resources = []

            resources.append(
                MCPResource(
                    uri="litestar://openapi",
                    name="openapi",
                    description="OpenAPI schema for this Litestar application",
                    mime_type="application/json",
                )
            )

            for name, handler in discovered_resources.items():
                description = handler.__doc__ or f"Resource: {name}"
                resources.append(
                    MCPResource(
                        uri=f"litestar://{name}",
                        name=name,
                        description=description.strip(),
                        mime_type="application/json",
                    )
                )

            return {
                "result": {
                    "resources": [
                        {
                            "uri": resource.uri,
                            "name": resource.name,
                            "description": resource.description,
                            "mimeType": resource.mime_type,
                        }
                        for resource in resources
                    ]
                }
            }

        elif method == "resources/read":
            resource_name = params.get("uri", "").replace("litestar://", "")

            if not resource_name:
                raise NotFoundException(detail="Resource URI not provided")

            if resource_name == "openapi":
                openapi_schema = request.app.openapi_schema
                return {
                    "result": {
                        "contents": [
                            {
                                "uri": "litestar://openapi",
                                "mimeType": "application/json",
                                "text": encode_json(openapi_schema).decode("utf-8"),
                            }
                        ]
                    }
                }

            if resource_name not in discovered_resources:
                raise NotFoundException(detail=f"Resource '{resource_name}' not found")

            handler = discovered_resources[resource_name]

            try:
                result = await execute_tool(handler, request.app, tool_args={})
                result_text = encode_json(result).decode("utf-8")
                return {  # noqa: TRY300
                    "result": {
                        "contents": [
                            {
                                "uri": f"litestar://{resource_name}",
                                "mimeType": "application/json",
                                "text": result_text,
                            }
                        ]
                    }
                }
            except Exception as e:
                raise NotFoundException(detail=f"Failed to fetch resource '{resource_name}': {e!s}") from e

        else:
            raise NotFoundException(detail=f"Unknown method: {method}")

    def _should_stream_tool(self, handler: BaseRouteHandler) -> bool:
        """Determine if tool should use SSE streaming.

        Checks if handler's return type annotation is AsyncGenerator.

        Args:
            handler: Route handler to check

        Returns:
            True if handler should stream, False otherwise
        """
        fn = get_handler_function(handler)
        sig = inspect.signature(fn)

        return_annotation = sig.return_annotation
        if return_annotation == inspect.Signature.empty:
            return False

        origin = getattr(return_annotation, "__origin__", None)
        return origin is AsyncGenerator or "AsyncGenerator" in str(return_annotation)
