"""Litestar MCP exceptions."""

__all__ = ("BridgeConnectionError", "BridgeMessageTooLargeError", "LitestarMCPError", "MissingDependencyError")


class LitestarMCPError(Exception):
    """Base exception for Litestar MCP."""


class BridgeConnectionError(LitestarMCPError):
    """Raised when the stdio bridge cannot connect to its target endpoint."""

    def __init__(self, endpoint: str) -> None:
        super().__init__(
            f"Could not connect to Litestar MCP endpoint at {endpoint}. "
            "Make sure the Litestar app is running and reachable, or pass --base-url/--endpoint with the correct URL.",
        )


class BridgeMessageTooLargeError(LitestarMCPError, ValueError):
    """Raised when a stdio JSON-RPC frame exceeds the bridge line limit."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"Stdio JSON-RPC message exceeded the bridge limit of {max_bytes} bytes.")


class MissingDependencyError(LitestarMCPError, ImportError):
    """Missing optional dependency.

    This exception is raised when a module depends on a dependency that has not
    been installed.

    Args:
        package: Name of the missing package.
        install_package: Optional alternative package name to install directly.
        extra: Optional project extra that installs the missing package.
    """

    def __init__(self, package: str, install_package: str | None = None, *, extra: str | None = None) -> None:
        install_package = install_package or package
        extra = extra or install_package
        super().__init__(
            f"Package {package!r} is not installed but required. You can install it by running "
            f"'pip install litestar-mcp[{extra}]' to install litestar-mcp with the required extra "
            f"or 'pip install {install_package}' to install the package separately",
        )
