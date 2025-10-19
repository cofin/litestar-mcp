"""MCP Tool Registry for handler metadata storage.

This module provides a plugin-managed registry that decouples MCP metadata
from handler objects to support re-registration and avoid __slots__ mutation issues.
"""

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
from weakref import ReferenceType
from weakref import ref as weakref_ref

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler

from litestar_mcp.utils import get_handler_function

__all__ = ("HandlerSignature", "MCPMetadata", "MCPToolRegistry")


@dataclass(frozen=True)
class HandlerSignature:
    """Stable composite key for handler identification.

    Uses route path, HTTP methods, and function qualname to uniquely
    identify handlers across re-registrations.

    Attributes:
        route_path: The HTTP route path (e.g., "/users")
        http_methods: Tuple of HTTP methods (e.g., ("GET",))
        endpoint_qualname: Fully qualified name (e.g., "app.routes.get_users")
        app_namespace: Optional namespace for multi-app scenarios
    """

    route_path: str
    http_methods: "tuple[str, ...]"
    endpoint_qualname: str
    app_namespace: Optional[str] = None

    @classmethod
    def from_handler(cls, handler: "BaseRouteHandler") -> "HandlerSignature":
        """Extract signature from Litestar handler.

        Args:
            handler: Litestar route handler to extract signature from.

        Returns:
            HandlerSignature instance with normalized values.
        """
        fn = get_handler_function(handler)
        methods = tuple(sorted(handler.http_methods))  # type: ignore[attr-defined]
        path = handler.path if hasattr(handler, "path") else "/"
        qualname = f"{fn.__module__}.{fn.__qualname__}"

        return cls(
            route_path=path,
            http_methods=methods,
            endpoint_qualname=qualname,
            app_namespace=None,
        )


@dataclass
class MCPMetadata:
    """MCP metadata for tools and resources.

    Attributes:
        type: Either "tool" or "resource"
        name: MCP-exposed name
        description: Optional description override
        handler_ref: Weak reference to avoid memory leaks
    """

    type: str
    name: str
    description: Optional[str] = None
    handler_ref: Optional["ReferenceType[BaseRouteHandler]"] = None


