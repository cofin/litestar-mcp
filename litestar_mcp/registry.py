"""Central registry for MCP tools and resources."""

from typing import TYPE_CHECKING, Any, Dict, Optional
from weakref import WeakKeyDictionary

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler


class Registry:
    """Central registry for MCP tools and resources.
    
    This class decouples metadata storage and discovery from the route handlers themselves,
    avoiding issues with __slots__ or object mutation.
    """

    def __init__(self) -> None:
        """Initialize the registry."""
        self._tools: Dict[str, "BaseRouteHandler"] = {}
        self._resources: Dict[str, "BaseRouteHandler"] = {}
        self._metadata: WeakKeyDictionary[Any, Dict[str, Any]] = WeakKeyDictionary()

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
