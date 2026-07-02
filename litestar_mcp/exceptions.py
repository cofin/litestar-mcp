"""Litestar MCP exceptions."""

__all__ = ("LitestarMCPError", "MissingDependencyError")


class LitestarMCPError(Exception):
    """Base exception for Litestar MCP."""


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
