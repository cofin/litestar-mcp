# ruff: noqa: BLE001
"""MCP-compatible REST API routes for Litestar applications."""

from typing import Any

from litestar import Controller, Request, get, post
from litestar.exceptions import NotFoundException
from litestar.handlers import BaseRouteHandler
from litestar.serialization import encode_json

from litestar_mcp.config import MCPConfig
from litestar_mcp.schema import MCPResource, MCPTool, ServerCapabilities


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
            handler_path = (
                next(iter(handler.paths)) if hasattr(handler, "paths") and handler.paths else f"/{resource_name}"
            )
            content = {"message": f"Resource {resource_name} would be fetched from {handler_path}"}

            return {
                "content": {
                    "uri": f"litestar://{resource_name}",
                    "mimeType": "application/json",
                    "text": encode_json(content).decode("utf-8"),
                }
            }
        except Exception as e:
            raise NotFoundException(detail=f"Failed to fetch resource '{resource_name}': {e!s}") from e

    @get("/tools", name="list_tools")
    async def list_tools(self, discovered_tools: dict[str, BaseRouteHandler]) -> dict[str, Any]:
        """List all available MCP tools."""
        tools = []

        for name, handler in discovered_tools.items():
            description = handler.__doc__ or f"Tool: {name}"
            input_schema = {"type": "object", "properties": {}, "description": f"Input parameters for {name}"}
            tools.append(MCPTool(name=name, description=description.strip(), input_schema=input_schema))

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

        try:
            arguments = data.get("arguments", {})

            handler_path = next(iter(handler.paths)) if hasattr(handler, "paths") and handler.paths else f"/{tool_name}"
            result = {
                "tool": tool_name,
                "handler_path": handler_path,
                "arguments": arguments,
                "message": f"Tool {tool_name} would be executed with arguments: {arguments}",
            }

            return {
                "content": [
                    {
                        "type": "text",
                        "text": encode_json(result).decode("utf-8"),
                    }
                ]
            }

        except Exception as e:
            return {
                "error": {
                    "code": -1,
                    "message": f"Tool execution failed: {e!s}",
                }
            }