class MCPToolRegistry:
    """Plugin-managed registry for MCP tools and resources.

    Decouples metadata storage from handler objects to support
    re-registration and avoid __slots__ mutation issues.

    Thread-safe with optional locking for concurrent access.
    """

    def __init__(self, thread_safe: bool = True) -> None:
        """Initialize the registry.

        Args:
            thread_safe: Enable thread-safe operations with RLock (default: True).
        """
        self._registry: dict[HandlerSignature, MCPMetadata] = {}
        self._lock: Optional[threading.RLock] = threading.RLock() if thread_safe else None

    def register(
        self,
        handler: "BaseRouteHandler",
        metadata_type: str,
        name: str,
        description: Optional[str] = None,
    ) -> None:
        """Register a handler with MCP metadata.

        Args:
            handler: The Litestar route handler
            metadata_type: "tool" or "resource"
            name: MCP-exposed name
            description: Optional description override

        Raises:
            ValueError: If handler signature already registered with different name
        """
        sig = HandlerSignature.from_handler(handler)

        if self._lock:
            with self._lock:
                self._register_unlocked(sig, handler, metadata_type, name, description)
        else:
            self._register_unlocked(sig, handler, metadata_type, name, description)

    def _register_unlocked(
        self,
        sig: HandlerSignature,
        handler: "BaseRouteHandler",
        metadata_type: str,
        name: str,
        description: Optional[str],
    ) -> None:
        """Internal registration without locking."""
        if sig in self._registry:
            existing = self._registry[sig]
            if existing.name != name:
                msg = (
                    f"Handler {sig.endpoint_qualname} already registered as "
                    f"'{existing.name}', cannot re-register as '{name}'"
                )
                raise ValueError(msg)

        self._registry[sig] = MCPMetadata(
            type=metadata_type,
            name=name,
            description=description,
            handler_ref=weakref_ref(handler),
        )

    def unregister(self, handler: "BaseRouteHandler") -> bool:
        """Unregister a handler.

        Args:
            handler: The Litestar route handler to unregister.

        Returns:
            True if handler was registered and removed, False otherwise
        """
        sig = HandlerSignature.from_handler(handler)

        if self._lock:
            with self._lock:
                return self._registry.pop(sig, None) is not None
        else:
            return self._registry.pop(sig, None) is not None

    def rebuild(self, route_handlers: "list[Any]") -> "tuple[set[str], set[str]]":
        """Rebuild registry from current route handlers.

        Args:
            route_handlers: Litestar route handlers to scan

        Returns:
            Tuple of (added_names, removed_names)
        """
        if self._lock:
            with self._lock:
                return self._rebuild_unlocked(route_handlers)
        else:
            return self._rebuild_unlocked(route_handlers)

    def _rebuild_unlocked(self, route_handlers: "list[Any]") -> "tuple[set[str], set[str]]":
        """Internal rebuild without locking."""
        from litestar.handlers import BaseRouteHandler

        old_names = {meta.name for meta in self._registry.values()}
        self._registry.clear()

        for handler in route_handlers:
            if isinstance(handler, BaseRouteHandler):
                fn = get_handler_function(handler)
                pending = getattr(fn, "_mcp_pending", None)

                if pending:
                    self._register_unlocked(
                        HandlerSignature.from_handler(handler),
                        handler,
                        pending["type"],
                        pending["name"],
                        pending.get("description"),
                    )
                elif handler.opt:
                    if "mcp_tool" in handler.opt:
                        self._register_unlocked(
                            HandlerSignature.from_handler(handler),
                            handler,
                            "tool",
                            handler.opt["mcp_tool"],
                            None,
                        )
                    if "mcp_resource" in handler.opt:
                        self._register_unlocked(
                            HandlerSignature.from_handler(handler),
                            handler,
                            "resource",
                            handler.opt["mcp_resource"],
                            None,
                        )

            if getattr(handler, "route_handlers", None):
                added, removed = self._rebuild_unlocked(handler.route_handlers)

        new_names = {meta.name for meta in self._registry.values()}
        added = new_names - old_names
        removed = old_names - new_names

        return (added, removed)

    def list_tools(self) -> "dict[str, BaseRouteHandler]":
        """Get all registered tools.

        Returns:
            Dict mapping tool name to handler (filters out dead weakrefs)
        """
        tools: dict[str, BaseRouteHandler] = {}

        if self._lock:
            with self._lock:
                for meta in self._registry.values():
                    if meta.type == "tool" and meta.handler_ref is not None:
                        handler = meta.handler_ref()
                        if handler is not None:
                            tools[meta.name] = handler
        else:
            for meta in self._registry.values():
                if meta.type == "tool" and meta.handler_ref is not None:
                    handler = meta.handler_ref()
                    if handler is not None:
                        tools[meta.name] = handler

        return tools

    def list_resources(self) -> "dict[str, BaseRouteHandler]":
        """Get all registered resources.

        Returns:
            Dict mapping resource name to handler (filters out dead weakrefs)
        """
        resources: dict[str, BaseRouteHandler] = {}

        if self._lock:
            with self._lock:
                for meta in self._registry.values():
                    if meta.type == "resource" and meta.handler_ref is not None:
                        handler = meta.handler_ref()
                        if handler is not None:
                            resources[meta.name] = handler
        else:
            for meta in self._registry.values():
                if meta.type == "resource" and meta.handler_ref is not None:
                    handler = meta.handler_ref()
                    if handler is not None:
                        resources[meta.name] = handler

        return resources

    def get_by_name(self, name: str) -> Optional["BaseRouteHandler"]:
        """Get handler by MCP name.

        Args:
            name: MCP tool or resource name to look up.

        Returns:
            Handler instance if found and still alive, None otherwise
        """
        if self._lock:
            with self._lock:
                for meta in self._registry.values():
                    if meta.name == name and meta.handler_ref is not None:
                        return meta.handler_ref()
        else:
            for meta in self._registry.values():
                if meta.name == name and meta.handler_ref is not None:
                    return meta.handler_ref()

        return None

    def get_metadata(self, handler: "BaseRouteHandler") -> Optional[MCPMetadata]:
        """Get metadata for a handler.

        Args:
            handler: The route handler to get metadata for.

        Returns:
            MCPMetadata if found, None otherwise
        """
        sig = HandlerSignature.from_handler(handler)

        if self._lock:
            with self._lock:
                return self._registry.get(sig)
        else:
            return self._registry.get(sig)
