"""Central registry for MCP tools and resources."""

from typing import TYPE_CHECKING, Any, Dict, Optional
from weakref import WeakKeyDictionary

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler
    from litestar_mcp.sse import SSEManager


class Registry:
    """Central registry for MCP tools and resources.
    
    This class decouples metadata storage and discovery from the route handlers themselves,
    avoiding issues with __slots__ or object mutation.
    """

    def __init__(self) -> None:
        """Initialize the registry."""
        self._tools: Dict[str, "BaseRouteHandler"] = {}
        self._resources: Dict[str, "BaseRouteHandler"] = {}
        self._metadata: Dict[Any, Dict[str, Any]] = {}
        self._sse_manager: Optional["SSEManager"] = None

    def set_sse_manager(self, manager: "SSEManager") -> None:
        """Set the SSE manager for notifications."""
        self._sse_manager = manager

    @property
    def tools(self) -> Dict[str, "BaseRouteHandler"]:
        """Get registered tools."""
        return self._tools

    @property
    def resources(self) -> Dict[str, "BaseRouteHandler"]:
        """Get registered resources."""
        return self._resources

    def register_tool(self, name: str, handler: "BaseRouteHandler") -> None:
        """Register a tool.
        
        Args:
            name: The tool name.
            handler: The route handler.
        """
        self._tools[name] = handler

    def register_resource(self, name: str, handler: "BaseRouteHandler") -> None:
        """Register a resource.
        
        Args:
            name: The resource name.
            handler: The route handler.
        """
        self._resources[name] = handler

    async def publish_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Publish a notification to all connected clients.
        
        Args:
            method: The notification method (e.g., 'notifications/resources/updated').
            params: The notification parameters.
        """
        if self._sse_manager:
            await self._sse_manager.broadcast({"method": method, "params": params})

    async def notify_resource_updated(self, uri: str) -> None:
        """Notify clients that a resource has been updated.
        
        Args:
            uri: The URI of the updated resource.
        """
        await self.publish_notification("notifications/resources/updated", {"uri": uri})

    def set_metadata(self, obj: Any, metadata: Dict[str, Any]) -> None:
        """Set MCP metadata for an object.
        
        Args:
            obj: The object (function or handler).
            metadata: The metadata dictionary.
        """
        self._metadata[obj] = metadata

    def get_metadata(self, obj: Any) -> Optional[Dict[str, Any]]:
        """Get MCP metadata for an object.
        
        Args:
            obj: The object to check.
            
        Returns:
            The metadata dictionary if found, else None.
        """
        return self._metadata.get(obj)
