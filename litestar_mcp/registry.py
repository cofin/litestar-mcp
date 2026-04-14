"""Central registry for MCP tools and resources."""

from typing import Any

from litestar.handlers import BaseRouteHandler

from litestar_mcp.sse import SSEManager


class Registry:
    """Central registry for MCP tools and resources.

    This class decouples metadata storage and discovery from the route handlers themselves,
    avoiding issues with __slots__ or object mutation.
    """

    def __init__(self) -> None:
        """Initialize the registry."""
        self._tools: dict[str, BaseRouteHandler] = {}
        self._resources: dict[str, BaseRouteHandler] = {}
        self._sse_manager: SSEManager | None = None

    def set_sse_manager(self, manager: SSEManager) -> None:
        """Set the SSE manager for notifications."""
        self._sse_manager = manager

    @property
    def sse_manager(self) -> SSEManager:
        """Return the configured SSE manager."""
        if self._sse_manager is None:
            msg = "SSE manager has not been configured"
            raise RuntimeError(msg)
        return self._sse_manager

    @property
    def tools(self) -> dict[str, BaseRouteHandler]:
        """Get registered tools."""
        return self._tools

    @property
    def resources(self) -> dict[str, BaseRouteHandler]:
        """Get registered resources."""
        return self._resources

    def register_tool(self, name: str, handler: BaseRouteHandler) -> None:
        """Register a tool.

        Args:
            name: The tool name.
            handler: The route handler.
        """
        self._tools[name] = handler

    def register_resource(self, name: str, handler: BaseRouteHandler) -> None:
        """Register a resource.

        Args:
            name: The resource name.
            handler: The route handler.
        """
        self._resources[name] = handler

    async def publish_notification(
        self,
        method: str,
        params: dict[str, Any],
        client_id: str | None = None,
    ) -> None:
        """Publish a JSON-RPC 2.0 notification to all connected clients.

        Args:
            method: The notification method (e.g., 'notifications/resources/updated').
            params: The notification parameters.
            client_id: Optional client group to target.
        """
        if self._sse_manager:
            # Wrap in JSON-RPC 2.0 notification envelope (no id)
            await self._sse_manager.publish(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                },
                client_id=client_id,
            )

    async def notify_resource_updated(self, uri: str) -> None:
        """Notify clients that a resource has been updated.

        Args:
            uri: The URI of the updated resource.
        """
        await self.publish_notification("notifications/resources/updated", {"uri": uri})

    async def notify_tools_list_changed(self) -> None:
        """Notify clients that the tool list has changed."""
        await self.publish_notification("notifications/tools/list_changed", {})
