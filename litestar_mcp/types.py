"""Public type definitions for the litestar-mcp integration surface."""

from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from litestar_mcp.executor import ToolExecutionContext

__all__ = ("MCPDependencyProvider",)

DependencyProviderContext = AbstractAsyncContextManager[Any] | AbstractContextManager[Any]
"""Return type of an :class:`MCPDependencyProvider` call.

Sync or async context managers are both accepted — async is preferred for
I/O-bound resources, sync works for purely synchronous adapters such as
DuckDB or stdlib ``sqlite3``.
"""


@runtime_checkable
class MCPDependencyProvider(Protocol):
    """Protocol for :attr:`MCPConfig.dependency_provider`.

    An implementation is a callable (typically an ``@asynccontextmanager``
    or ``@contextmanager``) that takes a
    :class:`~litestar_mcp.executor.ToolExecutionContext` and returns a
    (sync or async) context manager yielding a mapping of keyword arguments
    to inject into the tool handler.

    ``ctx.request`` is set to the inbound :class:`~litestar.Request` for
    HTTP-mode invocations and ``None`` for CLI/stdio invocations — providers
    that need request-scoped state (headers, request.state, DI containers
    bound to the request) should guard on ``ctx.request is not None``.
    """

    def __call__(
        self,
        ctx: "ToolExecutionContext",
    ) -> DependencyProviderContext: ...
